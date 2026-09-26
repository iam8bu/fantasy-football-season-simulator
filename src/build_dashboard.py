#!/usr/bin/env python3
"""Build the season standings dashboard and write index.html.

Reads output/season_sim_<league_id>.json (written by main.py) -- run main.py
first (or after) to refresh the underlying numbers, then run this to regenerate
the page. Manually triggered, like the World Cup dashboard's build script: no
auto-refresh, just re-run both whenever you want updated odds.

    python3 main.py && python3 build_dashboard.py

Each run also archives a dated snapshot into snapshots/ (one file per calendar
date, committed to git -- unlike output/, which is gitignored -- so history
survives across machines/clones). Every snapshot gets embedded directly into
index.html (it's a static file, so the date dropdown switches between data
already baked in, not something fetched at view time), and the standings
table is rendered client-side in JS from whichever snapshot is selected.
"""
import argparse
import json
from datetime import datetime
from html import escape as esc
from pathlib import Path

DEFAULT_LEAGUE_ID = "1392633709420646400"
OUT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = OUT_DIR / "output"
SNAPSHOT_DIR = OUT_DIR / "snapshots"

# ---------------------------------------------------------------------------
# CSS -- dark terminal surface, Inter, heatmap table cells (same component
# vocabulary as the World Cup dashboard, green-tinted rather than blue-tinted).
# ---------------------------------------------------------------------------
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

:root {
  --bg:        #0c1110;
  --surface:   #161d19;
  --surface2:  #1c2420;
  --border:    #2c352e;
  --text:      #e6edf3;
  --muted:     #7d8590;
  --gold:      #b87a1a;
  --green:     #26a06a;
  --amber:     #d29922;
  --red:       #f85149;
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
  background: linear-gradient(180deg, #10241a 0%, #0c1110 100%);
  border-bottom: 1px solid var(--border);
  padding: 36px 32px 28px;
  text-align: center;
}
header h1 {
  font-size: clamp(1.6rem, 3.5vw, 2.4rem);
  font-weight: 900; letter-spacing: -.6px; line-height: 1.1;
  margin-bottom: 12px;
}
header h1 span { color: var(--green); }
.subtitle {
  color: var(--muted); font-size: 13px; line-height: 1.6;
  max-width: 560px; margin: 0 auto;
}
.updated {
  color: var(--muted); font-size: 11px; margin-top: 10px;
}

/* -- Snapshot picker -- */
.snapshot-row {
  max-width: 900px; margin: 20px auto 0; padding: 0 14px;
  display: flex; align-items: center; justify-content: center; gap: 8px;
}
.snapshot-row label {
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted);
}
#snapshot-select {
  background: var(--surface); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 10px; font: inherit; font-size: 13px; cursor: pointer;
}
#snapshot-select:hover { border-color: var(--green); }

/* -- Standings table -- */
.tourn-wrap { max-width: 900px; margin: 0 auto; padding: 18px 14px 8px; }
.detail-hint { max-width: 900px; margin: 0 auto; padding: 0 14px 32px; text-align: center; color: var(--muted); font-size: 12px; }
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
.record-inline { color: var(--muted); font-weight: 400; font-size: 12px; }
.tourn-table tbody tr.has-detail { cursor: pointer; }

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

/* -- Roster detail modal -- */
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(0,0,0,.6);
  display: flex; align-items: flex-start; justify-content: center;
  padding: 6vh 14px; overflow-y: auto; z-index: 100;
}
.modal-box {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  max-width: 820px; width: 100%; padding: 20px;
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 4px;
}
.modal-head h2 { font-size: 18px; font-weight: 800; }
.modal-sub { color: var(--muted); font-size: 12px; margin-bottom: 14px; }
.modal-close {
  background: var(--surface2); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; width: 28px; height: 28px; font-size: 15px; cursor: pointer; line-height: 1;
  flex-shrink: 0;
}
.modal-close:hover { border-color: var(--red); color: var(--red); }
.roster-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }
.roster-table { width: 100%; border-collapse: collapse; font-size: 12.5px; min-width: 520px; }
.roster-table th {
  padding: 8px 9px; text-align: right; font-weight: 500; white-space: nowrap;
  border-bottom: 2px solid var(--border); background: var(--surface2); color: var(--muted);
}
.roster-table th:nth-child(1) { text-align: left; position: sticky; left: 0; background: var(--surface2); }
.roster-table td {
  padding: 7px 9px; text-align: right; border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums; color: var(--muted);
}
.roster-table td:nth-child(1) {
  text-align: left; white-space: nowrap; color: var(--text); font-weight: 500;
  position: sticky; left: 0; background: var(--surface);
}
.roster-table tr:last-child td { border-bottom: none; }
.roster-table td.started { color: var(--text); font-weight: 600; }
.roster-table td.bye { color: var(--border); }
.roster-table td.ros-total { color: var(--green); font-weight: 700; }
.roster-pos { color: var(--muted); font-weight: 400; font-size: 11px; }

