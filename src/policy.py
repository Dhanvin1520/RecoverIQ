"""Stopping rules and escalation logic.

These are plain, readable, independently testable functions. They map a
diagnosed root cause (+ current retry count) to an ActionDecision. Nothing
here talks to the outside world — that's the executor's job.

Compliance-minded design:
  * Fraud/risk-adjacent causes are NEVER retried; they escalate immediately.
  * Retries are capped at MAX_RETRIES per transaction; hitting the cap escalates.
  * Every blocked/escalated decision records `blocked_by_rule` so the audit
    trail explains *why* the agent stopped.
"""
from __future__ import annotations

from src.models import (
    Diagnosis,
    ActionDecision,
    CAUSE_BANK_TIMEOUT,
    CAUSE_NETWORK_ERROR,
    CAUSE_INSUFFICIENT_FUNDS,
    CAUSE_CARD_EXPIRED,
    CAUSE_RISK_BLOCK,
    CAUSE_UNKNOWN,
    ACTION_RETRY_NOW,
    ACTION_RETRY_DELAYED,
    ACTION_NOTIFY_CUSTOMER,
    ACTION_REQUEST_NEW_PAYMENT_METHOD,
    ACTION_ESCALATE_HUMAN,
    ACTION_NO_ACTION,
)

from src import economics

MAX_RETRIES = 2

RULE_FRAUD_NEVER_RETRY = "fraud_never_retry"
RULE_RETRY_CAP = "retry_cap_reached"
RULE_UNKNOWN_ESCALATE = "unknown_cause_escalate"
RULE_NEGATIVE_EV = "not_worth_cost"

_RETRY_ACTIONS = {ACTION_RETRY_NOW, ACTION_RETRY_DELAYED}

def is_fraud_adjacent(diagnosis: Diagnosis) -> bool:
    return diagnosis.root_cause_category == CAUSE_RISK_BLOCK

def retry_cap_reached(retry_count: int) -> bool:
    return retry_count >= MAX_RETRIES

def _base_action(category: str) -> tuple[str, str]:
    """Map a root cause to its intended action + human reason (pre-rules)."""
    mapping = {
        CAUSE_BANK_TIMEOUT: (ACTION_RETRY_NOW,
                             "Transient bank timeout is safe to retry immediately."),
        CAUSE_NETWORK_ERROR: (ACTION_RETRY_NOW,
                              "Transient network error is safe to retry immediately."),
        CAUSE_INSUFFICIENT_FUNDS: (ACTION_RETRY_DELAYED,
                                   "Funds may arrive; retry later and notify customer."),
        CAUSE_CARD_EXPIRED: (ACTION_REQUEST_NEW_PAYMENT_METHOD,
                             "Card expired; retrying is futile, request a new method."),
        CAUSE_RISK_BLOCK: (ACTION_ESCALATE_HUMAN,
                           "Risk/fraud-adjacent; must not retry, escalate to human."),
        CAUSE_UNKNOWN: (ACTION_ESCALATE_HUMAN,
                        "Ambiguous/unknown cause; escalate for human review."),
    }
    return mapping.get(category, (ACTION_ESCALATE_HUMAN, "Unmapped cause; escalate."))

def decide(diagnosis: Diagnosis, retry_count: int,
           amount: float | None = None) -> ActionDecision:
    """Turn a diagnosis + retry state into a compliant action decision.

    If `amount` is provided, a cost/expected-value gate is applied: a recovery
    action whose expected value is negative (the charge is too small to justify
    the cost of chasing it) is dropped to `no_action`. Pass `amount=None` to
    disable the economic gate (used in pure policy unit tests).
    """
    txn_id = diagnosis.transaction_id

    if is_fraud_adjacent(diagnosis):
        return ActionDecision(
            transaction_id=txn_id,
            action=ACTION_ESCALATE_HUMAN,
            reason="Fraud/risk-adjacent cause — retrying is prohibited.",
            blocked_by_rule=RULE_FRAUD_NEVER_RETRY,
        )

    action, reason = _base_action(diagnosis.root_cause_category)

    if action in _RETRY_ACTIONS and retry_cap_reached(retry_count):
        return ActionDecision(
            transaction_id=txn_id,
            action=ACTION_ESCALATE_HUMAN,
            reason=f"Retry cap of {MAX_RETRIES} reached; escalating instead.",
            blocked_by_rule=RULE_RETRY_CAP,
        )

    if diagnosis.root_cause_category == CAUSE_UNKNOWN:
        return ActionDecision(
            transaction_id=txn_id,
            action=ACTION_ESCALATE_HUMAN,
            reason=reason,
            blocked_by_rule=RULE_UNKNOWN_ESCALATE,
        )

    if amount is not None and action in _RETRY_ACTIONS \
            and not economics.is_worth_attempting(action, amount):
        ev = economics.expected_value(action, amount)
        return ActionDecision(
            transaction_id=txn_id,
            action=ACTION_NO_ACTION,
            reason=f"Expected value of {action} on {amount:.2f} is "
                   f"{ev:.2f} (<= 0); not worth the recovery cost.",
            blocked_by_rule=RULE_NEGATIVE_EV,
        )

    return ActionDecision(
        transaction_id=txn_id,
        action=action,
        reason=reason,
        blocked_by_rule=None,
    )
