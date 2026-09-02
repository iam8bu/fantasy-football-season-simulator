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
5. **Team calibration from real results, shrunk by how much the evidence actually supports** —
   once games are played, each team's actual score is compared to what this same engine would
   have projected for that week (run retroactively on the team's current roster), and the
   resulting actual-vs-projected ratio scales future-week projections. The shrinkage weight is
   NOT "ramp to full trust by week N" — that was the original design, but backtesting it (see
   the EDA section below) found a team's own early-season ratio only weakly predicts its
   future ratio, and heavy shrinkage is warranted even with a full season of data. The weight
   uses `n / (n + 63)`, fit directly to the measured relationship — at 6 played weeks that's a
   weight of ~0.09, not 1.0. Weekly volatility (std dev) is likewise estimated from the
   residuals between actual scores and this week-specific baseline, blended in with its OWN
   separately-fit shrinkage weight (`n / (n + 27)` — trusts real data roughly twice as fast
   as the ratio does, backtested independently, see the EDA section below), falling back to
   an empirically-derived default (see next) until there's enough data.
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

### EDA: checking the model's own assumptions against real data

`src/eda_assumptions.py` tests the assumptions above against 3 seasons of real historical
results, not just asserts them. Findings from the run that shaped the current constants:

- **Teammate independence** (the `Var(sum) = sum(Var)` assumption): mostly holds. WR1-WR2
  and QB-RB1 pairs (same real NFL team) showed no significant correlation, and committee
  RB1-RB2 pairs were slightly *negatively* correlated (touch-share tradeoffs). But
  **QB-WR1 (r=0.174, p=.0001) and QB-TE1 (r=0.120, p=.003) same-team correlations are real**
  — a genuine same-game "stack" effect. `lineup_std_from_picks` now adds the corresponding
  covariance term whenever a lineup's real QB and real WR1/TE1 share an actual NFL team,
  rather than treating them as independent.
- **Normality of team-level scores**: strongly holds. Replaying this league's 14 current
  rosters against 3 real seasons (51 weeks each), every single team passed a Shapiro-Wilk
  normality test, with low skew and slightly negative excess kurtosis (if anything,
  thinner-tailed than Normal). The individual-player skew that's real at the player level
  washes out once ~9 players are summed into a team score (Central Limit Theorem) — good
  support for the Normal-draw sampling model.
- **`FULL_TRUST_GAMES`**: split-half reliability of a player's own pooled std was already
  0.850 at just 10-19 games, barely improving to 0.886 at 30+. The old value of 24 was more
  conservative than the data supports; lowered to 15.
- **`DEFAULT_POOL_SIZE`**: at the position's rank cutoff, average points should still look
  like a real "startable" player, not a replacement-level one. RB's old cutoff of 60 landed
  at 4.3 pts/week (genuinely replacement level) vs. rank 40's 8.1 — lowered to 40. QB/WR/TE/
  K/DEF cutoffs already landed in defensible territory and were left as-is.

`src/eda_assumptions_2.py` went further, checking things the first pass didn't:

- **Rotowire's own projection bias**: backtested projected vs. actual for the same
  player/week across 3 seasons. RB/WR/TE are systematically over-projected, and
  *consistently* so — negative in all 3 years individually (RB: -1.79/-0.90/-1.09, WR:
  -1.67/-1.12/-1.20, TE: -0.91/-0.65/-0.54), not one anomalous season. QB/K/DEF showed no
  consistent-direction bias (sign flipped year to year) and are deliberately left uncorrected
  — see `projections.BIAS_CORRECTION`.
- **Is a PLAYER's own projection bias a stable trait worth correcting individually?** No —
  tested and rejected. Split-half reliability of an individual player's own bias was
  essentially zero (r=-0.08 to +0.01, all p>0.29) at every sample size, and a between/within
  variance decomposition showed over 96% of it is week-to-week noise. The position-level
  correction is the *statistically correct* granularity here, not a cruder stand-in for a
  better per-player one — going finer actively fits noise.
- **Does the ratio-calibration mechanism (step 5) actually work?** Partially, and much less
  than the original design assumed. Regressing (future-season ratio) on (known ratio through
  week N), for this league's 14 rosters replayed against 3 real seasons (n=42 team-seasons):
  the correlation is consistently positive at every split tested (2 through 12 weeks) — a
  real signal, unlike the per-player bias case — but weak (empirical optimal weight was only
  0.126 at week 6, never exceeding ~0.19 at any split). The mechanism's existence is
  evidence-backed; its original shrinkage schedule (full trust by week 6) was not, and has
  been replaced with `n/(n+63)`, fit directly to the measured slopes — see
  `strength.RATIO_SHRINKAGE_N0`.
- **Does the SAME shrinkage schedule also fit std-dev blending?** No — checked separately
  (the ratio backtest can't answer this; a team's own residual std is a different quantity
  than its ratio) and the two behave differently enough to need their own constant. The
  std-side signal is actually *stronger*: weeks 2-4 show a fragile, near-zero-to-negative
  relationship (too little data for a variance estimate to mean anything), but from week 5
  on it's real and grows faster than the ratio's — 0.32 at week 8, 0.50 at week 12, roughly
  double the ratio's weight at the same weeks. Makes sense: a roster's volatility *level*
  (built on boom/bust players or not) is a structural property that persists, while its
  directional luck is more transient. Fit separately as `n/(n+27)` — see
  `strength.STD_SHRINKAGE_N0`.
- **Scoring engine sanity**: only 1 of 61 nonzero scoring rules never fired in-sample
  (`fgmiss_0_19` — legitimately rare), and custom league-scoring differs from Sleeper's
  generic half-PPR by +0.83 on average, confirming the custom-scoring step does real work.
- **QB-WR2 correlation**: checked whether applying the QB-WR1 stack constant to a second
  same-team pass-catcher overstates it. It doesn't — QB-WR2 measured r=0.194, if anything
  slightly higher than WR1's 0.174.
- **Multi-season position-volatility stability**: reasonably stable year to year (no
  meaningful drift), supporting the choice to pool 3 seasons rather than use just one. K
  showed more year-to-year swing (2.95-4.20) than other positions, worth noting but not acted
  on.

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
playoff/bye/championship/last-place odds, heatmap-shaded), styled to match this author's
other model dashboards (dark terminal surface, Inter — see the
[World Cup watchability dashboard](https://iam8bu.github.io/world-cup-watchability-dashboard-2026/)
for the sibling project this reuses the design language from). No auto-refresh — same
manual workflow as that project: re-run both commands whenever you want updated numbers,
open `index.html` locally, or push it somewhere that serves static files (e.g. GitHub
Pages) if you want a shareable link.

**Every run also archives a dated snapshot** into `snapshots/<date>.json` (one file per
calendar date — re-running later the same day just updates that day's file). Unlike
`output/` and `data/`, `snapshots/` is **not** gitignored — it's meant to be committed, so
the history survives across machines and travels with the repo. All snapshots get embedded
directly into `index.html` (it's a static file with no backend, so the date dropdown at the
top switches between data already baked in at build time) and the standings table renders
client-side in JS from whichever snapshot is selected — that's also why sorting/the
playoff-line logic live in JS now rather than being pre-rendered in Python. Each snapshot
is auto-labeled "Preseason" (before week 1) or "Week N" once games are underway.

### Showing real names instead of Sleeper team names (optional, local-only)

`data/real_names.json` maps each `owner_id` to a real name, if you'd rather see that than
Sleeper display names/team names in the output. It's entirely optional (falls back to the
normal Sleeper name for anyone not listed) and lives under `data/`, which is gitignored --
it will never be committed or reach GitHub. A template with every `owner_id` in the league
is generated the first time you inspect `teams` (or hand-write one: `{"<owner_id>": "Real
Name", ...}`). Edit it locally with real names; nothing about who's in the league goes into
source control.

## Project layout

- `src/sleeper_api.py` — thin client for Sleeper's public API, with local JSON caching
  (`data/`, gitignored — always regenerable from the API).
- `src/league.py` — assembles teams/rosters/schedule/results into a clean `Team` model.
- `src/projections.py` — real per-player weekly stat-line projections, scored against the
  league's own scoring rules.
- `src/historical.py` — empirical weekly-volatility model from 3 past seasons' real
  results (per-player, rookie-class, and position-average tiers), scored with the same
  scoring function as projections. Also adds the measured QB+pass-catcher same-team
  stack covariance (see EDA below) on top of the naive independent-slots sum.
- `src/eda_assumptions.py` / `src/eda_assumptions_2.py` — checks the model's own assumptions
  against real historical data (teammate independence, Normality of team scores, per-player
  std reliability, position pool cliffs, projection bias, ratio-calibration signal strength).
  Run either directly any time to re-validate after a data refresh.
- `src/strength.py` — optimal lineup construction per week (returns WHO was picked, not
  just the total, so historical.py can price that specific lineup's volatility), and the
  actual-vs-projected team calibration (ratio + residual std).
- `src/simulate.py` — the Monte Carlo season + playoff bracket simulator (numpy).
- `src/main.py` — orchestrates a full run and prints/saves results.

## Known simplifications

- Projections come from a single source (Rotowire, via Sleeper's feed) — no ensembling
  across multiple projection systems. (Considered adding ESPN as a second source; its API
  is reachable but the useful player-pool endpoint requires ESPN auth cookies plus a stat-ID
  mapping table to rescoring against this league's rules — real effort with an uncertain
  payoff, so shelved. FantasyPros' API is real and well-documented but requires a paid HOF
  subscription, ~$108/year, for anything beyond sample data.)
- The empirical volatility model pools the last 3 seasons per player, but still can't
  reflect scoring-rule changes this season that didn't exist historically, and averages a
  player's own year-to-year level shifts away (each season's residuals are measured
  against that season's own mean) rather than treating "became a bigger role this year" as
  itself a source of uncertainty.
- The streaming ceiling's volatility (used for a bye-week backstop slot, since we don't
  know who it'd actually be) uses the general position average, not a "typical streamer"
  estimate specifically — plausible but unverified as its own tier.
- Only the QB-WR1/QB-TE1 same-team correlation is modeled (measured significant by the
  EDA); other pairs (WR-WR, QB-RB, RB-RB) showed no significant correlation and are still
  treated as independent, which the data supports. No explicit game-script/weather modeling
  beyond whatever Rotowire already bakes into its stat-line projections, and the draw is
  still a symmetric Normal rather than fantasy scoring's real right-skewed shape (though the
  EDA found team-level scores are close enough to Normal that this isn't a big concern).
- In-season roster moves (waivers/trades) are only reflected once re-fetched — a run always
  uses each team's *current* roster, including retroactively for past-week calibration.
- Streaming ceiling is shared across all teams (no waiver-contention modeling) — see above.
- Sleeper's own `winners_bracket` endpoint is ignored pre-playoffs — its seeds are just
  a placeholder until the regular season actually finishes, so seeding is computed here
  from simulated final standings instead.
