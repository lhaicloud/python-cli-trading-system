"""
Fail-closed authorization for NEW live entries (capital protection).

This module does not enable live trading. Defaults deny every new live entry.
`--yes` and `--live-execution` never satisfy Owner or Risk gates.

Required to allow a new live entry:
  LIVE_HALT is false or absent
  AND OWNER_AUTHORIZATION is a valid bool/token
  AND RISK_APPROVED is a valid bool/token
  AND (when RISK_APPROVED is a per-trade token) it matches trade_intent_id
  AND credentials are not expired

Deny codes (precedence, first match wins):
  live_halt              LIVE_HALT is true
  expired                a presented Owner/Risk credential is past its expiry
  yes_insufficient       Owner missing/false AND yes_flag is True
  missing_owner_auth     Owner missing/false (and yes_flag is False)
  missing_risk_approved  Owner valid AND Risk missing/false
  intent_mismatch        Risk is a per-trade token that does not match trade_intent_id

`--yes` never upgrades a missing Risk gate: Owner true + Risk false + yes_flag
→ missing_risk_approved, not yes_insufficient.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

DENY_LIVE_HALT = "live_halt"
DENY_MISSING_OWNER = "missing_owner_auth"
DENY_MISSING_RISK = "missing_risk_approved"
DENY_INTENT_MISMATCH = "intent_mismatch"
DENY_YES_INSUFFICIENT = "yes_insufficient"
DENY_EXPIRED = "expired"

_FALSEY = frozenset({"", "0", "false", "no", "off", "none", "null"})
_TRUEY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class LiveEntryAuthResult:
    allowed: bool
    deny_code: str | None = None
    reason: str = ""


class LiveEntryDenied(RuntimeError):
    """Raised when a live watcher/executor refuses to start or open because gates failed."""

    def __init__(self, result: LiveEntryAuthResult) -> None:
        self.result = result
        super().__init__(
            f"live entry denied ({result.deny_code}): {result.reason}"
        )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def parse_halt_flag(value: Any) -> bool:
    """True ⇒ deny all new live entries. Absent/false ⇒ do not halt by this flag.

    Unrecognised non-empty values fail closed (treated as halt).
    """
    if value is None or value is False:
        return False
    if value is True:
        return True
    text = _as_text(value)
    if text == "":
        return False
    lowered = text.lower()
    if lowered in _FALSEY:
        return False
    if lowered in _TRUEY:
        return True
    return True


def _parse_credential(value: Any) -> tuple[str, str]:
    """Return (kind, raw) where kind is 'missing' | 'bool' | 'token'."""
    if value is None or value is False:
        return "missing", ""
    if value is True:
        return "bool", "true"
    text = _as_text(value)
    if text == "" or text.lower() in _FALSEY:
        return "missing", ""
    if text.lower() in _TRUEY:
        return "bool", text
    return "token", text


def _parse_expiry(value: Any) -> tuple[float | None, bool]:
    """Return (unix_ts, malformed).

    malformed=True means a value was provided but could not be parsed → fail closed.
    """
    text = _as_text(value)
    if text == "":
        return None, False
    try:
        return float(text), False
    except ValueError:
        pass
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None, True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), False


def evaluate_live_entry(
    *,
    live_halt: Any = None,
    owner_authorization: Any = None,
    risk_approved: Any = None,
    trade_intent_id: Any = None,
    yes_flag: bool = False,
    live_execution: bool = False,
    owner_expires: Any = None,
    risk_expires: Any = None,
    now: float | None = None,
) -> LiveEntryAuthResult:
    """Pure fail-closed decision. `yes_flag` and `live_execution` never grant access."""
    del live_execution  # accepted so callers can pass it; never used as a credential

    if parse_halt_flag(live_halt):
        return LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_LIVE_HALT,
            reason="LIVE_HALT is set — new live entries are denied",
        )

    now_ts = time_now(now)
    owner_kind, _owner_raw = _parse_credential(owner_authorization)
    risk_kind, risk_raw = _parse_credential(risk_approved)
    intent = _as_text(trade_intent_id)

    owner_exp, owner_exp_bad = _parse_expiry(owner_expires)
    risk_exp, risk_exp_bad = _parse_expiry(risk_expires)

    owner_present = owner_kind != "missing"
    if owner_present and (owner_exp_bad or (owner_exp is not None and now_ts >= owner_exp)):
        return LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_EXPIRED,
            reason="OWNER_AUTHORIZATION is expired or has a malformed expiry",
        )

    if not owner_present:
        if yes_flag:
            return LiveEntryAuthResult(
                allowed=False,
                deny_code=DENY_YES_INSUFFICIENT,
                reason="--yes / yes_flag does not satisfy OWNER_AUTHORIZATION",
            )
        return LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_MISSING_OWNER,
            reason="OWNER_AUTHORIZATION is missing or false",
        )

    risk_present = risk_kind != "missing"
    if risk_present and (risk_exp_bad or (risk_exp is not None and now_ts >= risk_exp)):
        return LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_EXPIRED,
            reason="RISK_APPROVED is expired or has a malformed expiry",
        )

    if not risk_present:
        return LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_MISSING_RISK,
            reason="RISK_APPROVED is missing or false",
        )

    if risk_kind == "token" and risk_raw != intent:
        return LiveEntryAuthResult(
            allowed=False,
            deny_code=DENY_INTENT_MISMATCH,
            reason="RISK_APPROVED token does not match trade_intent_id",
        )

    return LiveEntryAuthResult(allowed=True, deny_code=None, reason="")


def time_now(now: float | None) -> float:
    if now is not None:
        return float(now)
    return datetime.now(timezone.utc).timestamp()


def _read_source(
    key: str,
    *,
    environ: Mapping[str, str] | None,
    settings_attr: str | None,
) -> str:
    """Prefer process env (emergency halt without restart), then Settings/.env.

    When `environ` is passed explicitly (tests), Settings is not consulted.
    """
    if environ is not None:
        raw = environ.get(key)
        return "" if raw is None else str(raw)
    if key in os.environ:
        return os.environ.get(key, "")
    if settings_attr:
        try:
            from app.config import get_settings

            val = getattr(get_settings(), settings_attr, "")
            if val is None or val is False:
                return ""
            if val is True:
                return "true"
            return str(val)
        except Exception:
            return ""
    return ""


def evaluate_live_entry_from_env(
    *,
    yes_flag: bool = False,
    live_execution: bool = False,
    trade_intent_id: str | None = None,
    environ: Mapping[str, str] | None = None,
    now: float | None = None,
) -> LiveEntryAuthResult:
    """Evaluate gates from environment / settings. Does not enable live trading."""
    intent = _as_text(trade_intent_id) or _read_source(
        "TRADE_INTENT_ID", environ=environ, settings_attr="trade_intent_id"
    )
    return evaluate_live_entry(
        live_halt=_read_source("LIVE_HALT", environ=environ, settings_attr="live_halt"),
        owner_authorization=_read_source(
            "OWNER_AUTHORIZATION", environ=environ, settings_attr="owner_authorization"
        ),
        risk_approved=_read_source(
            "RISK_APPROVED", environ=environ, settings_attr="risk_approved"
        ),
        trade_intent_id=intent,
        yes_flag=bool(yes_flag),
        live_execution=bool(live_execution),
        owner_expires=_read_source(
            "OWNER_AUTHORIZATION_EXPIRES",
            environ=environ,
            settings_attr="owner_authorization_expires",
        ),
        risk_expires=_read_source(
            "RISK_APPROVED_EXPIRES",
            environ=environ,
            settings_attr="risk_approved_expires",
        ),
        now=now,
    )


def require_live_entry_allowed(
    *,
    yes_flag: bool = False,
    live_execution: bool = False,
    trade_intent_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LiveEntryAuthResult:
    """Return the allow result or raise LiveEntryDenied. Never treats --yes as approval."""
    result = evaluate_live_entry_from_env(
        yes_flag=yes_flag,
        live_execution=live_execution,
        trade_intent_id=trade_intent_id,
        environ=environ,
    )
    if not result.allowed:
        raise LiveEntryDenied(result)
    return result
