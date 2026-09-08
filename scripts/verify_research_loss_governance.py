"""No-network verification for loss stops and tamper-evident governance."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from app.research.governance import (
    AuditChain,
    AuditRecord,
    ClosedRiskEvent,
    LossLimits,
    deny_manual_override,
    evaluate_loss_state,
)

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


now = ms("2026-09-08T08:00:00")

print("\n1. Period loss stops")
events = [
    ClosedRiskEvent(ms("2026-09-07T01:00:00"), -200),
    ClosedRiskEvent(ms("2026-09-08T01:00:00"), -150),
    ClosedRiskEvent(ms("2026-09-08T02:00:00"), -160),
]
state = evaluate_loss_state(
    initial_equity=10_000,
    current_equity=9_490,
    peak_equity=10_000,
    now_ms=now,
    closed_events=events,
    limits=LossLimits(daily_loss_pct=3, weekly_loss_pct=6, monthly_loss_pct=10, max_losing_streak=6, max_drawdown_pct=15),
)
check("daily loss stop triggers", "daily_loss_stop" in state.reasons, str(state.reasons))
check("weekly loss not falsely triggered", "weekly_loss_stop" not in state.reasons)

print("\n2. Losing streak and drawdown halt")
streak_events = [ClosedRiskEvent(now - i * 1000, -10) for i in range(6, 0, -1)]
streak = evaluate_loss_state(
    initial_equity=10_000,
    current_equity=8_400,
    peak_equity=10_000,
    now_ms=now,
    closed_events=streak_events,
)
check("losing streak stop triggers", "losing_streak_stop" in streak.reasons)
check("max drawdown halt triggers", "max_drawdown_halt" in streak.reasons)
check("risk state halts", streak.halted)

print("\n3. Invalid equity fails closed")
invalid = evaluate_loss_state(
    initial_equity=0,
    current_equity=0,
    peak_equity=0,
    now_ms=now,
    closed_events=[],
)
check("invalid equity halts", invalid.halted and invalid.reasons == ("invalid_equity_state",))

print("\n4. Tamper-evident audit chain")
chain = AuditChain()
first = chain.append(now, "TRADE_INTENT_CREATED", {"intent_id": "abc", "strategy_version": "1"})
second = deny_manual_override(
    chain,
    timestamp_ms=now + 1,
    actor="operator",
    requested_change={"stop_loss": "widen"},
)
check("valid chain verifies", chain.verify())
check("manual override explicitly denied", '"allowed":false' in second.payload_json)

records = list(chain.records)
tampered = replace(records[0], payload_json='{"intent_id":"forged"}')
try:
    AuditChain([tampered, records[1]])
except ValueError:
    tamper_detected = True
else:
    tamper_detected = False
check("audit payload tampering detected", tamper_detected)

try:
    AuditChain([replace(first, sequence=2)])
except ValueError:
    sequence_tamper = True
else:
    sequence_tamper = False
check("audit sequence tampering detected", sequence_tamper)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
raise SystemExit(1 if failures else 0)
