from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Group
from rich.table import Table
from rich.text import Text

from .models import ProviderSnapshot, QuotaWindow


def _bar(window: QuotaWindow, width: int = 14) -> Text:
    used = max(0.0, min(100.0, window.used_percent))
    filled = round(width * used / 100)
    color = "red" if used >= 90 else "yellow" if used >= 75 else "green"
    text = Text()
    text.append("█" * filled, style=color)
    text.append("░" * (width - filled), style="bright_black")
    text.append(f" {used:5.1f}%", style=color)
    return text


def _reset_label(timestamp: int | None) -> str:
    if timestamp is None:
        return "—"
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%m-%d %H:%M")


def build_dashboard(snapshots: list[ProviderSnapshot]) -> Group:
    table = Table(title="Quota Watch", expand=False, show_lines=False)
    table.add_column("Provider", style="bold")
    table.add_column("Window")
    table.add_column("Used", no_wrap=True)
    table.add_column("Resets", no_wrap=True)

    for snapshot in snapshots:
        provider_name = snapshot.provider.capitalize()
        if not snapshot.buckets:
            table.add_row(provider_name, "—", Text(snapshot.status, style="red"), "—")
            continue
        for bucket in snapshot.buckets:
            label = provider_name if bucket.name.lower() == provider_name.lower() else bucket.name
            label_text = Text(label, style="yellow" if snapshot.status == "stale" else "bold")
            if not bucket.windows:
                table.add_row(
                    label_text,
                    "—",
                    Text(snapshot.status, style="yellow"),
                    "—",
                )
                continue
            for index, window in enumerate(bucket.windows):
                table.add_row(
                    label_text if index == 0 else "",
                    window.label,
                    _bar(window),
                    _reset_label(window.resets_at),
                )

    updated = datetime.now().astimezone().strftime("Updated %Y-%m-%d %H:%M:%S %Z")
    notes = [f"{snapshot.provider.capitalize()}: {snapshot.message}" for snapshot in snapshots if snapshot.message]
    footer = Text(updated, style="dim")
    if notes:
        footer.append("\n" + " | ".join(notes), style="yellow")
    return Group(table, footer)
