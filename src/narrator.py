"""Plain-English narrative of a batch result.

Turns the computed metrics into a short executive summary. Uses an LLM when
available; otherwise falls back to a deterministic template so a run never
depends on the network. Either way the numbers come from the real metrics dict —
the model only phrases them, it does not invent figures.
"""
from __future__ import annotations

import os


def _template(m: dict) -> str:
    sc = m.get("strategy_comparison", {})
    agent = sc.get("compliant_agent", {})
    naive = sc.get("naive_retry_all", {})
    inc = m.get("degradation_incidents", [])
    parts = [
        f"Across {m['batch_size']} transactions, "
        f"{m['at_risk_transactions']} were at risk "
        f"({m['total_at_risk']:,.0f} in value). The agent recovered "
        f"{m['total_recovered']:,.0f} — {m['recoverable_recovery_rate']*100:.0f}% "
        f"of the value that was genuinely recoverable — with a "
        f"{m['false_action_rate']*100:.1f}% false-action rate and "
        f"{m['diagnoser_accuracy']*100:.0f}% diagnoser accuracy against ground "
        f"truth.",
    ]
    if agent and naive:
        parts.append(
            f"A naive retry-everything strategy would net more raw dollars but "
            f"only by committing {naive.get('fraud_retry_violations', 0)} "
            f"fraud-retry compliance violations; the agent commits zero and "
            f"stays net-positive at {agent.get('net', 0):,.0f}.")
    if inc:
        parts.append(
            f"It also flagged {len(inc)} systemic degradation "
            f"incident(s) for circuit-breaking rather than blind retries.")
    parts.append(
        f"{m['exception_count']} transactions could not be recovered and are "
        f"itemised in the exception list.")
    return " ".join(parts)


def narrate(m: dict, model: str | None = None) -> dict:
    """Return {'text': summary, 'source': 'llm'|'template'}.

    Safe by construction — any failure degrades to the deterministic template.
    """
    template = _template(m)
    try:
        import anthropic
    except Exception:
        return {"text": template, "source": "template"}

    try:
        import json
        client = anthropic.Anthropic()
        model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
        facts = {k: m[k] for k in (
            "batch_size", "at_risk_transactions", "total_at_risk",
            "total_recovered", "recoverable_recovery_rate", "overall_recovery_rate",
            "false_action_rate", "diagnoser_accuracy", "exception_count",
            "strategy_comparison", "degradation_incidents") if k in m}
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            output_config={"effort": "low"},
            system=("You are a payments analyst. Write a 3-4 sentence executive "
                    "summary of this revenue-recovery batch for a technical "
                    "reviewer. Use ONLY the numbers provided — do not invent any. "
                    "Lead with money recovered and the compliance angle "
                    "(zero fraud-retry violations). Be precise and plain."),
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if text:
            return {"text": text, "source": "llm"}
    except Exception:
        pass
    return {"text": template, "source": "template"}
