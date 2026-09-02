"""Thin client for Sleeper's public read-only API, with local JSON caching.

No auth required — all endpoints here are public. See https://docs.sleeper.com/
"""
import json
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

BASE = "https://api.sleeper.app/v1"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def _get(url: str, retries: int = 3):
    last_err = None
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def _cached(path: Path, fetch_fn, max_age_seconds=None):
    """Read JSON from cache if fresh enough, else fetch and write through."""
    if path.exists():
        if max_age_seconds is None:
            with open(path) as f:
                return json.load(f)
        age = time.time() - path.stat().st_mtime
        if age < max_age_seconds:
            with open(path) as f:
                return json.load(f)
    data = fetch_fn()
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def get_league(league_id: str, fresh=True):
    fn = lambda: _get(f"{BASE}/league/{league_id}")
    return _cached(DATA_DIR / f"league_{league_id}.json", fn, max_age_seconds=0 if fresh else None)


def get_rosters(league_id: str, fresh=True):
    fn = lambda: _get(f"{BASE}/league/{league_id}/rosters")
    return _cached(DATA_DIR / f"rosters_{league_id}.json", fn, max_age_seconds=0 if fresh else None)


def get_users(league_id: str, fresh=True):
    fn = lambda: _get(f"{BASE}/league/{league_id}/users")
    return _cached(DATA_DIR / f"users_{league_id}.json", fn, max_age_seconds=0 if fresh else None)


def get_matchups(league_id: str, week: int, fresh=True):
    fn = lambda: _get(f"{BASE}/league/{league_id}/matchups/{week}")
    return _cached(DATA_DIR / f"matchups_{league_id}_wk{week}.json", fn, max_age_seconds=0 if fresh else None)


def get_draft_picks(draft_id: str):
    # Draft is immutable once complete -- cache forever.
    fn = lambda: _get(f"{BASE}/draft/{draft_id}/picks")
    return _cached(DATA_DIR / f"draft_picks_{draft_id}.json", fn, max_age_seconds=None)


def get_nfl_state():
    fn = lambda: _get(f"{BASE}/state/nfl")
    return _cached(DATA_DIR / "nfl_state.json", fn, max_age_seconds=0)


def get_all_players():
    """~15MB, changes rarely -- cache for a week."""
    fn = lambda: _get(f"{BASE}/players/nfl")
    return _cached(DATA_DIR / "players.json", fn, max_age_seconds=7 * 24 * 3600)


def get_research(season: str, week: int):
    """Ownership/start-rate research data. Note: not under /v1/ in Sleeper's API."""
    fn = lambda: _get(f"https://api.sleeper.app/players/nfl/research/regular/{season}/{week}")
    return _cached(DATA_DIR / f"research_{season}_wk{week}.json", fn, max_age_seconds=24 * 3600)


def get_projections(season: str, week: int):
    """Real per-player weekly projections (stat-line level, Rotowire via Sleeper).

    Undocumented endpoint -- not under /v1/. Returns a list of entries, one per
    player, each with a raw projected 'stats' dict (yards, TDs, FG buckets, etc.)
    that can be scored against a league's own scoring_settings. Refreshed
    frequently upstream (injury news, etc.), so cache is short-lived.
    """
    fn = lambda: _get(f"https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular")
    return _cached(DATA_DIR / f"projections_{season}_wk{week}.json", fn, max_age_seconds=6 * 3600)
