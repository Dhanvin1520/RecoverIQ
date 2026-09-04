"""Simulated recovery-action execution.

All "external" side effects (retrying a charge, notifying a customer, requesting
a new payment method, escalating to a human) go through the `RecoveryAdapter`
interface. The only runtime implementation is `SimulatedAdapter`, which fabricates
deterministic outcomes for test-mode. Swapping in real Razorpay test-mode API
calls later means writing a `RazorpayTestModeAdapter(RecoveryAdapter)` and
changing one factory line — not rewriting the pipeline.

IMPORTANT: the executor never reads `true_label`. Simulated success/failure is
derived only from the diagnosed cause and a deterministic hash of the
transaction id, so it is reproducible but independent of ground truth. Metrics.py
scores honesty separately against the hidden labels.
"""
from __future__ import annotations

import abc
import hashlib

from src.models import (
    ActionDecision,
    RecoveryOutcome,
    ACTION_RETRY_NOW,
    ACTION_RETRY_DELAYED,
    ACTION_NOTIFY_CUSTOMER,
    ACTION_REQUEST_NEW_PAYMENT_METHOD,
    ACTION_ESCALATE_HUMAN,
    ACTION_NO_ACTION,
    RESULT_RECOVERED,
    RESULT_STILL_FAILED,
    RESULT_ESCALATED,
)

class RecoveryAdapter(abc.ABC):
    """Interface for the system that actually performs a recovery action."""

    @abc.abstractmethod
    def execute(self, decision: ActionDecision, amount: float,
                attempt: int = 0) -> RecoveryOutcome:
        ...

def _deterministic_unit(txn_id: str, attempt: int = 0) -> float:
    """Stable pseudo-random value in [0,1) per (txn id, attempt).

    Salting by attempt models each retry as an INDEPENDENT chance — a bank
    timeout retried a second time genuinely has a fresh shot — which is more
    realistic than reusing one outcome across attempts, and keeps this executor
    consistent with the ground-truth-aware world model in `strategies.py`.
    """
    h = hashlib.sha256(f"{txn_id}#a{attempt}".encode()).hexdigest()
    return (int(h[:8], 16) % 10_000) / 10_000.0

from src.economics import SUCCESS_PROB as _SUCCESS_PROB

class SimulatedAdapter(RecoveryAdapter):
    """Deterministic, offline stand-in for real payment-recovery calls."""

    def execute(self, decision: ActionDecision, amount: float,
                attempt: int = 0) -> RecoveryOutcome:
        action = decision.action
        txn_id = decision.transaction_id

        if action == ACTION_ESCALATE_HUMAN:
            return RecoveryOutcome(txn_id, action, RESULT_ESCALATED, 0.0)

        if action == ACTION_NO_ACTION:
            return RecoveryOutcome(txn_id, action, RESULT_STILL_FAILED, 0.0)

        if action == ACTION_NOTIFY_CUSTOMER:
            return RecoveryOutcome(txn_id, action, RESULT_STILL_FAILED, 0.0)

        prob = _SUCCESS_PROB.get(action, 0.0)
        roll = _deterministic_unit(txn_id, attempt)
        if roll < prob:
            return RecoveryOutcome(txn_id, action, RESULT_RECOVERED, round(amount, 2))
        return RecoveryOutcome(txn_id, action, RESULT_STILL_FAILED, 0.0)

class RazorpayTestModeAdapter(RecoveryAdapter):
    """Real Razorpay test-mode integration behind the same interface.

    This proves the adapter design is genuinely swappable: for a retry action it
    makes a LIVE call to Razorpay's test-mode API (creating a test order for the
    same amount — the first step of a real recovery), and derives the outcome
    from the actual API response, attaching the real Razorpay order id as
    evidence in the outcome's action_taken field.

    Requires the `razorpay` SDK and test-mode keys in the environment
    (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET). It is NOT the default — the honest
    metrics run on the deterministic SimulatedAdapter so scoring is reproducible
    and offline. Use this adapter to demonstrate real connectivity, not to
    compute the honesty numbers.
    """

    def __init__(self):
        import os
        import razorpay  # optional dependency
        key_id = os.environ.get("RAZORPAY_KEY_ID")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise RuntimeError(
                "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set — cannot use the "
                "Razorpay test-mode executor.")
        if not key_id.startswith("rzp_test_"):
            raise RuntimeError("Refusing to run: use TEST-mode keys "
                               "(rzp_test_...), never live keys.")
        self._client = razorpay.Client(auth=(key_id, key_secret))

    def execute(self, decision: ActionDecision, amount: float,
                attempt: int = 0) -> RecoveryOutcome:
        action = decision.action
        txn_id = decision.transaction_id
        if action in (ACTION_ESCALATE_HUMAN,):
            return RecoveryOutcome(txn_id, action, RESULT_ESCALATED, 0.0)
        if action in (ACTION_NO_ACTION, ACTION_NOTIFY_CUSTOMER):
            return RecoveryOutcome(txn_id, action, RESULT_STILL_FAILED, 0.0)
        try:
            order = self._client.order.create({
                "amount": int(round(amount * 100)),  # paise
                "currency": "INR",
                "receipt": f"recovery_{txn_id}",
                "notes": {"recovery_for": txn_id, "action": action},
            })
            evidence = f"{action}#rzp_order:{order.get('id', '?')}"
            return RecoveryOutcome(txn_id, evidence, RESULT_RECOVERED,
                                   round(amount, 2))
        except Exception as exc:
            return RecoveryOutcome(txn_id, f"{action}#rzp_error:"
                                   f"{type(exc).__name__}", RESULT_STILL_FAILED, 0.0)


class RecoveryExecutor:
    """Thin orchestrator around whichever adapter is configured."""

    def __init__(self, adapter: RecoveryAdapter | None = None):
        self.adapter = adapter or SimulatedAdapter()

    def run(self, decision: ActionDecision, amount: float,
            attempt: int = 0) -> RecoveryOutcome:
        return self.adapter.execute(decision, amount, attempt)

def get_default_executor() -> RecoveryExecutor:
    return RecoveryExecutor(SimulatedAdapter())

def get_executor(mode: str = "simulated") -> RecoveryExecutor:
    """Select an executor by name. Default stays offline/deterministic.

    mode="simulated" -> SimulatedAdapter (default, honest-scoring basis)
    mode="razorpay"  -> RazorpayTestModeAdapter (live test-mode API demo)
    """
    if mode == "razorpay":
        return RecoveryExecutor(RazorpayTestModeAdapter())
    return RecoveryExecutor(SimulatedAdapter())
