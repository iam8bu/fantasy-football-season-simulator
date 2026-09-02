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

3. Bye-week streaming: if a required starting slot has NO usable rostered player
   that week (every rostered player at that position is on bye/unprojected --
   the common case being a team with zero bench depth at QB or TE), that slot is
   filled with the best true free agent at that position leaguewide instead of a
   hard zero. This only fires when the slot would otherwise be empty -- a team's
   actual rostered starter is always used over the streaming option whenever
   they're playing, even if a leaguewide free agent projects higher that week.
   It's a shared ceiling available to every team (does NOT model 14 teams
   competing for the same one streamer), and it is NOT applied retroactively, so
   the actual-vs-projected calibration in stage 2 stays honest about each team's
   real roster.

The LEAGUE_FALLBACK_STD constant below is only a last-resort default (used if
main.py doesn't supply a real one). In practice main.py computes an empirically
grounded fallback from a past season's actual results -- see historical.py --
and passes it into calibrate_team() instead of relying on this guess.
"""
import math

import projections

SLOT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
LEAGUE_FALLBACK_STD = 22.0     # used until a team has enough played weeks for its own residual std
RATIO_FULL_WEIGHT_WEEK = 6     # by this many played weeks, trust the team's own performance ratio fully
RATIO_CLAMP = (0.75, 1.30)     # keep calibration from overreacting to small samples / one wild week


def best_lineup_points(
    player_ids: list, points_by_pid: dict, position_by_pid: dict, slot_requirements: dict,
    stream_ceilings: dict = None,
) -> float:
    """Optimal starting lineup total for one week, given roster slot counts.

    slot_requirements example: {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'K': 1, 'DEF': 1}
    FLEX eligible positions: RB/WR/TE.

    stream_ceilings, if given: {'QB': best_free_agent_points, ...}. A required slot is
    only ever filled by this when NO rostered player at that position has a nonzero
    (i.e. non-bye/unprojected) value that week -- a real rostered starter who's playing
    always wins over a hypothetical streamer, no matter the streamer's projection. Also
    covers a team not rostering enough players at a position to fill the slot at all
    (e.g. carrying zero kickers), not just a bye -- both leave the slot empty the same way.
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
        used[pos] = len(take)  # real rostered players consumed, for FLEX below -- unaffected by streaming
        if stream_ceilings and pos in stream_ceilings:
            take = [v if v > 0 else stream_ceilings[pos] for v in take]
            deficit = n - len(take)   # roster doesn't even have enough players at this position at all
            if deficit > 0:
                take = take + [stream_ceilings[pos]] * deficit
        total += sum(take)

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


STREAMABLE_POSITIONS = SLOT_POSITIONS  # bye-week backstop applies to any position


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
    return best_lineup_points(team_players, week_points, position_lookup, slot_req, stream_ceilings)


def calibrate_team(team, retro_projection_by_week: dict, fallback_std: float = LEAGUE_FALLBACK_STD):
    """Compare a team's actual scores so far to this engine's own retroactive
    projection for those same weeks, and return (ratio, std) for scaling/spreading
    future-week base projections.

    fallback_std: used both pre-season (no played weeks yet) and to shrink toward
    before a team has enough of its own played weeks to trust. Pass the empirically
    derived composite from historical.py rather than the flat module default when
    available -- see main.py.
    """
    played_weeks = [w for w in team.weekly_scores if w in retro_projection_by_week]
    n = len(played_weeks)
    if n == 0:
        return 1.0, None  # no data yet -- caller falls back to fallback_std

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
        std = math.sqrt(var) if var > 0 else fallback_std
    else:
        std = fallback_std

    # Blend toward the fallback until there's a real sample to trust.
    std = weight * std + (1 - weight) * fallback_std
    return ratio, max(std, 8.0)
