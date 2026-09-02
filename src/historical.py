"""Empirical week-to-week scoring volatility, derived from real past results --
replaces a guessed flat std dev with actual data, at three levels:

1. Position average: the generic fallback, pooled across a "startable" pool at
   each position.
2. Per-player: a specific rostered player's OWN measured volatility, pooled
   across multiple past seasons (not just one) so a single fluky season doesn't
   look like a permanent trait. Shrunk toward the position average by sample
   size for anyone still light on games.
3. Per-position rookie average: for a player with ZERO personal history (this
   year's actual rookies), the fallback is the average volatility of recent
   rookie classes in their debut season -- not the general position average,
   which is dominated by proven veterans and would understate a rookie's real
   uncertainty.

All of it scored with THIS league's own scoring rules (same function used for
projections). Bye weeks are excluded from every variance calc (a player simply
has no entry that week) -- a bye is a predictable mean-shift handled elsewhere
(see strength.py's streaming backstop), not week-to-week randomness, and
counting it as a 0 here would conflate the two and inflate volatility falsely.
"""
import math

import projections
import sleeper_api as api

HISTORY_SEASONS_BACK = 3      # pool this many past completed seasons per player
MIN_GAMES_PER_SEASON = 3      # ignore a player-season with fewer real games than this

# Split-half reliability of a player's own pooled std, measured via eda_assumptions.py:
# 0.850 at 10-19 games, 0.850 at 20-29, 0.886 at 30+ -- reliability is already high well
# before 24 games, so full trust at 15 is evidence-based, not just a rounder number.
FULL_TRUST_GAMES = 15

# "Startable" pool sizes for a 14-team league (starters + streaming candidates) --
# big enough to be a stable sample, small enough to exclude scrubs whose one-game
# noise isn't representative of a real starter's volatility. Set from the rank-vs-
# average-points cliff measured in eda_assumptions.py: e.g. RB was previously 60,
# but rank 60 averages just 4.3 pts/week (replacement level) vs rank 40's 8.1 --
# 60 was diluting "typical starter" volatility with committee/deep-bench players.
DEFAULT_POOL_SIZE = {"QB": 24, "RB": 40, "WR": 72, "TE": 24, "K": 24, "DEF": 24}

# Same idea, scaled to how many rookies are actually fantasy-relevant in a given
# season/position -- without this, a rookie pool with no floor includes every
# drafted-but-inactive rookie, whose consistently-tiny scores make "rookie
# volatility" look artificially low (someone who never plays doesn't vary much).
ROOKIE_POOL_SIZE = {"QB": 8, "RB": 20, "WR": 24, "TE": 10}

# Last-resort fallback if the historical pull fails entirely (e.g. API outage) --
# these are simply this session's measured 2025-only values, not re-derived live.
HARDCODED_FALLBACK_STD = {"QB": 8.6, "RB": 7.5, "WR": 7.0, "TE": 6.7, "K": 3.6, "DEF": 5.7}

# Same-real-NFL-team QB + pass-catcher correlation, measured via eda_assumptions.py
# (Pearson r on weekly points, same weeks, 3 seasons, ~81-83 team-seasons each,
# p=.0001 and p=.003 respectively -- real and significant, unlike WR-WR or QB-RB
# pairs which showed no significant correlation). Used to add the covariance term
# Var(sum) actually requires (Var(sum) = sum(Var) + 2*sum(Cov)) whenever a team's
# optimal lineup includes their real QB alongside their real WR1/TE1 from the same
# NFL team -- a same-game "stack" effect the naive independence assumption misses.
QB_WR_STACK_CORR = 0.174
QB_TE_STACK_CORR = 0.120

ROOKIE_ELIGIBLE_POSITIONS = ("QB", "RB", "WR", "TE")  # K/DEF have no meaningful "rookie" volatility distinction


