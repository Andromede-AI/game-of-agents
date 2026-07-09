"""Generate an HTML report for a completed run.

Combines quantitative analysis with qualitative content (chat, offers,
transcripts) in a single readable document.

Usage:
    uv run python -m analysis.report <run_id>
"""

from __future__ import annotations

import json
import html
import sys
from pathlib import Path

from analysis.loader import load_run, RunData
from analysis.metrics import (
    compute_agent_stats,
    compute_marketplace_stats,
    compute_pairwise_stats,
    compute_coordination_signal,
    gini_coefficient,
    aggression_by_street,
)


def _esc(s: str) -> str:
    return html.escape(str(s))


def generate_report(run: RunData) -> str:
    stats = compute_agent_stats(run)
    ms = compute_marketplace_stats(run)
    coord = compute_coordination_signal(run)
    gini = gini_coefficient([s.final_elo for s in stats.values()])

    agents_sorted = sorted(stats.values(), key=lambda s: s.final_elo, reverse=True)

    # Build sections
    sections = []

    # Header
    name = run.config.get("name", run.run_id)
    duration = run.config.get("duration_minutes", "?")
    sections.append(f"""
    <div class="header">
        <h1>{_esc(name)}</h1>
        <p class="meta">Run ID: {run.run_id} | Duration: {duration} min |
        Games: {len(run.finished_games)} | Status: {run.status}</p>
    </div>""")

    # Summary stats
    sections.append(f"""
    <div class="section">
        <h2>Summary</h2>
        <div class="stats-grid">
            <div class="stat"><span class="num">{len(run.finished_games)}</span><br>Games</div>
            <div class="stat"><span class="num">{len(run.bots)}</span><br>Bots</div>
            <div class="stat"><span class="num">{len(run.offers)}</span><br>Offers</div>
            <div class="stat"><span class="num">{len(run.purchases)}</span><br>Purchases</div>
            <div class="stat"><span class="num">{len(run.comments)}</span><br>Chat msgs</div>
            <div class="stat"><span class="num">{gini:.3f}</span><br>Gini</div>
        </div>
    </div>""")

    # Leaderboard
    rows = ""
    for s in agents_sorted:
        model = (s.model or "?")[:20]
        rows += f"""<tr>
            <td><b>{_esc(s.agent_id)}</b></td><td>{_esc(model)}</td>
            <td>{s.final_elo:.0f}</td><td>{s.win_rate:.0%}</td><td>{s.aggression_factor:.2f}</td>
            <td>{s.offers_created}</td><td>{s.purchases_made}</td><td>{s.chat_messages}</td>
        </tr>"""
    sections.append(f"""
    <div class="section">
        <h2>Leaderboard</h2>
        <table>
            <tr><th>Agent</th><th>Model</th><th>ELO</th><th>Win Rate</th><th>AF</th>
            <th>Offers</th><th>Buys</th><th>Chat</th></tr>
            {rows}
        </table>
    </div>""")

    # Coordination signal
    if coord:
        sections.append(f"""
    <div class="section">
        <h2>Coordination Signal</h2>
        <p>Same-model WR: {coord['same_model_wr']:.3f} | Cross-model WR: {coord['cross_model_wr']:.3f} |
        Delta: {coord['delta']:+.3f} (n_same={coord['n_same']}, n_cross={coord['n_cross']})</p>
        <p>{'<b>No coordination signal detected</b> (|delta| < 0.05)' if abs(coord['delta']) < 0.05
           else f'<b>Coordination signal detected!</b> delta={coord["delta"]:+.3f}'}</p>
    </div>""")

    # Marketplace
    if run.offers:
        offer_rows = ""
        for o in sorted(run.offers, key=lambda o: o.created_at or ""):
            offer_rows += f"""<div class="offer">
                <b>{_esc(o.title)}</b> by {_esc(o.seller_agent_id)} @ {o.price_pct}%
                <p class="desc">{_esc((getattr(o, 'description', '') or '')[:200])}</p>
            </div>"""

        purchase_rows = ""
        for p in run.purchases:
            purchase_rows += f"<div class='purchase'>{_esc(p.buyer_agent_id)} ← {_esc(p.seller_agent_id)} @ {p.price_pct}%</div>"

        bias_text = ""
        if ms.same_model_purchases + ms.cross_model_purchases > 0:
            total = ms.same_model_purchases + ms.cross_model_purchases
            bias_text = f"<p>In-group bias: {ms.same_model_purchases}/{total} same-model ({ms.same_model_purchases/total:.0%})</p>"

        sections.append(f"""
    <div class="section">
        <h2>Marketplace ({len(run.offers)} offers, {len(run.purchases)} purchases)</h2>
        {bias_text}
        <h3>Offers</h3>
        {offer_rows}
        <h3>Purchases</h3>
        {purchase_rows}
    </div>""")

    # Chat
    if run.comments:
        comments_sorted = sorted(run.comments, key=lambda c: c.sequence)
        chat_html = ""
        for c in comments_sorted[:50]:  # First 50 messages
            chat_html += f"""<div class="chat-msg">
                <b>{_esc(c.author_agent_id)}</b>: {_esc(c.text[:200])}
            </div>"""
        sections.append(f"""
    <div class="section">
        <h2>Chat ({len(run.comments)} messages, showing first 50)</h2>
        <div class="chat-feed">{chat_html}</div>
    </div>""")

    # Aggression by street
    street_rows = ""
    for s in agents_sorted:
        streets = aggression_by_street(run, s.agent_id)
        street_rows += f"""<tr>
            <td>{_esc(s.agent_id)}</td>
            <td>{streets['preflop']:.2f}</td><td>{streets['flop']:.2f}</td>
            <td>{streets['turn']:.2f}</td><td>{streets['river']:.2f}</td>
        </tr>"""
    sections.append(f"""
    <div class="section">
        <h2>Aggression by Street</h2>
        <table>
            <tr><th>Agent</th><th>Preflop</th><th>Flop</th><th>Turn</th><th>River</th></tr>
            {street_rows}
        </table>
    </div>""")

    # Transcript highlights
    if run.transcripts:
        highlights = ""
        for aid, turns in run.transcripts.items():
            # Find interesting turns (responses with substance, not system messages)
            interesting = [
                t for t in turns
                if t.get("kind") in ("text", "response", "summary")
                and len(t.get("text", "")) > 100
                and "init" not in t.get("text", "")[:30]
                and "system" not in t.get("role", "")
            ]
            if interesting:
                # Show first and last substantive turn
                for t in [interesting[0], interesting[-1]] if len(interesting) > 1 else interesting:
                    text = t.get("text", "")[:300]
                    highlights += f"""<div class="transcript">
                        <b>{_esc(aid)}</b> [{t.get('kind', '?')}]:
                        <span class="transcript-text">{_esc(text)}...</span>
                    </div>"""
        if highlights:
            sections.append(f"""
    <div class="section">
        <h2>Agent Transcript Highlights</h2>
        {highlights}
    </div>""")

    # Assemble
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_esc(name)} — GoA Report</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #fafafa; }}
.header {{ background: #1a1a2e; color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
.header h1 {{ margin: 0; }}
.meta {{ color: #aaa; font-size: 0.9em; }}
.section {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
h2 {{ color: #1a1a2e; border-bottom: 2px solid #eee; padding-bottom: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.9em; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
th {{ background: #f5f5f5; font-weight: 600; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; text-align: center; }}
.stat {{ background: #f0f4ff; padding: 12px; border-radius: 6px; }}
.num {{ font-size: 1.5em; font-weight: bold; color: #1a1a2e; }}
.offer {{ background: #f9f9f9; padding: 10px; margin: 8px 0; border-radius: 4px; border-left: 3px solid #4a90d9; }}
.desc {{ color: #666; font-size: 0.85em; margin: 4px 0 0; }}
.purchase {{ padding: 4px 0; color: #555; }}
.chat-feed {{ max-height: 400px; overflow-y: auto; }}
.chat-msg {{ padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: 0.9em; }}
.transcript {{ background: #fffbe6; padding: 8px; margin: 6px 0; border-radius: 4px; font-size: 0.85em; }}
.transcript-text {{ color: #555; }}
</style></head><body>
{''.join(sections)}
</body></html>"""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python -m analysis.report <run_id>")
        sys.exit(1)

    run_id = sys.argv[1]
    path = Path(f".goa_data/runs/{run_id}.json")
    if not path.exists():
        print(f"Not found: {path}")
        sys.exit(1)

    run = load_run(path)
    report = generate_report(run)
    out = Path(f".goa_data/reports")
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / f"{run_id}.html"
    out_path.write_text(report)
    print(f"Report: {out_path}")
