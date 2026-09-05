"""FastAPI server for the Revenue Recovery Command Center.

Wraps the existing pipeline modules to expose REST endpoints for the
dashboard frontend. Does NOT modify any of the core src/ logic — it only
reads and invokes it.

Run:
    uvicorn api.server:app --reload --port 8001
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.models import (
    PaymentEvent, STATUS_SUCCESS, STATUS_FAILED, STATUS_DEGRADED,
    CAUSE_BANK_TIMEOUT, CAUSE_NETWORK_ERROR, CAUSE_INSUFFICIENT_FUNDS,
    CAUSE_CARD_EXPIRED, CAUSE_RISK_BLOCK, CAUSE_UNKNOWN,
)
from src.diagnoser import RuleBasedDiagnoser, LLMDiagnoser, get_diagnoser
from src.policy import decide
from src.executor import get_default_executor
from src.audit_log import read_records
from src import strategies as strat
from src import degradation

DEFAULT_EVENTS = os.path.join(_REPO_ROOT, "data", "events.jsonl")
DEFAULT_AUDIT = os.path.join(_REPO_ROOT, "reports", "audit.jsonl")
DEFAULT_REPORT_JSON = os.path.join(_REPO_ROOT, "reports", "report.json")

app = FastAPI(title="RecoverIQ API",
              description="API for RecoverIQ — A Compliant AI Revenue Recovery Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_events_list(path: str = DEFAULT_EVENTS) -> list[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _load_report(path: str = DEFAULT_REPORT_JSON) -> dict:
    if not os.path.exists(path):
        raise HTTPException(status_code=404,
                            detail="Report not found. Run the pipeline first.")
    with open(path) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@app.get("/api/metrics")
def get_metrics():
    """Return the full computed metrics (report.json)."""
    return _load_report()


@app.get("/api/events")
def get_events():
    """Return all events from the generated batch."""
    events = _load_events_list()
    return {"count": len(events), "events": events}


@app.get("/api/audit")
def get_audit():
    """Return the full audit log."""
    if not os.path.exists(DEFAULT_AUDIT):
        raise HTTPException(status_code=404, detail="Audit log not found.")
    records = read_records(DEFAULT_AUDIT)
    return {"count": len(records), "records": records}


@app.get("/api/strategy-comparison")
def get_strategy_comparison():
    """Run the 3-way strategy showdown on the current batch."""
    report = _load_report()
    return report.get("strategy_comparison", {})


@app.get("/api/degradation")
def get_degradation():
    """Return systemic degradation incidents."""
    report = _load_report()
    return {"incidents": report.get("degradation_incidents", [])}


@app.post("/api/run-pipeline")
def run_pipeline(diagnoser_mode: str = "rules"):
    """Run the full pipeline: generate events -> diagnose -> decide -> execute -> metrics."""
    from data.generate_events import generate, write_jsonl
    events_path = DEFAULT_EVENTS
    audit_path = DEFAULT_AUDIT

    # 1. Generate events
    events = generate()
    write_jsonl(events, events_path)

    # 2. Run pipeline
    from src.pipeline import run as run_pipe
    run_pipe(events_path, audit_path, diagnoser_mode)

    # 3. Compute metrics
    sys.path.insert(0, os.path.join(_REPO_ROOT, "reports"))
    from reports.metrics import compute, render_markdown
    m = compute(events_path, audit_path)

    report_json_path = os.path.join(_REPO_ROOT, "reports", "report.json")
    report_md_path = os.path.join(_REPO_ROOT, "reports", "report.md")
    with open(report_json_path, "w") as f:
        json.dump(m, f, indent=2)
    with open(report_md_path, "w") as f:
        f.write(render_markdown(m))

    return m


class DiagnoseRequest(BaseModel):
    failure_reason: str
    amount: Optional[float] = 500.0
    payment_method: Optional[str] = "card"
    transaction_id: Optional[str] = "txn_playground"
    use_llm: Optional[bool] = False


@app.post("/api/diagnose")
def diagnose_single(req: DiagnoseRequest):
    """Diagnose a single transaction from raw failure text (playground mode)."""
    event_view = {
        "transaction_id": req.transaction_id,
        "merchant_id": "playground_merchant",
        "customer_id": "playground_customer",
        "amount": req.amount,
        "currency": "INR",
        "payment_method": req.payment_method,
        "status": STATUS_FAILED,
        "failure_reason": req.failure_reason,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "retry_count": 0,
    }

    diagnoser = get_diagnoser("llm" if req.use_llm else "rules")
    diagnosis = diagnoser.diagnose(event_view)

    decision = decide(diagnosis, retry_count=0, amount=req.amount)

    executor = get_default_executor()
    outcome = executor.run(decision, req.amount)

    return {
        "event": event_view,
        "diagnosis": diagnosis.to_dict(),
        "decision": decision.to_dict(),
        "outcome": outcome.to_dict(),
    }


@app.get("/api/transaction/{txn_id}")
def get_transaction_detail(txn_id: str):
    """Get full audit trail for a single transaction."""
    if not os.path.exists(DEFAULT_AUDIT):
        raise HTTPException(status_code=404, detail="Audit log not found.")
    records = read_records(DEFAULT_AUDIT)
    txn_records = [r for r in records if r.get("transaction_id") == txn_id]
    if not txn_records:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found.")

    events = _load_events_list()
    event = next((e for e in events if e.get("transaction_id") == txn_id), None)

    return {
        "transaction_id": txn_id,
        "event": event,
        "audit_trail": txn_records,
    }