/* -- Responsive -- */
@media (max-width: 640px) {
  header { padding: 28px 18px 22px; }
  .modal-box { padding: 14px; }
}
"""


def snapshot_label(current_week: int) -> str:
    return "Preseason" if current_week <= 1 else f"Week {current_week}"


def archive_snapshot(data: dict, reuse_latest: bool = False) -> str:
    """Writes/overwrites today's snapshot file. Returns the date key written.

    If reuse_latest, overwrites the most recently-dated existing snapshot in
    place (keeping its original date label) instead of creating a new dated
    entry -- for a refresh where a new dropdown entry isn't warranted (e.g.
    no new games played since the last archive).
    """
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    key = None
    if reuse_latest:
        existing = sorted(p.stem for p in SNAPSHOT_DIR.glob("*.json"))
        if existing:
            key = existing[-1]
    if key is None:
        key = datetime.now().strftime("%Y-%m-%d")
    snap = {
        "date": key,
        "label": snapshot_label(data["current_week"]),
        "teams": sorted(data["teams"], key=lambda t: (-t["avg_final_wins"], -t["avg_final_pts"])),
    }
    with open(SNAPSHOT_DIR / f"{key}.json", "w") as f:
        json.dump(snap, f, indent=2)
    return key


def load_all_snapshots() -> dict:
    """date -> {date, label, teams}, for every snapshot ever taken."""
    snaps = {}
    if SNAPSHOT_DIR.exists():
        for p in sorted(SNAPSHOT_DIR.glob("*.json")):
            with open(p) as f:
                s = json.load(f)
            snaps[s["date"]] = s
    return snaps


# JS ports of the Python row-rendering/heatmap/sort-divider logic used before --
# now client-side so the dropdown can redraw the table for any archived date
# without a page reload (index.html has no backend to ask for other snapshots).
TABLE_JS = """
function heatBg(val,maxVal,rgb){
  if(!val||maxVal<=0)return '';
  var intensity=Math.round((val/maxVal)*0.30*1000)/1000;
  return "background:rgba("+rgb+","+intensity+")";
}

function renderSnapshot(key){
  var teams=SNAPSHOTS[key].teams;
  var tbody=document.getElementById('standings-body');
  while(tbody.firstChild)tbody.removeChild(tbody.firstChild);
  var maxPlayoff=Math.max.apply(null,teams.map(function(t){return t.playoff_pct;}))||1;
  var maxBye=Math.max.apply(null,teams.map(function(t){return t.bye_pct;}))||1;
  var maxChamp=Math.max.apply(null,teams.map(function(t){return t.champ_pct;}))||1;
  var maxLast=Math.max.apply(null,teams.map(function(t){return t.last_pct;}))||1;

  function cell(text,style){
    var td=document.createElement('td');
    if(style)td.setAttribute('style',style);
    td.textContent=text;
    return td;
  }

  teams.forEach(function(t,i){
    var tr=document.createElement('tr');
    var nameTd=document.createElement('td');
    nameTd.appendChild(document.createTextNode(t.team+' '));
    var rec=document.createElement('span');
    rec.className='record-inline';
    rec.textContent='('+t.record+')';
    nameTd.appendChild(rec);
    tr.appendChild(cell(String(i+1)));
    tr.appendChild(nameTd);
    tr.appendChild(cell(t.avg_final_wins.toFixed(1)));
    tr.appendChild(cell(typeof t.avg_final_pts==='number'?t.avg_final_pts.toFixed(1):'-'));
    tr.appendChild(cell(t.playoff_pct.toFixed(1)+'%',heatBg(t.playoff_pct,maxPlayoff,'38,160,106')));
    tr.appendChild(cell(t.bye_pct.toFixed(1)+'%',heatBg(t.bye_pct,maxBye,'38,160,106')));
    tr.appendChild(cell(t.champ_pct.toFixed(1)+'%',heatBg(t.champ_pct,maxChamp,'184,122,26')));
    tr.appendChild(cell(t.last_pct.toFixed(1)+'%',heatBg(t.last_pct,maxLast,'248,81,73')));
    if(t.roster_id!=null && ROSTER_DETAIL.teams[String(t.roster_id)]){
      tr.className='has-detail';
      tr.addEventListener('click',function(){openRosterModal(String(t.roster_id));});
    }
    tbody.appendChild(tr);
  });

  var divider=document.createElement('tr');
  divider.className='playoff-line';
  var td=document.createElement('td');
  td.colSpan=8;
  var inner=document.createElement('div');
  inner.className='line-inner';
  var rule1=document.createElement('div'); rule1.className='rule';
  var label=document.createElement('div'); label.className='label'; label.textContent='Playoff line';
  var rule2=document.createElement('div'); rule2.className='rule';
  inner.appendChild(rule1); inner.appendChild(label); inner.appendChild(rule2);
  td.appendChild(inner);
  divider.appendChild(td);
  tbody.children[PLAYOFF_TEAMS-1].insertAdjacentElement('afterend',divider);

  _tDir=-1; _tCol=2;
  document.querySelectorAll('#tourn-table th').forEach(function(th,i){
    th.className=i===2?'sort-active':'';
    th.innerHTML=th.innerHTML.replace(/[ \\u2191\\u2193]/g,'')+(i===2?' \\u2193':'');
  });
}

