from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class QuotaWindow:
    label: str
    used_percent: float
    window_minutes: int | None = None
    resets_at: int | None = None


@dataclass(frozen=True)
class QuotaBucket:
    bucket_id: str
    name: str
    windows: list[QuotaWindow] = field(default_factory=list)
    plan_type: str | None = None


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: str
    status: str
    buckets: list[QuotaBucket] = field(default_factory=list)
    captured_at: str = field(default_factory=utc_now_iso)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def unavailable_snapshot(provider: str, message: str) -> ProviderSnapshot:
    return ProviderSnapshot(provider=provider, status="unavailable", message=message)
