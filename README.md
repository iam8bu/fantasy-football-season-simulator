# Sleeper Season Sim

Monte Carlo season simulator for a real Sleeper fantasy football league — projects
remaining-season standings, playoff odds, and championship odds.

League: **David's Yard Restoration PAC** (Sleeper league `1392633709420646400`, 2026 season,
14 teams, 13-week regular season, top 6 make playoffs).

## How it works

1. **Pull real league data** from Sleeper's public API: rosters, users, the full-season
   schedule (matchup pairings for every week are set at season start), and results for
   weeks already played.
2. **Estimate each team's weekly scoring strength**, blending two sources:
   - *Preseason projection* — no external projections needed. Each rostered player gets an
     estimated points/week value from Sleeper's own `search_rank` (skill positions) or
     ownership % (K/DEF, which Sleeper doesn't rank). Each team's value is its *optimal
     starting lineup* total given the league's actual roster slots.
   - *Actual results* — once games are played, each team's real average score & variance.
   - The blend shifts weight from projection to actuals as the season progresses (full
     weight on actuals by week 6).
3. **Simulate the rest of the season** thousands of times: for every remaining week, each
   team's score is drawn from a normal distribution (mean/std from step 2), matchups are
   scored against the real schedule, and final regular-season standings are tallied
   (ties broken by total points, matching Sleeper's default).
4. **Simulate the playoff bracket** each run (standard 6-team format: top 2 seeds bye,
   3v6 / 4v5 in round 1, reseeded round 2, then the championship).
5. Aggregate across all simulations into playoff / bye / championship odds per team.

## Usage

```bash
pip install -r requirements.txt
cd src
python3 main.py                          # default league, 10,000 sims
python3 main.py --sims 20000             # more sims = smoother odds
python3 main.py --league-id <other_id>   # point at a different Sleeper league
```

Re-run any time (e.g. weekly) — it always pulls fresh data, so odds update as real
results come in and the actual/projection blend shifts.

Output: a results table printed to console, and a CSV at `output/season_sim_<league_id>.csv`.

## Project layout

- `src/sleeper_api.py` — thin client for Sleeper's public API, with local JSON caching
  (`data/`, gitignored — always regenerable from the API).
- `src/league.py` — assembles teams/rosters/schedule/results into a clean `Team` model.
- `src/strength.py` — rank/ownership -> projected points, optimal lineup construction,
  projection/actuals blending.
- `src/simulate.py` — the Monte Carlo season + playoff bracket simulator (numpy).
- `src/main.py` — orchestrates a full run and prints/saves results.

## Known simplifications

- The rank -> points curve is a heuristic (not a real projections feed), calibrated
  only for *relative* team strength — good enough for playoff odds, not exact point
  forecasts.
- Weekly scores are sampled independently (no positional correlation, no injury/bye-week
  modeling, no in-season roster moves reflected until re-fetched).
- Sleeper's own `winners_bracket` endpoint is ignored pre-playoffs — its seeds are just
  a placeholder until the regular season actually finishes, so seeding is computed here
  from simulated final standings instead.
