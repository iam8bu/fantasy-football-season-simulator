"""Second pass of EDA: assumptions not covered by eda_assumptions.py.

E) Are Rotowire's projections (the entire basis for `week_mean`) actually well
   calibrated? Sleeper archives projections as they existed pre-game, so we can
   compare projected vs. actual for the same player/week, historically.
F) Are the MEAN-side calibration constants (RATIO_FULL_WEIGHT_WEEK=6,
   RATIO_CLAMP=(0.75,1.30)) reasonable? Same backtest technique as the std-side
   FULL_TRUST_GAMES check, applied to the ratio instead.
G) Scoring engine sanity: any of this league's nonzero scoring rules that never
   actually fire (dead rule), and how much the custom scoring differs from
   Sleeper's generic half-PPR number.
H) Is applying the full QB-WR1 correlation to ANY same-team pass-catcher pick
   (not just the specific "WR1") overstating it for a second same-team pick?
I) Is the 3-season pooling window hiding meaningful year-to-year drift in
   position volatility?
"""
import json
from collections import defaultdict

import numpy as np
from scipy import stats as sps

import sleeper_api as api
import projections
import league as league_mod
import strength
import historical

LEAGUE_ID = "1392633709420646400"
SEASONS = ["2025", "2024", "2023"]
WEEKS = list(range(1, 18))


def load_context():
    league, teams = league_mod.load_league(LEAGUE_ID, fresh=False)
    players_db = json.load(open("../data/players.json"))
    position_lookup = strength.build_position_lookup(players_db)
    return league, teams, players_db, position_lookup, league["scoring_settings"]


# ---------------------------------------------------------------------------
# E) Rotowire projection calibration: projected vs. actual, same player/week.
# ---------------------------------------------------------------------------
def projection_calibration(scoring_settings, position_lookup):
    print("=== E) Rotowire projection calibration (projected vs. actual) ===")
    by_pos = defaultdict(lambda: {"proj": [], "actual": []})

    for season in SEASONS:
        for week in WEEKS:
            proj_raw = api.get_projections(season, week)
            actual_map = historical.week_player_actuals(season, week, scoring_settings)
            for e in proj_raw:
                pid = e.get("player_id")
                stats = e.get("stats")
                pos = position_lookup.get(pid)
                if not pid or not stats or pos not in ("QB", "RB", "WR", "TE", "K", "DEF"):
                    continue
                proj_pts = projections.score_stats(stats, scoring_settings)
                actual_pts = actual_map.get(pid)
                if actual_pts is None:
                    continue  # bye/inactive that week -- not a projection miss, just no game
                if proj_pts < 2:
                    continue  # skip scrubs barely worth projecting -- noise, not signal
                by_pos[pos]["proj"].append(proj_pts)
                by_pos[pos]["actual"].append(actual_pts)

    print(f"{'Pos':5} {'n':>6} {'mean proj':>10} {'mean actual':>12} {'bias':>8} {'MAE':>7} {'corr(r)':>8}")
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        proj = np.array(by_pos[pos]["proj"])
        actual = np.array(by_pos[pos]["actual"])
        if len(proj) < 30:
            continue
        bias = (actual - proj).mean()
        mae = np.abs(actual - proj).mean()
        r, _ = sps.pearsonr(proj, actual)
        print(f"{pos:5} {len(proj):>6} {proj.mean():>10.2f} {actual.mean():>12.2f} {bias:>8.2f} {mae:>7.2f} {r:>8.3f}")
    print()


