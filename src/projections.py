"""Turns Sleeper's real per-player weekly projections into fantasy points, using
the league's OWN scoring rules -- not a generic PPR/half-PPR number.

Sleeper's projection feed (undocumented, but public) gives each player a raw
projected stat line for a given week: yards, TDs, FG-make buckets, points-allowed
buckets for defenses, etc. The stat key names are the same taxonomy the league's
`scoring_settings` dict uses (e.g. "rec", "rush_yd", "fgm_40_49", "pts_allow_21_27"),
so scoring a projection is just a dot product over the keys the two dicts share.

For threshold/bucket-style rules (FG distance, points allowed, etc.) the projection
gives an expected-count/probability per bucket (they sum to ~1 across buckets), so
the dot product naturally computes the correct expected value -- not a double count.

A player missing from a given week's feed (bye, or just not projected) contributes
0 for that week, which is also the behavior you want: a bye-week player shouldn't
be started, so they simply won't be picked for the optimal lineup that week.
"""
import sleeper_api as api


def score_stats(stats: dict, scoring_settings: dict) -> float:
    if not stats:
        return 0.0
    return sum(stats[k] * v for k, v in scoring_settings.items() if k in stats)


def week_player_points(season: str, week: int, scoring_settings: dict) -> dict:
    """player_id -> projected fantasy points for this week, per the league's own rules."""
    raw = api.get_projections(season, week)
    points = {}
    for entry in raw:
        pid = entry.get("player_id")
        stats = entry.get("stats")
        if not pid or not stats:
            continue
        points[pid] = score_stats(stats, scoring_settings)
    return points
