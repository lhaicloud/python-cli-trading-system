"""Fail-closed loss-stop state and tamper-evident research audit trail."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class LossLimits:
    daily_loss_pct: float = 3.0
    weekly_loss_pct: float = 6.0
    monthly_loss_pct: float = 10.0
    max_losing_streak: int = 6
    max_drawdown_pct: float = 15.0


@dataclass(frozen=True)
class ClosedRiskEvent:
    closed_at_ms: int
    pnl: float


@dataclass(frozen=True)
class LossState:
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    losing_streak: int
    drawdown_pct: float
    halted: bool
    reasons: tuple[str, ...]


def _period_keys(timestamp_ms: int) -> tuple[str, str, str]:
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return dt.strftime("%Y-%m-%d"), f"{iso_year:04d}-W{iso_week:02d}", dt.strftime("%Y-%m")


def evaluate_loss_state(
    *,
    initial_equity: float,
    current_equity: float,
    peak_equity: float,
    now_ms: int,
    closed_events: Iterable[ClosedRiskEvent],
    limits: LossLimits | None = None,
) -> LossState:
    """Compute loss stops from immutable closed-PnL events; missing/invalid equity halts."""
    lim = limits or LossLimits()
    if initial_equity <= 0 or current_equity <= 0 or peak_equity <= 0:
        return LossState(0.0, 0.0, 0.0, 0, 100.0, True, ("invalid_equity_state",))
    if current_equity > peak_equity:
        peak_equity = current_equity

    day_key, week_key, month_key = _period_keys(now_ms)
    events = sorted(closed_events, key=lambda e: e.closed_at_ms)
    daily = weekly = monthly = 0.0
    streak = 0
    for event in events:
        event_day, event_week, event_month = _period_keys(event.closed_at_ms)
        pnl = float(event.pnl)
        if event_day == day_key:
            daily += pnl
        if event_week == week_key:
            weekly += pnl
        if event_month == month_key:
            monthly += pnl
        if pnl <= 0:
            streak += 1
        else:
            streak = 0

    dd = max(0.0, (peak_equity - current_equity) / peak_equity * 100.0)
    reasons: list[str] = []
    if daily <= -(initial_equity * lim.daily_loss_pct / 100.0):
        reasons.append("daily_loss_stop")
    if weekly <= -(initial_equity * lim.weekly_loss_pct / 100.0):
        reasons.append("weekly_loss_stop")
    if monthly <= -(initial_equity * lim.monthly_loss_pct / 100.0):
        reasons.append("monthly_loss_stop")
    if streak >= lim.max_losing_streak:
        reasons.append("losing_streak_stop")
    if dd >= lim.max_drawdown_pct:
        reasons.append("max_drawdown_halt")

    return LossState(daily, weekly, monthly, streak, dd, bool(reasons), tuple(reasons))


@dataclass(frozen=True)
class AuditRecord:
    sequence: int
    timestamp_ms: int
    event_type: str
    payload_json: str
    previous_hash: str
    record_hash: str


def _record_hash(
    sequence: int,
    timestamp_ms: int,
    event_type: str,
    payload_json: str,
    previous_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "sequence": sequence,
            "timestamp_ms": timestamp_ms,
            "event_type": event_type,
            "payload_json": payload_json,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditChain:
    """Append-only in-memory hash chain suitable for persisted audit serialization."""

    def __init__(self, records: Iterable[AuditRecord] = ()) -> None:
        self._records = list(records)
        if not self.verify():
            raise ValueError("audit chain verification failed")

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def append(self, timestamp_ms: int, event_type: str, payload: dict) -> AuditRecord:
        if timestamp_ms <= 0 or not event_type.strip():
            raise ValueError("invalid audit event")
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        sequence = len(self._records) + 1
        previous = self._records[-1].record_hash if self._records else "GENESIS"
        digest = _record_hash(sequence, timestamp_ms, event_type, payload_json, previous)
        record = AuditRecord(sequence, timestamp_ms, event_type, payload_json, previous, digest)
        self._records.append(record)
        return record

    def verify(self) -> bool:
        previous = "GENESIS"
        for expected_sequence, record in enumerate(self._records, start=1):
            if record.sequence != expected_sequence or record.previous_hash != previous:
                return False
            expected = _record_hash(
                record.sequence,
                record.timestamp_ms,
                record.event_type,
                record.payload_json,
                record.previous_hash,
            )
            if record.record_hash != expected:
                return False
            previous = record.record_hash
        return True


def deny_manual_override(
    chain: AuditChain,
    *,
    timestamp_ms: int,
    actor: str,
    requested_change: dict,
) -> AuditRecord:
    """Record and deny an unauthorized runtime risk override."""
    return chain.append(
        timestamp_ms,
        "MANUAL_OVERRIDE_DENIED",
        {"actor": actor, "requested_change": requested_change, "allowed": False},
    )
