from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ProviderSnapshot, QuotaBucket, QuotaWindow, unavailable_snapshot


class ClaudeError(RuntimeError):
    pass


_RATE_LIMIT_WINDOWS = (
    ("five_hour", "5h", 300),
    ("seven_day", "7d", 10_080),
)

ACTIVE_REFRESH_INTERVAL_SECONDS = 600
_USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"

_WSL_USAGE_HELPER = r"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

endpoint = "https://api.anthropic.com/api/oauth/usage"
credentials_path = Path.home() / ".claude" / ".credentials.json"

def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)

try:
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    fail("Claude OAuth credentials are unavailable in WSL.")

oauth = credentials.get("claudeAiOauth")
if not isinstance(oauth, dict):
    fail("Claude OAuth credentials are unavailable in WSL.")

access_token = oauth.get("accessToken")
if not isinstance(access_token, str) or not access_token:
    fail("Claude OAuth access token is unavailable in WSL.")

request = urllib.request.Request(
    endpoint,
    headers={
        "Accept": "application/json",
        "Authorization": "Bearer " + access_token,
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-cli/2.1.0 (external, cli)",
    },
)

try:
    with urllib.request.urlopen(request, timeout=float(sys.argv[1])) as response:
        payload = json.load(response)
except urllib.error.HTTPError as error:
    fail(f"Claude usage request failed with HTTP status {error.code}.")
except (OSError, ValueError, urllib.error.URLError):
    fail("Claude usage request failed in WSL.")

if not isinstance(payload, dict):
    fail("Claude usage response is invalid.")

filtered = {"subscription_type": oauth.get("subscriptionType")}
for key in ("five_hour", "seven_day"):
    window = payload.get(key)
    if isinstance(window, dict):
        filtered[key] = {
            "utilization": window.get("utilization"),
            "resets_at": window.get("resets_at"),
        }

filtered["limits"] = []
limits = payload.get("limits")
for limit in limits if isinstance(limits, list) else []:
    if not isinstance(limit, dict):
        continue
    scope = limit.get("scope")
    model = scope.get("model") if isinstance(scope, dict) else None
    filtered["limits"].append({
        "kind": limit.get("kind"),
        "percent": limit.get("percent"),
        "resets_at": limit.get("resets_at"),
        "model_name": model.get("display_name") if isinstance(model, dict) else None,
    })