# ---------------------------------------------------------------------------
# F) Mean-side ratio calibration constants: does 6 weeks / [0.75,1.30] make sense?
# ---------------------------------------------------------------------------
def ratio_calibration_backtest(teams, players_db, position_lookup, scoring_settings, slot_req):
    print("=== F) Mean-side ratio calibration backtest (this league's current rosters) ===")
    print("(Cumulative actual-vs-projected ratio, week by week, replayed against real history)")

    season = SEASONS[0]  # most recent complete season
    week_actuals = {w: historical.week_player_actuals(season, w, scoring_settings) for w in WEEKS}
    week_projs = {}
    for w in WEEKS:
        raw = api.get_projections(season, w)
        week_projs[w] = {e["player_id"]: projections.score_stats(e["stats"], scoring_settings)
                          for e in raw if e.get("player_id") and e.get("stats")}

    all_final_ratios = []
    out_of_clamp_weeks = 0
    total_weeks_checked = 0
    for rid, team in teams.items():
        cum_actual, cum_proj = 0.0, 0.0
        ratios_over_time = []
        for w in WEEKS:
            actual = strength.team_week_projection(team.players, week_actuals[w], position_lookup, slot_req)
            proj = strength.team_week_projection(team.players, week_projs[w], position_lookup, slot_req)
            cum_actual += actual
            cum_proj += proj
            ratio = cum_actual / cum_proj if cum_proj > 0 else 1.0
            ratios_over_time.append(ratio)
            total_weeks_checked += 1
            if not (0.75 <= ratio <= 1.30):
                out_of_clamp_weeks += 1
        all_final_ratios.append(ratios_over_time[-1])
        # how much does the ratio move AFTER week 6 vs before -- validates the "full trust by 6" cutoff
        early, late = ratios_over_time[:6], ratios_over_time[6:]
        drift = max(late) - min(late) if late else 0
        print(f"{team.team_name[:26]:26} wk3={ratios_over_time[2]:.2f}  wk6={ratios_over_time[5]:.2f}  "
              f"wk13={ratios_over_time[12]:.2f}  post-wk6 drift={drift:.2f}")

    print(f"\nWeeks where cumulative ratio fell outside the [0.75, 1.30] clamp: "
          f"{out_of_clamp_weeks}/{total_weeks_checked}")
    print(f"Final-week (13) ratio range across teams: {min(all_final_ratios):.2f} - {max(all_final_ratios):.2f}")
    print()


# ---------------------------------------------------------------------------
# G) Scoring engine sanity: dead rules + magnitude vs. Sleeper's generic number.
# ---------------------------------------------------------------------------
def scoring_engine_sanity(scoring_settings, position_lookup):
    print("=== G) Scoring engine sanity checks ===")
    nonzero_rules = {k: v for k, v in scoring_settings.items() if v != 0}
    triggered = set()
    generic_diffs = []

    for week in WEEKS[:6]:  # a handful of weeks is enough to see which rules ever fire
        raw = api.get_stats(SEASONS[0], week)
        for e in raw:
            stats = e.get("stats") or {}
            for k in stats:
                if k in nonzero_rules:
                    triggered.add(k)
            pid = e.get("player_id")
            pos = position_lookup.get(pid)
            if pos in ("QB", "RB", "WR", "TE") and "pts_half_ppr" in stats:
                custom = projections.score_stats(stats, scoring_settings)
                generic_diffs.append(custom - stats["pts_half_ppr"])

    dead_rules = {k: v for k, v in nonzero_rules.items() if k not in triggered}
    print(f"Nonzero scoring rules: {len(nonzero_rules)}, of which never observed firing "
          f"in {len(WEEKS[:6])} sample weeks: {len(dead_rules)}")
    if dead_rules:
        print("  Never-triggered rules (may be fine if legitimately rare, e.g. 2pt/blocked kicks):")
        for k, v in dead_rules.items():
            print(f"    {k}: {v}")

    diffs = np.array(generic_diffs)
    print(f"\nCustom league-scoring vs. Sleeper's generic half-PPR number "
          f"(n={len(diffs)}): mean diff={diffs.mean():.2f}, std={diffs.std():.2f}, "
          f"max abs diff={np.abs(diffs).max():.2f}")
    print()


