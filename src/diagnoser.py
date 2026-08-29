"""Root-cause diagnosis of failed/degraded payment events.

Design note (LLM-ready):
    The public contract is the abstract `Diagnoser` interface with a single
    `diagnose(event_view: dict) -> Diagnosis` method. The default and only
    runtime implementation is `RuleBasedDiagnoser`, which is 100% deterministic
    and offline (no network, no LLM) so a live demo can never break on an API
    issue. An `LLMDiagnoser` could be dropped in later behind the same interface
    (accepting the same agent-visible dict, returning the same `Diagnosis`)
    without touching the pipeline, policy, or executor.

The diagnoser only ever receives the agent-visible view of an event
(`PaymentEvent.agent_view()`), which excludes the hidden `true_label`.
"""
from __future__ import annotations

import abc
import re

from src.models import (
    Diagnosis,
    CAUSE_BANK_TIMEOUT,
    CAUSE_NETWORK_ERROR,
    CAUSE_INSUFFICIENT_FUNDS,
    CAUSE_CARD_EXPIRED,
    CAUSE_RISK_BLOCK,
    CAUSE_UNKNOWN,
)

class Diagnoser(abc.ABC):
    """Interface for any root-cause classifier (rule-based today, LLM later)."""

    @abc.abstractmethod
    def diagnose(self, event_view: dict) -> Diagnosis:
        ...

_RULES: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"fraud|risk|blacklist|velocity|suspected", re.I), CAUSE_RISK_BLOCK, 0.97),
    (re.compile(r"timeout|did not respond|timed out", re.I), CAUSE_BANK_TIMEOUT, 0.95),
    (re.compile(r"network|gateway|connection", re.I), CAUSE_NETWORK_ERROR, 0.93),
    (re.compile(r"insufficient funds|low balance", re.I), CAUSE_INSUFFICIENT_FUNDS, 0.96),
    (re.compile(r"expired", re.I), CAUSE_CARD_EXPIRED, 0.96),

    (re.compile(r"do not honour|do not honor|lost/stolen|blocked|closed|invalid|limit",
                re.I), CAUSE_UNKNOWN, 0.60),
]

class RuleBasedDiagnoser(Diagnoser):
    """Deterministic, offline classifier keyed off the failure_reason text."""

    def diagnose(self, event_view: dict) -> Diagnosis:
        txn_id = event_view.get("transaction_id", "")
        reason = (event_view.get("failure_reason") or "").strip()
        status = event_view.get("status")

        if status == "success":
            return Diagnosis(
                transaction_id=txn_id,
                root_cause_category=CAUSE_UNKNOWN,
                confidence=1.0,
                reasoning="Transaction succeeded; no failure to diagnose.",
            )

        if not reason:
            return Diagnosis(
                transaction_id=txn_id,
                root_cause_category=CAUSE_UNKNOWN,
                confidence=0.4,
                reasoning="No failure_reason present; cannot classify -> escalate.",
            )

        for pattern, category, confidence in _RULES:
            if pattern.search(reason):
                return Diagnosis(
                    transaction_id=txn_id,
                    root_cause_category=category,
                    confidence=confidence,
                    reasoning=f"Matched '{pattern.pattern}' in reason "
                              f"\"{reason}\" -> {category}.",
                )

        return Diagnosis(
            transaction_id=txn_id,
            root_cause_category=CAUSE_UNKNOWN,
            confidence=0.3,
            reasoning=f"No rule matched reason \"{reason}\" -> escalate for review.",
        )

_VALID_CATEGORIES = {
    CAUSE_BANK_TIMEOUT, CAUSE_NETWORK_ERROR, CAUSE_INSUFFICIENT_FUNDS,
    CAUSE_CARD_EXPIRED, CAUSE_RISK_BLOCK, CAUSE_UNKNOWN,
}

_LLM_SYSTEM_PROMPT = (
    "You are a payments failure-classification engine. Given one failed "
    "transaction's agent-visible fields, classify its root cause into EXACTLY "
    "one of these categories:\n"
    f"  {CAUSE_BANK_TIMEOUT} - transient acquiring-bank/timeout failure\n"
    f"  {CAUSE_NETWORK_ERROR} - transient network/gateway failure\n"
    f"  {CAUSE_INSUFFICIENT_FUNDS} - customer has insufficient funds\n"
    f"  {CAUSE_CARD_EXPIRED} - the payment instrument has expired\n"
    f"  {CAUSE_RISK_BLOCK} - fraud/risk/blacklist/velocity block (NEVER retry)\n"
    f"  {CAUSE_UNKNOWN} - ambiguous, permanent decline, or anything unclear\n"
    "When in doubt, or for permanent declines you cannot safely retry, choose "
    f"{CAUSE_UNKNOWN}. Respond with ONLY a JSON object: "
    '{"root_cause_category": "<one category>", "confidence": <0..1>, '
    '"reasoning": "<one short sentence>"}.'
)

class LLMDiagnoser(Diagnoser):
    """LLM-assisted classifier (Anthropic Claude) behind the same interface.

    This proves the interface is genuinely swappable — the pipeline, policy,
    executor, and audit log are untouched. It is NOT the default: the rule-based
    diagnoser stays the runtime default so the live demo can't break on an API
    issue. This path is safe by construction — if the `anthropic` SDK isn't
    installed, no credentials are configured, or any call/parse fails, it falls
    back to the deterministic RuleBasedDiagnoser for that transaction.
    """

    def __init__(self, model: str | None = None):
        import os
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
        self._fallback = RuleBasedDiagnoser()
        self._client = None
        try:
            import anthropic
            self._client = anthropic.Anthropic()
        except Exception:
            self._client = None

    def diagnose(self, event_view: dict) -> Diagnosis:
        txn_id = event_view.get("transaction_id", "")
        if event_view.get("status") == "success":
            return self._fallback.diagnose(event_view)
        if self._client is None:
            return self._degrade(event_view, "LLM unavailable; used rules")

        try:
            import json
            payload = {k: event_view.get(k) for k in
                       ("transaction_id", "payment_method", "amount",
                        "currency", "status", "failure_reason", "retry_count")}
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=512,
                output_config={"effort": "low"},
                system=_LLM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            data = json.loads(text[text.index("{"): text.rindex("}") + 1])
            category = data.get("root_cause_category")
            if category not in _VALID_CATEGORIES:
                return self._degrade(event_view, "LLM returned invalid category")
            return Diagnosis(
                transaction_id=txn_id,
                root_cause_category=category,
                confidence=float(data.get("confidence", 0.5)),
                reasoning="[LLM] " + str(data.get("reasoning", ""))[:200],
            )
        except Exception as exc:
            return self._degrade(event_view, f"LLM error ({type(exc).__name__})")

    def _degrade(self, event_view: dict, note: str) -> Diagnosis:
        d = self._fallback.diagnose(event_view)
        d.reasoning = f"[fallback: {note}] {d.reasoning}"
        return d

def get_default_diagnoser() -> Diagnoser:
    """Factory the pipeline uses. Swap the return type to switch strategies."""
    return RuleBasedDiagnoser()

def get_diagnoser(mode: str = "rules") -> Diagnoser:
    """Select a diagnoser strategy by name. Default stays deterministic.

    mode="rules" -> RuleBasedDiagnoser (default, offline, demo-safe)
    mode="llm"   -> LLMDiagnoser (Claude-assisted, auto-falls back to rules)
    """
    if mode == "llm":
        return LLMDiagnoser()
    return RuleBasedDiagnoser()
