"""Monte Carlo season simulator: remaining regular season -> standings -> playoff bracket."""
import numpy as np


def simulate_season(
    teams: dict,
    regular_season_weeks: int,
    current_week: int,
    playoff_teams: int,
    team_dist: dict,           # roster_id -> (mean, std)
    n_sims: int = 10000,
    seed: int = 42,
):
    """Returns per-roster_id aggregate results across n_sims simulations.

    team_dist[roster_id] = (mean_points, std_points) for that team's weekly score,
    used to sample every remaining regular-season week AND every playoff game.
    """
    rng = np.random.default_rng(seed)
    roster_ids = list(teams.keys())
    n_teams = len(roster_ids)
    idx_of = {rid: i for i, rid in enumerate(roster_ids)}

    means = np.array([team_dist[rid][0] for rid in roster_ids])
    stds = np.array([team_dist[rid][1] for rid in roster_ids])

    # Starting point: actual record/points already accumulated through current_week - 1.
    base_wins = np.array([teams[rid].wins for rid in roster_ids], dtype=float)
    base_losses = np.array([teams[rid].losses for rid in roster_ids], dtype=float)
    base_pts = np.array([teams[rid].fpts for rid in roster_ids], dtype=float)

    # Remaining regular-season weeks and each team's opponent per week.
    remaining_weeks = [w for w in range(current_week, regular_season_weeks + 1)]

    # aggregate trackers
    made_playoffs = np.zeros(n_teams)
    got_bye = np.zeros(n_teams)
    made_final = np.zeros(n_teams)
    champion = np.zeros(n_teams)
    final_wins_sum = np.zeros(n_teams)
    final_pts_sum = np.zeros(n_teams)
    seed_sum = np.zeros(n_teams)

    for _ in range(n_sims):
        wins = base_wins.copy()
        losses = base_losses.copy()
        pts = base_pts.copy()

        for week in remaining_weeks:
            scores = rng.normal(means, stds)
            scores = np.clip(scores, 0, None)
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
                    losses[j] += 1
                elif scores[j] > scores[i]:
                    wins[j] += 1
                    losses[i] += 1
                else:
                    wins[i] += 0.5
                    wins[j] += 0.5

        final_wins_sum += wins
        final_pts_sum += pts

        # Seed by (wins desc, points desc) -- matches Sleeper's default tiebreak.
        order = np.lexsort((-pts, -wins))  # last key is primary
        for seed_pos, team_idx in enumerate(order, start=1):
            seed_sum[team_idx] += seed_pos

        playoff_idx = order[:playoff_teams]
        made_playoffs[playoff_idx] += 1

        # ---- Simulate playoff bracket (standard: top 2 seeds bye, reseed each round) ----
        seeds = list(playoff_idx)  # seeds[0] = 1-seed ... seeds[playoff_teams-1] = last seed
        if playoff_teams == 6:
            byes = seeds[:2]
            got_bye[byes] += 1
            r1_pairs = [(seeds[2], seeds[5]), (seeds[3], seeds[4])]  # 3v6, 4v5
            r1_winners = []
            for a, b in r1_pairs:
                sa, sb = rng.normal(means[a], stds[a]), rng.normal(means[b], stds[b])
                r1_winners.append(a if sa >= sb else b)

            # Reseed round 2: byes + r1 winners, best seed vs worst seed.
            remaining = byes + r1_winners
            remaining.sort(key=lambda t: seeds.index(t))
            r2_pairs = [(remaining[0], remaining[-1]), (remaining[1], remaining[-2])]
            r2_winners = []
            for a, b in r2_pairs:
                sa, sb = rng.normal(means[a], stds[a]), rng.normal(means[b], stds[b])
                r2_winners.append(a if sa >= sb else b)
            made_final[r2_winners] += 1

            fa, fb = r2_winners
            sa, sb = rng.normal(means[fa], stds[fa]), rng.normal(means[fb], stds[fb])
            champ = fa if sa >= sb else fb
            champion[champ] += 1
        else:
            # Generic fallback for non-6-team playoff formats: single-elim, no byes/reseed.
            bracket = list(seeds)
            while len(bracket) > 1:
                nxt = []
                for k in range(0, len(bracket) - 1, 2):
                    a, b = bracket[k], bracket[k + 1]
                    sa, sb = rng.normal(means[a], stds[a]), rng.normal(means[b], stds[b])
                    nxt.append(a if sa >= sb else b)
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
