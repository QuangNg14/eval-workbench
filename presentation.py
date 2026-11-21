"""Offline views. Fixture strings and ballot rationales are always inert text."""

import json
from html import escape

from eval_core import Packet, Result, ROLES, PIN


def literal(value: object) -> str:
    return json.dumps(value, ensure_ascii=True)


def page(title: str, intro: str, body: str, packet: Packet) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
body {{max-width:68rem;margin:2rem auto;padding:0 1rem;color:#182b31;background:#fafaf6;font:1rem/1.55 system-ui,sans-serif}}
header,article,section {{border-bottom:1px solid #a3b5af;padding:1rem 0}}
h1,h2,h3 {{line-height:1.2}} h3 {{color:#16594f}}
.pair {{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,21rem),1fr));gap:1rem}}
.card {{border:1px solid #abbdb7;background:white;border-radius:.35rem;padding:0 1rem}}
pre,code {{white-space:pre-wrap;overflow-wrap:anywhere}} table {{border-collapse:collapse;width:100%}}
td,th {{border-bottom:1px solid #c4cfca;padding:.3rem;text-align:left;vertical-align:top}}
small {{overflow-wrap:anywhere}} dt {{font-weight:bold}} .fail {{color:#8d2a17;font-weight:bold}}
</style></head><body><header><small>LEAF ROOM / OFFLINE EVAL &nbsp; · &nbsp;
{"SCRIPTED DEMO" if packet.mode == "demo" else "MANUAL BALLOT"}</small>
<h1>{escape(title)}</h1><p>{escape(intro)}</p>
<small>Packet: {packet.id}<br>Frozen fixture SHA-256: {PIN}</small></header>
<main>{body}</main></body></html>"""


def review_html(data: dict, packet: Packet) -> str:
    parts = [
        "<section><h2>Review instructions</h2><p>Prefer the more correct, grounded and useful response. "
        "For policy questions, check the clause and whether the response should defer. For code, compare "
        "the specification and recorded examples. Do not run either snippet.</p><p>In the companion "
        "<code>ballot.json</code>, set each <code>choice</code> to <b>A</b>, <b>B</b>, <b>tie</b> "
        "(equally useful), or <b>abstain</b> (cannot judge). Give a short evidence-based "
        "<code>reason</code> for each. Return the completed JSON, not this page.</p></section>",
        "<section><h2>Fictional Leaf Room rules</h2><dl>",
    ]
    for ref, text in data["policy"].items():
        parts.append(f"<dt>{escape(ref)}</dt><dd>{escape(text)}</dd>")
    parts.append("</dl></section>")
    for n, case in enumerate(data["cases"], 1):
        i = case["id"]
        parts.append(f'<article><small>CASE {n} / {escape(i)}</small><h2>{escape(case["prompt"])}</h2>')
        if "tests" in case:
            parts.append("<h3>Frozen examples (input → expected)</h3><ul>")
            for name, test in case["tests"].items():
                parts.append(f'<li>{escape(name)}: <code>{escape(literal(test["input"]))} → '
                             f'{escape(literal(test["want"]))}</code></li>')
            parts.append("</ul>")
        parts.append('<div class="pair">')
        for side in ("A", "B"):
            out = case["outputs"][packet.role(i, side)]
            parts.append(f'<div class="card"><h3>Response {side}</h3><pre>{escape(out["text"])}</pre>')
            if "observed" in out:
                parts.append("<b>Pre-recorded observations</b><ul>")
                for name, got in out["observed"].items():
                    parts.append(f"<li>{escape(name)}: <code>{escape(literal(got))}</code></li>")
                parts.append("</ul>")
            parts.append("</div>")
        parts.append("</div></article>")
    return page("Blind comparison", "The requests, responses and recorded observations are synthetic. "
                "No model, user session or code execution produced these observations.", "".join(parts), packet)


def report_html(packet: Packet, rows: list[Result], groups: dict[str, dict[str, str]]) -> str:
    parts = []
    for title, items in groups.items():
        parts.append(f"<section><h2>{escape(title)}</h2><table><tbody>")
        for label, value in items.items():
            parts.append(f"<tr><th scope='row'>{escape(label)}</th><td>{escape(value)}</td></tr>")
        parts.append("</tbody></table></section>")
    parts.append("<p>Preference regressions are votes for the baseline, not failed checks. "
                 "A case passes only if all its checks pass, even when the reviewer abstains. "
                 "A justified deferral can pass without counting as an answer.</p><h2>Case ledger</h2>")
    for row in rows:
        parts.append(f"<article><h3>{escape(row.id)}: {escape(row.preference)}</h3>"
                     f"<p>Choice: <b>{escape(row.choice)}</b> · A = {packet.role(row.id, 'A')}, "
                     f"B = {packet.role(row.id, 'B')}.<br>Rationale: {escape(row.reason)}</p>")
        for role in ROLES:
            status = "PASS" if row.passed(role) else "FAIL"
            parts.append(f"<details><summary>{role}: {status}</summary><table><thead><tr>"
                         "<th>Check</th><th>Expected</th><th>Recorded</th><th>Result</th></tr></thead><tbody>")
            for c in row.by_role[role]:
                verdict = "pass" if c.passed else "<span class='fail'>fail</span>"
                parts.append(f"<tr><th scope='row'>{escape(c.label)}</th><td>{escape(literal(c.expected))}</td>"
                             f"<td>{escape(literal(c.recorded))}</td><td>{verdict}</td></tr>")
            parts.append("</tbody></table></details>")
        parts.append("</article>")
    origin = "Scripted fixture votes, not human judgments." if packet.mode == "demo" else (
        "Submitted manual ballot; reviewer identity is not verified.")
    return page("Unblinded result", f"{origin} All cases and outputs are invented. "
                "These figures do not measure real model or user performance.", "".join(parts), packet)


def report_text(packet: Packet, rows: list[Result], groups: dict[str, dict[str, str]]) -> str:
    lines = ["SCRIPTED DEMO: fixture votes" if packet.mode == "demo" else "MANUAL BALLOT: identity unverified",
             "Synthetic outputs; not model or user performance."]
    for title, items in groups.items():
        lines.append(f"\n{title}")
        lines.extend(f"  {label}: {value}" for label, value in items.items())
    lines.append("\nCase | preference | baseline -> candidate (all checks) | reason")
    for row in rows:
        states = ["pass" if row.passed(r) else "FAIL" for r in ROLES]
        lines.append(f"{row.id} | {row.preference} | {' -> '.join(states)} | {literal(row.reason)}")
    return "\n".join(lines)
