"""Three-way strategy comparison on the SAME batch.

The whole pitch of a recovery agent is incremental value. To prove it we run
three strategies over the identical set of failed transactions and account for
NET money (gross recovered minus what it cost to recover, minus fraud fallout):

  1. do_nothing        -- the status quo. Recovers nothing.
  2. naive_retry_all   -- the "obvious" agent everyone builds: retry every
                          failure up to the cap, no diagnosis, no compliance.
                          Recovers a bit more gross, but retries FRAUD (a
                          compliance violation) and pays chargebacks + penalties,
                          often going net-NEGATIVE.
  3. compliant_agent   -- this project: diagnose -> policy -> cost-aware action.
                          Recovers less gross, but is net-positive and never
                          retries fraud.

Everything is deterministic and offline, using the same executor + economics as
the live pipeline, so the comparison is apples-to-apples.
"""
from __future__ import annotations

import hashlib

from src.models import (
    STATUS_FAILED,
    STATUS_DEGRADED,
    LABEL_FRAUD,
    LABEL_RECOVERABLE,
    LABEL_NOT_RECOVERABLE,
    ACTION_RETRY_NOW,
    ACTION_RETRY_DELAYED,
    ACTION_REQUEST_NEW_PAYMENT_METHOD,
    ACTION_NO_ACTION,
)
from src.diagnoser import get_default_diagnoser
from src.policy import decide, MAX_RETRIES
from src import economics

_RECOVERY_ACTIONS = {ACTION_RETRY_NOW, ACTION_RETRY_DELAYED,
                     ACTION_REQUEST_NEW_PAYMENT_METHOD}
_RETRY_ACTIONS = {ACTION_RETRY_NOW, ACTION_RETRY_DELAYED}
FAILED_STATES = {STATUS_FAILED, STATUS_DEGRADED}

def _roll(txn_id: str, attempt: int) -> float:
    """Deterministic pseudo-random value in [0,1) per (txn, attempt)."""
    h = hashlib.sha256(f"{txn_id}#a{attempt}".encode()).hexdigest()
    return (int(h[:8], 16) % 10_000) / 10_000.0

def _world_outcome(action: str, event_dict: dict, attempt: int) -> bool:
    """Ground-truth-aware simulation of whether an action REALLY recovers.

    This is the environment/reality model — it may read true_label (the agent
    never does). It encodes the physical truth a blind retry can't change:
      * recoverable charges recover with the action's success probability;
      * genuinely not_recoverable charges NEVER recover (retrying is futile);
      * fraud charges may 'go through' with the action's probability — gross
        money appears now, but it will be charged back (costed by the caller).
    """
    if action not in _RECOVERY_ACTIONS:
        return False
    prob = economics.success_prob(action)
    label = event_dict.get("true_label")
    if label == LABEL_NOT_RECOVERABLE:
        return False

    return _roll(event_dict["transaction_id"], attempt) < prob

def _blank() -> dict:
    return {
        "gross_recovered": 0.0,
        "action_cost": 0.0,
        "fraud_cost": 0.0,
        "fraud_retry_violations": 0,
        "recovered_count": 0,
        "net": 0.0,
    }

def _run(events: list, decider) -> dict:
    """Run a strategy. `decider(event_dict, retry_count) -> action`."""
    agg = _blank()

    for e in events:
        d = e if isinstance(e, dict) else e.to_dict()
        if d.get("status") not in FAILED_STATES:
            continue
        amount = d["amount"]
        is_fraud = d.get("true_label") == LABEL_FRAUD
        retry_count = 0
        attempt = 0
        while True:
            attempt += 1
            action = decider(d, retry_count)
            agg["action_cost"] += economics.action_cost(action)

            if action in _RETRY_ACTIONS and is_fraud:

                agg["fraud_retry_violations"] += 1

            recovered = _world_outcome(action, d, attempt)
            if recovered:
                agg["gross_recovered"] += amount
                agg["recovered_count"] += 1
                if is_fraud:

                    agg["fraud_cost"] += economics.fraud_incident_cost(amount)
                break

            if action not in _RETRY_ACTIONS:
                break
            retry_count += 1
            if retry_count > MAX_RETRIES:
                break

    agg["gross_recovered"] = round(agg["gross_recovered"], 2)
    agg["action_cost"] = round(agg["action_cost"], 2)
    agg["fraud_cost"] = round(agg["fraud_cost"], 2)
    agg["net"] = round(agg["gross_recovered"] - agg["action_cost"]
                       - agg["fraud_cost"], 2)
    return agg

def _do_nothing(event_dict, retry_count) -> str:
    return ACTION_NO_ACTION

def _naive_retry_all(event_dict, retry_count) -> str:

    return ACTION_RETRY_NOW

def _make_agent_decider():
    diagnoser = get_default_diagnoser()

    def _agent(event_dict, retry_count) -> str:
        view = {k: v for k, v in event_dict.items() if k != "true_label"}
        diagnosis = diagnoser.diagnose(view)
        decision = decide(diagnosis, retry_count, event_dict["amount"])
        return decision.action

    return _agent

def compare(events: list) -> dict:
    return {
        "do_nothing": _run(events, _do_nothing),
        "naive_retry_all": _run(events, _naive_retry_all),
        "compliant_agent": _run(events, _make_agent_decider()),
    }
