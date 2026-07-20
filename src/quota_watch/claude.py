from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
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


def cache_path() -> Path:
    override = os.environ.get("QUOTA_WATCH_CACHE_DIR")
    if override:
        root = Path(override).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "quota-watch"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "quota-watch"
    return root / "claude.json"


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


def _parse_limit(payload: Any, label: str, minutes: int) -> QuotaWindow | None:
    if not isinstance(payload, dict):
        return None
    used = payload.get("used_percentage")
    if not isinstance(used, (int, float)) or not 0 <= float(used) <= 100:
        return None
    resets_at = payload.get("resets_at")
    if not isinstance(resets_at, int):
        resets_at = None
    return QuotaWindow(
        label=label,
        used_percent=float(used),
        window_minutes=minutes,
        resets_at=resets_at,
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
        buckets=[QuotaBucket(bucket_id="claude", name="Claude", windows=windows, plan_type=plan_type)],
    )
    _atomic_json_write(
        destination,
        {
            "schema_version": 1,
            "provider": snapshot.provider,
            "status": snapshot.status,
            "captured_at": snapshot.captured_at,
            "buckets": [
                {
                    "bucket_id": "claude",
                    "name": "Claude",
                    "plan_type": plan_type,
                    "windows": [
                        {
                            "label": window.label,
                            "used_percent": window.used_percent,
                            "window_minutes": window.window_minutes,
                            "resets_at": window.resets_at,
                        }
                        for window in windows
                    ],
                }
            ],
        },
    )
    return snapshot


def read_cached_snapshot(source: Path | None = None, stale_after_seconds: int = 900) -> ProviderSnapshot:
    source = source or cache_path()
    if not source.exists():
        return unavailable_snapshot("claude", "Run `quota setup-claude`, then use Claude Code once.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        captured_at = payload["captured_at"]
        bucket_payloads = payload.get("buckets", [])
        if bucket_payloads and not _normalize_subscription_type(bucket_payloads[0].get("plan_type")):
            plan_type = query_subscription_type()
            if plan_type:
                bucket_payloads[0]["plan_type"] = plan_type
                try:
                    _atomic_json_write(source, payload)
                except OSError:
                    pass
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
                ],
            )
            for bucket in bucket_payloads
        ]
        captured = datetime.fromisoformat(captured_at)
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