function sortTourn(col){
  var tbl=document.getElementById("tourn-table");
  var tbody=tbl.querySelector("tbody");
  var rows=Array.from(tbody.querySelectorAll("tr:not(.playoff-line)"));
  var divider=tbody.querySelector(".playoff-line");
  if(col===_tCol)_tDir*=-1;else{_tDir=-1;_tCol=col;}
  rows.sort(function(a,b){
    var av=a.cells[col].innerText.replace("%","");
    var bv=b.cells[col].innerText.replace("%","");
    var an=parseFloat(av),bn=parseFloat(bv);
    if(isNaN(an)||isNaN(bn))return _tDir*av.localeCompare(bv);
    return _tDir*(bn-an);
  });
  tbody.querySelectorAll("tr").forEach(function(r){tbody.removeChild(r);});
  rows.forEach(function(r,i){r.cells[0].innerText=i+1;tbody.appendChild(r);});
  // Position-based, not team-based: the 6 best teams by whatever's currently sorted
  // are always "in", so the line sits after row 6 when they're on top (descending
  // for every column except Last Place, where lower is better, so it's ascending
  // there instead) or after row (N-6) when they're at the bottom (sort reversed).
  var nTeams=rows.length;
  var bestFirst=(col===LAST_PLACE_COL)?(_tDir===-1):(_tDir===1);
  var cutoffIdx=bestFirst?PLAYOFF_TEAMS:(nTeams-PLAYOFF_TEAMS);
  if(divider&&cutoffIdx>0&&cutoffIdx<=tbody.children.length){
    tbody.children[cutoffIdx-1].insertAdjacentElement("afterend",divider);
  }
  tbl.querySelectorAll("th").forEach(function(th,i){
    th.className=i===_tCol?"sort-active":"";
    th.innerHTML=th.innerHTML.replace(/[ \\u2191\\u2193]/g,"")+(i===_tCol?(_tDir===-1?" \\u2193":" \\u2191"):"");
  });
}

function fmtCell(val,started){
  if(val==null)return '<td class="bye">bye</td>';
  return '<td'+(started?' class="started"':'')+'>'+val.toFixed(1)+'</td>';
}

function openRosterModal(rid){
  var detail=ROSTER_DETAIL.teams[rid];
  if(!detail)return;
  var weeks=ROSTER_DETAIL.weeks;
  var html='<div class="modal-backdrop" id="roster-modal" onclick="if(event.target===this)closeRosterModal()">';
  html+='<div class="modal-box"><div class="modal-head"><h2>'+detail.team+'</h2>';
  html+='<button class="modal-close" onclick="closeRosterModal()">&times;</button></div>';
  html+='<p class="modal-sub">Rest-of-season projections, weeks '+weeks[0]+'-'+weeks[weeks.length-1]+
        ' &mdash; bold = started that week\\'s optimal lineup.</p>';
  html+='<div class="roster-scroll"><table class="roster-table"><thead><tr><th>Player</th>';
  weeks.forEach(function(w){html+='<th>Wk'+w+'</th>';});
  html+='<th>Projected Points</th></tr></thead><tbody>';
  detail.players.forEach(function(p){
    html+='<tr><td>'+p.name+' <span class="roster-pos">'+p.pos+(p.nfl_team?(' '+p.nfl_team):'')+'</span></td>';
    weeks.forEach(function(w){
      var v=p.weekly[String(w)];
      html+=fmtCell(v,p.started_weeks.indexOf(w)!==-1);
    });
    html+='<td class="ros-total">'+p.ros_total.toFixed(1)+'</td></tr>';
  });
  html+='</tbody></table></div></div></div>';
  var host=document.createElement('div');
  host.innerHTML=html;
  document.body.appendChild(host.firstChild);
  document.addEventListener('keydown',escCloseRosterModal);
}

