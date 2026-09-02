"""Empirical week-to-week scoring volatility, derived from a real past season's
actual results -- replaces a guessed flat std dev with real data.

Method: pull a full season of real per-player stat lines, score them with THIS
league's own scoring rules (same function used for projections), and for a
"startable" pool at each position, measure how much a typical player's own score
varies week to week around their own season average. Bye weeks are excluded from
that variance calc (a player simply has no entry that week) -- a bye is a
predictable mean-shift already handled elsewhere (see strength.py's streaming
backstop), not week-to-week randomness, and including a 0 for it would conflate
the two and inflate volatility artificially.

Those per-position "typical starter" variances are then combined into one team-
level std via error propagation: assuming a lineup's starting slots are roughly
independent, Var(sum of slots) = sum of each slot's variance. FLEX is treated as
the average of RB/WR/TE variance, since which position actually fills it varies
team to team and week to week.
"""
import math

import projections
import sleeper_api as api

# Reasonable "startable" pool sizes for a 14-team league (starters + streaming
# candidates) -- big enough to be a stable sample, small enough to exclude scrubs
# whose single-game noise isn't representative of a real starter's volatility.
DEFAULT_POOL_SIZE = {"QB": 24, "RB": 60, "WR": 72, "TE": 24, "K": 24, "DEF": 24}


def week_player_actuals(season: str, week: int, scoring_settings: dict) -> dict:
    """player_id -> actual fantasy points for this week. Players with no game that
    week (bye, inactive, not in the feed) are simply absent -- not zero.
    """
    raw = api.get_stats(season, week)
    points = {}
    for entry in raw:
        pid = entry.get("player_id")
        stats = entry.get("stats")
        if not pid or not stats:
            continue
        points[pid] = projections.score_stats(stats, scoring_settings)
    return points


def build_season_series(season: str, weeks: list, scoring_settings: dict) -> dict:
    """player_id -> [points in each week they actually played], across a season."""
    series = {}
    for week in weeks:
        for pid, pts in week_player_actuals(season, week, scoring_settings).items():
            series.setdefault(pid, []).append(pts)
    return series


def estimate_position_std(
    season: str, weeks: list, scoring_settings: dict, position_lookup: dict,
    pool_sizes: dict = None,
) -> dict:
    """position -> typical single-starter week-to-week std, from real history.

    For each position, take the top-N players by season average (the "startable"
    pool), compute each player's own sample std around their own season average,
    and average those stds across the pool -- a player-agnostic measure of how
    volatile a normal week is at that position.
    """
    pool_sizes = pool_sizes or DEFAULT_POOL_SIZE
    series = build_season_series(season, weeks, scoring_settings)

    by_position = {}
    for pid, pts_list in series.items():
        pos = position_lookup.get(pid)
        if pos not in pool_sizes:
            continue
        if len(pts_list) < 3:
            continue  # too few games to say anything about volatility
        avg = sum(pts_list) / len(pts_list)
        by_position.setdefault(pos, []).append((avg, pts_list))

    position_std = {}
    for pos, players in by_position.items():
        players.sort(key=lambda x: -x[0])
        pool = players[: pool_sizes[pos]]
        stds = []
        for avg, pts_list in pool:
            n = len(pts_list)
            var = sum((p - avg) ** 2 for p in pts_list) / (n - 1)
            stds.append(math.sqrt(var))
        position_std[pos] = sum(stds) / len(stds) if stds else None

    return position_std


def composite_lineup_std(slot_req: dict, position_std: dict) -> float:
    """Combine per-position starter volatility into one team-level weekly std,
    via error propagation over the actual starting slots (assumed independent):
    Var(sum) = sum(Var). FLEX uses the average variance of RB/WR/TE.
    """
    total_var = 0.0
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        n = slot_req.get(pos, 0)
        std = position_std.get(pos)
        if std:
            total_var += n * std ** 2

    flex_n = slot_req.get("FLEX", 0)
    if flex_n:
        flex_vars = [position_std[p] ** 2 for p in ("RB", "WR", "TE") if position_std.get(p)]
        if flex_vars:
            total_var += flex_n * (sum(flex_vars) / len(flex_vars))

    return math.sqrt(total_var)