def week_player_actuals(season: str, week: int, scoring_settings: dict) -> dict:
    """player_id -> actual fantasy points for this week. A player with no game
    that week (bye, inactive, not in the feed) is simply absent -- not zero.

    Also excludes a real but hollow case: unlike a bye week (which omits the
    player from the feed entirely), an injured/inactive player who's still on
    an active roster (e.g. on IR) can get a stats entry with NO actual
    counting stats -- just metadata/rank sentinels -- which would otherwise
    score to a false 0.0 and get counted as a genuinely bad game. Detected by
    requiring at least one key that actually overlaps the scoring rules (a
    real game, even a quiet one, always has some: pass_att, rush_att, rec_tgt,
    etc.); confirmed via a real case (Joe Burrow, 2025 IR stint) where the
    injured-week entry had zero overlapping keys vs. his real played weeks.
    """
    raw = api.get_stats(season, week)
    points = {}
    for entry in raw:
        pid = entry.get("player_id")
        stats = entry.get("stats")
        if not pid or not stats:
            continue
        if not (stats.keys() & scoring_settings.keys()):
            continue  # no real counting stats -- injured/inactive, not a real 0
        points[pid] = projections.score_stats(stats, scoring_settings)
    return points


def build_season_series(season: str, weeks: list, scoring_settings: dict) -> dict:
    """player_id -> [points in each week they actually played], for one season."""
    series = {}
    for week in weeks:
        for pid, pts in week_player_actuals(season, week, scoring_settings).items():
            series.setdefault(pid, []).append(pts)
    return series


def _sample_std(pts_list: list):
    n = len(pts_list)
    if n < 2:
        return 0.0
    avg = sum(pts_list) / n
    var = sum((p - avg) ** 2 for p in pts_list) / (n - 1)
    return math.sqrt(var)


def _pooled_std(point_lists: list):
    """Pool several season-blocks of one player's own games into one std, using
    each season's own mean (so a level shift year-to-year, e.g. backup -> starter,
    doesn't get mistaken for volatility). Returns (std, total_games) or (None, 0).
    """
    total_ss, total_df, total_n = 0.0, 0, 0
    for pts in point_lists:
        n = len(pts)
        if n < 2:
            continue
        avg = sum(pts) / n
        total_ss += sum((p - avg) ** 2 for p in pts)
        total_df += n - 1
        total_n += n
    if total_df <= 0:
        return None, 0
    return math.sqrt(total_ss / total_df), total_n


def build_std_model(
    current_season: str, scoring_settings: dict, position_lookup: dict, players_db: dict,
    weeks_per_season: list = None, seasons_back: int = HISTORY_SEASONS_BACK,
    pool_sizes: dict = None,
) -> dict:
    """Pulls `seasons_back` past completed seasons and builds the full volatility
    model. Returns {'player_std': {pid: (std, n_games)}, 'position_avg_std': {pos: std},
    'rookie_std': {pos: std}}.
    """
    weeks_per_season = weeks_per_season or list(range(1, 18))
    pool_sizes = pool_sizes or DEFAULT_POOL_SIZE
    seasons = [str(int(current_season) - i) for i in range(1, seasons_back + 1)]

    blocks_by_pid = {}  # pid -> [(season, points_list), ...]
    for season in seasons:
        series = build_season_series(season, weeks_per_season, scoring_settings)
        for pid, pts_list in series.items():
            if position_lookup.get(pid) not in pool_sizes or len(pts_list) < MIN_GAMES_PER_SEASON:
                continue
            blocks_by_pid.setdefault(pid, []).append((season, pts_list))

    player_std = {}
    position_season_stats = {}   # pos -> [(season_avg, season_std), ...] every qualifying player-season
    rookie_season_stats = {}     # pos -> [(season_avg, season_std), ...] rookie-season blocks only

    for pid, blocks in blocks_by_pid.items():
        pos = position_lookup.get(pid)
        rookie_year = (players_db.get(pid, {}).get("metadata") or {}).get("rookie_year")

        std, n = _pooled_std([pts for _, pts in blocks])
        if std is not None:
            player_std[pid] = (std, n)

        for season, pts in blocks:
            avg = sum(pts) / len(pts)
            block_std = _sample_std(pts)
            position_season_stats.setdefault(pos, []).append((avg, block_std))
            if rookie_year == season and pos in ROOKIE_ELIGIBLE_POSITIONS:
                rookie_season_stats.setdefault(pos, []).append((avg, block_std))

    position_avg_std = {}
    for pos, entries in position_season_stats.items():
        entries.sort(key=lambda x: -x[0])
        pool = entries[: pool_sizes.get(pos, len(entries))]
        position_avg_std[pos] = sum(s for _, s in pool) / len(pool) if pool else None
    for pos, fallback in HARDCODED_FALLBACK_STD.items():
        position_avg_std.setdefault(pos, fallback)

    # Same top-N-by-production filter as position_avg_std, just scaled to rookie
    # pool sizes -- otherwise inactive/deep-bench drafted rookies (consistently
    # near-zero, so "low variance") swamp the average and understate real rookie risk.
    rookie_std = {}
    for pos, entries in rookie_season_stats.items():
        entries.sort(key=lambda x: -x[0])
        pool = entries[: ROOKIE_POOL_SIZE.get(pos, len(entries))]
        rookie_std[pos] = sum(s for _, s in pool) / len(pool) if pool else None
    for pos in ROOKIE_ELIGIBLE_POSITIONS:
        if not rookie_std.get(pos):
            rookie_std[pos] = position_avg_std.get(pos)  # no rookie-class sample found -- fall back

    return {"player_std": player_std, "position_avg_std": position_avg_std, "rookie_std": rookie_std}


