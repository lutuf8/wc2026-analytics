#!/usr/bin/env python3
"""
WC2026 Analytics Pipeline
Runs every 30 mins via GitHub Actions
Fetches finished World Cup matches & Official Standings → saves to Supabase
"""

import os
import time
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

# ── CONFIG ───────────────────────────────────────────────────────────────────
API_KEY      = os.environ.get('APISPORTS_KEY', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

API_BASE    = "https://v3.football.api-sports.io"
API_HEADERS = {"x-apisports-key": API_KEY}
LEAGUE_ID   = 1
SEASON      = 2026

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── PPI CALCULATOR ───────────────────────────────────────────────────────────

def normalize(value, max_val):
    """Scale a raw stat to 0-10"""
    if not value or max_val == 0:
        return 0.0
    return min((float(value) / max_val) * 10, 10.0)

def calculate_ppi(stats, position):
    """
    Calculate position-based PPI (0-10)
    Weights locked in after design discussion.
    """
    g   = stats.get('goals', {})       or {}
    s   = stats.get('shots', {})       or {}
    p   = stats.get('passes', {})      or {}
    t   = stats.get('tackles', {})     or {}
    d   = stats.get('duels', {})       or {}
    dr  = stats.get('dribbles', {})    or {}
    f   = stats.get('fouls', {})       or {}
    gm  = stats.get('games', {})       or {}
    gk  = stats.get('goalkeeper', {})  or {}

    rating        = float(gm.get('rating') or 0)
    goals         = int(g.get('total') or 0)
    assists       = int(g.get('assists') or 0)
    shots_on      = int(s.get('on') or 0)
    key_passes    = int(p.get('key') or 0)
    pass_acc_raw  = str(p.get('accuracy') or '0').replace('%', '')
    pass_acc      = float(pass_acc_raw) / 100 if pass_acc_raw else 0.0
    tackles       = int(t.get('total') or 0)
    intercepts    = int(t.get('interceptions') or 0)
    duels_total   = int(d.get('total') or 0)
    duels_won     = int(d.get('won') or 0)
    drib_att      = int(dr.get('attempts') or 0)
    drib_succ     = int(dr.get('success') or 0)
    fouls_drawn   = int(f.get('drawn') or 0)
    saves         = int(gk.get('saves') or 0)
    goals_conceded= int(gk.get('goals_conceded') or 0)

    duels_ratio = (duels_won / duels_total)              if duels_total > 0 else 0.0
    drib_ratio  = (drib_succ / drib_att)                 if drib_att > 0   else 0.0
    save_ratio  = saves / (saves + goals_conceded)       if (saves + goals_conceded) > 0 else 0.5

    r_rating      = rating                     
    r_goals       = normalize(goals, 3)
    r_assists     = normalize(assists, 3)
    r_shots_on    = normalize(shots_on, 8)
    r_key_passes  = normalize(key_passes, 8)
    r_pass_acc    = pass_acc * 10
    r_tackles     = normalize(tackles, 8)
    r_intercepts  = normalize(intercepts, 8)
    r_duels       = duels_ratio * 10
    r_dribbles    = drib_ratio * 10
    r_fouls_drawn = normalize(fouls_drawn, 6)
    r_saves       = normalize(saves, 10)
    r_save_ratio  = save_ratio * 10

    pos = (position or 'M').upper()

    if pos in ['F', 'ATTACKER', 'FORWARD']:
        ppi = (r_goals      * 0.30 +
               r_rating     * 0.25 +
               r_assists    * 0.15 +
               r_shots_on   * 0.10 +
               r_dribbles   * 0.10 +
               r_key_passes * 0.10)
    elif pos in ['M', 'MIDFIELDER']:
        ppi = (r_rating     * 0.25 +
               r_key_passes * 0.25 +
               r_pass_acc   * 0.20 +
               r_assists    * 0.15 +
               r_duels      * 0.10 +
               r_goals      * 0.05)
    elif pos in ['D', 'DEFENDER']:
        ppi = (r_rating      * 0.30 +
               r_duels       * 0.25 +
               r_intercepts  * 0.20 +
               r_tackles     * 0.15 +
               r_fouls_drawn * 0.10)
    elif pos in ['G', 'GOALKEEPER']:
        ppi = (r_rating     * 0.30 +
               r_save_ratio * 0.30 +
               r_saves      * 0.20 +
               r_save_ratio * 0.20)
    else:
        ppi = r_rating

    return round(min(ppi, 10.0), 2)


# ── API CLIENT ───────────────────────────────────────────────────────────────

def api_get(endpoint, params=None):
    try:
        resp = requests.get(
            f"{API_BASE}/{endpoint}",
            headers=API_HEADERS,
            params=params or {},
            timeout=15
        )
        data = resp.json()
        if data.get('errors') and data['errors']:
            print(f"  ⚠ API error [{endpoint}]: {data['errors']}")
            return []
        return data.get('response', [])
    except Exception as e:
        print(f"  ✗ Request failed [{endpoint}]: {e}")
        return []

def get_finished_matches():
    return api_get("fixtures", {
        "league": LEAGUE_ID,
        "season": SEASON,
        "status": "FT",
        "last": 20
    })

def get_statistics(fixture_id):
    time.sleep(0.5)
    return api_get("fixtures/statistics", {"fixture": fixture_id})

def get_players(fixture_id):
    time.sleep(0.5)
    return api_get("fixtures/players", {"fixture": fixture_id})

def get_events(fixture_id):
    time.sleep(0.5)
    return api_get("fixtures/events", {"fixture": fixture_id})

def get_lineups(fixture_id):
    time.sleep(0.5)
    return api_get("fixtures/lineups", {"fixture": fixture_id})


# ── DATABASE ─────────────────────────────────────────────────────────────────

def match_already_processed(fixture_id):
    result = supabase.table('matches').select('fixture_id').eq('fixture_id', fixture_id).execute()
    return len(result.data) > 0

def safe_int(val):
    try:
        return int(str(val).replace('%', '').strip())
    except:
        return None

def safe_float(val):
    try:
        return float(val)
    except:
        return None

def update_standings():
    data = api_get("standings", {"league": LEAGUE_ID, "season": SEASON})
    if not data:
        return
        
    standings_arrays = data[0].get('league', {}).get('standings', [])
    unique_teams = {}
    
    # Valid World Cup Groups
    valid_groups = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L']
    
    for grp in standings_arrays:
        for t in grp:
            raw_group = str(t.get('group', ''))
            
            # CRITICAL FIX: Completely ignore the glitched "Group Stage" array
            if raw_group == "Group Stage" or raw_group == "Stage":
                continue
                
            g_name = raw_group.replace('Group', '').strip()
            
            # Double check it is a valid single-letter group
            if g_name not in valid_groups:
                continue

            team_id = t['team']['id']
            unique_teams[team_id] = {
                'team_id': team_id,
                'team_name': t['team']['name'],
                'group_name': g_name,
                'rank': t.get('rank', 0),
                'points': t.get('points', 0),
                'goals_diff': t.get('goalsDiff', 0),
                'played': t['all'].get('played', 0),
                'win': t['all'].get('win', 0),
                'draw': t['all'].get('draw', 0),
                'lose': t['all'].get('lose', 0),
                'goals_for': t['all']['goals'].get('for', 0),
                'goals_against': t['all']['goals'].get('against', 0)
            }
            
    rows = list(unique_teams.values())
    if rows:
        supabase.table('standings').upsert(rows).execute()
        print(f"  ✓ Standings synced directly from API ({len(rows)} unique teams)")
# ── MAIN ─────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*55}")
    print(f"WC2026 Pipeline | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*55}")

    if not API_KEY or API_KEY == 'placeholder':
        print("⚠  No API key — skipping. Update APISPORTS_KEY secret when ready.")
        return

    # Unconditionally pull standings first, wrapped in try/except so it never halts matches
    print("\n→ Syncing official standings...")
    try:
        update_standings()
    except Exception as e:
        print(f"  ✗ Failed to sync standings: {e}")

    finished = get_finished_matches()
    if not finished:
        print("No finished matches found.")
        return

    new = [m for m in finished if not match_already_processed(m['fixture']['id'])]
    print(f"Finished: {len(finished)} | New to process: {len(new)}")

    for match in new:
        fid  = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        print(f"\n→ {home} vs {away}  (fixture {fid})")

        stats   = get_statistics(fid)
        players = get_players(fid)
        events  = get_events(fid)
        lineups = get_lineups(fid)

        save_match(match, stats)
        save_events(fid, events)
        save_players_and_stats(fid, players, lineups)

        print(f"✅ Done: {home} vs {away}")

    print(f"\n✅ Pipeline complete\n")


if __name__ == "__main__":
    run()
