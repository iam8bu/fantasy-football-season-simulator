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


def _split_long_fg_buckets(stats: dict, scoring_settings: dict) -> dict:
    """Projections lump every 50+ yd kick into fgm_50p/fgmiss_50p, while leagues
    commonly score only the split 50_59 / 60p buckets (and give 50p zero) -- so a
    projected long FG silently scored nothing. Actual stat lines carry both the
    lumped and split keys, so only fill the split bucket when it's absent; 60+
    attempts are rare enough that treating them all as 50-59 is the right estimate.
    """
    out = stats
    for made_or_miss in ("fgm", "fgmiss"):
        lumped = f"{made_or_miss}_50p"
        split = (f"{made_or_miss}_50_59", f"{made_or_miss}_60p")
        if scoring_settings.get(lumped):
            continue  # league scores the lumped bucket directly -- nothing missing
        if lumped in stats and not any(k in stats for k in split):
            if out is stats:
                out = dict(stats)
            out[split[0]] = stats[lumped]
    return out


def score_stats(stats: dict, scoring_settings: dict) -> float:
    if not stats:
        return 0.0
    stats = _split_long_fg_buckets(stats, scoring_settings)
    return sum(stats[k] * v for k, v in scoring_settings.items() if k in stats)


def week_player_points(season: str, week: int, scoring_settings: dict) -> dict:
    """player_id -> projected fantasy points for this week, per the league's own rules.

    Excludes a hollow entry with no keys overlapping the scoring rules (metadata/rank
    sentinels only, no actual projected production) rather than scoring it as a false
    0 -- confirmed this pattern is real for injured/inactive players in the actuals
    feed (see historical.week_player_actuals), applied here too since projections
    share the same underlying data pipeline.
    """
    raw = api.get_projections(season, week)
    points = {}
    for entry in raw:
        pid = entry.get("player_id")
        stats = entry.get("stats")
        if not pid or not stats:
            continue
        if not (stats.keys() & scoring_settings.keys()):
            continue
        points[pid] = score_stats(stats, scoring_settings)
    return points