def effective_std(pid: str, pos: str, model: dict, players_db: dict, current_season: str) -> float:
    """The std to use for one specific rostered player: their own (shrunk toward
    the position average by sample size) if we have any history for them, else
    the rookie-class average if they're new, else the position average.
    """
    position_avg = model["position_avg_std"].get(pos, 0.0)

    own = model["player_std"].get(pid)
    if own:
        own_std, own_n = own
        weight = min(own_n / FULL_TRUST_GAMES, 1.0)
        return weight * own_std + (1 - weight) * position_avg

    player = players_db.get(pid, {})
    rookie_year = (player.get("metadata") or {}).get("rookie_year")
    is_new = rookie_year == current_season or player.get("years_exp") == 0
    if is_new and pos in model["rookie_std"] and model["rookie_std"][pos]:
        return model["rookie_std"][pos]

    return position_avg


def lineup_std_from_picks(picks: list, model: dict, players_db: dict, current_season: str) -> float:
    """picks: [(position, player_id_or_None), ...] from strength.best_lineup_points
    -- player_id is None for a hypothetical streamed replacement (bye backstop),
    which uses the position average since we don't know exactly who it'd be.

    Var(sum) = sum(Var) + 2*sum(Cov): if the lineup's QB and one of its WR/TE picks
    are real players on the same actual NFL team, adds the measured stack covariance
    (QB_WR_STACK_CORR / QB_TE_STACK_CORR) rather than treating them as independent --
    see eda_assumptions.py for why that assumption doesn't hold for this specific pair.
    """
    stds_by_pick = []
    for pos, pid in picks:
        std = model["position_avg_std"].get(pos, 0.0) if pid is None else effective_std(
            pid, pos, model, players_db, current_season
        )
        stds_by_pick.append((pos, pid, std))

    total_var = sum(std ** 2 for _, _, std in stds_by_pick)

    qb_picks = [(pid, std) for pos, pid, std in stds_by_pick if pos == "QB" and pid]
    if qb_picks:
        qb_pid, qb_std = qb_picks[0]
        qb_team = (players_db.get(qb_pid) or {}).get("team")
        if qb_team:
            for pos, pid, std in stds_by_pick:
                if pos not in ("WR", "TE") or not pid:
                    continue
                if (players_db.get(pid) or {}).get("team") != qb_team:
                    continue
                corr = QB_WR_STACK_CORR if pos == "WR" else QB_TE_STACK_CORR
                total_var += 2 * corr * qb_std * std

    return math.sqrt(max(total_var, 0.0))


def composite_lineup_std(slot_req: dict, position_avg_std: dict) -> float:
    """One generic team-level std from position averages only -- a quick sanity-
    check number for logging, NOT what the simulator actually uses per team (see
    lineup_std_from_picks for the real, roster-specific mechanism). Error
    propagation over the starting slots: Var(sum) = sum(Var); FLEX = average of
    RB/WR/TE.
    """
    total_var = 0.0
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        n = slot_req.get(pos, 0)
        std = position_avg_std.get(pos)
        if std:
            total_var += n * std ** 2

    flex_n = slot_req.get("FLEX", 0)
    if flex_n:
        flex_vars = [position_avg_std[p] ** 2 for p in ("RB", "WR", "TE") if position_avg_std.get(p)]
        if flex_vars:
            total_var += flex_n * (sum(flex_vars) / len(flex_vars))

    return math.sqrt(total_var)
