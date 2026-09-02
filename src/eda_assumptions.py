"""EDA on the modeling assumptions baked into strength.py / historical.py / simulate.py,
checked against real historical data already available via sleeper_api.get_stats.

Covers:
A) Independence between teammates (the Var(sum) = sum(Var) assumption) -- correlation
   between a real NFL team's QB and its pass-catchers/RB, same weeks, same season.
B) Normality of team-level weekly scores -- this league's actual current rosters,
   replayed against 3 seasons of real historical results.
C) Per-player std reliability vs. sample size -- validates FULL_TRUST_GAMES=24.
D) Position "startable pool" cliff -- validates DEFAULT_POOL_SIZE cutoffs.

Run directly: python3 eda_assumptions.py
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


def gather_weekly_entries(season, weeks, scoring_settings, position_lookup):
    """[{pid, pos, team, week, points}, ...] for one season, skill positions only."""
    entries = []
    for week in weeks:
        raw = api.get_stats(season, week)
        for e in raw:
            pid = e.get("player_id")
            stats = e.get("stats")
            pos = position_lookup.get(pid)
            if not pid or not stats or pos not in ("QB", "RB", "WR", "TE"):
                continue
            entries.append({
                "pid": pid, "pos": pos, "team": e.get("team"), "week": week,
                "points": projections.score_stats(stats, scoring_settings),
            })
    return entries


# ---------------------------------------------------------------------------
# A) Teammate correlation -- tests the independence assumption behind
#    Var(sum of lineup slots) = sum of slot variances.
# ---------------------------------------------------------------------------
def teammate_correlations(scoring_settings, position_lookup):
    pairs = defaultdict(list)  # label -> [r, r, ...]

    for season in SEASONS:
        entries = gather_weekly_entries(season, WEEKS, scoring_settings, position_lookup)
        by_team = defaultdict(lambda: defaultdict(dict))
        pos_by_pid = {}
        for e in entries:
            if not e["team"]:
                continue
            by_team[e["team"]][e["pid"]][e["week"]] = e["points"]
            pos_by_pid[e["pid"]] = e["pos"]

        for team, players in by_team.items():
            qbs = [(pid, wp) for pid, wp in players.items() if pos_by_pid[pid] == "QB"]
            if not qbs:
                continue
            qb_pid, qb_weeks = max(qbs, key=lambda x: len(x[1]))
            if len(qb_weeks) < 8:
                continue

            catchers = sorted(
                ((pid, wp) for pid, wp in players.items() if pos_by_pid[pid] in ("WR", "TE") and pid != qb_pid),
                key=lambda x: -sum(x[1].values()),
            )
            rbs = sorted(
                ((pid, wp) for pid, wp in players.items() if pos_by_pid[pid] == "RB"),
                key=lambda x: -sum(x[1].values()),
            )
            wrs_only = [c for c in catchers if pos_by_pid[c[0]] == "WR"]
            tes_only = [c for c in catchers if pos_by_pid[c[0]] == "TE"]

            def corr(weeks_a, weeks_b):
                common = sorted(set(weeks_a) & set(weeks_b))
                if len(common) < 6:
                    return None
                a = [weeks_a[w] for w in common]
                b = [weeks_b[w] for w in common]
                if len(set(a)) == 1 or len(set(b)) == 1:
                    return None
                r, _ = sps.pearsonr(a, b)
                return r

            if wrs_only:
                r = corr(qb_weeks, wrs_only[0][1])
                if r is not None:
                    pairs["QB vs WR1 (own team)"].append(r)
            if len(wrs_only) >= 2:
                r = corr(wrs_only[0][1], wrs_only[1][1])
                if r is not None:
                    pairs["WR1 vs WR2 (own team)"].append(r)
            if tes_only:
                r = corr(qb_weeks, tes_only[0][1])
                if r is not None:
                    pairs["QB vs TE1 (own team)"].append(r)
            if rbs:
                r = corr(qb_weeks, rbs[0][1])
                if r is not None:
                    pairs["QB vs RB1 (own team)"].append(r)
            if len(rbs) >= 2:
                r = corr(rbs[0][1], rbs[1][1])
                if r is not None:
                    pairs["RB1 vs RB2 (own team, committee effect)"].append(r)

    print("=== A) Teammate correlation (independence assumption) ===")
    print(f"{'Pair':38} {'n':>4} {'mean r':>8} {'p (vs 0)':>10}")
    for label, rs in pairs.items():
        arr = np.array(rs)
        # Fisher z-transform for a proper mean/CI on correlation coefficients
        z = np.arctanh(np.clip(arr, -0.999, 0.999))
        mean_r = np.tanh(z.mean())
        t_stat, p = sps.ttest_1samp(z, 0)
        print(f"{label:38} {len(arr):>4} {mean_r:>8.3f} {p:>10.4f}")
    print()


# ---------------------------------------------------------------------------
# B) Normality of team-level weekly scores -- tests the Normal-sampling assumption.
#    Replays this league's CURRENT 14 rosters against 3 real historical seasons.
# ---------------------------------------------------------------------------
def team_score_normality(teams, players_db, position_lookup, scoring_settings, slot_req):
    print("=== B) Normality of team-level weekly scores ===")
    print("(This league's current rosters, replayed against 3 real past seasons)")
    print(f"{'Team':26} {'n_weeks':>8} {'skew':>7} {'exkurt':>7} {'Shapiro p':>10}")

    all_standardized = []
    fail_count, total = 0, 0
    per_team_scores = defaultdict(list)
    for season in SEASONS:
        week_actuals = {w: historical.week_player_actuals(season, w, scoring_settings) for w in WEEKS}
        for rid, team in teams.items():
            for w in WEEKS:
                total_pts = strength.team_week_projection(team.players, week_actuals[w], position_lookup, slot_req)
                per_team_scores[rid].append(total_pts)

    for rid, team in teams.items():
        scores = np.array(per_team_scores[rid])
        skew = sps.skew(scores)
        exkurt = sps.kurtosis(scores)  # excess kurtosis (0 = normal)
        shapiro_p = sps.shapiro(scores).pvalue
        total += 1
        if shapiro_p < 0.05:
            fail_count += 1
        print(f"{team.team_name[:26]:26} {len(scores):>8} {skew:>7.2f} {exkurt:>7.2f} {shapiro_p:>10.4f}")
        standardized = (scores - scores.mean()) / scores.std()
        all_standardized.extend(standardized.tolist())

    pooled = np.array(all_standardized)
    print(f"\nPooled across all teams (standardized): skew={sps.skew(pooled):.2f}, "
          f"excess kurtosis={sps.kurtosis(pooled):.2f}, n={len(pooled)}")
    print(f"Teams failing Shapiro-Wilk normality test (p<0.05): {fail_count}/{total}")
    print()


# ---------------------------------------------------------------------------
# C) Per-player std reliability vs. sample size -- validates FULL_TRUST_GAMES=24.
# ---------------------------------------------------------------------------
def std_reliability(scoring_settings, position_lookup):
    print("=== C) Per-player std split-half reliability (validates FULL_TRUST_GAMES) ===")
    series = {}
    for season in SEASONS:
        s = historical.build_season_series(season, WEEKS, scoring_settings)
        for pid, pts in s.items():
            series.setdefault(pid, []).extend(pts)

    buckets = {"10-19 games": [], "20-29 games": [], "30+ games": []}
    for pid, pts in series.items():
        if position_lookup.get(pid) not in ("QB", "RB", "WR", "TE"):
            continue
        n = len(pts)
        if n < 10:
            continue
        half_a = pts[0::2]
        half_b = pts[1::2]
        if len(half_a) < 4 or len(half_b) < 4:
            continue
        std_a, std_b = np.std(half_a, ddof=1), np.std(half_b, ddof=1)
        bucket = "10-19 games" if n < 20 else ("20-29 games" if n < 30 else "30+ games")
        buckets[bucket].append((std_a, std_b))

    print(f"{'Sample size bucket':20} {'n players':>10} {'corr(half A, half B)':>22}")
    for label, pairs in buckets.items():
        if len(pairs) < 5:
            print(f"{label:20} {len(pairs):>10} {'(too few)':>22}")
            continue
        a = [p[0] for p in pairs]
        b = [p[1] for p in pairs]
        r, _ = sps.pearsonr(a, b)
        print(f"{label:20} {len(pairs):>10} {r:>22.3f}")
    print()


# ---------------------------------------------------------------------------
# D) Position pool cliff -- validates DEFAULT_POOL_SIZE cutoffs.
# ---------------------------------------------------------------------------
def position_pool_cliff(scoring_settings, position_lookup):
    print("=== D) Position value cliff (validates DEFAULT_POOL_SIZE) ===")
    ranks_to_show = [1, 5, 10, 15, 20, 24, 30, 40, 50, 60, 70, 72, 80]

    combined = defaultdict(list)  # pos -> [season_avg, ...] across all 3 seasons, one row per player-season
    for season in SEASONS:
        series = historical.build_season_series(season, WEEKS, scoring_settings)
        by_pos = defaultdict(list)
        for pid, pts in series.items():
            pos = position_lookup.get(pid)
            if pos not in ("QB", "RB", "WR", "TE", "K", "DEF") or len(pts) < 3:
                continue
            by_pos[pos].append(sum(pts) / len(pts))
        for pos, avgs in by_pos.items():
            combined[pos].extend(avgs)

    configured_cutoff = historical.DEFAULT_POOL_SIZE
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        avgs = sorted(combined[pos], reverse=True)
        n_seasons = len(SEASONS)
        print(f"\n{pos} (avg weekly pts by rank, pooled/{n_seasons} seasons -> divide rank by {n_seasons} for per-season rank):")
        row = []
        for r in ranks_to_show:
            idx = r * n_seasons - 1  # rank r per season ~ rank r*n_seasons in the pooled/combined list
            if idx < len(avgs):
                row.append(f"#{r}={avgs[idx]:.1f}")
        print("  " + "  ".join(row))
        print(f"  --> configured pool size: {configured_cutoff.get(pos)}")
    print()


def main():
    league, teams, players_db, position_lookup, scoring_settings = load_context()
    slot_req = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}

    teammate_correlations(scoring_settings, position_lookup)
    team_score_normality(teams, players_db, position_lookup, scoring_settings, slot_req)
    std_reliability(scoring_settings, position_lookup)
    position_pool_cliff(scoring_settings, position_lookup)


if __name__ == "__main__":
    main()
