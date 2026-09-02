"""Estimates each team's expected weekly fantasy score.

Two ingredients, blended by how much of the season has actually been played:

1. Preseason projection: derived from Sleeper's own player rank / ownership data
   (no external projections needed), by picking each team's best starting lineup
   and converting rank -> an estimated points-per-week value via a decay curve.
2. In-season actuals: each team's real average score / stdev so far this season.

Blend weight shifts from (1) to (2) as actual weeks accumulate, reaching full
weight on actuals by BLEND_FULL_WEIGHT_WEEK.
"""
import math

BLEND_FULL_WEIGHT_WEEK = 6          # by this many played weeks, trust actuals 100%
LEAGUE_AVG_STD = 22.0                # fallback weekly score stdev when too little data
SKILL_POS = {"QB", "RB", "WR", "TE", "K"}


def _rank_to_points(rank: int) -> float:
    """Rough overall-rank -> expected weekly points curve, half-PPR-ish.

    Calibrated loosely: rank 1 ~27 pts/wk, rank 50 ~17, rank 150 ~10, rank 300+ ~8.
    This is a heuristic stand-in for real projections -- good enough for relative
    team strength, not meant to match any specific projection system.
    """
    return 8.0 + 20.0 * math.exp(-rank / 60.0)


def _ownership_to_points(owned_pct: float) -> float:
    """For K/DEF, which Sleeper doesn't rank -- ownership % as a proxy for quality."""
    owned_pct = owned_pct or 0.0
    return 2.0 + 6.0 * (owned_pct / 100.0)


def build_player_values(players_db: dict, research: dict) -> dict:
    """player_id -> {'position', 'points'} expected weekly points."""
    values = {}
    for pid, p in players_db.items():
        fpos = p.get("fantasy_positions") or []
        pos = p.get("position") or (fpos[0] if fpos else None)
        if pos not in SKILL_POS:
            continue
        r = research.get(pid, {})
        if pos == "DEF":
            points = _ownership_to_points(r.get("owned"))
        else:
            rank = p.get("search_rank")
            if rank is None or rank >= 900000:
                # Unranked/inactive players get a low floor value.
                points = 3.0
            else:
                points = _rank_to_points(rank)
        values[pid] = {"position": pos, "points": points}
    return values


def best_lineup_points(player_ids: list, player_values: dict, slot_requirements: dict) -> float:
    """Greedy-optimal starting lineup total, given roster slot counts.

    slot_requirements example: {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'K': 1, 'DEF': 1}
    FLEX eligible positions: RB/WR/TE.
    """
    by_pos = {"QB": [], "RB": [], "WR": [], "TE": [], "K": [], "DEF": []}
    for pid in player_ids:
        v = player_values.get(pid)
        if not v:
            continue
        by_pos.setdefault(v["position"], []).append(v["points"])
    for pos in by_pos:
        by_pos[pos].sort(reverse=True)

    total = 0.0
    used = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "K": 0, "DEF": 0}

    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        n = slot_requirements.get(pos, 0)
        vals = by_pos.get(pos, [])
        take = vals[:n]
        total += sum(take)
        used[pos] = len(take)

    # FLEX: best remaining RB/WR/TE
    flex_n = slot_requirements.get("FLEX", 0)
    remaining = []
    for pos in ("RB", "WR", "TE"):
        remaining.extend(by_pos.get(pos, [])[used[pos]:])
    remaining.sort(reverse=True)
    total += sum(remaining[:flex_n])

    return total


def team_preseason_mean(team_players: list, player_values: dict, slot_requirements: dict) -> float:
    return best_lineup_points(team_players, player_values, slot_requirements)


def blended_mean_std(preseason_mean: float, weekly_scores: dict):
    """Combine preseason projection with actual scores so far this season."""
    n = len(weekly_scores)
    if n == 0:
        return preseason_mean, LEAGUE_AVG_STD

    actual_vals = list(weekly_scores.values())
    actual_mean = sum(actual_vals) / n
    if n >= 2:
        var = sum((x - actual_mean) ** 2 for x in actual_vals) / (n - 1)
        actual_std = math.sqrt(var) if var > 0 else LEAGUE_AVG_STD
    else:
        actual_std = LEAGUE_AVG_STD

    weight = min(n / BLEND_FULL_WEIGHT_WEEK, 1.0)
    mean = weight * actual_mean + (1 - weight) * preseason_mean
    std = weight * actual_std + (1 - weight) * LEAGUE_AVG_STD
    return mean, max(std, 8.0)  # floor stdev so sim never gets a near-deterministic team