print(json.dumps(filtered, separators=(",", ":")))
"""


def cache_path() -> Path:
    override = os.environ.get("QUOTA_WATCH_CACHE_DIR")
    if override:
        root = Path(override).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "quota-watch"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "quota-watch"
    return root / "claude.json"


def oauth_refresh_state_path() -> Path:
    return cache_path().with_name("claude-oauth-refresh.json")


def claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalize_used_percentage(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            used = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str):
        try:
            used = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(used) or used < 0:
        return None
    return min(used, 100.0)


def _parse_limit(payload: Any, label: str, minutes: int) -> QuotaWindow | None:
    if not isinstance(payload, dict):
        return None
    used = _normalize_used_percentage(payload.get("used_percentage"))
    if used is None:
        return None
    resets_at = payload.get("resets_at")
    if not isinstance(resets_at, int):
        resets_at = None
    return QuotaWindow(
        label=label,
        used_percent=used,
        window_minutes=minutes,
        resets_at=resets_at,
    )


def _parse_reset_timestamp(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _parse_oauth_limit(payload: Any, label: str, minutes: int) -> QuotaWindow | None:
    if not isinstance(payload, dict):
        return None
    used = _normalize_used_percentage(payload.get("utilization"))
    if used is None:
        return None
    return QuotaWindow(
        label=label,
        used_percent=used,
        window_minutes=minutes,
        resets_at=_parse_reset_timestamp(payload.get("resets_at")),
    )


def _normalize_subscription_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= 64 else None


def _subscription_type(payload: Any) -> str | None:
    if not isinstance(payload, dict) or payload.get("loggedIn") is False:
        return None
    return _normalize_subscription_type(payload.get("subscriptionType"))


def _run_auth_status(command: list[str], timeout: float) -> str | None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        return _subscription_type(json.loads(completed.stdout))
    except json.JSONDecodeError:
        return None


def query_subscription_type(timeout: float = 3.0) -> str | None:
    executable = shutil.which("claude")
    if executable:
        subscription_type = _run_auth_status([executable, "auth", "status", "--json"], timeout)
        if subscription_type:
            return subscription_type

    if os.name != "nt":
        return None
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return None
    return _run_auth_status(
        [wsl, "--exec", "bash", "-lc", "claude auth status --json"],
        timeout,
    )


class _NoUsageRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        # Never forward a provider credential to a redirect target.
        return None


def query_oauth_usage_windows(timeout: float = 8.0) -> dict[str, Any]:
    if os.name != "nt":
        raise ClaudeError("Windows Claude usage queries are available only on Windows.")
    if timeout <= 0:
        raise ClaudeError("Windows Claude usage query timed out.")

    access_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    plan_type = None
    if not access_token:
        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
        try:
            credentials_path = config_dir / ".credentials.json"
            if credentials_path.stat().st_size > 1024 * 1024:
                raise ClaudeError("Windows Claude Code credentials are invalid.")
            credentials = json.loads(credentials_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError, RecursionError):
            raise ClaudeError("Windows Claude Code credentials are unavailable.") from None
        oauth = credentials.get("claudeAiOauth") if isinstance(credentials, dict) else None
        if not isinstance(oauth, dict):
            raise ClaudeError("Windows Claude Code OAuth credentials are unavailable.")
        access_token = oauth.get("accessToken")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ClaudeError("Windows Claude Code access token is unavailable.")
        access_token = access_token.strip()
        expires_at = oauth.get("expiresAt")
        if (
            not isinstance(expires_at, bool)
            and isinstance(expires_at, (int, float))
            and expires_at <= datetime.now(timezone.utc).timestamp() * 1000
        ):
            raise ClaudeError("Windows Claude Code access token has expired; renew the login in Claude Code.")
        scopes = oauth.get("scopes")
        if isinstance(scopes, list) and "user:profile" not in scopes:
            raise ClaudeError("Windows Claude Code token lacks quota-query permission (user:profile).")
        plan_type = _normalize_subscription_type(oauth.get("subscriptionType"))

    try:
        request = urllib.request.Request(
            _USAGE_ENDPOINT,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + access_token,
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-cli/2.1.0 (external, cli)",
            },
            method="GET",
        )
        opener = urllib.request.build_opener(_NoUsageRedirect())
        with opener.open(request, timeout=timeout) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ClaudeError("Windows Claude usage response is too large.")
        payload = json.loads(body.decode("utf-8-sig"))
    except urllib.error.HTTPError as error:
        raise ClaudeError(f"Windows Claude usage request failed with HTTP status {error.code}.") from None
    except (OSError, UnicodeError, ValueError, RecursionError, urllib.error.URLError):
        raise ClaudeError("Windows Claude usage request failed.") from None
    if not isinstance(payload, dict):
        raise ClaudeError("Windows Claude usage response is invalid.")

    filtered: dict[str, Any] = {"subscription_type": plan_type, "limits": []}
    for key in ("five_hour", "seven_day"):
        window = payload.get(key)
        if isinstance(window, dict):
            filtered[key] = {name: window.get(name) for name in ("utilization", "resets_at")}
    limits = payload.get("limits")
    for limit in limits if isinstance(limits, list) else []:
        if not isinstance(limit, dict):
            continue
        scope = limit.get("scope")
        model = scope.get("model") if isinstance(scope, dict) else None
        filtered["limits"].append({
            "kind": limit.get("kind"),
            "percent": limit.get("percent"),
            "resets_at": limit.get("resets_at"),
            "model_name": model.get("display_name") if isinstance(model, dict) else None,
        })
    return filtered


def query_oauth_usage_wsl(timeout: float = 8.0, distro: str | None = None) -> dict[str, Any]:
    if os.name != "nt":
        raise ClaudeError("Claude active refresh through WSL is available only on Windows.")
    executable = shutil.which("wsl.exe")
    if not executable:
        raise ClaudeError("wsl.exe was not found.")

    selected_distro = distro or os.environ.get("QUOTA_WATCH_CLAUDE_WSL_DISTRO")
    command = [executable]
    if selected_distro:
        command.extend(["--distribution", selected_distro])
    command.extend(["--exec", "python3", "-c", _WSL_USAGE_HELPER, str(timeout)])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClaudeError(f"Could not query Claude usage through WSL: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "WSL helper failed."
        raise ClaudeError(detail[:240])
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ClaudeError("Claude usage helper returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise ClaudeError("Claude usage helper returned an invalid payload.")
    return payload


def parse_oauth_usage(payload: dict[str, Any]) -> ProviderSnapshot:
    windows = [
        window
        for key, label, minutes in _RATE_LIMIT_WINDOWS
        for window in (_parse_oauth_limit(payload.get(key), label, minutes),)
        if window is not None
    ]
    plan_type = _normalize_subscription_type(payload.get("subscription_type"))
    windows_by_label = {window.label: window for window in windows}
    model_buckets = {}
    limits = payload.get("limits")
    for limit in limits if isinstance(limits, list) else []:
        if not isinstance(limit, dict):
            continue
        kind = limit.get("kind")
        if kind not in ("session", "weekly_all", "weekly_scoped"):
            continue
        window = _parse_oauth_limit(
            {"utilization": limit.get("percent"), "resets_at": limit.get("resets_at")},
            "5h" if kind == "session" else "7d",
            300 if kind == "session" else 10_080,
        )
        if window is None:
            continue
        if kind != "weekly_scoped":
            windows_by_label[window.label] = window
            continue
        name = limit.get("model_name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        model_buckets[name.casefold()] = QuotaBucket(
            bucket_id="claude_model_" + name.casefold(), name=name,
            windows=[window], plan_type=plan_type,
        )
    windows = [windows_by_label[label] for _, label, _ in _RATE_LIMIT_WINDOWS if label in windows_by_label]
    if not windows and not model_buckets:
        raise ClaudeError("Claude usage response has no usable quota windows.")
    return ProviderSnapshot(
        provider="claude",
        status="available",
        buckets=[
            QuotaBucket(
                bucket_id="claude",
                name="All models",
                windows=windows,
                plan_type=_normalize_subscription_type(payload.get("subscription_type")),
            )
        ] + list(model_buckets.values()),
    )


def _cached_plan_type(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        buckets = payload.get("buckets", [])
        return _normalize_subscription_type(buckets[0].get("plan_type")) if buckets else None
    except (AttributeError, OSError, TypeError, json.JSONDecodeError):
        return None


def _cached_unexpired_windows(path: Path) -> dict[str, QuotaWindow]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        buckets = payload.get("buckets", [])
        window_payloads = buckets[0].get("windows", []) if buckets else []
        minutes_by_label = {label: minutes for _, label, minutes in _RATE_LIMIT_WINDOWS}
        now = int(datetime.now(timezone.utc).timestamp())
        windows: dict[str, QuotaWindow] = {}
        for window_payload in window_payloads:
            if not isinstance(window_payload, dict):
                continue
            label = window_payload.get("label")
            minutes = minutes_by_label.get(label)
            if minutes is None:
                continue
            window = _parse_limit(
                {
                    "used_percentage": window_payload.get("used_percent"),
                    "resets_at": window_payload.get("resets_at"),
                },
                label,
                minutes,
            )
            if window is not None and window.resets_at is not None and window.resets_at > now:
                windows[label] = window
        return windows
    except (AttributeError, OSError, TypeError, json.JSONDecodeError):
        return {}


def _reset_time_key(bucket_id: Any, label: Any, minutes: Any) -> tuple[str, str, int] | None:
    if not isinstance(bucket_id, str) or not isinstance(label, str):
        return None
    if type(minutes) is not int or minutes <= 0:
        return None
    return bucket_id, label, minutes


def _cached_reset_times(source: Path) -> dict[tuple[str, str, int], int]:
    try:
        if source.stat().st_size > 1024 * 1024:
            return {}
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {}
    if not isinstance(payload, dict) or payload.get("provider") != "claude":
        return {}

    now = int(datetime.now(timezone.utc).timestamp())
    remembered: dict[tuple[str, str, int], int] = {}

    def remember(bucket_id: Any, window: Any) -> None:
        if not isinstance(window, dict):
            return
        key = _reset_time_key(bucket_id, window.get("label"), window.get("window_minutes"))
        resets_at = window.get("resets_at")
        if key is not None and type(resets_at) is int and resets_at > now:
            remembered[key] = resets_at

    entries = payload.get("remembered_reset_times")
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            remember(entry.get("bucket_id"), entry)
    buckets = payload.get("buckets")
    for bucket in buckets if isinstance(buckets, list) else []:
        if not isinstance(bucket, dict):
            continue
        windows = bucket.get("windows")
        for window in windows if isinstance(windows, list) else []:
            remember(bucket.get("bucket_id"), window)
    return remembered


def _reuse_reset_times(
    snapshot: ProviderSnapshot, remembered: dict[tuple[str, str, int], int],
) -> ProviderSnapshot:
    now = int(datetime.now(timezone.utc).timestamp())
    reused = False
    buckets = []
    for bucket in snapshot.buckets:
        windows = []
        for window in bucket.windows:
            key = _reset_time_key(bucket.bucket_id, window.label, window.window_minutes)
            resets_at = remembered.get(key) if key is not None else None
            if window.resets_at is None and resets_at is not None and resets_at > now:
                window = replace(window, resets_at=resets_at)
                reused = True
            windows.append(window)
        buckets.append(replace(bucket, windows=windows))
    message = snapshot.message
    if reused and message:
        message += " Using remembered reset times where available."
    return replace(snapshot, buckets=buckets, message=message)


def _write_snapshot(destination: Path, snapshot: ProviderSnapshot) -> ProviderSnapshot:
    remembered = _cached_reset_times(destination)
    snapshot = _reuse_reset_times(snapshot, remembered)
    now = int(datetime.now(timezone.utc).timestamp())
    for bucket in snapshot.buckets:
        for window in bucket.windows:
            key = _reset_time_key(bucket.bucket_id, window.label, window.window_minutes)
            if key is None or window.resets_at is None:
                continue
            remembered.pop(key, None)
            if type(window.resets_at) is int and window.resets_at > now:
                remembered[key] = window.resets_at
    _atomic_json_write(
        destination,
        {
            "schema_version": 1,
            "provider": snapshot.provider,
            "status": snapshot.status,
            "captured_at": snapshot.captured_at,
            "remembered_reset_times": [
                {"bucket_id": bucket_id, "label": label, "window_minutes": minutes, "resets_at": resets_at}
                for (bucket_id, label, minutes), resets_at in remembered.items()
            ],
            "buckets": [
                {
                    "bucket_id": bucket.bucket_id,
                    "name": bucket.name,
                    "plan_type": bucket.plan_type,
                    "windows": [
                        {
                            "label": window.label,
                            "used_percent": window.used_percent,
                            "window_minutes": window.window_minutes,
                            "resets_at": window.resets_at,
                        }
                        for window in bucket.windows
                    ],
                }
                for bucket in snapshot.buckets
            ],
        },
    )
    return snapshot


def ingest_statusline(payload: dict[str, Any], destination: Path | None = None) -> ProviderSnapshot:
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        raise ClaudeError("Claude status-line input does not contain rate_limits.")

    incoming_windows = [
        window
        for key, label, minutes in _RATE_LIMIT_WINDOWS
        for window in (
            _parse_limit(rate_limits.get(key), label, minutes),
        )
        if window is not None
    ]
    if not incoming_windows:
        raise ClaudeError("Claude status-line input has no usable quota windows yet.")

    destination = destination or cache_path()
    windows_by_label = {window.label: window for window in incoming_windows}
    for label, window in _cached_unexpired_windows(destination).items():
        windows_by_label.setdefault(label, window)
    windows = [
        windows_by_label[label]
        for _, label, _ in _RATE_LIMIT_WINDOWS
        if label in windows_by_label
    ]
    plan_type = _cached_plan_type(destination)
    snapshot = ProviderSnapshot(
        provider="claude",
        status="available",
        buckets=[QuotaBucket(bucket_id="claude", name="All models", windows=windows, plan_type=plan_type)],
    )
    cached = read_cached_snapshot(destination)
    snapshot.buckets.extend(
        bucket for bucket in cached.buckets
        if bucket.bucket_id.startswith("claude_model_") and bucket.windows
    )
    return _write_snapshot(destination, snapshot)


def read_cached_snapshot(
    source: Path | None = None, stale_after_seconds: int = 900, *, enrich_plan: bool = True,
) -> ProviderSnapshot:
    source = source or cache_path()
    if not source.exists():
        return unavailable_snapshot("claude", "Run `quota setup-claude`, then use Claude Code once.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        captured_at = payload["captured_at"]
        bucket_payloads = payload.get("buckets", [])
        if enrich_plan and bucket_payloads and not _normalize_subscription_type(bucket_payloads[0].get("plan_type")):
            plan_type = query_subscription_type()
            if plan_type:
                bucket_payloads[0]["plan_type"] = plan_type
                try:
                    _atomic_json_write(source, payload)
                except OSError:
                    pass
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        buckets = [
            QuotaBucket(
                bucket_id=str(bucket["bucket_id"]),
                name=str(bucket["name"]),
                plan_type=_normalize_subscription_type(bucket.get("plan_type")),
                windows=[
                    QuotaWindow(
                        label=str(window["label"]),
                        used_percent=float(window["used_percent"]),
                        window_minutes=window.get("window_minutes"),
                        resets_at=window.get("resets_at"),
                    )
                    for window in bucket.get("windows", [])
                    if not (
                        isinstance(window.get("resets_at"), int)
                        and window["resets_at"] <= now_timestamp
                    )
                ],
            )
            for bucket in bucket_payloads
        ]
        captured = datetime.fromisoformat(captured_at)
        buckets = [bucket for bucket in buckets if bucket.windows or bucket.bucket_id == "claude"]
        age = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()
        status = "stale" if age > stale_after_seconds else "available"
        message = f"Last Claude update is {int(age // 60)} minutes old." if status == "stale" else None
        return ProviderSnapshot(
            provider="claude",
            status=status,
            buckets=buckets,
            captured_at=captured_at,
            message=message,
        )
    except (AttributeError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return unavailable_snapshot("claude", f"Claude cache is invalid: {error}")


def _active_refresh_due(path: Path, interval_seconds: int, now: datetime) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        last_attempt = datetime.fromisoformat(str(payload["last_attempt_at"]))
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=timezone.utc)
        age = (now - last_attempt.astimezone(timezone.utc)).total_seconds()
        return age >= interval_seconds
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return True


def _has_quota_windows(snapshot: ProviderSnapshot) -> bool:
    return any(bucket.windows for bucket in snapshot.buckets)


def _desktop_history_paths() -> list[Path]:
    if os.name != "nt":
        return []

    paths: list[Path] = []
    if os.environ.get("APPDATA"):
        paths.append(Path(os.environ["APPDATA"]) / "Claude" / "plan-usage-history.json")
    if os.environ.get("LOCALAPPDATA"):
        paths.append(
            Path(os.environ["LOCALAPPDATA"])
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
            / "plan-usage-history.json"
        )
        paths.append(
            Path(os.environ["LOCALAPPDATA"])
            / "Claude-Data"
            / "claude_pzs8sxrjxfjjc"
            / "plan-usage-history.json"
        )
    return paths


def read_desktop_snapshot(
    source: Path | None = None,
    stale_after_seconds: int = 900,
) -> ProviderSnapshot:
    newest: ProviderSnapshot | None = None
    newest_capture = datetime.min.replace(tzinfo=timezone.utc)
    for path in [source] if source is not None else _desktop_history_paths():
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                continue
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("version") != 2:
            continue
        samples = payload.get("samples")
        if not isinstance(samples, list):
            continue

        now = datetime.now(timezone.utc)
        for sample in samples:
            if not isinstance(sample, dict) or not isinstance(sample.get("u"), dict):
                continue
            timestamp = sample.get("t")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            if timestamp <= 0:
                continue
            try:
                captured = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
            if captured <= newest_capture or timestamp > now.timestamp() * 1000 + 60_000:
                continue

            windows = [
                window
                for key, label, minutes in (("fh", "5h", 300), ("sd", "7d", 10_080))
                for window in (
                    _parse_limit({"used_percentage": sample["u"].get(key)}, label, minutes),
                )
                if window is not None
            ]
            if not windows:
                continue
            age = max(0.0, (now - captured).total_seconds())
            newest = ProviderSnapshot(
                provider="claude",
                status="stale" if age > stale_after_seconds else "available",
                buckets=[QuotaBucket(bucket_id="claude", name="All models", windows=windows)],
                captured_at=captured.isoformat(),
                message=(
                    f"Claude Desktop fallback ({int(age // 60)} min old). "
                    "Model quotas and plan unavailable."
                ),
            )
            newest_capture = captured

    return newest or unavailable_snapshot("claude", "Claude Desktop quota history is unavailable.")


def _local_fallback_snapshot(cached: ProviderSnapshot, destination: Path) -> ProviderSnapshot:
    # Prefer recent CLI data over the desktop app's partial history.
    if cached.status == "available" and _has_quota_windows(cached):
        return cached
    desktop = read_desktop_snapshot()
    if not _has_quota_windows(desktop):
        return cached
    desktop = _reuse_reset_times(desktop, _cached_reset_times(destination))
    if not _has_quota_windows(cached):
        return desktop
    try:
        desktop_capture = datetime.fromisoformat(desktop.captured_at).astimezone(timezone.utc)
        cached_capture = datetime.fromisoformat(cached.captured_at).astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return cached
    return desktop if desktop_capture > cached_capture else cached


def fetch_claude_snapshot(
    timeout: float = 8.0,
    destination: Path | None = None,
    refresh_state: Path | None = None,
    refresh_interval_seconds: int = ACTIVE_REFRESH_INTERVAL_SECONDS,
) -> ProviderSnapshot:
    destination = destination or cache_path()
    refresh_state = refresh_state or oauth_refresh_state_path()
    # Collection must not start WSL merely to fill a missing plan label.
    cached = read_cached_snapshot(destination, enrich_plan=False)
    now = datetime.now(timezone.utc)
    if not _active_refresh_due(refresh_state, refresh_interval_seconds, now):
        return _local_fallback_snapshot(cached, destination)

    try:
        _atomic_json_write(refresh_state, {"last_attempt_at": now.isoformat()})
    except OSError:
        pass

    deadline = time.monotonic() + timeout
    try:
        snapshot = parse_oauth_usage(query_oauth_usage_windows(timeout=timeout))
        return _write_snapshot(destination, snapshot)
    except (ClaudeError, OSError) as windows_error:
        fallback = _local_fallback_snapshot(cached, destination)
        if fallback.status == "available" and _has_quota_windows(fallback):
            return fallback
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClaudeError("Claude query timed out before WSL fallback.")
            snapshot = parse_oauth_usage(query_oauth_usage_wsl(timeout=remaining))
            return _write_snapshot(destination, snapshot)
        except (ClaudeError, OSError) as wsl_error:
            failure = f"Windows: {windows_error} WSL fallback: {wsl_error}"
        if _has_quota_windows(fallback):
            return fallback
        return unavailable_snapshot("claude", f"Claude active refresh unavailable: {failure}")


def compact_status(snapshot: ProviderSnapshot) -> str:
    values = [
        f"{window.label} {window.used_percent:g}%"
        for bucket in snapshot.buckets
        for window in bucket.windows
    ]
    return "Claude " + " · ".join(values)


def statusline_command() -> str:
    arguments = [sys.executable, "-m", "quota_watch", "claude-ingest"]
    if os.name != "nt":
        return shlex.join(arguments)

    # Claude Code may launch status lines through Git Bash on Windows. An
    # explicit PowerShell hop keeps native paths valid in either host shell.
    executable = str(sys.executable).replace("'", "''")
    return (
        "powershell.exe -NoLogo -NoProfile -NonInteractive "
        f'-Command "& \'{executable}\' -m quota_watch claude-ingest"'
    )


def configure_statusline(
    settings_path: Path | None = None,
    dry_run: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    settings_path = settings_path or claude_settings_path()
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ClaudeError(f"Claude settings are not valid JSON: {error}") from error
        if not isinstance(settings, dict):
            raise ClaudeError("Claude settings root must be a JSON object.")
    else:
        settings = {}

    entry = {
        "type": "command",
        "command": statusline_command(),
        "refreshInterval": 5,
    }
    if "statusLine" in settings:
        return False, "Existing Claude statusLine was left unchanged.", entry

    updated = dict(settings)
    updated["statusLine"] = entry
    if dry_run:
        return True, "Dry run: Claude statusLine can be added.", entry

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        backup = settings_path.with_name(settings_path.name + ".quota-watch.bak")
        shutil.copy2(settings_path, backup)
    _atomic_json_write(settings_path, updated)
    return True, f"Configured Claude statusLine in {settings_path}.", entry
