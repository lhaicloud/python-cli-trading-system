"""
Offline unit tests for live-entry fail-closed gates.

No network, no Binance, no pytest. Run from repo root:

    python -m scripts.test_live_entry_auth
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.live.live_entry_auth import (  # noqa: E402
    DENY_EXPIRED,
    DENY_INTENT_MISMATCH,
    DENY_LIVE_HALT,
    DENY_MISSING_OWNER,
    DENY_MISSING_RISK,
    DENY_YES_INSUFFICIENT,
    LiveEntryAuthResult,
    evaluate_live_entry,
    evaluate_live_entry_from_env,
)
from app.live.executor import LiveExecutor  # noqa: E402


class TrackingClient:
    """Records place_* calls. Must not be invoked on a denied open_trade."""

    def __init__(self) -> None:
        self.placed: list[str] = []

    def get_equity(self):
        raise AssertionError("get_equity must not run before live-entry auth")

    def get_balance(self):
        raise AssertionError("get_balance must not run before live-entry auth")

    def get_mark_price(self, _s):
        raise AssertionError("get_mark_price must not run before live-entry auth")

    def place_market_order(self, *a, **k):
        self.placed.append("market")
        return {"orderId": 1, "avgPrice": "100"}

    def place_limit_order(self, *a, **k):
        self.placed.append("limit")
        return {"orderId": 2}

    def place_stop_market(self, *a, **k):
        self.placed.append("stop")
        return {"algoId": "sl"}

    def place_take_profit_market(self, *a, **k):
        self.placed.append("tp")
        return {"algoId": "tp"}


class LiveEntryAuthTests(unittest.TestCase):
    def test_default_deny_missing_owner(self) -> None:
        result = evaluate_live_entry()
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_MISSING_OWNER)

    def test_from_env_default_deny(self) -> None:
        result = evaluate_live_entry_from_env(environ={})
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_MISSING_OWNER)

    def test_yes_alone_deny(self) -> None:
        result = evaluate_live_entry(yes_flag=True)
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_YES_INSUFFICIENT)

    def test_live_execution_flag_never_satisfies_gates(self) -> None:
        result = evaluate_live_entry(yes_flag=True, live_execution=True)
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_YES_INSUFFICIENT)

    def test_owner_only_deny_missing_risk(self) -> None:
        result = evaluate_live_entry(owner_authorization=True, risk_approved=False)
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_MISSING_RISK)

    def test_owner_plus_yes_without_risk_is_missing_risk_not_yes(self) -> None:
        result = evaluate_live_entry(
            owner_authorization=True,
            risk_approved=False,
            yes_flag=True,
            live_execution=True,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_MISSING_RISK)

    def test_both_allow(self) -> None:
        result = evaluate_live_entry(
            owner_authorization=True, risk_approved=True,
        )
        self.assertTrue(result.allowed)
        self.assertIsNone(result.deny_code)

    def test_both_allow_string_tokens_bool_form(self) -> None:
        result = evaluate_live_entry(
            owner_authorization="true", risk_approved="1",
        )
        self.assertTrue(result.allowed)

    def test_live_halt_deny_even_when_both_valid(self) -> None:
        result = evaluate_live_entry(
            live_halt=True,
            owner_authorization=True,
            risk_approved=True,
            yes_flag=True,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_LIVE_HALT)

    def test_live_halt_absent_does_not_halt_by_itself(self) -> None:
        # Halt absent, but owner/risk still required.
        result = evaluate_live_entry(live_halt=None)
        self.assertEqual(result.deny_code, DENY_MISSING_OWNER)

    def test_per_trade_token_requires_matching_intent(self) -> None:
        result = evaluate_live_entry(
            owner_authorization=True,
            risk_approved="intent-abc",
            trade_intent_id="intent-xyz",
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_INTENT_MISMATCH)

    def test_per_trade_token_matches_intent(self) -> None:
        result = evaluate_live_entry(
            owner_authorization="owner-token",
            risk_approved="intent-abc",
            trade_intent_id="intent-abc",
        )
        self.assertTrue(result.allowed)

    def test_expired_owner(self) -> None:
        result = evaluate_live_entry(
            owner_authorization=True,
            risk_approved=True,
            owner_expires="1000",
            now=2000.0,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.deny_code, DENY_EXPIRED)

    def test_from_env_yes_alone(self) -> None:
        result = evaluate_live_entry_from_env(yes_flag=True, environ={})
        self.assertEqual(result.deny_code, DENY_YES_INSUFFICIENT)

    def test_from_env_owner_plus_yes_without_risk(self) -> None:
        result = evaluate_live_entry_from_env(
            yes_flag=True,
            live_execution=True,
            environ={"OWNER_AUTHORIZATION": "true"},
        )
        self.assertEqual(result.deny_code, DENY_MISSING_RISK)

    def test_from_env_both_allow(self) -> None:
        result = evaluate_live_entry_from_env(
            environ={
                "OWNER_AUTHORIZATION": "true",
                "RISK_APPROVED": "true",
            }
        )
        self.assertTrue(result.allowed)

    def test_from_env_live_halt(self) -> None:
        result = evaluate_live_entry_from_env(
            environ={
                "LIVE_HALT": "true",
                "OWNER_AUTHORIZATION": "true",
                "RISK_APPROVED": "true",
            }
        )
        self.assertEqual(result.deny_code, DENY_LIVE_HALT)


class LiveExecutorHardStopTests(unittest.TestCase):
    def test_open_trade_denied_before_any_place_order(self) -> None:
        denied = LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_MISSING_OWNER,
            reason="OWNER_AUTHORIZATION is missing or false",
        )
        client = TrackingClient()
        executor = LiveExecutor(client, threading.Lock())
        sig = SimpleNamespace(signal="BUY")
        with patch(
            "app.live.executor.evaluate_live_entry_from_env",
            return_value=denied,
        ) as mock_auth:
            outcome, trade_id = executor.open_trade("BTCUSDT", sig, None)
        self.assertEqual(outcome, "blocked")
        self.assertIsNone(trade_id)
        self.assertEqual(client.placed, [])
        mock_auth.assert_called_once()
        kwargs = mock_auth.call_args.kwargs
        self.assertTrue(kwargs.get("live_execution"))
        self.assertFalse(kwargs.get("yes_flag"))

    def test_open_trade_yes_flag_still_denied_and_places_nothing(self) -> None:
        denied = LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_YES_INSUFFICIENT,
            reason="--yes / yes_flag does not satisfy OWNER_AUTHORIZATION",
        )
        client = TrackingClient()
        executor = LiveExecutor(client, threading.Lock(), yes_flag=True)
        sig = SimpleNamespace(signal="BUY")
        with patch(
            "app.live.executor.evaluate_live_entry_from_env",
            return_value=denied,
        ) as mock_auth:
            outcome, trade_id = executor.open_trade("BTCUSDT", sig, None)
        self.assertEqual(outcome, "blocked")
        self.assertIsNone(trade_id)
        self.assertEqual(client.placed, [])
        self.assertTrue(mock_auth.call_args.kwargs.get("yes_flag"))

    def test_real_default_env_denies_before_place(self) -> None:
        """Default process env (no Owner/Risk) must hard-stop inside open_trade."""
        client = TrackingClient()
        executor = LiveExecutor(client, threading.Lock())
        sig = SimpleNamespace(signal="BUY")
        with patch(
            "app.live.executor.evaluate_live_entry_from_env",
            side_effect=lambda **kw: evaluate_live_entry_from_env(
                yes_flag=kw.get("yes_flag", False),
                live_execution=kw.get("live_execution", False),
                trade_intent_id=kw.get("trade_intent_id"),
                environ={},
            ),
        ):
            outcome, trade_id = executor.open_trade("BTCUSDT", sig, None)
        self.assertEqual(outcome, "blocked")
        self.assertIsNone(trade_id)
        self.assertEqual(client.placed, [])


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
