"""Builds each team's projected weekly score, week by week, for the rest of the season.

Two stages:

1. Base projection: real per-player weekly stat-line projections (Rotowire, via
   Sleeper) scored against the league's own scoring rules (see projections.py),
   then reduced to a team total via the optimal starting lineup for that week
   (byes/injuries fall out naturally -- a player with no projection that week
   just won't be picked).

2. Team calibration: once games are played, compare each team's actual score to
   what this same engine would have projected for that week (using the team's
   *current* roster, applied retroactively). The ratio of actual-to-projected,
   shrunk toward 1.0 based on sample size, is applied to future-week base
   projections -- so a team that's been consistently over/under-performing its
   own matchup-specific projections gets adjusted, not just blended against a
   flat preseason average. Weekly volatility (std dev) is likewise estimated
   from the residuals between actual scores and this week-specific baseline.

3. Streaming (DEF/K only): future-week projections assume a team can replace its
   rostered DEF/K with the best true free agent at that position leaguewide, if
   that's better than what they have rostered -- approximating a manager who
   streams the position rather than assuming a static roster all season. This is
   a shared ceiling available to every team (it does NOT model 14 teams competing
   for the same one streamer), and it is NOT applied retroactively, so the
   actual-vs-projected calibration in stage 2 stays honest about each team's real
   roster.
"""
import math
from collections import ChainMap

import projections

SLOT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
LEAGUE_FALLBACK_STD = 22.0     # used until a team has enough played weeks for its own residual std
RATIO_FULL_WEIGHT_WEEK = 6     # by this many played weeks, trust the team's own performance ratio fully
RATIO_CLAMP = (0.75, 1.30)     # keep calibration from overreacting to small samples / one wild week


def best_lineup_points(player_ids: list, points_by_pid: dict, position_by_pid: dict, slot_requirements: dict) -> float:
    """Optimal starting lineup total for one week, given roster slot counts.

    slot_requirements example: {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'K': 1, 'DEF': 1}
    FLEX eligible positions: RB/WR/TE.
    """
    by_pos = {pos: [] for pos in SLOT_POSITIONS}
    for pid in player_ids:
        pos = position_by_pid.get(pid)
        if pos not in by_pos:
            continue
        by_pos[pos].append(points_by_pid.get(pid, 0.0))
    for pos in by_pos:
        by_pos[pos].sort(reverse=True)

    total = 0.0
    used = {pos: 0 for pos in SLOT_POSITIONS}
    for pos in SLOT_POSITIONS:
        n = slot_requirements.get(pos, 0)
        take = by_pos[pos][:n]
        total += sum(take)
        used[pos] = len(take)

    flex_n = slot_requirements.get("FLEX", 0)
    remaining = []
    for pos in ("RB", "WR", "TE"):
        remaining.extend(by_pos[pos][used[pos]:])
    remaining.sort(reverse=True)
    total += sum(remaining[:flex_n])
    return total


def build_position_lookup(players_db: dict) -> dict:
    lookup = {}
    for pid, p in players_db.items():
        fpos = p.get("fantasy_positions") or []
        pos = p.get("position") or (fpos[0] if fpos else None)
        if pos in SLOT_POSITIONS:
            lookup[pid] = pos
    return lookup


def project_all_weeks(season: str, weeks: list, scoring_settings: dict) -> dict:
    """week -> {player_id: projected_points} for every week in `weeks`."""
    return {week: projections.week_player_points(season, week, scoring_settings) for week in weeks}


STREAMABLE_POSITIONS = ("DEF", "K")


def rostered_player_ids(teams: dict) -> set:
    """All player_ids on any roster in the league -- i.e. NOT available to stream."""
    return {pid for team in teams.values() for pid in team.players}


def position_id_list(position_lookup: dict, position: str) -> list:
    return [pid for pid, pos in position_lookup.items() if pos == position]


def streaming_ceiling(week_points: dict, candidate_ids: list, rostered_ids: set) -> float:
    """Best projected points among true free agents at this position, for one week."""
    best = 0.0
    for pid in candidate_ids:
        if pid in rostered_ids:
            continue
        pts = week_points.get(pid)
        if pts is not None and pts > best:
            best = pts
    return best


def team_week_projection(
    team_players: list, week_points: dict, position_lookup: dict, slot_req: dict,
    stream_ceilings: dict = None,
) -> float:
    """stream_ceilings, if given: {'DEF': best_free_agent_points, 'K': ...} -- lets the
    lineup optimizer swap in a hypothetical streamed replacement if it beats what's
    actually rostered. Uses ChainMap so we never copy the full (huge) points/position
    lookups just to add a couple of synthetic entries.
    """
    if not stream_ceilings:
        return best_lineup_points(team_players, week_points, position_lookup, slot_req)

    extra_points, extra_positions, synthetic_ids = {}, {}, []
    for pos, ceiling in stream_ceilings.items():
        sid = f"__stream_{pos}__"
        extra_points[sid] = ceiling
        extra_positions[sid] = pos
        synthetic_ids.append(sid)

    player_ids = list(team_players) + synthetic_ids
    points_by_pid = ChainMap(extra_points, week_points)
    position_by_pid = ChainMap(extra_positions, position_lookup)
    return best_lineup_points(player_ids, points_by_pid, position_by_pid, slot_req)


def calibrate_team(team, retro_projection_by_week: dict):
    """Compare a team's actual scores so far to this engine's own retroactive
    projection for those same weeks, and return (ratio, std) for scaling/spreading
    future-week base projections.
    """
    played_weeks = [w for w in team.weekly_scores if w in retro_projection_by_week]
    n = len(played_weeks)
    if n == 0:
        return 1.0, None  # no data yet -- caller falls back to league default std

    actual = [team.weekly_scores[w] for w in played_weeks]
    proj = [retro_projection_by_week[w] for w in played_weeks]

    total_actual = sum(actual)
    total_proj = sum(proj)
    raw_ratio = total_actual / total_proj if total_proj > 0 else 1.0

    weight = min(n / RATIO_FULL_WEIGHT_WEEK, 1.0)
    ratio = 1.0 + weight * (raw_ratio - 1.0)
    ratio = max(RATIO_CLAMP[0], min(RATIO_CLAMP[1], ratio))

    if n >= 2:
        residuals = [a - p for a, p in zip(actual, proj)]
        mean_resid = sum(residuals) / n
        var = sum((r - mean_resid) ** 2 for r in residuals) / (n - 1)
        std = math.sqrt(var) if var > 0 else LEAGUE_FALLBACK_STD
    else:
        std = LEAGUE_FALLBACK_STD

    # Blend toward the league fallback until there's a real sample to trust.
    std = weight * std + (1 - weight) * LEAGUE_FALLBACK_STD
    return ratio, max(std, 8.0)
