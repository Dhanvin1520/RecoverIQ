# AI Revenue Recovery Agent

An agent for **Payment Degradation → Root Cause → Recovery Action**. It takes a
batch of payment transactions, diagnoses *why* each one failed, decides a
**compliant** recovery action under explicit stopping rules, simulates executing
that action, and writes a full **audit trail**. A separate metrics stage then
scores the whole batch **honestly against hidden ground truth** — including an
**exception list of everything it could not recover**.

The point isn't "we detected a problem." The point is **measured money
recovered across a full batch, with a compliant escalation path, stopping rules,
and an auditable paper trail** — and honest numbers, not one cherry-picked win.

## What it does (in one line)

> Load transactions → **diagnose root cause** → **decide action within policy**
> → **simulate recovery** → **log every step** → **compute real metrics + an
> exception list**.

## What makes this different from the usual build

Most builds stop at "detect a failure, retry it, show gross recovered."
This one goes three steps further — and each is computed, not claimed:

1. **Strategy showdown (net, not gross).** It runs three strategies on the
   *same* batch through a ground-truth-aware world model: do-nothing,
   naive-retry-all (the usual build), and this compliant agent. The naive
   approach nets more *raw dollars* — but only by **retrying fraud-adjacent
   charges**, a disqualifying compliance breach for any PSP. Its dollars are
   **inadmissible**. The compliant agent commits **0 fraud-retry violations**,
   is fully auditable, and is still net-positive. That's the honest, payments-
   company-relevant conclusion — not a rigged "we make more money."
2. **Cost / expected-value engine.** Recovery isn't free: retries cost fees,
   human escalation costs analyst time, retried fraud costs chargebacks +
   scheme penalties. Every decision is scored on **net** money via
   `src/economics.py`, and the policy drops actions whose expected value is
   negative (not worth chasing a tiny charge).
3. **Systemic degradation detection.** Beyond per-transaction recovery,
   `src/degradation.py` scans the stream for correlated bursts — the same
   failure hitting one rail in a tight window (e.g. an acquiring bank timing
   out repeatedly) — and recommends a **systemic** response (circuit-break +
   failover) instead of blindly retrying into a brownout.

## Architecture

```
                    ┌──────────────────────────────────────────────┐
 data/              │  events.jsonl  (synthetic batch, 150 events)  │
 generate_events.py │  each event carries a HIDDEN true_label       │
                    └───────────────┬──────────────────────────────┘
                                    │  agent_view()  (true_label stripped out)
                                    ▼
        ┌────────────┐     ┌────────────┐     ┌────────────┐     ┌────────────┐
        │ diagnoser  │ ──► │  policy    │ ──► │ executor   │ ──► │ audit_log  │
        │ root cause │     │ stop rules │     │ (adapter)  │     │  JSONL     │
        │ RULES ONLY │     │ escalation │     │ simulated  │     │ audit.jsonl│
        └────────────┘     └────────────┘     └────────────┘     └─────┬──────┘
             ▲  interface       ▲ retry cap        ▲ Razorpay-           │
             │  (LLM-ready)     │ fraud=escalate   │ swap-ready          │
             └──────────────────┴──────────────────┘                    │
                                                                        ▼
                                              ┌──────────────────────────────────┐
                     reports/metrics.py  ───► │ report.md + report.json           │
                     (ONLY module that reads  │ money recovered, recovery rate by │
                      the hidden true_label)  │ cause, false-action rate,         │
                                              │ diagnoser accuracy, EXCEPTION LIST│
                                              └──────────────────────────────────┘
```

The agent path (`diagnoser → policy → executor`) **never sees `true_label`**.
It is stripped by `PaymentEvent.agent_view()` before diagnosis. Only
`reports/metrics.py` reads the ground truth, so scoring stays honest.

## How to run it (two commands)

```bash
python3 data/generate_events.py   # 1. generate the synthetic batch -> data/events.jsonl
python3 -m src.pipeline           # 2. run agent over the batch  -> reports/audit.jsonl
python3 reports/metrics.py        # 3. score it honestly         -> reports/report.md + .json
```

Or all at once, and build the visual dashboard:

```bash
python3 data/generate_events.py && python3 -m src.pipeline && python3 reports/metrics.py && python3 reports/dashboard.py
```

Then open **`reports/dashboard.html`** in any browser — a self-contained
(no-server, offline) visual dashboard with the headline KPIs, the compliance
showdown, recovery-by-cause bars, degradation incidents, and the exception list.

Optional AI + live-integration variants:

```bash
python3 -m src.pipeline --diagnoser llm        # Claude-assisted root-cause classification
python3 -m src.pipeline --executor razorpay    # live Razorpay test-mode API calls
```

Run the tests (38 unit tests):

```bash
python3 -m unittest discover -s tests -v
```

No install, no accounts, no network, no API keys for the default path. Standard
library only (Python 3.10+); `requirements.txt` lists a single *optional*
dependency used only by the LLM diagnoser path.

## Where the real metrics live

- **`reports/report.md`** — the human-readable report.
- **`reports/report.json`** — the same metrics, machine-readable.
- **`reports/audit.jsonl`** — the raw per-stage audit trail every metric is
  computed from.

Every number is computed from an actual run. **Nothing is hardcoded.** Re-run
the three commands and the reports regenerate from scratch.

### What the metrics cover

