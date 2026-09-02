# Sleeper Season Sim

Monte Carlo season simulator for a real Sleeper fantasy football league — projects
remaining-season standings, playoff odds, and championship odds.

League: **David's Yard Restoration PAC** (Sleeper league `1392633709420646400`, 2026 season,
14 teams, 13-week regular season, top 6 make playoffs).

## How it works

1. **Pull real league data** from Sleeper's public API: rosters, users, the full-season
   schedule (matchup pairings for every week are set at season start), and results for
   weeks already played.
2. **Real per-player weekly projections** — Sleeper exposes an undocumented but public
   projections feed (Rotowire) with a full projected stat line per player per week
   (yards, TDs, FG-make buckets, points-allowed buckets, etc.), not just a single generic
   point total. See `src/projections.py`.
3. **Score those stat lines with the league's own scoring rules** — a dot product between
   the projected stats and this league's actual `scoring_settings` (its real FG-distance
   values, points-allowed tiers, bonuses, etc.), not Sleeper's generic PPR/half-PPR number.
4. **Optimal starting lineup per team, per week** — each team's projected score for a given
   week is its best possible lineup from that week's player values, given the league's real
   roster slots. Byes and missing projections fall out for free: a player with no projection
   that week is simply worth 0 and won't be selected.
5. **Team calibration from real results** — once games are played, each team's actual score
   is compared to what this same engine would have projected for that week (run
   retroactively on the team's current roster). The resulting actual-vs-projected ratio,
   shrunk toward 1.0 based on sample size (full weight by 6 played weeks), scales that
   team's future-week projections — so a team over/under-performing its own matchup-specific
   projections gets adjusted, not just blended against a flat preseason average. Weekly
   volatility (std dev) is likewise estimated from the residuals between actual scores and
   this week-specific baseline, falling back to an empirically-derived default (see next)
   until there's enough data.
6. **Weekly volatility (floor/ceiling) grounded in real history AND real roster
   composition, not a guess** — `src/historical.py` pulls the last 3 completed seasons of
   *actual* results (same undocumented Sleeper endpoint family, same scoring function) and
   measures how much each player's own score varies week to week around their own season
   average, pooling across all 3 seasons so one fluky year doesn't look like a permanent
   trait. That gives three tiers of fallback, richest first:
   - **A specific rostered player's own measured volatility** (e.g. Josh Allen's own std
     from his last 3 seasons), shrunk toward the position average by how many games are in
     the sample (full trust by 24 pooled games).
   - **A recent-rookie-class average** for a player with zero history (this year's actual
     rookies) — not the general position average, which is dominated by proven vets and
     would understate a rookie's real uncertainty. Restricted to a "productive rookies"
     pool (top-N by season average), so inactive/deep-bench drafted rookies don't drag it
     down artificially.
   - **The general position average**, pooled across a "startable" pool at each position, as
     the ultimate fallback and the shrinkage target above.
   Every remaining week, each team's lineup optimizer already knows exactly which specific
   players it started (see step 4) — that pick list is run back through this model, and
   `Var(sum of picks) = sum of each pick's own variance` gives a std dev for THAT team, THAT
   week, driven by its actual roster: a lineup full of boom/bust players gets a wider band
   than one full of steady vets at the same projected mean, and a bye-week streamed slot
   uses the position average (since we don't know who it'd actually be). Bye weeks are
   excluded from every underlying calc (a predictable mean-shift, not week-to-week
   randomness) so they don't inflate volatility artificially.
