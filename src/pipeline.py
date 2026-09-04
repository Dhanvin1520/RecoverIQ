"""End-to-end orchestration: load events -> diagnose -> decide -> execute -> log.

The pipeline is the only place the stages are wired together. It:
  * reads events.jsonl (agent sees `agent_view()` — never `true_label`),
  * runs the retry loop honoring the policy's stopping rules,
  * simulates recovery via the executor adapter,
  * writes a full audit trail to audit.jsonl.

Retry loop: for retry actions we re-attempt up to MAX_RETRIES times. Each
attempt increments retry_count and is re-run through the policy, so once the
cap is hit the policy escalates instead of retrying again — all recorded.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from src.models import (
    PaymentEvent,
    AuditRecord,
    STATUS_SUCCESS,
    ACTION_RETRY_NOW,
    ACTION_RETRY_DELAYED,
    ACTION_NOTIFY_CUSTOMER,
    ACTION_ESCALATE_HUMAN,
    ACTION_NO_ACTION,
    RESULT_RECOVERED,
    RESULT_STILL_FAILED,
)
from src.diagnoser import get_default_diagnoser, get_diagnoser
from src.policy import decide, MAX_RETRIES
from src.executor import get_default_executor, get_executor
from src.audit_log import AuditLog

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EVENTS = os.path.join(_REPO_ROOT, "data", "events.jsonl")
DEFAULT_AUDIT = os.path.join(_REPO_ROOT, "reports", "audit.jsonl")

_RETRY_ACTIONS = {ACTION_RETRY_NOW, ACTION_RETRY_DELAYED}

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def load_events(path: str) -> list[PaymentEvent]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(PaymentEvent.from_dict(json.loads(line)))
    return events

def process_event(event: PaymentEvent, diagnoser, executor, audit: AuditLog) -> None:
    """Handle a single event through diagnose -> decide -> execute with retries."""
    view = event.agent_view()

    if event.status == STATUS_SUCCESS:
        audit.write(AuditRecord(
            transaction_id=event.transaction_id,
            timestamp=_now_iso(),
            stage="skip",
            input_snapshot=view,
            decision={"action": ACTION_NO_ACTION, "reason": "Transaction succeeded."},
            outcome={"simulated_result": "n/a", "amount_recovered": 0.0},
        ))
        return

    diagnosis = diagnoser.diagnose(view)
    audit.write(AuditRecord(
        transaction_id=event.transaction_id,
        timestamp=_now_iso(),
        stage="diagnose",
        input_snapshot=view,
        decision=diagnosis.to_dict(),
        outcome={},
    ))

    retry_count = event.retry_count
    attempt = 0
    while True:
        attempt += 1
        decision = decide(diagnosis, retry_count, event.amount)
        outcome = executor.run(decision, event.amount, attempt)

        audit.write(AuditRecord(
            transaction_id=event.transaction_id,
            timestamp=_now_iso(),
            stage="action",
            input_snapshot={"attempt": attempt, "retry_count": retry_count,
                            "root_cause": diagnosis.root_cause_category},
            decision=decision.to_dict(),
            outcome=outcome.to_dict(),
        ))

        if outcome.simulated_result == RESULT_RECOVERED:
            break
        if decision.action not in _RETRY_ACTIONS:
            break

        retry_count += 1
        if retry_count > MAX_RETRIES:

            final = decide(diagnosis, retry_count, event.amount)
            final_outcome = executor.run(final, event.amount, attempt + 1)
            audit.write(AuditRecord(
                transaction_id=event.transaction_id,
                timestamp=_now_iso(),
                stage="action",
                input_snapshot={"attempt": attempt + 1, "retry_count": retry_count,
                                "root_cause": diagnosis.root_cause_category},
                decision=final.to_dict(),
                outcome=final_outcome.to_dict(),
            ))
            break

def run(events_path: str = DEFAULT_EVENTS, audit_path: str = DEFAULT_AUDIT,
        diagnoser_mode: str = "rules", executor_mode: str = "simulated") -> str:
    diagnoser = get_diagnoser(diagnoser_mode)
    executor = get_executor(executor_mode)
    events = load_events(events_path)

    with AuditLog(audit_path) as audit:
        for event in events:
            process_event(event, diagnoser, executor, audit)

    print(f"Processed {len(events)} events -> audit log at {audit_path}")
    return audit_path

def main() -> None:
    ap = argparse.ArgumentParser(description="Run the recovery pipeline")
    ap.add_argument("--events", default=DEFAULT_EVENTS)
    ap.add_argument("--audit", default=DEFAULT_AUDIT)
    ap.add_argument("--diagnoser", choices=["rules", "llm"], default="rules",
                    help="rules = deterministic/offline (default); "
                         "llm = LLM-assisted, auto-falls back to rules")
    ap.add_argument("--executor", choices=["simulated", "razorpay"],
                    default="simulated",
                    help="simulated = offline honest-scoring default; "
                         "razorpay = live test-mode API (needs razorpay SDK + keys)")
    args = ap.parse_args()
    run(args.events, args.audit, args.diagnoser, args.executor)

if __name__ == "__main__":
    main()
