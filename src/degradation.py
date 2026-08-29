"""Systemic degradation detection.

Per-transaction recovery is reactive. This layer is proactive: it scans the
event stream for *systemic* degradation — a cluster of the same failure hitting
the same payment rail in a short window (e.g. one acquiring bank timing out
repeatedly). That's the difference between "retry this one card" and "netbanking
is degraded right now — stop routing to it and alert ops."

Detection is a sliding time-window over time-ordered events: if within any
window of `WINDOW_SECONDS` at least `MIN_CLUSTER` failures share the same
(payment_method, root_cause) signature, we raise a DegradationIncident with a
recommended systemic action.

Deterministic and offline; uses the same rule-based diagnoser as the agent.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from collections import defaultdict

from src.diagnoser import get_default_diagnoser
from src.models import (
    STATUS_FAILED,
    STATUS_DEGRADED,
    CAUSE_BANK_TIMEOUT,
    CAUSE_NETWORK_ERROR,
)

WINDOW_SECONDS = 600
MIN_CLUSTER = 5

_SYSTEMIC_ACTION = {
    CAUSE_BANK_TIMEOUT: "circuit-break this rail + exponential backoff; "
                        "route new charges to an alternate method",
    CAUSE_NETWORK_ERROR: "back off and retry via alternate gateway",
}
_DEFAULT_SYSTEMIC_ACTION = "alert ops + hold automated retries on this rail"

@dataclass
class DegradationIncident:
    payment_method: str
    root_cause: str
    count: int
    window_start: str
    window_end: str
    amount_at_risk: float
    transaction_ids: list
    recommended_systemic_action: str

    def to_dict(self) -> dict:
        return asdict(self)

def _parse_epoch(ts: str) -> int:
    import time
    return int(time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))

def detect(events: list) -> list[DegradationIncident]:
    """Find systemic degradation incidents in a batch of PaymentEvents/dicts."""
    diagnoser = get_default_diagnoser()

    rows = []
    for e in events:
        d = e if isinstance(e, dict) else e.to_dict()
        if d.get("status") not in (STATUS_FAILED, STATUS_DEGRADED):
            continue
        view = {k: v for k, v in d.items() if k != "true_label"}
        cause = diagnoser.diagnose(view).root_cause_category
        rows.append({
            "txn": d["transaction_id"],
            "method": d["payment_method"],
            "cause": cause,
            "amount": d["amount"],
            "epoch": _parse_epoch(d["timestamp"]),
            "ts": d["timestamp"],
        })

    buckets = defaultdict(list)
    for r in rows:
        buckets[(r["method"], r["cause"])].append(r)

    incidents: list[DegradationIncident] = []
    for (method, cause), items in buckets.items():
        items.sort(key=lambda x: x["epoch"])
        n = len(items)
        i = 0
        used = set()
        for i in range(n):
            if items[i]["txn"] in used:
                continue
            window = [items[i]]
            for j in range(i + 1, n):
                if items[j]["epoch"] - items[i]["epoch"] <= WINDOW_SECONDS:
                    window.append(items[j])
                else:
                    break
            if len(window) >= MIN_CLUSTER:
                for w in window:
                    used.add(w["txn"])
                incidents.append(DegradationIncident(
                    payment_method=method,
                    root_cause=cause,
                    count=len(window),
                    window_start=window[0]["ts"],
                    window_end=window[-1]["ts"],
                    amount_at_risk=round(sum(w["amount"] for w in window), 2),
                    transaction_ids=[w["txn"] for w in window],
                    recommended_systemic_action=_SYSTEMIC_ACTION.get(
                        cause, _DEFAULT_SYSTEMIC_ACTION),
                ))
    incidents.sort(key=lambda inc: inc.amount_at_risk, reverse=True)
    return incidents
