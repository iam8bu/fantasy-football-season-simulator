#!/usr/bin/env python3
"""Build the season standings dashboard and write index.html.

Reads output/season_sim_<league_id>.json (written by main.py) -- run main.py
first (or after) to refresh the underlying numbers, then run this to regenerate
the page. Manually triggered, like the World Cup dashboard's build script: no
auto-refresh, just re-run both whenever you want updated odds.

    python3 main.py && python3 build_dashboard.py
"""
import argparse
import json
from datetime import datetime
from html import escape as esc
from pathlib import Path

DEFAULT_LEAGUE_ID = "1392633709420646400"
OUT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = OUT_DIR / "output"

PLAYOFF_TIERS = [
    (65, "green", "Strong position"),
    (35, "amber", "Bubble team"),
    (0, "red", "Longshot"),
]

# ---------------------------------------------------------------------------
# CSS -- same token system and component vocabulary as the World Cup dashboard
# (dark terminal surface, Inter, heatmap table cells over separate bar meters).
# ---------------------------------------------------------------------------
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

:root {
  --bg:        #0d1117;
  --surface:   #161b22;
  --surface2:  #21262d;
  --border:    #30363d;
  --text:      #e6edf3;
  --muted:     #7d8590;
  --blue:      #2f81f7;
  --blue-dim:  rgba(47,129,247,.09);
  --blue-bdr:  rgba(47,129,247,.28);
  --green:     #3fb950;
  --green-dim: rgba(63,185,80,.09);
  --green-bdr: rgba(63,185,80,.28);
  --amber:     #d29922;
  --amber-dim: rgba(210,153,34,.09);
  --amber-bdr: rgba(210,153,34,.28);
  --red:       #f85149;
  --red-dim:   rgba(248,81,73,.07);
  --red-bdr:   rgba(248,81,73,.22);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* -- Header -- */
header {
  background: linear-gradient(180deg, #111d35 0%, #0d1117 100%);
  border-bottom: 1px solid var(--border);
  padding: 36px 32px 28px;
  text-align: center;
}
.header-eyebrow {
  font-size: 10px; font-weight: 700;
  letter-spacing: 2.5px; text-transform: uppercase;
  color: var(--blue); margin-bottom: 12px;
}
header h1 {
  font-size: clamp(1.6rem, 3.5vw, 2.4rem);
  font-weight: 900; letter-spacing: -.6px; line-height: 1.1;
  margin-bottom: 12px;
}
header h1 span { color: var(--blue); }
.subtitle {
  color: var(--muted); font-size: 13px; line-height: 1.6;
  max-width: 560px; margin: 0 auto;
}

/* -- Summary strip -- */
.summary-strip {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex; justify-content: center; flex-wrap: wrap;
}
.summary-item {
  padding: 15px 30px; text-align: center;
  border-right: 1px solid var(--border); min-width: 160px;
}
.summary-item:last-child { border-right: none; }
.s-label {
  display: block; color: var(--muted); font-size: 10px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 1.4px; margin-bottom: 5px;
}
.s-value { display: block; font-size: 1.2rem; font-weight: 800; letter-spacing: -.3px; }
.s-value.small { font-size: .9rem; font-weight: 700; }
.s-sub { display: block; font-size: 10px; color: var(--muted); margin-top: 4px; font-weight: 500; }

/* -- Legend -- */
.legend-bar {
  display: flex; justify-content: center; gap: 20px; flex-wrap: wrap;
  padding: 8px 16px; background: var(--surface2);
  border-bottom: 1px solid var(--border); font-size: 11px; color: var(--muted);
}
.legend-bar span { display: flex; align-items: center; gap: 5px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.green { background: var(--green); }
.dot.amber { background: var(--amber); }
.dot.red   { background: var(--red); }
.dot.blue  { background: var(--blue); }

/* -- Standings table -- */
.tourn-wrap { max-width: 900px; margin: 0 auto; padding: 26px 14px 10px; }
.section-label {
  font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.4px;
  color: var(--muted); margin-bottom: 13px;
}
.tourn-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
.tourn-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 620px; }
.tourn-table th {
  cursor: pointer; padding: 9px 10px; text-align: right; font-weight: 500;
  border-bottom: 2px solid var(--border); background: var(--surface);
  color: var(--muted); white-space: nowrap; user-select: none;
}
.tourn-table th:nth-child(2) { text-align: left; }
.tourn-table th.sort-active { color: var(--text); }
.tourn-table td {
  padding: 9px 10px; text-align: right; border-bottom: 1px solid var(--border); font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.tourn-table td:nth-child(2) { text-align: left; white-space: nowrap; }
.tourn-table tr:last-child td { border-bottom: none; }
.tourn-table tbody tr:hover td { background: var(--surface2) !important; }
.team-name-cell { display: flex; align-items: center; gap: 7px; justify-content: flex-start; }
.fav-badge {
  font-size: 9px; font-weight: 800; padding: 2px 7px; border-radius: 4px;
  background: var(--blue-dim); color: var(--blue); border: 1px solid var(--blue-bdr);
  text-transform: uppercase; letter-spacing: .4px;
}
.record-sub { color: var(--muted); font-size: 11px; }

.playoff-line td { padding: 0; border-bottom: none; }
.playoff-line .line-inner {
  display: flex; align-items: center; gap: 10px; padding: 3px 10px;
  background: var(--surface);
}
.playoff-line .rule { flex: 1; height: 1px; background: var(--border); }
.playoff-line .label {
  font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--muted);
  white-space: nowrap;
}

.tourn-note { color: var(--muted); font-size: 11px; padding: 12px 4px 4px; }

/* -- Footer -- */
footer {
  text-align: center; padding: 24px 20px 30px;
  color: var(--muted); font-size: 11px; line-height: 1.8;
}
footer a { color: var(--blue); text-decoration: none; }
footer a:hover { text-decoration: underline; }

/* -- Responsive -- */
@media (max-width: 640px) {
  header { padding: 28px 18px 22px; }
  .summary-strip { flex-direction: column; }
  .summary-item { border-right: none; border-bottom: 1px solid var(--border); }
  .summary-item:last-child { border-bottom: none; }
  .legend-bar { overflow-x: auto; justify-content: flex-start; }
}
"""


def tier_for(playoff_pct: float):
    for threshold, cls, label in PLAYOFF_TIERS:
        if playoff_pct >= threshold:
            return cls, label
    return "red", "Longshot"


def heat_bg(val: float, max_val: float, cls: str) -> str:
    """Cell-background heatmap intensity, scaled to this column's max (same
    technique as the World Cup tournament table)."""
    if not val or max_val <= 0:
        return ""
    color = {"green": "63,185,80", "amber": "210,153,34", "red": "248,81,73"}[cls]
    intensity = round((val / max_val) * 0.30, 3)  # capped well below opaque -- text stays readable
    return f' style="background:rgba({color},{intensity})"'


def build_html(data: dict) -> str:
    teams = data["teams"]
    league_name = data["league_name"]
    current_week = data["current_week"]
    regular_season_weeks = data["regular_season_weeks"]
    playoff_teams = data["playoff_teams"]
    n_sims = data["n_sims"]

    favorite = max(teams, key=lambda t: t["champ_pct"])

    # Tightest race: biggest rank move would swap in/out of the playoff line.
    by_playoff = sorted(teams, key=lambda t: -t["playoff_pct"])
    bubble_pair = None
    if len(by_playoff) > playoff_teams:
        a, b = by_playoff[playoff_teams - 1], by_playoff[playoff_teams]
        bubble_pair = (a, b, abs(a["playoff_pct"] - b["playoff_pct"]))

    try:
        generated = datetime.fromisoformat(data["generated_at"]).strftime("%b %d, %Y")
    except Exception:
        generated = data.get("generated_at", "")

    max_playoff = max((t["playoff_pct"] for t in teams), default=1) or 1
    max_bye = max((t["bye_pct"] for t in teams), default=1) or 1
    max_champ = max((t["champ_pct"] for t in teams), default=1) or 1

    rows_sorted = sorted(teams, key=lambda t: (-t["avg_final_wins"], -t["avg_final_pts"]))

    body_rows = []
    for i, t in enumerate(rows_sorted, start=1):
        tier_cls, tier_label = tier_for(t["playoff_pct"])
        is_fav = t is favorite
        name_cell = (
            f'<div class="team-name-cell"><span class="dot {tier_cls}"></span>'
            f'<span>{esc(t["team"])}</span>'
            + (f'<span class="fav-badge">Favorite</span>' if is_fav else "")
            + f'</div><div class="record-sub">{esc(t["record"])} actual &bull; '
            f'{t["avg_final_wins"]:.1f} avg wins</div>'
        )
        body_rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{name_cell}</td>"
            f'<td{heat_bg(t["playoff_pct"], max_playoff, "green")}>{t["playoff_pct"]:.1f}%</td>'
            f'<td{heat_bg(t["bye_pct"], max_bye, "green")}>{t["bye_pct"]:.1f}%</td>'
            f'<td{heat_bg(t["champ_pct"], max_champ, "amber")}>{t["champ_pct"]:.1f}%</td>'
            "</tr>"
        )
        if i == playoff_teams:
            body_rows.append(
                '<tr class="playoff-line"><td colspan="5"><div class="line-inner">'
                '<div class="rule"></div><div class="label">Playoff line</div><div class="rule"></div>'
                "</div></td></tr>"
            )

    bubble_html = ""
    if bubble_pair:
        a, b, gap = bubble_pair
        bubble_html = f"{esc(a['team'])} vs {esc(b['team'])}"
        bubble_sub = f"{a['playoff_pct']:.1f}% vs {b['playoff_pct']:.1f}% for the last spot"
    else:
        bubble_sub = ""

    parts = [
        "<!doctype html><html lang=\"en\"><head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>{esc(league_name)} &mdash; Season Projections</title>",
        f"<style>{CSS}</style>",
        "</head><body>",
        "<header>",
        f'<p class="header-eyebrow">{esc(data["season"])} Season &bull; Week {current_week} of {regular_season_weeks}</p>',
        f'<h1>&#127944; {esc(league_name)} <span>&mdash; Playoff Odds</span></h1>',
        '<p class="subtitle">Every team\'s championship odds, simulated '
        f'{n_sims:,} times from real weekly player projections and this league\'s exact scoring rules.</p>',
        "</header>",
        '<div class="summary-strip">',
        '<div class="summary-item"><span class="s-label">Favorite</span>',
        f'<span class="s-value small">{esc(favorite["team"])}</span>',
        f'<span class="s-sub">{favorite["champ_pct"]:.1f}% to win it all</span></div>',
        '<div class="summary-item"><span class="s-label">Tightest Race</span>',
        f'<span class="s-value small">{bubble_html or "&mdash;"}</span>',
        f'<span class="s-sub">{esc(bubble_sub)}</span></div>',
        '<div class="summary-item"><span class="s-label">Regular Season</span>',
        f'<span class="s-value">Wk {current_week} / {regular_season_weeks}</span></div>',
        '<div class="summary-item"><span class="s-label">Last Updated</span>',
        f'<span class="s-value small">{esc(generated)}</span></div>',
        "</div>",
        '<div class="legend-bar">',
        '<span><span class="dot green"></span>Strong position (&ge;65% playoff chance)</span>',
        '<span><span class="dot amber"></span>Bubble team (35&ndash;64%)</span>',
        '<span><span class="dot red"></span>Longshot (&lt;35%)</span>',
        '<span><span class="fav-badge">Favorite</span><span>Best championship odds</span></span>',
        "</div>",
        '<div class="tourn-wrap">',
        '<div class="section-label">Projected Standings &amp; Playoff Odds</div>',
        '<div class="tourn-scroll">',
        '<table id="tourn-table" class="tourn-table"><thead><tr>',
        '<th onclick="sortTourn(0)" data-col="0">#</th>',
        '<th onclick="sortTourn(1)" data-col="1">Team</th>',
        '<th onclick="sortTourn(2)" data-col="2">Make Playoffs</th>',
        '<th onclick="sortTourn(3)" data-col="3">1st-Round Bye</th>',
        '<th onclick="sortTourn(4)" data-col="4" class="sort-active">Win It All &#8595;</th>',
        "</tr></thead><tbody>",
        "".join(body_rows),
        "</tbody></table>",
        "</div>",
        '<p class="tourn-note">Probabilities from a '
        f'{n_sims:,}-run Monte Carlo simulation. Updated manually &mdash; re-run the model for fresh odds '
        "as real games are played.</p>",
        "</div>",
        "<footer><p>Real per-player projections, this league&rsquo;s exact scoring rules, "
        "and roster-specific volatility from 3 seasons of history "
        "&bull; Updated manually each week &bull; Built with Claude</p></footer>",
        "<script>",
        "var _tDir=-1,_tCol=4;",
        "function sortTourn(col){",
        'var tbl=document.getElementById("tourn-table");',
        'var tbody=tbl.querySelector("tbody");',
        'var rows=Array.from(tbody.querySelectorAll("tr:not(.playoff-line)"));',
        "if(col===_tCol)_tDir*=-1;else{_tDir=-1;_tCol=col;}",
        "rows.sort(function(a,b){",
        'var av=a.cells[col].innerText.replace("%","");',
        'var bv=b.cells[col].innerText.replace("%","");',
        "var an=parseFloat(av),bn=parseFloat(bv);",
        "if(isNaN(an)||isNaN(bn))return _tDir*av.localeCompare(bv);",
        "return _tDir*(bn-an);",
        "});",
        'tbody.querySelectorAll("tr").forEach(function(r){tbody.removeChild(r);});',
        "rows.forEach(function(r,i){r.cells[0].innerText=i+1;tbody.appendChild(r);});",
        'tbl.querySelectorAll("th").forEach(function(th,i){',
        'th.className=i===_tCol?"sort-active":"";',
        'th.innerHTML=th.innerHTML.replace(/[ \\u2191\\u2193]/g,"")+(i===_tCol?(_tDir===-1?" \\u2193":" \\u2191"):"");',
        "});",
        "}",
        "</script>",
        "</body></html>",
    ]
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", default=DEFAULT_LEAGUE_ID)
    args = ap.parse_args()

    json_path = DATA_DIR / f"season_sim_{args.league_id}.json"
    if not json_path.exists():
        raise SystemExit(f"No data at {json_path} -- run main.py first.")
    with open(json_path) as f:
        data = json.load(f)

    html = build_html(data)
    out_path = OUT_DIR / "index.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
