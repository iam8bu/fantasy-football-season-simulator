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

Volatility (std dev) itself is NOT computed in this module -- calibrate_team_ratio
below only returns a team's own residual std once it has real results. The
week-specific fallback/blend-target comes from historical.py, which derives it
from actual past results for the SPECIFIC players in a given week's lineup,
rather than a flat guess.
"""
import math

import projections

SLOT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

# How much should a team's early-season actual-vs-projected ratio move its future
# projections? Fit via eda_assumptions_2.py: regressing (future-season ratio - 1)
# on (known-ratio-through-week-N - 1) for this league's 14 real rosters replayed
# against 3 real seasons (n=42 team-seasons per split). The empirical slope --
# i.e. the CORRECT weight, not an assumed one -- was 0.126 at week 6 and never
# exceeded ~0.19 at any split tested, far below the old min(n/6, 1.0) formula's
# full trust (1.0) by week 6. Fitting weight(n) = n/(n+n0) to those slopes gives
# n0 ~= 63 -- even a full 17-week season only justifies ~0.21 trust. The signal
# is real (consistently positive across every split, unlike per-player projection
# bias which was pure noise -- see historical.py) but much weaker than assumed.
RATIO_SHRINKAGE_N0 = 63.0
RATIO_CLAMP = (0.75, 1.30)     # rarely binds under the reshaped weight above -- kept as a safety backstop

# Separate shrinkage for STD blending -- this used to just reuse the ratio weight
# above, which was never validated for this purpose. Tested the same way (regress
# future residual std on known residual std, relative to the roster-composition
# baseline): the signal here is actually STRONGER than the ratio's, especially
# past week ~8 (weight ~0.32 at week 8, ~0.50 at week 12, vs the ratio's ~0.11-0.16
# at the same weeks) -- a team's volatility LEVEL (built on boom/bust players or
# not) is a more persistent, structural trait than its directional luck, which
# tends to be more transient. Weeks 2-4 showed a fragile, slightly negative
# relationship -- too little data for a variance estimate to mean anything, not a
# real effect -- so the n0 fit below used only weeks 5-12, inverse-variance-
# weighted to trust the tighter (lower standard-error) estimates more.
STD_SHRINKAGE_N0 = 26.7


def best_lineup_points(
    player_ids: list, points_by_pid: dict, position_by_pid: dict, slot_requirements: dict,
    stream_ceilings: dict = None,
):
    """Optimal starting lineup for one week, given roster slot counts.

    slot_requirements example: {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'K': 1, 'DEF': 1}
    FLEX eligible positions: RB/WR/TE.

    stream_ceilings, if given: {'QB': best_free_agent_points, ...}. A required slot is
    only ever filled by this when NO rostered player at that position has a nonzero
    (i.e. non-bye/unprojected) value that week -- a real rostered starter who's playing
    always wins over a hypothetical streamer, no matter the streamer's projection. Also
    covers a team not rostering enough players at a position to fill the slot at all
    (e.g. carrying zero kickers), not just a bye -- both leave the slot empty the same way.

    Returns (total, picks) where picks is [(position, player_id_or_None), ...] for
    every starting slot filled -- player_id is None for a hypothetical streamed
    replacement, since we don't know exactly who it'd be. Used downstream to derive
    a lineup's volatility from the specific real players in it (see historical.py).
    """
    by_pos = {pos: [] for pos in SLOT_POSITIONS}
    for pid in player_ids:
        pos = position_by_pid.get(pid)
        if pos not in by_pos:
            continue
        by_pos[pos].append((points_by_pid.get(pid, 0.0), pid))
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: -x[0])

    total = 0.0
    picks = []
    used = {pos: 0 for pos in SLOT_POSITIONS}
    for pos in SLOT_POSITIONS:
        n = slot_requirements.get(pos, 0)
        take = by_pos[pos][:n]
        used[pos] = len(take)  # real rostered players consumed, for FLEX below -- unaffected by streaming
        filled = list(take)
        if stream_ceilings and pos in stream_ceilings:
            filled = [(v, pid) if v > 0 else (stream_ceilings[pos], None) for v, pid in filled]
            deficit = n - len(filled)   # roster doesn't even have enough players at this position at all
            if deficit > 0:
                filled += [(stream_ceilings[pos], None)] * deficit
        for pts, pid in filled:
            total += pts
            picks.append((pos, pid))

    flex_n = slot_requirements.get("FLEX", 0)
    remaining = []
    for pos in ("RB", "WR", "TE"):
        remaining.extend((pts, pid, pos) for pts, pid in by_pos[pos][used[pos]:])
    remaining.sort(key=lambda x: -x[0])
    for pts, pid, pos in remaining[:flex_n]:
        total += pts
        picks.append((pos, pid))

    return total, picks


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
    total, _picks = best_lineup_points(team_players, week_points, position_lookup, slot_req, stream_ceilings)
    return total


def team_week_lineup(
    team_players: list, week_points: dict, position_lookup: dict, slot_req: dict,
    stream_ceilings: dict = None,
):
    """Like team_week_projection, but also returns the picks list (see
    best_lineup_points) -- used when the caller needs to know WHO was started,
    not just the point total, e.g. to derive that lineup's own volatility.
    """
    return best_lineup_points(team_players, week_points, position_lookup, slot_req, stream_ceilings)


def calibrate_team_ratio(team, retro_projection_by_week: dict):
    """Compare a team's actual scores so far to this engine's own retroactive
    projection for those same weeks. Returns (ratio, std_weight, own_std):

    - ratio: scales future-week base projections -- a team over/under-performing
      its own matchup-specific projections gets adjusted, not just blended
      against a flat preseason average. Uses RATIO_SHRINKAGE_N0 -- this is NOT
      "full trust eventually," it caps well below 1.0 because the evidence shows
      most of the observed swing at realistic sample sizes is noise, not a
      persistent team effect.
    - std_weight: how much to trust this team's OWN residual std vs. a
      roster-composition fallback, based on sample size (STD_SHRINKAGE_N0 --
      backtested SEPARATELY from the ratio's weight, and found to trust real
      data roughly twice as fast; see the constant's comment for why).
    - own_std: this team's own residual std (actual - retroactive projection), or
      None if there's not enough data yet (< 2 played weeks).

    The caller (main.py) blends own_std with a per-week, roster-composition-based
    std from historical.py -- std is NOT resolved here, since the right fallback
    varies week to week (who's actually in that week's lineup), not just team to
    team.
    """
    played_weeks = [w for w in team.weekly_scores if w in retro_projection_by_week]
    n = len(played_weeks)
    if n == 0:
        return 1.0, 0.0, None

    actual = [team.weekly_scores[w] for w in played_weeks]
    proj = [retro_projection_by_week[w] for w in played_weeks]

    total_actual = sum(actual)
    total_proj = sum(proj)
    raw_ratio = total_actual / total_proj if total_proj > 0 else 1.0

    ratio_weight = n / (n + RATIO_SHRINKAGE_N0)
    ratio = 1.0 + ratio_weight * (raw_ratio - 1.0)
    ratio = max(RATIO_CLAMP[0], min(RATIO_CLAMP[1], ratio))

    std_weight = n / (n + STD_SHRINKAGE_N0)
    own_std = None
    if n >= 2:
        residuals = [a - p for a, p in zip(actual, proj)]
        mean_resid = sum(residuals) / n
        var = sum((r - mean_resid) ** 2 for r in residuals) / (n - 1)
        own_std = math.sqrt(var) if var > 0 else None

    return ratio, std_weight, own_std
