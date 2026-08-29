"""Synthetic batch generator for payment events.

Produces a realistic-looking mix of successful, degraded, and failed
transactions with a hidden ground-truth `true_label` on each event so the
metrics stage can score the agent honestly.

The generator is fully deterministic given a seed, so demo runs are
reproducible.

Run:
    python3 data/generate_events.py            # writes data/events.jsonl (150 events)
    python3 data/generate_events.py --count 300 --seed 7
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src import models

MIX = {
    "recoverable_failed": 0.30,
    "not_recoverable": 0.20,
    "fraud_adjacent": 0.10,
    "degraded": 0.10,
    "ambiguous": 0.06,

}

AMBIGUOUS_REASONS = [
    (None, "Transaction failed"),
    (None, "Payment could not be completed"),
    (None, "Processor declined"),
]

CURRENCIES = ["INR", "USD", "EUR"]
PAYMENT_METHODS = ["card", "netbanking", "upi", "wallet"]
MERCHANTS = [f"merch_{i:03d}" for i in range(1, 13)]

RECOVERABLE_REASONS = [
    ("bank_timeout", "Acquiring bank did not respond within timeout window"),
    ("network_error", "Transient network error contacting gateway"),
    ("insufficient_funds", "Insufficient funds in customer account"),
    ("card_expired", "Card expired"),
]
NOT_RECOVERABLE_REASONS = [
    ("do_not_honour", "Issuer declined: do not honour"),
    ("card_blocked", "Card reported lost/stolen and blocked by issuer"),
    ("invalid_account", "Account closed or invalid"),
    ("limit_exceeded", "Permanent credit limit restriction"),
]
FRAUD_REASONS = [
    ("risk_block", "Blocked by risk engine: suspected fraud"),
    ("velocity_block", "Blocked: abnormal transaction velocity"),
    ("blacklist_hit", "Blocked: customer on risk blacklist"),
]
DEGRADED_REASONS = [
    ("slow_settlement", "Payment succeeded but settlement delayed"),
    ("partial_auth", "Partial authorization completed"),
]

def _amount(rng: random.Random) -> float:

    base = rng.choice([1, 1, 1, 10, 10, 100])
    return round(rng.uniform(50, 950) * base / 10, 2)

def _counts(total: int) -> dict:
    counts = {k: int(round(total * frac)) for k, frac in MIX.items()}
    counts["success"] = total - sum(counts.values())
    if counts["success"] < 0:
        counts["success"] = 0
    return counts

INCIDENT = {
    "enabled": True,
    "payment_method": "netbanking",
    "count": 8,
    "reason": ("bank_timeout", "Acquiring bank did not respond within timeout window"),
    "true_label": "recoverable",
    "gap_seconds": 40,
}

def generate(total: int = 150, seed: int = 42) -> list[models.PaymentEvent]:
    rng = random.Random(seed)
    counts = _counts(total)

    events: list[models.PaymentEvent] = []

    base_epoch = 1_724_900_000

    def make(idx: int, status: str, reason, true_label) -> models.PaymentEvent:
        rc, rtext = reason if reason else (None, None)
        ts_epoch = base_epoch + idx * rng.randint(3, 120)

        return models.PaymentEvent(
            transaction_id=f"txn_{idx:05d}",
            merchant_id=rng.choice(MERCHANTS),
            customer_id=f"cust_{rng.randint(1000, 9999)}",
            amount=_amount(rng),
            currency=rng.choice(CURRENCIES),
            payment_method=rng.choice(PAYMENT_METHODS),
            status=status,
            failure_reason=rtext,
            timestamp=_iso(ts_epoch),
            retry_count=0,
            true_label=true_label,
        )

    idx = 0
    plan = []
    plan += [("recoverable_failed",)] * counts["recoverable_failed"]
    plan += [("not_recoverable",)] * counts["not_recoverable"]
    plan += [("fraud_adjacent",)] * counts["fraud_adjacent"]
    plan += [("degraded",)] * counts["degraded"]
    plan += [("ambiguous",)] * counts["ambiguous"]
    plan += [("success",)] * counts["success"]
    rng.shuffle(plan)

    for (kind,) in plan:
        if kind == "recoverable_failed":
            reason = rng.choice(RECOVERABLE_REASONS)
            ev = make(idx, models.STATUS_FAILED, reason, models.LABEL_RECOVERABLE)
        elif kind == "not_recoverable":
            reason = rng.choice(NOT_RECOVERABLE_REASONS)
            ev = make(idx, models.STATUS_FAILED, reason, models.LABEL_NOT_RECOVERABLE)
        elif kind == "fraud_adjacent":
            reason = rng.choice(FRAUD_REASONS)
            ev = make(idx, models.STATUS_FAILED, reason, models.LABEL_FRAUD)
        elif kind == "ambiguous":
            reason = rng.choice(AMBIGUOUS_REASONS)
            ev = make(idx, models.STATUS_FAILED, reason, models.LABEL_RECOVERABLE)
        elif kind == "degraded":

            reason = rng.choice(DEGRADED_REASONS)
            ev = make(idx, models.STATUS_DEGRADED, reason, models.LABEL_NOT_RECOVERABLE)
        else:
            ev = make(idx, models.STATUS_SUCCESS, None, models.LABEL_NOT_RECOVERABLE)
        events.append(ev)
        idx += 1

    if INCIDENT.get("enabled"):
        rc, rtext = INCIDENT["reason"]
        incident_merchant = rng.choice(MERCHANTS)
        cluster_epoch = base_epoch + idx * 60
        for k in range(INCIDENT["count"]):
            ts_epoch = cluster_epoch + k * INCIDENT["gap_seconds"]
            events.append(models.PaymentEvent(
                transaction_id=f"txn_{idx:05d}",
                merchant_id=incident_merchant,
                customer_id=f"cust_{rng.randint(1000, 9999)}",
                amount=_amount(rng),
                currency=rng.choice(CURRENCIES),
                payment_method=INCIDENT["payment_method"],
                status=models.STATUS_FAILED,
                failure_reason=rtext,
                timestamp=_iso(ts_epoch),
                retry_count=0,
                true_label=INCIDENT["true_label"],
            ))
            idx += 1

    return events

def _iso(epoch: int) -> str:
    """Format an epoch second count as a UTC ISO-8601 string deterministically."""
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

def write_jsonl(events: list[models.PaymentEvent], path: str) -> None:
    with open(path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev.to_dict()) + "\n")

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic payment events")
    ap.add_argument("--count", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--out",
        default=os.path.join(_REPO_ROOT, "data", "events.jsonl"),
    )
    args = ap.parse_args()

    events = generate(args.count, args.seed)
    write_jsonl(events, args.out)

    from collections import Counter
    by_status = Counter(e.status for e in events)
    by_label = Counter(e.true_label for e in events)
    print(f"Wrote {len(events)} events to {args.out}")
    print(f"  by status: {dict(by_status)}")
    print(f"  by true_label: {dict(by_label)}")
    print("Sample events:")
    for e in events[:3]:
        print("  " + json.dumps(e.to_dict()))

if __name__ == "__main__":
    main()
