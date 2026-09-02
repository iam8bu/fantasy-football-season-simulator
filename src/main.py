"""Entry point: pull fresh Sleeper data, run the season simulation, print + save results.

Usage:
    python3 main.py [--sims 10000] [--league-id 1392633709420646400]
"""
import argparse
import csv
from pathlib import Path

import sleeper_api as api
import league as league_mod
import strength
import simulate

DEFAULT_LEAGUE_ID = "1392633709420646400"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUT_DIR.mkdir(exist_ok=True)


def slot_requirements_from_roster_positions(roster_positions: list) -> dict:
    req = {}
    for slot in roster_positions:
        if slot in ("BN", "IR", "TAXI"):
            continue
        req[slot] = req.get(slot, 0) + 1
    return req


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", default=DEFAULT_LEAGUE_ID)
    ap.add_argument("--sims", type=int, default=10000)
    args = ap.parse_args()

    print(f"Fetching league data for {args.league_id} ...")
    league, teams = league_mod.load_league(args.league_id, fresh=True)
    nfl_state = api.get_nfl_state()
    season = league["season"]

    regular_season_weeks = league["settings"]["playoff_week_start"] - 1
    playoff_teams = league["settings"]["playoff_teams"]
    current_week = nfl_state["week"] if nfl_state.get("season") == season else 1
    current_week = max(current_week, 1)

    print(f"Regular season: weeks 1-{regular_season_weeks} | Current week: {current_week} | Playoff teams: {playoff_teams}")

    league_mod.load_schedule_and_results(args.league_id, teams, regular_season_weeks, current_week)

    slot_req = slot_requirements_from_roster_positions(league["roster_positions"])
    print(f"Starting lineup slots: {slot_req}")

    players_db = api.get_all_players()
    position_lookup = strength.build_position_lookup(players_db)
    scoring_settings = league["scoring_settings"]

    played_weeks = list(range(1, current_week))
    remaining_weeks = list(range(current_week, regular_season_weeks + 1))
    playoff_rounds = simulate.playoff_round_count(playoff_teams)
    playoff_weeks = [regular_season_weeks + r for r in range(1, playoff_rounds + 1)]

    weeks_needed = sorted(set(played_weeks + remaining_weeks + playoff_weeks))
    print(f"Pulling real per-player projections for weeks {weeks_needed} (this may take a moment) ...")
    week_points = strength.project_all_weeks(season, weeks_needed, scoring_settings)

    team_week_means = {}
    team_std = {}
    for rid, team in teams.items():
        retro_by_week = {
            w: strength.team_week_projection(team.players, week_points[w], position_lookup, slot_req)
            for w in played_weeks
        }
        ratio, std = strength.calibrate_team(team, retro_by_week)
        if std is None:
            std = strength.LEAGUE_FALLBACK_STD
        team_std[rid] = std

        means = {}
        for w in remaining_weeks + playoff_weeks:
            base = strength.team_week_projection(team.players, week_points[w], position_lookup, slot_req)
            means[w] = base * ratio
        team_week_means[rid] = means

    print(f"Running {args.sims} season simulations ...")
    results = simulate.simulate_season(
        teams, regular_season_weeks, current_week, playoff_teams,
        team_week_means, team_std, n_sims=args.sims,
    )

    rows = []
    for rid, team in teams.items():
        r = results[rid]
        next_week_mean = team_week_means[rid].get(current_week, team_week_means[rid][remaining_weeks[0]] if remaining_weeks else 0)
        rows.append({
            "team": team.team_name,
            "record": f"{team.wins}-{team.losses}" + (f"-{team.ties}" if team.ties else ""),
            "proj_next_wk": round(next_week_mean, 1),
            "avg_final_wins": round(r["avg_final_wins"], 1),
            "avg_final_pts": round(r["avg_final_pts"], 1),
            "avg_seed": round(r["avg_seed"], 1),
            "playoff_pct": round(r["playoff_pct"], 1),
            "bye_pct": round(r["bye_pct"], 1),
            "final_pct": round(r["final_pct"], 1),
            "champ_pct": round(r["champ_pct"], 1),
        })

    rows.sort(key=lambda x: (-x["champ_pct"], -x["playoff_pct"], -x["avg_final_wins"]))

    headers = ["Team", "Record", "Proj Wk" + str(current_week), "Avg Final W", "Avg Pts", "Avg Seed",
               "Playoff%", "Bye%", "Final%", "Champ%"]
    keys = ["team", "record", "proj_next_wk", "avg_final_wins", "avg_final_pts",
            "avg_seed", "playoff_pct", "bye_pct", "final_pct", "champ_pct"]
    widths = [24, 8, 9, 12, 9, 9, 9, 6, 7, 7]

    def fmt_row(vals):
        return "  ".join(str(v).ljust(w) for v, w in zip(vals, widths))

    print()
    print(fmt_row(headers))
    print("-" * (sum(widths) + 2 * len(widths)))
    for row in rows:
        print(fmt_row([row[k] for k in keys]))

    out_path = OUT_DIR / f"season_sim_{args.league_id}.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writerow(dict(zip(keys, headers)))
        for row in rows:
            writer.writerow({k: row[k] for k in keys})
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