# ---------------------------------------------------------------------------
# H) Is the QB-WR1 correlation overstated when applied to a SECOND same-team pick?
# ---------------------------------------------------------------------------
def qb_wr2_correlation(scoring_settings, position_lookup):
    print("=== H) QB vs. WR2 (second same-team pass-catcher) correlation ===")
    rs = []
    for season in SEASONS:
        entries = []
        for week in WEEKS:
            raw = api.get_stats(season, week)
            for e in raw:
                pid = e.get("player_id")
                stats = e.get("stats")
                pos = position_lookup.get(pid)
                if not pid or not stats or pos not in ("QB", "WR"):
                    continue
                entries.append((pid, pos, e.get("team"), week, projections.score_stats(stats, scoring_settings)))

        by_team = defaultdict(lambda: defaultdict(dict))
        pos_by_pid = {}
        for pid, pos, team, week, pts in entries:
            if not team:
                continue
            by_team[team][pid][week] = pts
            pos_by_pid[pid] = pos

        for team, players in by_team.items():
            qbs = [(pid, wp) for pid, wp in players.items() if pos_by_pid[pid] == "QB"]
            if not qbs:
                continue
            qb_pid, qb_weeks = max(qbs, key=lambda x: len(x[1]))
            if len(qb_weeks) < 8:
                continue
            wrs = sorted(
                ((pid, wp) for pid, wp in players.items() if pos_by_pid[pid] == "WR"),
                key=lambda x: -sum(x[1].values()),
            )
            if len(wrs) < 2:
                continue
            wr2_pid, wr2_weeks = wrs[1]
            common = sorted(set(qb_weeks) & set(wr2_weeks))
            if len(common) < 6:
                continue
            a = [qb_weeks[w] for w in common]
            b = [wr2_weeks[w] for w in common]
            if len(set(a)) == 1 or len(set(b)) == 1:
                continue
            r, _ = sps.pearsonr(a, b)
            rs.append(r)

    arr = np.array(rs)
    z = np.arctanh(np.clip(arr, -0.999, 0.999))
    mean_r = np.tanh(z.mean())
    t, p = sps.ttest_1samp(z, 0)
    print(f"QB vs WR2 (own team): n={len(arr)}, mean r={mean_r:.3f}, p={p:.4f}")
    print(f"(compare to QB vs WR1's measured r=0.174 -- currently BOTH use the same constant)")
    print()


# ---------------------------------------------------------------------------
# I) Multi-season stability of position volatility -- is pooling 3 seasons hiding drift?
# ---------------------------------------------------------------------------
def multi_season_stability(scoring_settings, position_lookup):
    print("=== I) Per-season position std stability (is 3-season pooling hiding drift?) ===")
    print(f"{'Pos':5}", end="")
    for season in SEASONS:
        print(f"{season:>10}", end="")
    print()
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        print(f"{pos:5}", end="")
        for season in SEASONS:
            series = historical.build_season_series(season, WEEKS, scoring_settings)
            vals = []
            for pid, pts in series.items():
                if position_lookup.get(pid) != pos or len(pts) < 3:
                    continue
                avg = sum(pts) / len(pts)
                vals.append((avg, pts))
            vals.sort(key=lambda x: -x[0])
            pool_n = historical.DEFAULT_POOL_SIZE.get(pos, 24)
            pool = vals[:pool_n]
            stds = [np.std(pts, ddof=1) for _, pts in pool]
            print(f"{np.mean(stds):>10.2f}", end="")
        print()
    print()


def main():
    league, teams, players_db, position_lookup, scoring_settings = load_context()
    slot_req = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}

    projection_calibration(scoring_settings, position_lookup)
    ratio_calibration_backtest(teams, players_db, position_lookup, scoring_settings, slot_req)
    scoring_engine_sanity(scoring_settings, position_lookup)
    qb_wr2_correlation(scoring_settings, position_lookup)
    multi_season_stability(scoring_settings, position_lookup)


if __name__ == "__main__":
    main()
