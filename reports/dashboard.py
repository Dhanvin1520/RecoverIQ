"""Render reports/report.json into a self-contained HTML dashboard.

No server, no build step, no external assets — one file you double-click to open
in any browser. Everything (styles, bars) is inline so it works fully offline.

    python3 reports/dashboard.py        # -> reports/dashboard.html
"""
from __future__ import annotations

import argparse
import json
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_JSON = os.path.join(_REPO_ROOT, "reports", "report.json")
DEFAULT_HTML = os.path.join(_REPO_ROOT, "reports", "dashboard.html")


def _money(x):
    return f"{x:,.0f}"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _bar(pct, color):
    w = max(0.0, min(1.0, pct)) * 100
    return (f'<div class="bar"><div class="fill" style="width:{w:.1f}%;'
            f'background:{color}"></div></div>')


def render(m: dict) -> str:
    sc = m.get("strategy_comparison", {})
    agent = sc.get("compliant_agent", {})
    naive = sc.get("naive_retry_all", {})
    nothing = sc.get("do_nothing", {})
    narr = m.get("narrative", {})
    mode = m.get("diagnoser_mode", "rules")
    ai_badge = ("Claude LLM" if mode == "llm" else "Rule-based")

    kpis = [
        ("Value at risk", _money(m["total_at_risk"]), f"{m['at_risk_transactions']} txns"),
        ("Recovered", _money(m["total_recovered"]),
         f"{m['recoverable_recovery_rate']*100:.0f}% of recoverable value"),
        ("False-action rate", f"{m['false_action_rate']*100:.1f}%",
         "actions on fraud/dead txns"),
        ("Diagnoser accuracy", f"{m['diagnoser_accuracy']*100:.0f}%",
         f"{m['diagnoser_correct']}/{m['diagnoser_total']} vs ground truth"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{_esc(l)}</div>'
        f'<div class="kpi-val">{_esc(v)}</div>'
        f'<div class="kpi-sub">{_esc(s)}</div></div>'
        for l, v, s in kpis)

    # Strategy showdown rows
    def strat_row(name, s, admissible):
        badge = ('<span class="tag bad">inadmissible</span>' if not admissible
                 else '<span class="tag good">shippable</span>')
        return (f"<tr><td>{_esc(name)}</td>"
                f"<td class='num'>{_money(s.get('gross_recovered',0))}</td>"
                f"<td class='num'>{_money(s.get('fraud_cost',0))}</td>"
                f"<td class='num'>{s.get('fraud_retry_violations',0)}</td>"
                f"<td class='num'><b>{_money(s.get('net',0))}</b></td>"
                f"<td>{badge}</td></tr>")

    strat_html = ""
    if nothing:
        strat_html += strat_row("Do nothing", nothing, True)
    if naive:
        strat_html += strat_row("Naive retry-all (usual build)", naive, False)
    if agent:
        strat_html += strat_row("This agent (diagnose + policy + EV)", agent, True)

    # Recovery by cause bars
    cats = m.get("recovery_rate_by_category", {})
    cause_html = ""
    for cat, v in cats.items():
        cause_html += (
            f'<div class="row"><div class="row-label">{_esc(cat)}</div>'
            f'{_bar(v["recovery_rate"], "#4f9dff")}'
            f'<div class="row-val">{v["recovery_rate"]*100:.0f}% '
            f'· {_money(v["recovered"])}/{_money(v["at_risk"])}</div></div>')

    # Degradation incidents
    inc = m.get("degradation_incidents", [])
    if inc:
        inc_html = "".join(
            f'<tr><td>{_esc(i["payment_method"])}</td>'
            f'<td>{_esc(i["root_cause"])}</td>'
            f'<td class="num">{i["count"]}</td>'
            f'<td class="num">{_money(i["amount_at_risk"])}</td>'
            f'<td>{_esc(i["recommended_systemic_action"])}</td></tr>'
            for i in inc)
        inc_section = (
            '<table><thead><tr><th>Rail</th><th>Cause</th><th>Failures</th>'
            '<th>$ at risk</th><th>Recommended systemic action</th></tr></thead>'
            f'<tbody>{inc_html}</tbody></table>')
    else:
        inc_section = '<p class="muted">No systemic degradation detected.</p>'

    # Exception list (first 25)
    exc = m.get("exceptions", [])[:25]
    exc_html = "".join(
        f'<tr><td>{_esc(e["transaction_id"])}</td>'
        f'<td class="num">{_money(e["amount"])}</td>'
        f'<td>{_esc(e["root_cause"])}</td>'
        f'<td>{_esc(e["final_action"])}</td>'
        f'<td>{_esc(e["reason"])}</td></tr>'
        for e in exc)
    more = m.get("exception_count", 0) - len(exc)
    more_note = (f'<p class="muted">+ {more} more in reports/report.md</p>'
                 if more > 0 else "")

    narr_src = ("Claude (LLM)" if narr.get("source") == "llm" else "auto")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Revenue Recovery — Dashboard</title>
<style>
:root {{ --bg:#0d1117; --card:#161b22; --line:#232a34; --txt:#e6edf3;
        --muted:#8b949e; --good:#2ea043; --bad:#f85149; --accent:#4f9dff; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--txt);
       font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:1040px; margin:0 auto; padding:32px 20px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; }}
h2 {{ font-size:16px; text-transform:uppercase; letter-spacing:.06em;
      color:var(--muted); margin:36px 0 14px; }}
.sub {{ color:var(--muted); margin:0 0 8px; }}
.badge {{ display:inline-block; padding:3px 10px; border-radius:999px;
         font-size:12px; font-weight:600; background:#1f6feb22;
         color:var(--accent); border:1px solid #1f6feb55; }}
.grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px; }}
.kpi-label {{ color:var(--muted); font-size:12px; text-transform:uppercase;
             letter-spacing:.05em; }}
.kpi-val {{ font-size:26px; font-weight:700; margin:6px 0 2px; }}
.kpi-sub {{ color:var(--muted); font-size:12px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:18px 20px; }}
.quote {{ font-size:16px; line-height:1.6; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); }}
th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.tag {{ padding:2px 8px; border-radius:999px; font-size:12px; font-weight:600; }}
.tag.good {{ background:#2ea04322; color:var(--good); }}
.tag.bad {{ background:#f8514922; color:var(--bad); }}
.row {{ display:grid; grid-template-columns:150px 1fr 190px; align-items:center;
       gap:12px; margin:8px 0; }}
.row-label {{ font-family:ui-monospace,Menlo,monospace; font-size:13px; }}
.row-val {{ color:var(--muted); font-size:13px; text-align:right; }}
.bar {{ background:#0d111799; border:1px solid var(--line); border-radius:6px;
       height:16px; overflow:hidden; }}
.fill {{ height:100%; }}
.muted {{ color:var(--muted); }}
.note {{ color:var(--muted); font-size:12px; margin-top:40px; }}
@media(max-width:760px){{ .grid{{grid-template-columns:repeat(2,1fr);}}
  .row{{grid-template-columns:1fr;}} }}
</style></head><body><div class="wrap">
<h1>AI Revenue Recovery — Batch Dashboard</h1>
<p class="sub">Payment degradation → root cause → recovery action.
Every figure computed from a live run, scored against hidden ground truth.</p>
<p><span class="badge">Diagnoser: {_esc(ai_badge)}</span></p>

<h2>Executive summary <span class="muted" style="text-transform:none">· {_esc(narr_src)}</span></h2>
<div class="card quote">{_esc(narr.get('text',''))}</div>

<h2>Headline</h2>
<div class="grid">{kpi_html}</div>

<h2>Compliance showdown — net money, not gross</h2>
<div class="card"><table><thead><tr><th>Strategy</th><th class="num">Gross</th>
<th class="num">Fraud fallout</th><th class="num">Violations</th>
<th class="num">Net</th><th>Verdict</th></tr></thead>
<tbody>{strat_html}</tbody></table></div>

<h2>Recovery rate by root cause</h2>
<div class="card">{cause_html}</div>

<h2>Systemic degradation detected</h2>
<div class="card">{inc_section}</div>

<h2>Exception list — {m.get('exception_count',0)} unresolved</h2>
<div class="card"><table><thead><tr><th>Txn</th><th class="num">Amount</th>
<th>Root cause</th><th>Action</th><th>Reason</th></tr></thead>
<tbody>{exc_html}</tbody></table>{more_note}</div>

<p class="note">Generated from reports/report.json · nothing hardcoded ·
regenerate with: python3 data/generate_events.py &amp;&amp; python3 -m src.pipeline
&amp;&amp; python3 reports/metrics.py &amp;&amp; python3 reports/dashboard.py</p>
</div></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the HTML dashboard")
    ap.add_argument("--report-json", default=DEFAULT_JSON)
    ap.add_argument("--out", default=DEFAULT_HTML)
    args = ap.parse_args()
    with open(args.report_json) as f:
        m = json.load(f)
    with open(args.out, "w") as f:
        f.write(render(m))
    print(f"Wrote dashboard -> {args.out}  (open it in a browser)")


if __name__ == "__main__":
    main()
