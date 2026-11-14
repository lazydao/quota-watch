"""Gemini quota through the Antigravity CLI (`agy`) status-line payload.

`agy` pipes its session state, including the remaining quota of every model
group, to a user-configured status-line command. Quota Watch keeps only the
filtered quota fields from that payload; it reads no Google credential and
makes no network request.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .claude import _atomic_json_write, cache_path as _claude_cache_path
from .models import ProviderSnapshot, QuotaBucket, QuotaWindow, unavailable_snapshot


class GeminiError(RuntimeError):
    pass


PROVIDER = "gemini"
# Quota only moves while agy is in use, so an idle cache stays meaningful for
# longer than a polled provider's would.
AGE_NOTICE_SECONDS = 900
STALE_AFTER_SECONDS = 86_400

_GROUP_NAMES = {
    "gemini": "Gemini",
    "3p": "Claude + GPT",
}
_NAMED_WINDOWS = {
    "hourly": ("1h", 60),
    "daily": ("1d", 1_440),
    "weekly": ("7d", 10_080),
    "monthly": ("30d", 43_200),
}
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1_440, "w": 10_080}
_DURATION = re.compile(r"^(\d{1,4})([mhdw])$")
_MAX_BUCKETS = 32


def cache_path() -> Path:
    return _claude_cache_path().with_name("gemini.json")


def settings_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def _split_bucket_key(key: str) -> tuple[str, str, int | None]:
    """Split a key such as ``gemini-5h`` into its group, label and duration."""
    group, separator, suffix = key.rpartition("-")
    if separator and group:
        suffix = suffix.lower()
        if suffix in _NAMED_WINDOWS:
            label, minutes = _NAMED_WINDOWS[suffix]
            return group, label, minutes
        match = _DURATION.match(suffix)
        if match:
            minutes = int(match.group(1)) * _UNIT_MINUTES[match.group(2)]
            if minutes > 0:
                return group, suffix, minutes
    return key, "quota", None


def _remaining_fraction(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        fraction = float(value)
    except OverflowError:
        return None
    if not math.isfinite(fraction):
        return None
    return max(0.0, min(1.0, fraction))


def _reset_timestamp(payload: dict[str, Any], now: int) -> int | None:
    reset_time = payload.get("reset_time")
    if isinstance(reset_time, str):
        try:
            parsed = datetime.fromisoformat(reset_time.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
    seconds = payload.get("reset_in_seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    if not math.isfinite(seconds) or seconds < 0 or seconds > 366 * 86_400:
        return None
    return now + int(seconds)


def _plan_tier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= 64 else None


def parse_statusline(payload: dict[str, Any], now: int | None = None) -> ProviderSnapshot:
    quota = payload.get("quota")
    if not isinstance(quota, dict):
        raise GeminiError("Antigravity status-line input does not contain quota.")
    now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    plan_type = _plan_tier(payload.get("plan_tier"))

    grouped: dict[str, list[QuotaWindow]] = {}
    for key, entry in list(quota.items())[:_MAX_BUCKETS]:
        if not isinstance(key, str) or not key.strip() or len(key) > 64 or not isinstance(entry, dict):
            continue
        if entry.get("disabled") is True:
            continue
        # Buckets reported only as an absolute amount have no known total.
        fraction = _remaining_fraction(entry.get("remaining_fraction"))
        if fraction is None:
            continue
        group, label, minutes = _split_bucket_key(key.strip())
        grouped.setdefault(group, []).append(
            QuotaWindow(
                label=label,
                used_percent=round((1.0 - fraction) * 100, 2),
                window_minutes=minutes,
                resets_at=_reset_timestamp(entry, now),
            )
        )

    if not grouped:
        raise GeminiError("Antigravity status-line input has no usable quota buckets yet.")

    buckets = []
    for group, windows in grouped.items():
        windows.sort(key=lambda window: (window.window_minutes is None, window.window_minutes or 0))
        buckets.append(
            QuotaBucket(
                bucket_id="gemini_" + group.casefold(),
                name=_GROUP_NAMES.get(group.casefold(), group),
                windows=windows,
                plan_type=plan_type,
            )
        )
    buckets.sort(key=lambda bucket: (bucket.bucket_id != "gemini_gemini", bucket.name.casefold()))
    return ProviderSnapshot(provider=PROVIDER, status="available", buckets=buckets)


def _read_cache(source: Path, now: int) -> tuple[str, list[QuotaBucket]]:
    """Return the capture time and the cached buckets with expired windows removed."""
    payload = json.loads(source.read_text(encoding="utf-8"))
    captured_at = str(payload["captured_at"])
    buckets = []
    for bucket in payload.get("buckets", []):
        windows = []
        for window in bucket.get("windows", []):
            resets_at = window.get("resets_at")
            if type(resets_at) is not int:
                resets_at = None
            # The usage of a window that has reset is no longer known.
            if resets_at is not None and resets_at <= now:
                continue
            minutes = window.get("window_minutes")
            windows.append(
                QuotaWindow(
                    label=str(window["label"]),
                    used_percent=float(window["used_percent"]),
                    window_minutes=minutes if type(minutes) is int else None,
                    resets_at=resets_at,
                )
            )
        if windows:
            buckets.append(
                QuotaBucket(
                    bucket_id=str(bucket["bucket_id"]),
                    name=str(bucket["name"]),
                    windows=windows,
                    plan_type=_plan_tier(bucket.get("plan_type")),
                )
            )
    return captured_at, buckets


def ingest_statusline(payload: dict[str, Any], destination: Path | None = None) -> ProviderSnapshot:
    snapshot = parse_statusline(payload)
    destination = destination or cache_path()
    _atomic_json_write(
        destination,
        {
            "schema_version": 1,
            "provider": snapshot.provider,
            "status": snapshot.status,
            "captured_at": snapshot.captured_at,
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


def _age_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 120:
        return f"{minutes} minutes"
    hours = minutes // 60
    return f"{hours} hours" if hours < 48 else f"{hours // 24} days"


def read_cached_snapshot(source: Path | None = None) -> ProviderSnapshot:
    source = source or cache_path()
    if not source.exists():
        return unavailable_snapshot(PROVIDER, "Run `quota setup-gemini`, then use agy once.")
    try:
        now = datetime.now(timezone.utc)
        captured_at, buckets = _read_cache(source, int(now.timestamp()))
        captured = datetime.fromisoformat(captured_at)
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        age = (now - captured.astimezone(timezone.utc)).total_seconds()
    except (AttributeError, OSError, ValueError, KeyError, TypeError) as error:
        return unavailable_snapshot(PROVIDER, f"Gemini cache is invalid: {error}")

    if not buckets:
        return ProviderSnapshot(
            provider=PROVIDER,
            status="stale",
            captured_at=captured_at,
            message="Gemini quota windows have reset since the last agy session; use agy to refresh.",
        )
    message = None
    if age > AGE_NOTICE_SECONDS:
        message = f"Last Gemini update from agy is {_age_text(age)} old."
    return ProviderSnapshot(
        provider=PROVIDER,
        status="stale" if age > STALE_AFTER_SECONDS else "available",
        buckets=buckets,
        captured_at=captured_at,
        message=message,
    )


def fetch_gemini_snapshot(source: Path | None = None) -> ProviderSnapshot | None:
    """Return the Gemini snapshot, or None until the agy bridge has been set up."""
    source = source or cache_path()
    if not source.exists():
        return None
    return read_cached_snapshot(source)


def compact_status(snapshot: ProviderSnapshot) -> str:
    parts = []
    for bucket in snapshot.buckets:
        values = " · ".join(f"{window.label} {window.used_percent:g}%" for window in bucket.windows)
        parts.append(f"{bucket.name} {values}")
    return " | ".join(parts)


def statusline_command() -> str:
    arguments = [sys.executable, "-m", "quota_watch", "gemini-ingest"]
    if os.name != "nt":
        return shlex.join(arguments)
    # agy runs the status line through `cmd /c` on Windows.
    return f'"{sys.executable}" -m quota_watch gemini-ingest'


def read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GeminiError(f"Antigravity CLI settings are not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise GeminiError("Antigravity CLI settings root must be a JSON object.")
    return payload


def existing_statusline(settings: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return the current statusLine entry and its command, when one is active."""
    current = settings.get("statusLine")
    if current is None:
        return {}, None
    if not isinstance(current, dict):
        raise GeminiError("Antigravity CLI statusLine must be a JSON object.")
    command = current.get("command")
    if command is not None and not isinstance(command, str):
        raise GeminiError("Antigravity CLI statusLine command must be a string.")
    # agy ships a placeholder entry with an empty command and enabled=false.
    if not command or not command.strip() or current.get("enabled") is False:
        return dict(current), None
    return dict(current), command


def configure_statusline(
    path: Path | None = None,
    dry_run: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    path = path or settings_path()
    settings = read_settings(path)
    current, existing_command = existing_statusline(settings)
    command = statusline_command()
    entry = {**current, "type": "command", "command": command, "enabled": True}
    if existing_command:
        if "gemini-ingest" in existing_command:
            return False, "Antigravity CLI statusLine already forwards quota data.", current
        return False, "Existing Antigravity CLI statusLine was left unchanged.", entry
    # Keep agy's built-in status line visible next to the bridge's output.
    entry.setdefault("stack_with_default", True)

    updated = dict(settings)
    updated["statusLine"] = entry
    if dry_run:
        return True, "Dry run: Antigravity CLI statusLine can be added.", entry

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + ".quota-watch.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    _atomic_json_write(path, updated)
    return True, f"Configured Antigravity CLI statusLine in {path}.", entry
