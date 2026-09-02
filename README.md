# Fantasy Football Season Sim

Monte Carlo season simulator for my fantasy league. Projects
remaining-season standings, playoff odds, and championship odds. The first layer of projection consists of player-level projections and volatility which feed into an optimal weekly lineup per team. The second layer is each team's own history of over/underperforming its own projections, which shifts its future mean and volatility, capturing manager tendencies, roster-specific projection bias, and early trends.

League: 14 teams, 0.5 PPR, 0.5 PPFD

## How it works

1. **Data**: from Sleeper's public API: rosters, schedule, and results.
2. **Player Projections**: Sleeper API has a full projected stat line per player per week. See
   `src/projections.py`.
3. **Scoring**: a dot product between
   the projected stats and our league `scoring_settings`.
4. **Lineups**: each team's projected score for a given
   week is based on its best possible lineup from that week's projected player scores. If a roster doesn't have a backup available, the model pulls in the best player on waivers (see below).
5. **Team calibration**: once games
   are played, each team's actual score is compared to what this engine would have projected
   for that week and the resulting
   actual-vs-projected ratio scales future projections. The decay weight is
   `n / (n + 63)`, fit by backtesting this league's rosters against the past 3 seasons. Weekly volatility (std dev) is estimated the same way, from a
   the residuals between actual scores and this week-specific baseline, with its own
   separately-fit decay weight (`n / (n + 22.5)`)
6. **Weekly volatility calculation**: `src/historical.py` pulls the last 3 completed seasons of actual results
   and measures how much each player's own score
   varies week to week around their season average. Modifications:
   - **Veterans**, decayed toward the position
     average based on sample size (`weight = n / (n + 10.5)`. Position average is calculated across a "startable" pool at each position.
   - **Rookies** for a player with no history, use data from a
     "productive rookies" pool 
   Each team's lineup optimizer already knows which players it started (step 4) —
   that pick list is run back through this model, and `Var(sum of picks) = sum of each
   pick's own variance` gives a std dev for
   each team each week.
7. **Same-team stacking**: Exploratory data analysis found that QB-WR and QB-TE pairs on the same real
   NFL team carry a measured positive correlation. Other
   teammate pairs (WR-WR, QB-RB, RB-RB) showed no significant correlation and are treated as
   independent. `lineup_std_from_picks` adds the corresponding covariance term whenever a
   lineup's real QB and WR1/TE1 share an NFL team.
8. **Simulate the rest of the season**: For every remaining week, each
   team's score is drawn from a normal distribution centered on that week's calibrated
   projection with that team's std dev, matchups are scored against the real schedule, and
   final regular-season standings are tallied (ties broken by total points).
9. **Simulate the playoff bracket**: each run using the same per-week projections for weeks
   14-16 (standard 6-team format: top 2 seeds bye, 3v6 / 4v5 in round 1, reseeded round 2,
   then the championship).
10. Aggregate across all simulations into playoff / bye / championship / last place odds per team.

### Bye weeks and streaming

- Most bye weeks are already covered by the optimal lineup (e.g. your RB2 is out so the model selects the best active bench player)
- Bye weeks with no bench depth at that position fall back to the best player on waivers at
  that position.
- Defense streaming is modeled by taking the highest scorer between a team's roster and the waiver wire.
- Every team gets equal hypothetical access to the same top streamer (no simulation of competing waiver claims)

## Project layout

- `src/sleeper_api.py` — thin client for Sleeper's public API, with local JSON caching
  (`data/`, gitignored — always regenerable from the API).
- `src/league.py` — assembles teams/rosters/schedule/results into a clean `Team` model.
- `src/projections.py` — real per-player weekly stat-line projections, scored against the
  league's own scoring rules.
- `src/historical.py` — empirical weekly-volatility model from 3 past seasons' real
  results (per-player, rookie-class, and position-average tiers), plus the same-team
  stack covariance used on top of the naive independent-slots sum.
- `src/eda_assumptions.py` / `src/eda_assumptions_2.py` — validates the model's assumptions
  against real historical data. Run either directly any time to re-validate after a data
  refresh.
- `src/strength.py` — optimal lineup construction per week (returns who was picked, not
  just the total, so `historical.py` can price that specific lineup's volatility), and the
  actual-vs-projected team calibration (ratio + residual std).
- `src/simulate.py` — the Monte Carlo season + playoff bracket simulator (numpy).
- `src/main.py` — orchestrates a full run and prints/saves results.
- `src/build_dashboard.py` — renders the static dashboard from a run's output JSON.
