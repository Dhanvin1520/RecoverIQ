"""Core data structures for the Revenue Recovery agent.

All structures are plain dataclasses so they serialize cleanly to JSON for the
audit log and are trivial to inspect in tests and in the demo.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_DEGRADED = "degraded"

CAUSE_BANK_TIMEOUT = "bank_timeout"
CAUSE_NETWORK_ERROR = "network_error"
CAUSE_INSUFFICIENT_FUNDS = "insufficient_funds"
CAUSE_CARD_EXPIRED = "card_expired"
CAUSE_RISK_BLOCK = "risk_block"
CAUSE_UNKNOWN = "unknown"

ACTION_RETRY_NOW = "retry_now"
ACTION_RETRY_DELAYED = "retry_delayed"
ACTION_NOTIFY_CUSTOMER = "notify_customer"
ACTION_REQUEST_NEW_PAYMENT_METHOD = "request_new_payment_method"
ACTION_ESCALATE_HUMAN = "escalate_human"
ACTION_NO_ACTION = "no_action"

RESULT_RECOVERED = "recovered"
RESULT_STILL_FAILED = "still_failed"
RESULT_ESCALATED = "escalated"

LABEL_RECOVERABLE = "recoverable"
LABEL_NOT_RECOVERABLE = "not_recoverable"
LABEL_FRAUD = "fraud"

@dataclass
class PaymentEvent:
    transaction_id: str
    merchant_id: str
    customer_id: str
    amount: float
    currency: str
    payment_method: str
    status: str
    failure_reason: Optional[str]
    timestamp: str
    retry_count: int = 0

    true_label: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PaymentEvent":
        return PaymentEvent(**d)

    def agent_view(self) -> dict:
        """The subset of fields the agent is allowed to see (no true_label)."""
        d = asdict(self)
        d.pop("true_label", None)
        return d

@dataclass
class Diagnosis:
    transaction_id: str
    root_cause_category: str
    confidence: float
    reasoning: str

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class ActionDecision:
    transaction_id: str
    action: str
    reason: str
    blocked_by_rule: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class RecoveryOutcome:
    transaction_id: str
    action_taken: str
    simulated_result: str
    amount_recovered: float

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class AuditRecord:
    transaction_id: str
    timestamp: str
    stage: str
    input_snapshot: dict = field(default_factory=dict)
    decision: dict = field(default_factory=dict)
    outcome: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