7. **Simulate the rest of the season** thousands of times: for every remaining week, each
   team's score is drawn from a normal distribution centered on that week's calibrated
   projection, with that team's std dev (see above), matchups are scored against the real
   schedule, and final regular-season standings are tallied (ties broken by total points,
   matching Sleeper's default).
8. **Simulate the playoff bracket** each run using the same per-week projections for weeks
   14-16 (standard 6-team format: top 2 seeds bye, 3v6 / 4v5 in round 1, reseeded round 2,
   then the championship).
9. Aggregate across all simulations into playoff / bye / championship odds per team.

### Bye weeks and streaming

- **A rostered player who's playing that week always starts, full stop.** Streaming never
  competes against an active rostered starter, no matter how good the leaguewide free
  agent pool looks that week -- it only ever fills a slot that would otherwise be empty.
- **Bye weeks with bench depth are already covered by the optimal lineup**: if a team's
  starting TE is on bye but they have a second TE (or spare RB/WR for FLEX), that bench
  player is used automatically -- no streaming involved.
- **Bye weeks with NO bench depth at that position** (the common case: 8 of this league's
  14 teams roster exactly one QB and/or one TE) fall back to the best true free agent at
  that position leaguewide (not rostered by anyone), instead of a hard zero. This applies
  to every position (QB/RB/WR/TE/K/DEF), though in practice it's only ever DEF/K/thin-QB/
  thin-TE that actually hit a real zero -- RB/WR depth almost never runs out leaguewide.
- This is a *future-weeks-only*, shared ceiling: every team gets equal hypothetical access
  to the same top streamer (no simulation of 14 teams competing for one waiver claim), and
  it is never applied retroactively, so the actual-vs-projected calibration above stays
  honest about what each team's real roster actually scored.

### Randomness model

Every remaining week and every playoff game, a team's score is one independent draw:
`score = clip(Normal(week_mean, week_std), min=0)`. Both `week_mean` (steps 2-5) and
`week_std` (step 6) are specific to that team AND that week — not one flat number reused
all season. Once a team has real results, its own actual-vs-projected residual std blends
in too (same shrinkage-by-sample-size pattern as the projection ratio), progressively
replacing the roster-composition estimate as more of the season actually plays out. The
draw itself is still a symmetric Normal rather than fantasy scoring's real right-skewed
shape — see Known simplifications.

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
- `src/projections.py` — real per-player weekly stat-line projections, scored against the
  league's own scoring rules.
- `src/historical.py` — empirical weekly-volatility model from 3 past seasons' real
  results (per-player, rookie-class, and position-average tiers), scored with the same
  scoring function as projections.
- `src/strength.py` — optimal lineup construction per week (returns WHO was picked, not
  just the total, so historical.py can price that specific lineup's volatility), and the
  actual-vs-projected team calibration (ratio + residual std).
- `src/simulate.py` — the Monte Carlo season + playoff bracket simulator (numpy).
- `src/main.py` — orchestrates a full run and prints/saves results.

## Known simplifications

- Projections come from a single source (Rotowire, via Sleeper's feed) — no ensembling
  across multiple projection systems.
- The empirical volatility model pools the last 3 seasons per player, but still can't
  reflect scoring-rule changes this season that didn't exist historically, and averages a
  player's own year-to-year level shifts away (each season's residuals are measured
  against that season's own mean) rather than treating "became a bigger role this year" as
  itself a source of uncertainty.
- The streaming ceiling's volatility (used for a bye-week backstop slot, since we don't
  know who it'd actually be) uses the general position average, not a "typical streamer"
  estimate specifically — plausible but unverified as its own tier.
- Weekly scores are sampled independently (no positional correlation across a team's own
  players, e.g. same-game stacks; no explicit game-script/weather modeling beyond whatever
  Rotowire already bakes into its stat-line projections), and drawn from a symmetric Normal
  rather than fantasy scoring's real right-skewed shape.
- In-season roster moves (waivers/trades) are only reflected once re-fetched — a run always
  uses each team's *current* roster, including retroactively for past-week calibration.
- Streaming ceiling is shared across all teams (no waiver-contention modeling) — see above.
- Sleeper's own `winners_bracket` endpoint is ignored pre-playoffs — its seeds are just
  a placeholder until the regular season actually finishes, so seeding is computed here
  from simulated final standings instead.