function closeRosterModal(){
  var m=document.getElementById('roster-modal');
  if(m)m.parentNode.removeChild(m);
  document.removeEventListener('keydown',escCloseRosterModal);
}

function escCloseRosterModal(e){
  if(e.key==='Escape')closeRosterModal();
}
"""


def build_html(data: dict, snapshots: dict, latest_key: str, roster_detail: dict) -> str:
    league_name = data["league_name"]
    n_sims = data["n_sims"]

    try:
        generated = datetime.fromisoformat(data["generated_at"]).strftime("%b %d, %Y")
    except Exception:
        generated = data.get("generated_at", "")

    options = []
    for key in sorted(snapshots.keys()):
        snap = snapshots[key]
        selected = " selected" if key == latest_key else ""
        options.append(
            f'<option value="{esc(key)}"{selected}>{esc(snap["label"])}</option>'
        )

    # Guard against a team name containing "</script>" and breaking out of the
    # embedded JSON block -- extremely unlikely for real names, but free to prevent.
    snapshots_json = json.dumps(snapshots).replace("</", "<\\/")
    roster_detail_json = json.dumps(roster_detail).replace("</", "<\\/")

    parts = [
        "<!doctype html><html lang=\"en\"><head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>{esc(league_name)} &mdash; Season Projections</title>",
        f"<style>{CSS}</style>",
        "</head><body>",
        "<header>",
        f'<h1>{esc(league_name)} <span>&mdash; Playoff Odds</span></h1>',
        f'<p class="subtitle">Based on {n_sims:,} simulations of the season.</p>',
        f'<p class="updated">Last updated {esc(generated)}</p>',
        "</header>",
        '<div class="snapshot-row">',
        '<label for="snapshot-select">Viewing</label>',
        f'<select id="snapshot-select" onchange="renderSnapshot(this.value)">{"".join(options)}</select>',
        "</div>",
        '<div class="tourn-wrap">',
        '<div class="tourn-scroll">',
        '<table id="tourn-table" class="tourn-table"><thead><tr>',
        '<th onclick="sortTourn(0)" data-col="0">#</th>',
        '<th onclick="sortTourn(1)" data-col="1">Team</th>',
        '<th onclick="sortTourn(2)" data-col="2" class="sort-active">Proj. Wins &#8595;</th>',
        '<th onclick="sortTourn(3)" data-col="3">Projected Points</th>',
        '<th onclick="sortTourn(4)" data-col="4">Make Playoffs</th>',
        '<th onclick="sortTourn(5)" data-col="5">1st Round Bye</th>',
        '<th onclick="sortTourn(6)" data-col="6">Champion</th>',
        '<th onclick="sortTourn(7)" data-col="7">Last Place</th>',
        '</tr></thead><tbody id="standings-body"></tbody></table>',
        "</div>",
        "</div>",
        '<p class="detail-hint">Click a team to see its rest-of-season player projections.</p>',
        "<script>",
        f"var SNAPSHOTS={snapshots_json};",
        f"var ROSTER_DETAIL={roster_detail_json};",
        f'var PLAYOFF_TEAMS={data["playoff_teams"]},LAST_PLACE_COL=7;',
        "var _tDir=-1,_tCol=2;",
        TABLE_JS,
        f"renderSnapshot('{latest_key}');",
        "</script>",
        "</body></html>",
    ]
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", default=DEFAULT_LEAGUE_ID)
    ap.add_argument("--no-new-archive", action="store_true",
                     help="Refresh the most recent snapshot in place instead of creating a new dated one.")
    args = ap.parse_args()

    json_path = DATA_DIR / f"season_sim_{args.league_id}.json"
    if not json_path.exists():
        raise SystemExit(f"No data at {json_path} -- run main.py first.")
    with open(json_path) as f:
        data = json.load(f)

    roster_path = DATA_DIR / f"roster_detail_{args.league_id}.json"
    roster_detail = {"weeks": [], "teams": {}}
    if roster_path.exists():
        with open(roster_path) as f:
            roster_detail = json.load(f)

    latest_key = archive_snapshot(data, reuse_latest=args.no_new_archive)
    snapshots = load_all_snapshots()

    html = build_html(data, snapshots, latest_key, roster_detail)
    out_path = OUT_DIR / "index.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Saved: {out_path}")
    print(f"Snapshot archived: snapshots/{latest_key}.json ({len(snapshots)} total)")


if __name__ == "__main__":
    main()
