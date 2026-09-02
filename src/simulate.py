"""Monte Carlo season simulator: remaining regular season -> standings -> playoff bracket.

Each team's score in a given week is sampled from Normal(week_mean, week_std), where
both week_mean and week_std are that team's calibrated, WEEK-SPECIFIC values (byes/
matchups/roster composition already baked in -- see strength.py and historical.py)
rather than one flat number reused all season.
"""
import numpy as np


def playoff_round_count(playoff_teams: int) -> int:
    if playoff_teams <= 1:
        return 0
    return (playoff_teams - 1).bit_length()  # 6 -> 3, 4 -> 2, 8 -> 3


def simulate_season(
    teams: dict,
    regular_season_weeks: int,
    current_week: int,
    playoff_teams: int,
    team_week_means: dict,     # roster_id -> {week: mean_points}, covers remaining + playoff weeks
    team_week_std: dict,       # roster_id -> {week: std_points}, same coverage
    n_sims: int = 10000,
    seed: int = 42,
):
    rng = np.random.default_rng(seed)
    roster_ids = list(teams.keys())
    n_teams = len(roster_ids)
    idx_of = {rid: i for i, rid in enumerate(roster_ids)}

    base_wins = np.array([teams[rid].wins for rid in roster_ids], dtype=float)
    base_pts = np.array([teams[rid].fpts for rid in roster_ids], dtype=float)

    remaining_weeks = list(range(current_week, regular_season_weeks + 1))
    week_means = {w: np.array([team_week_means[rid][w] for rid in roster_ids]) for w in remaining_weeks}
    week_stds = {w: np.array([team_week_std[rid][w] for rid in roster_ids]) for w in remaining_weeks}

    playoff_rounds = playoff_round_count(playoff_teams)
    playoff_weeks = [regular_season_weeks + r for r in range(1, playoff_rounds + 1)]
    playoff_means = {w: np.array([team_week_means[rid][w] for rid in roster_ids]) for w in playoff_weeks}
    playoff_stds = {w: np.array([team_week_std[rid][w] for rid in roster_ids]) for w in playoff_weeks}

    made_playoffs = np.zeros(n_teams)
    got_bye = np.zeros(n_teams)
    made_final = np.zeros(n_teams)
    champion = np.zeros(n_teams)
    final_wins_sum = np.zeros(n_teams)
    final_pts_sum = np.zeros(n_teams)
    seed_sum = np.zeros(n_teams)

    for _ in range(n_sims):
        wins = base_wins.copy()
        pts = base_pts.copy()

        for week in remaining_weeks:
            scores = np.clip(rng.normal(week_means[week], week_stds[week]), 0, None)
            pts += scores
            seen = set()
            for rid in roster_ids:
                if rid in seen:
                    continue
                opp = teams[rid].weekly_opp.get(week)
                if opp is None or opp not in idx_of:
                    continue
                i, j = idx_of[rid], idx_of[opp]
                seen.add(rid)
                seen.add(opp)
                if scores[i] > scores[j]:
                    wins[i] += 1
                elif scores[j] > scores[i]:
                    wins[j] += 1
                else:
                    wins[i] += 0.5
                    wins[j] += 0.5

        final_wins_sum += wins
        final_pts_sum += pts

        # Seed by (wins desc, points desc) -- matches Sleeper's default tiebreak.
        order = np.lexsort((-pts, -wins))
        for seed_pos, team_idx in enumerate(order, start=1):
            seed_sum[team_idx] += seed_pos

        playoff_idx = order[:playoff_teams]
        made_playoffs[playoff_idx] += 1

        def play(a, b, week):
            m, s = playoff_means[week], playoff_stds[week]
            sa, sb = rng.normal(m[a], s[a]), rng.normal(m[b], s[b])
            return a if sa >= sb else b

        seeds = list(playoff_idx)
        if playoff_teams == 6 and len(playoff_weeks) == 3:
            byes = seeds[:2]
            got_bye[byes] += 1
            w1, w2, w3 = playoff_weeks
            r1_winners = [play(seeds[2], seeds[5], w1), play(seeds[3], seeds[4], w1)]

            remaining = byes + r1_winners
            remaining.sort(key=lambda t: seeds.index(t))
            r2_winners = [play(remaining[0], remaining[-1], w2), play(remaining[1], remaining[-2], w2)]
            made_final[r2_winners] += 1

            champ = play(r2_winners[0], r2_winners[1], w3)
            champion[champ] += 1
        else:
            # Generic fallback: single-elim, no byes/reseed, one round per playoff week.
            bracket = list(seeds)
            for rnd, week in enumerate(playoff_weeks):
                if len(bracket) <= 1:
                    break
                nxt = []
                for k in range(0, len(bracket) - 1, 2):
                    nxt.append(play(bracket[k], bracket[k + 1], week))
                if len(bracket) % 2 == 1:
                    nxt.append(bracket[-1])
                bracket = nxt
                if len(bracket) == 2:
                    made_final[bracket] += 1
            champion[bracket[0]] += 1

    results = {}
    for i, rid in enumerate(roster_ids):
        results[rid] = {
            "playoff_pct": made_playoffs[i] / n_sims * 100,
            "bye_pct": got_bye[i] / n_sims * 100,
            "final_pct": made_final[i] / n_sims * 100,
            "champ_pct": champion[i] / n_sims * 100,
            "avg_final_wins": final_wins_sum[i] / n_sims,
            "avg_final_pts": final_pts_sum[i] / n_sims,
            "avg_seed": seed_sum[i] / n_sims,
        }
    return results
