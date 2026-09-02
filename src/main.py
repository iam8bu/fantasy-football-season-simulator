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

    regular_season_weeks = league["settings"]["playoff_week_start"] - 1
    playoff_teams = league["settings"]["playoff_teams"]
    current_week = nfl_state["week"] if nfl_state.get("season") == league["season"] else 1
    # If season hasn't started (week 1, no scores posted), treat as fully preseason.
    current_week = max(current_week, 1)

    print(f"Regular season: weeks 1-{regular_season_weeks} | Current week: {current_week} | Playoff teams: {playoff_teams}")

    league_mod.load_schedule_and_results(args.league_id, teams, regular_season_weeks, current_week)

    print("Fetching player pool + ownership data for preseason projections ...")
    players_db = api.get_all_players()
    research = api.get_research(league["season"], 1)
    player_values = strength.build_player_values(players_db, research)

    slot_req = slot_requirements_from_roster_positions(league["roster_positions"])
    print(f"Starting lineup slots: {slot_req}")

    team_dist = {}
    for rid, team in teams.items():
        preseason_mean = strength.team_preseason_mean(team.players, player_values, slot_req)
        mean, std = strength.blended_mean_std(preseason_mean, team.weekly_scores)
        team_dist[rid] = (mean, std)

    print(f"Running {args.sims} season simulations ...")
    results = simulate.simulate_season(
        teams, regular_season_weeks, current_week, playoff_teams, team_dist, n_sims=args.sims,
    )

    rows = []
    for rid, team in teams.items():
        r = results[rid]
        mean, std = team_dist[rid]
        rows.append({
            "team": team.team_name,
            "record": f"{team.wins}-{team.losses}" + (f"-{team.ties}" if team.ties else ""),
            "proj_wk_pts": round(mean, 1),
            "avg_final_wins": round(r["avg_final_wins"], 1),
            "avg_final_pts": round(r["avg_final_pts"], 1),
            "avg_seed": round(r["avg_seed"], 1),
            "playoff_pct": round(r["playoff_pct"], 1),
            "bye_pct": round(r["bye_pct"], 1),
            "final_pct": round(r["final_pct"], 1),
            "champ_pct": round(r["champ_pct"], 1),
        })

    rows.sort(key=lambda x: (-x["champ_pct"], -x["playoff_pct"], -x["avg_final_wins"]))

    # ---- Print table ----
    headers = ["Team", "Record", "Proj/Wk", "Avg Final W", "Avg Pts", "Avg Seed",
               "Playoff%", "Bye%", "Final%", "Champ%"]
    keys = ["team", "record", "proj_wk_pts", "avg_final_wins", "avg_final_pts",
            "avg_seed", "playoff_pct", "bye_pct", "final_pct", "champ_pct"]
    widths = [24, 8, 8, 12, 9, 9, 9, 6, 7, 7]

    def fmt_row(vals):
        return "  ".join(str(v).ljust(w) for v, w in zip(vals, widths))

    print()
    print(fmt_row(headers))
    print("-" * (sum(widths) + 2 * len(widths)))
    for row in rows:
        print(fmt_row([row[k] for k in keys]))

    # ---- Save CSV ----
    out_path = OUT_DIR / f"season_sim_{args.league_id}.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writerow(dict(zip(keys, headers)))
        for row in rows:
            writer.writerow({k: row[k] for k in keys})
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