- Total **$ at risk** (sum of failed/degraded amounts).
- Total **$ recovered**, and the **overall recovery rate**.
- **Recovery rate on *truly-recoverable* value** — the honest denominator, since
  a big chunk of at-risk money is fraud/dead value the agent *correctly refuses
  to chase*.
- **Recovery rate broken down by root cause.**
- **False-action rate** — how often the agent took a recovery action on a
  transaction whose hidden `true_label` was fraud or not-recoverable. (Target: 0.)
- **Diagnoser accuracy** vs. ground truth (recoverable vs. must-escalate).
- **Exception list** — every transaction it could NOT resolve, with the reason
  (retry cap reached, fraud → escalate, ambiguous cause → escalate, or the
  recovery action simply failed).

## Failure taxonomy → recovery mapping

| Root cause | Action | Why |
|---|---|---|
| `bank_timeout` | `retry_now` | Transient; safe to retry immediately |
| `network_error` | `retry_now` | Transient; safe to retry immediately |
| `insufficient_funds` | `retry_delayed` + `notify_customer` | Funds may arrive later |
| `card_expired` | `request_new_payment_method` | Retrying is futile |
| `risk_block` / fraud-adjacent | **`escalate_human` — NEVER retry** | Compliance |
| `unknown` / ambiguous | `escalate_human` (reasoning logged) | Don't guess |

## Stopping rules / escalation (`src/policy.py`)

Deliberately kept as plain, unit-tested functions — **not buried in the executor**:

- Retries capped at `MAX_RETRIES` (default **2**) per transaction; hitting the
  cap → `escalate_human` (`blocked_by_rule = retry_cap_reached`).
- Anything fraud-adjacent → **never retry**, escalate immediately
  (`blocked_by_rule = fraud_never_retry`).
- Ambiguous/unknown cause → escalate with reasoning logged
  (`blocked_by_rule = unknown_cause_escalate`).

Every escalation records *which* rule stopped it, so the audit trail explains the
agent's restraint, not just its actions.

## Design choices worth calling out

**Diagnoser defaults to RULES ONLY (deterministic, offline)** — no LLM calls at
runtime, so the live demo can never break on an API issue and every result is
reproducible. But the diagnoser sits behind a clean `Diagnoser` interface
(`diagnose(event_view) -> Diagnosis`), and an **`LLMDiagnoser` (Claude) is
already implemented** behind that same interface to prove the design is real —
the pipeline, policy, executor, and audit log are untouched by the swap. Run it
with:

```bash
python3 -m src.pipeline --diagnoser llm     # Claude-assisted classification
```

The LLM path is **safe by construction**: if the `anthropic` SDK isn't
installed, no credentials are configured, or any API call/parse fails, it
**transparently falls back to the deterministic rules** for that transaction —
so it can never break a run. That's the intended posture: deterministic rules as
the shippable default, real AI available as a drop-in when you want it. See
`src/diagnoser.py`.

**Executor: real Razorpay integration, behind one adapter.** All "external"
side effects go through the `RecoveryAdapter` interface. The default
`SimulatedAdapter` is deterministic and offline (the honest-scoring basis). A
**`RazorpayTestModeAdapter` is also implemented** — with `--executor razorpay`
(plus the `razorpay` SDK and `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` test keys)
each retry makes a **live call to Razorpay's test-mode API**, and the real order
id is written into the audit trail as evidence. It refuses to run with live
(`rzp_live_…`) keys. Default stays simulated so the metrics remain reproducible
and offline. The simulated success probabilities are modelling assumptions
(documented in `src/executor.py`), **not** ground truth — the executor never
reads `true_label`; honesty is scored independently.

**Audit log is JSONL** — one JSON record per line, human-readable, git-friendly,
trivial to inspect or replay.

## Repo structure

```
data/generate_events.py   synthetic batch generator + failure-mix config
data/events.jsonl         generated batch (regenerable)
src/models.py             dataclasses for the core data structures
src/diagnoser.py          rule-based root-cause classification (LLM-ready interface)
src/policy.py             stopping rules / escalation logic + cost-aware EV gate
src/economics.py          cost model + expected-value engine (net-money truth)
src/executor.py           simulated recovery actions (Razorpay-swap-ready adapter)
src/strategies.py         3-way showdown: do-nothing vs naive-retry vs this agent
src/degradation.py        systemic degradation / brownout detection
src/audit_log.py          append structured records to audit.jsonl
src/pipeline.py           orchestrates generate → diagnose → decide → execute → log
src/narrator.py           plain-English batch summary (Claude, offline fallback)
reports/metrics.py        reads audit log + ground truth, computes all metrics
reports/dashboard.py      renders report.json into a self-contained HTML dashboard
reports/report.md         generated human-readable report
reports/report.json       generated machine-readable metrics
reports/dashboard.html    generated visual dashboard (open in a browser)
tests/                    unit tests for diagnoser, policy, executor, economics,
                          degradation, strategies
```

## A note on honesty

The synthetic batch includes fraud, permanently-dead transactions, and
deliberately **ambiguous** failures the rule-based diagnoser genuinely can't
classify. Those don't get swept under the rug — they surface in the exception
list and pull the diagnoser accuracy below 100%, which is the point. The
headline recovery number is diluted by money that *should never be recovered*,
so the report reports both the raw rate and the rate on truly-recoverable value.
