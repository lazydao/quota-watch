from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .models import ProviderSnapshot, QuotaBucket, QuotaWindow, unavailable_snapshot


class CodexError(RuntimeError):
    pass


def find_codex_executable() -> Path:
    override = os.environ.get("QUOTA_WATCH_CODEX_PATH")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise CodexError(f"QUOTA_WATCH_CODEX_PATH does not exist: {candidate}")

    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        # The official standalone installer uses this location.
        candidates.append(Path(local_app_data) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")

    if local_app_data:
        # Codex Desktop may expose a bundled CLI here. Keep it as a fallback.
        bin_dir = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        candidates.append(bin_dir / "codex.exe")
        if bin_dir.is_dir():
            nested = sorted(
                bin_dir.glob("*/codex.exe"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(nested)

    for name in ("codex.exe", "codex"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise CodexError("Codex executable was not found. Install Codex or set QUOTA_WATCH_CODEX_PATH.")


def _process_command(executable: Path) -> list[str]:
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", str(executable), "app-server"]
    return [str(executable), "app-server"]


def _start_reader(stream: Any, output: queue.Queue[str]) -> threading.Thread:
    def read_lines() -> None:
        for line in stream:
            output.put(line.rstrip("\r\n"))

    thread = threading.Thread(target=read_lines, daemon=True)
    thread.start()
    return thread


def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise CodexError("Codex app-server stdin is unavailable.")
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _wait_for_id(
    output: queue.Queue[str],
    request_id: int,
    deadline: float,
    process: subprocess.Popen[str],
) -> dict[str, Any]:
    while time.monotonic() < deadline:
        if process.poll() is not None and output.empty():
            raise CodexError(f"Codex app-server exited with code {process.returncode}.")
        try:
            line = output.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise CodexError(f"Codex app-server error: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise CodexError("Codex app-server returned an invalid result.")
        return result
    raise CodexError(f"Timed out waiting for Codex app-server request {request_id}.")


def query_rate_limits(timeout: float = 8.0, executable: Path | None = None) -> dict[str, Any]:
    executable = executable or find_codex_executable()
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        _process_command(executable),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise CodexError("Codex app-server pipes are unavailable.")

    output: queue.Queue[str] = queue.Queue()
    errors: queue.Queue[str] = queue.Queue()
    _start_reader(process.stdout, output)
    _start_reader(process.stderr, errors)
    deadline = time.monotonic() + timeout

    try:
        _send(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "quota-watch",
                        "title": "Quota Watch",
                        "version": "0.1.0",
                    },
                    "capabilities": {},
                },
            },
        )
        _wait_for_id(output, 1, deadline, process)
        _send(process, {"method": "initialized", "params": {}})
        _send(process, {"id": 2, "method": "account/rateLimits/read", "params": {}})
        return _wait_for_id(output, 2, deadline, process)
    except CodexError as error:
        error_lines: list[str] = []
        while not errors.empty() and len(error_lines) < 3:
            error_lines.append(errors.get_nowait())
        suffix = f" ({' | '.join(error_lines)})" if error_lines else ""
        raise CodexError(f"{error}{suffix}") from error
    finally:
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def _window_label(minutes: int | None, fallback: str) -> str:
    if minutes is None:
        return fallback
    if minutes % 10_080 == 0:
        return f"{minutes // 10_080}w"
    if minutes % 1_440 == 0:
        return f"{minutes // 1_440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _parse_window(payload: Any, fallback: str) -> QuotaWindow | None:
    if not isinstance(payload, dict):
        return None
    used_percent = payload.get("usedPercent")
    if not isinstance(used_percent, (int, float)):
        return None
    minutes = payload.get("windowDurationMins")
    if not isinstance(minutes, int):
        minutes = None
    resets_at = payload.get("resetsAt")
    if not isinstance(resets_at, int):
        resets_at = None
    return QuotaWindow(
        label=_window_label(minutes, fallback),
        used_percent=float(used_percent),
        window_minutes=minutes,
        resets_at=resets_at,
    )


def parse_rate_limits(result: dict[str, Any]) -> ProviderSnapshot:
    raw_buckets = result.get("rateLimitsByLimitId")
    if not isinstance(raw_buckets, dict) or not raw_buckets:
        single = result.get("rateLimits")
        if not isinstance(single, dict):
            raise CodexError("Codex response does not contain rate limits.")
        bucket_id = single.get("limitId") or "codex"
        raw_buckets = {str(bucket_id): single}

    buckets: list[QuotaBucket] = []
    for key, payload in raw_buckets.items():
        if not isinstance(payload, dict):
            continue
        bucket_id = str(payload.get("limitId") or key)
        display_name = payload.get("limitName")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = "Codex" if bucket_id == "codex" else bucket_id
        windows = [
            window
            for window in (
                _parse_window(payload.get("primary"), "primary"),
                _parse_window(payload.get("secondary"), "secondary"),
            )
            if window is not None
        ]
        windows.sort(key=lambda window: window.window_minutes or 0)
        plan_type = payload.get("planType")
        buckets.append(
            QuotaBucket(
                bucket_id=bucket_id,
                name=display_name,
                windows=windows,
                plan_type=plan_type if isinstance(plan_type, str) else None,
            )
        )

    if not buckets:
        raise CodexError("Codex response contains no usable quota buckets.")
    buckets.sort(key=lambda bucket: (bucket.bucket_id != "codex", bucket.name.lower()))
    return ProviderSnapshot(provider="codex", status="available", buckets=buckets)


def fetch_codex_snapshot(timeout: float = 8.0) -> ProviderSnapshot:
    try:
        return parse_rate_limits(query_rate_limits(timeout=timeout))
    except (CodexError, OSError, ValueError) as error:
        return unavailable_snapshot("codex", str(error))
