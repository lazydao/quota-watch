from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Sequence

from rich.console import Console
from rich.live import Live

from . import __version__
from .claude import (
    ClaudeError,
    compact_status,
    configure_statusline,
    ingest_statusline,
    read_cached_snapshot,
)
from .codex import fetch_codex_snapshot
from .models import ProviderSnapshot, utc_now_iso
from .render import build_dashboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor Claude Code and Codex quotas in the terminal.")
    parser.add_argument("--version", action="version", version=f"quota-watch {__version__}")
    parser.add_argument("--json", action="store_true", help="Print one machine-readable JSON snapshot.")
    parser.add_argument("--codex-timeout", type=float, default=8.0, help="Codex query timeout in seconds.")

    commands = parser.add_subparsers(dest="command")
    watch = commands.add_parser("watch", help="Refresh the dashboard until interrupted.")
    watch.add_argument("--interval", type=float, default=30.0, help="Refresh interval in seconds.")
    watch.add_argument(
        "--codex-timeout",
        type=float,
        default=argparse.SUPPRESS,
        help="Codex query timeout in seconds.",
    )

    commands.add_parser("claude-ingest", help=argparse.SUPPRESS)
    setup = commands.add_parser("setup-claude", help="Connect Claude Code through a local status-line bridge.")
    setup.add_argument("--dry-run", action="store_true", help="Show what would be configured without writing.")
    return parser


def collect_snapshots(codex_timeout: float) -> list[ProviderSnapshot]:
    return [read_cached_snapshot(), fetch_codex_snapshot(timeout=codex_timeout)]


def _json_payload(snapshots: list[ProviderSnapshot]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "providers": [snapshot.to_dict() for snapshot in snapshots],
    }


def run_once(console: Console, codex_timeout: float, as_json: bool) -> int:
    snapshots = collect_snapshots(codex_timeout)
    if as_json:
        sys.stdout.write(json.dumps(_json_payload(snapshots), ensure_ascii=False, indent=2) + "\n")
    else:
        console.print(build_dashboard(snapshots))
    return 0


def run_watch(console: Console, interval: float, codex_timeout: float) -> int:
    if interval < 1:
        raise ValueError("--interval must be at least 1 second.")
    try:
        dashboard = build_dashboard(collect_snapshots(codex_timeout))
        with Live(
            dashboard,
            console=console,
            screen=True,
            auto_refresh=False,
            transient=False,
        ) as live:
            while True:
                time.sleep(interval)
                live.update(build_dashboard(collect_snapshots(codex_timeout)), refresh=True)
    except KeyboardInterrupt:
        return 0


def run_claude_ingest() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ClaudeError("Claude status-line input must be a JSON object.")
        try:
            snapshot = ingest_statusline(payload)
        except ClaudeError:
            cached = read_cached_snapshot()
            if cached.buckets:
                print(f"{compact_status(cached)} · cached")
            else:
                print("Claude quota waiting for first response")
            return 0
        print(compact_status(snapshot))
        return 0
    except (ClaudeError, json.JSONDecodeError) as error:
        print(f"Claude quota unavailable: {error}")
        return 0


def run_setup_claude(console: Console, dry_run: bool) -> int:
    try:
        changed, message, entry = configure_statusline(dry_run=dry_run)
    except ClaudeError as error:
        console.print(f"[red]{error}[/red]")
        return 1
    console.print(message)
    if dry_run or not changed:
        console.print("Add or chain this command in the existing statusLine configuration:")
        console.print(entry["command"])
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.command == "watch":
        try:
            return run_watch(console, args.interval, args.codex_timeout)
        except ValueError as error:
            parser.error(str(error))
    if args.command == "claude-ingest":
        return run_claude_ingest()
    if args.command == "setup-claude":
        return run_setup_claude(console, args.dry_run)
    return run_once(console, args.codex_timeout, args.json)
