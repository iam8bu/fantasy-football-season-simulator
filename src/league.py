"""Assembles a clean view of the league: teams, rosters, schedule, and results so far."""
import json
from dataclasses import dataclass, field
from pathlib import Path

import sleeper_api as api

REAL_NAMES_PATH = Path(__file__).resolve().parent.parent / "data" / "real_names.json"


def load_real_names() -> dict:
    """owner_id -> real name, if data/real_names.json exists (gitignored -- never
    committed). Lets output show real names locally without any of that ever
    reaching source control. Missing file / missing entries just fall back to
    each team's normal Sleeper display name, so this is fully optional.
    """
    if not REAL_NAMES_PATH.exists():
        return {}
    with open(REAL_NAMES_PATH) as f:
        return json.load(f)


def _write_real_names_template(display_name_by_owner: dict):
    """First run only: seeds data/real_names.json with owner_id -> current Sleeper
    display name, so there's something to hand-edit into real names locally. Never
    overwrites an existing file (i.e. never clobbers names you've already filled in).
    """
    if REAL_NAMES_PATH.exists():
        return
    REAL_NAMES_PATH.parent.mkdir(exist_ok=True)
    with open(REAL_NAMES_PATH, "w") as f:
        json.dump(display_name_by_owner, f, indent=2, ensure_ascii=False)


@dataclass
class Team:
    roster_id: int
    owner_id: str
    team_name: str
    players: list           # all player_ids on roster
    starters: list           # starting lineup player_ids (most recent week's starters on file)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: float = 0.0        # total points for, season to date
    fpts_against: float = 0.0
    weekly_scores: dict = field(default_factory=dict)   # week -> points scored
    weekly_opp: dict = field(default_factory=dict)      # week -> opponent roster_id


def _team_display_name(user: dict) -> str:
    meta = user.get("metadata") or {}
    return meta.get("team_name") or user.get("display_name") or f"User {user.get('user_id')}"


def load_league(league_id: str, fresh=True):
    league = api.get_league(league_id, fresh=fresh)
    rosters = api.get_rosters(league_id, fresh=fresh)
    users = api.get_users(league_id, fresh=fresh)
    user_by_id = {u["user_id"]: u for u in users}
    real_names = load_real_names()

    teams = {}
    display_names = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        user = user_by_id.get(owner_id, {})
        settings = r.get("settings") or {}
        display_name = _team_display_name(user) if user else f"Roster {r['roster_id']}"
        display_names[owner_id] = display_name
        teams[r["roster_id"]] = Team(
            roster_id=r["roster_id"],
            owner_id=owner_id,
            team_name=real_names.get(owner_id, display_name),
            players=r.get("players") or [],
            starters=r.get("starters") or [],
            wins=settings.get("wins", 0),
            losses=settings.get("losses", 0),
            ties=settings.get("ties", 0),
            fpts=settings.get("fpts", 0) + settings.get("fpts_decimal", 0) / 100,
        )
    _write_real_names_template(display_names)
    return league, teams


def load_schedule_and_results(league_id: str, teams: dict, regular_season_weeks: int, current_week: int):
    """Fill in each team's weekly_scores / weekly_opp for weeks 1..regular_season_weeks.

    Weeks that haven't been played yet still have a fixed matchup_id pairing (Sleeper
    pre-generates the full schedule), just with points=0 -- useful for knowing future
    opponents even though there's no score yet.
    """
    for week in range(1, regular_season_weeks + 1):
        is_played = week < current_week
        matchups = api.get_matchups(league_id, week, fresh=is_played)
        by_matchup = {}
        for m in matchups:
            by_matchup.setdefault(m["matchup_id"], []).append(m)

        for matchup_id, pair in by_matchup.items():
            if len(pair) != 2:
                continue  # bye or malformed
            a, b = pair
            ra, rb = a["roster_id"], b["roster_id"]
            if ra not in teams or rb not in teams:
                continue
            teams[ra].weekly_opp[week] = rb
            teams[rb].weekly_opp[week] = ra
            if is_played:
                teams[ra].weekly_scores[week] = a.get("points") or 0.0
                teams[rb].weekly_scores[week] = b.get("points") or 0.0
    return teams
