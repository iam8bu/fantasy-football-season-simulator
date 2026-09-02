# Sleeper Season Sim

Monte Carlo season simulator for a real Sleeper fantasy football league — projects
remaining-season standings, playoff odds, and championship odds.

League: **David's Yard Restoration PAC** (Sleeper league `1392633709420646400`, 2026 season,
14 teams, 13-week regular season, top 6 make playoffs).

## How it works

1. **Pull real league data** from Sleeper's public API: rosters, users, the full-season
   schedule, and results for weeks already played.
2. **Real per-player weekly projections** — Sleeper exposes a public projections feed
   (Rotowire) with a full projected stat line per player per week (yards, TDs, FG-make
   buckets, points-allowed buckets, etc.), not just a single generic point total. See
   `src/projections.py`.
3. **Score those stat lines with the league's own scoring rules** — a dot product between
   the projected stats and this league's actual `scoring_settings` (its real FG-distance
   values, points-allowed tiers, bonuses, etc.), not Sleeper's generic PPR/half-PPR number.
4. **Optimal starting lineup per team, per week** — each team's projected score for a given
   week is its best possible lineup from that week's player values, given the league's real
   roster slots. Byes fall out for free: a player with no projection that week is worth 0
   and won't be selected.
5. **Team calibration from real results, shrunk to what the evidence supports** — once games
   are played, each team's actual score is compared to what this engine would have projected
   for that week (run retroactively on the team's current roster), and the resulting
   actual-vs-projected ratio scales future projections. The shrinkage weight is
   `n / (n + 63)`, fit by backtesting this league's rosters against 3 real seasons — a team's
   own ratio is a real but weak signal even with a full season of data, so trust stays modest
   throughout (~0.21 at week 17). Weekly volatility (std dev) is estimated the same way, from
   the residuals between actual scores and this week-specific baseline, with its own
   separately-fit shrinkage weight (`n / (n + 22.5)` — trusts real data about twice as fast as
   the ratio does), falling back to the historical model below until there's enough data.
6. **Weekly volatility (floor/ceiling) grounded in real history and real roster
   composition** — `src/historical.py` pulls the last 3 completed seasons of actual results
   (same scoring function as projections) and measures how much each player's own score
   varies week to week around their season average, pooled across all 3 seasons. Three tiers
   of fallback, richest first:
   - **A specific rostered player's own measured volatility**, shrunk toward the position
     average based on sample size (`weight = n / (n + 10.5)`, never reaching full trust —
     a player's own history is informative but a persistently noisy signal).
   - **A recent-rookie-class average** for a player with no history, restricted to a
     "productive rookies" pool (top-N by season average) so inactive/deep-bench rookies
     don't distort it.
   - **The general position average**, pooled across a "startable" pool at each position, as
     the ultimate fallback and shrinkage target above.
   Each team's lineup optimizer already knows exactly which players it started (step 4) —
   that pick list is run back through this model, and `Var(sum of picks) = sum of each
   pick's own variance` (plus a same-team stack covariance term, below) gives a std dev for
   that specific team, that specific week. Bye weeks are excluded from every underlying
   calculation, since they're a predictable mean-shift rather than week-to-week randomness.
7. **Same-team stacking is modeled explicitly.** QB-WR1 and QB-TE1 pairs on the same real
   NFL team carry a measured positive correlation (a genuine "stack" effect); other
   teammate pairs (WR-WR, QB-RB, RB-RB) showed no significant correlation and are treated as
   independent. `lineup_std_from_picks` adds the corresponding covariance term whenever a
   lineup's real QB and WR1/TE1 share an NFL team.
8. **Simulate the rest of the season** thousands of times: for every remaining week, each
   team's score is drawn from a normal distribution centered on that week's calibrated
   projection with that team's std dev, matchups are scored against the real schedule, and
   final regular-season standings are tallied (ties broken by total points, matching
   Sleeper's default).
9. **Simulate the playoff bracket** each run using the same per-week projections for weeks
   14-16 (standard 6-team format: top 2 seeds bye, 3v6 / 4v5 in round 1, reseeded round 2,
   then the championship).
10. Aggregate across all simulations into playoff / bye / championship odds per team.

### Bye weeks and streaming

- A rostered player who's playing that week always starts — streaming never competes
  against an active rostered starter, no matter how the leaguewide free agent pool looks
  that week. It only fills a slot that would otherwise be empty.
- Bye weeks with bench depth are already covered by the optimal lineup (e.g. a second TE
  or a spare RB/WR for FLEX) — no streaming involved.
- Bye weeks with no bench depth at that position fall back to the best true free agent at
  that position leaguewide, instead of a hard zero. This applies to every position, though
  in practice only DEF/K/thin-QB/thin-TE ever hit a real zero — RB/WR depth rarely runs out.
- The streaming ceiling is a shared, future-weeks-only baseline: every team gets equal
  hypothetical access to the same top streamer (no simulation of 14 teams competing for one
  waiver claim), and it's never applied retroactively, so the actual-vs-projected
  calibration in step 5 stays honest about what each team's real roster actually scored.

### Randomness model

Every remaining week and every playoff game, a team's score is one independent draw:
`score = clip(Normal(week_mean, week_std), min=0)`. Both `week_mean` and `week_std` are
specific to that team and that week, not a flat number reused all season. Once a team has
real results, its own actual-vs-projected residual std blends in too, progressively
replacing the roster-composition estimate as more of the season plays out.

## Validation

Every modeling assumption above was checked against 3 seasons of real historical Sleeper
data before being trusted, using `src/eda_assumptions.py` and `src/eda_assumptions_2.py`.
Headline findings:

- **Team-level scores are close to Normal.** Replaying this league's 14 current rosters
  against 3 real seasons (51 weeks each), every team passed a Shapiro-Wilk normality test —
  individual-player skew washes out once ~9 players sum into a team score, supporting the
  Normal-draw sampling model.
- **Teammate independence mostly holds, with one real exception.** WR1-WR2 and QB-RB1
  pairs showed no significant same-team correlation; QB-WR1 and QB-TE1 do, and that
  covariance is modeled explicitly (see step 7 above).
- **Calibration shrinkage constants are fit, not assumed.** Both `RATIO_SHRINKAGE_N0` (63)
  and `STD_SHRINKAGE_N0` (22.5) come from regressing future-season values on known values
  through week N, backtested separately since the two behave differently — a roster's
  volatility level is a more persistent trait than its directional luck.
- **Per-player projection bias is not a stable individual trait.** Split-half reliability
  of an individual player's own bias was statistically indistinguishable from zero, so
  correction happens at the position level (where a real, measurable signal exists), not
  the player level.
- **Position pool cutoffs (`DEFAULT_POOL_SIZE`) are calibrated to a genuinely "startable"
  player**, not a replacement-level one, checked by confirming the cutoff rank's average
  points looks like a real starter at that position rather than falling off a cliff.

Re-run either script directly any time to re-validate after a data refresh.

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

Output: a results table printed to console, a CSV at `output/season_sim_<league_id>.csv`,
and a JSON at `output/season_sim_<league_id>.json` (same data, plus run context like the
current week and sim count) that the dashboard below reads.

## Dashboard

```bash
cd src
python3 main.py && python3 build_dashboard.py
```

Reads `output/season_sim_<league_id>.json` and writes a static `index.html` at the repo
root — title, last-updated date, and a sortable standings table (projected wins,
playoff/bye/championship/last-place odds, heatmap-shaded). No auto-refresh: re-run both
commands whenever you want updated numbers, open `index.html` locally, or push it
somewhere that serves static files (e.g. GitHub Pages) for a shareable link.

Every run also archives a dated snapshot into `snapshots/<date>.json` (one file per
calendar date). Unlike `output/` and `data/`, `snapshots/` is committed to the repo, so
history survives across machines. All snapshots are embedded directly into `index.html`,
and a date dropdown switches between them client-side — the standings table (including
sorting and the playoff-line divider) renders entirely in JS from whichever snapshot is
selected. Each snapshot is auto-labeled "Preseason" (before week 1) or "Week N."

### Showing real names instead of Sleeper team names (optional, local-only)

`data/real_names.json` maps each `owner_id` to a real name, if you'd rather see that than
Sleeper display names/team names in the output. It's optional (falls back to the normal
Sleeper name for anyone not listed) and lives under `data/`, which is gitignored — it will
never be committed or reach GitHub. A template with every `owner_id` in the league is
generated the first time you inspect `teams`.

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

## Known limitations

- Projections come from a single source (Rotowire, via Sleeper's feed) — no ensembling
  across multiple projection systems.
- The empirical volatility model pools the last 3 seasons per player but can't reflect
  scoring-rule changes that didn't exist historically, and measures each season's
  residuals against that season's own mean rather than treating a year-over-year role
  change as its own source of uncertainty.
- The streaming ceiling's volatility uses the general position average rather than a
  "typical streamer" estimate specific to that tier.
- Only QB-WR1/QB-TE1 same-team correlation is modeled; other pairs are treated as
  independent, consistent with what the data supports. Score draws are a symmetric Normal
  rather than fantasy scoring's real right-skewed shape, though team-level scores were
  found close enough to Normal for this not to be a material concern.
- In-season roster moves (waivers/trades) are only reflected once re-fetched — a run
  always uses each team's current roster, including retroactively for past-week
  calibration.
- The streaming ceiling is shared across all teams — no waiver-contention modeling.
- Sleeper's own `winners_bracket` endpoint is ignored pre-playoffs (its seeds are only a
  placeholder until the regular season finishes); seeding is computed here from simulated
  final standings instead.
