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
        clean_sheet = 1.0 if goals_conceded == 0 and int(gm.get('minutes') or 0) >= 60 else 0.0
        ppi = (r_rating      * 0.40 +
               r_saves       * 0.25 +
               r_save_ratio  * 0.20 +
               (clean_sheet * 10) * 0.15)
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

def save_match(fixture, stats_data):
    home_id = fixture['teams']['home']['id']
    home_stats, away_stats = {}, {}

    for team in stats_data:
        stat_map = {s['type']: s['value'] for s in team.get('statistics', [])}
        if team['team']['id'] == home_id:
            home_stats = stat_map
        else:
            away_stats = stat_map

    row = {
        'fixture_id':             fixture['fixture']['id'],
        'home_team':              fixture['teams']['home']['name'],
        'away_team':              fixture['teams']['away']['name'],
        'home_team_id':           fixture['teams']['home']['id'],
        'away_team_id':           fixture['teams']['away']['id'],
        'home_score':             fixture['goals']['home'] or 0,
        'away_score':             fixture['goals']['away'] or 0,
        'date':                   fixture['fixture']['date'],
        'round':                  fixture['league']['round'],
        'group_name':             fixture['league'].get('group', ''),
        'venue':                  fixture['fixture']['venue']['name'],
        'city':                   fixture['fixture']['venue']['city'],
        'status':                 fixture['fixture']['status']['short'],
        'home_possession':        safe_int(str(home_stats.get('Ball Possession', '0')).replace('%','')),
        'away_possession':        safe_int(str(away_stats.get('Ball Possession', '0')).replace('%','')),
        'home_shots':             safe_int(home_stats.get('Total Shots')),
        'away_shots':             safe_int(away_stats.get('Total Shots')),
        'home_shots_on_target':   safe_int(home_stats.get('Shots on Goal')),
        'away_shots_on_target':   safe_int(away_stats.get('Shots on Goal')),
        'home_xg':                safe_float(home_stats.get('expected_goals')),
        'away_xg':                safe_float(away_stats.get('expected_goals')),
        'home_corners':           safe_int(home_stats.get('Corner Kicks')),
        'away_corners':           safe_int(away_stats.get('Corner Kicks')),
        'home_passes':            safe_int(home_stats.get('Total passes')),
        'away_passes':            safe_int(away_stats.get('Total passes')),
        'home_passes_accuracy':   safe_int(str(home_stats.get('Passes %', '0')).replace('%','')),
        'away_passes_accuracy':   safe_int(str(away_stats.get('Passes %', '0')).replace('%','')),
        'home_yellow_cards':      safe_int(home_stats.get('Yellow Cards')) or 0,
        'away_yellow_cards':      safe_int(away_stats.get('Yellow Cards')) or 0,
        'home_red_cards':         safe_int(home_stats.get('Red Cards')) or 0,
        'away_red_cards':         safe_int(away_stats.get('Red Cards')) or 0,
    }

    supabase.table('matches').upsert(row).execute()
    print(f"  ✓ Match: {row['home_team']} {row['home_score']}-{row['away_score']} {row['away_team']}")

def save_events(fixture_id, events):
    rows = [{
        'fixture_id':   fixture_id,
        'team':         e.get('team', {}).get('name', ''),
        'player_id':    e.get('player', {}).get('id'),
        'player_name':  e.get('player', {}).get('name', ''),
        'type':         e.get('type', ''),
        'detail':       e.get('detail', ''),
        'minute':       e.get('time', {}).get('elapsed'),
        'extra_minute': e.get('time', {}).get('extra'),
    } for e in events]

    if rows:
        supabase.table('match_events').upsert(rows).execute()
    print(f"  ✓ {len(rows)} events saved")

def save_players_and_stats(fixture_id, players_data, lineups_data):
    pos_map = {}
    for team in lineups_data:
        for entry in team.get('startXI', []) + team.get('substitutes', []):
            p = entry.get('player', {})
            if p.get('id'):
                pos_map[p['id']] = p.get('pos', '')

    player_profiles_to_upsert = []
    player_stats_to_upsert = []

    for team_data in players_data:
        team_name = team_data.get('team', {}).get('name', '')
        for player_entry in team_data.get('players', []):
            player = player_entry.get('player', {})
            stats  = (player_entry.get('statistics') or [{}])[0]
            pid    = player.get('id')
            
            if not pid:
                continue

            position = (stats.get('games', {}) or {}).get('position', 'M') or 'M'
            pos_ppi  = calculate_ppi(stats, position)

            player_profiles_to_upsert.append({
                'player_id':       pid,
                'name':            player.get('name', ''),
                'nationality':     player.get('nationality', ''),
                'team':            team_name,
                'position':        position,
                'position_detail': pos_map.get(pid, ''),
                'photo_url':       player.get('photo', ''),
            })

            g  = stats.get('goals', {})    or {}
            s  = stats.get('shots', {})    or {}
            p_ = stats.get('passes', {})   or {}
            t  = stats.get('tackles', {})  or {}
            d  = stats.get('duels', {})    or {}
            dr = stats.get('dribbles', {}) or {}
            f  = stats.get('fouls', {})    or {}
            gm = stats.get('games', {})    or {}
            gk = stats.get('goalkeeper',{})or {}
            c  = stats.get('cards', {})    or {}

            player_stats_to_upsert.append({
                'fixture_id':          fixture_id,
                'player_id':           pid,
                'team':                team_name,
                'position':            position,
                'position_detail':     pos_map.get(pid, ''),
                'minutes_played':      gm.get('minutes') or 0,
                'rating':              safe_float(gm.get('rating')),
                'goals':               g.get('total') or 0,
                'assists':             g.get('assists') or 0,
                'shots_total':         s.get('total') or 0,
                'shots_on_target':     s.get('on') or 0,
                'key_passes':          p_.get('key') or 0,
                'passes_total':        p_.get('total') or 0,
                'passes_accuracy':     safe_int(str(p_.get('accuracy') or '0').replace('%','')) or 0,
                'dribbles_attempted':  dr.get('attempts') or 0,
                'dribbles_completed':  dr.get('success') or 0,
                'duels_total':         d.get('total') or 0,
                'duels_won':           d.get('won') or 0,
                'tackles':             t.get('total') or 0,
                'interceptions':       t.get('interceptions') or 0,
                'fouls_drawn':         f.get('drawn') or 0,
                'fouls_committed':     f.get('committed') or 0,
                'yellow_cards':        c.get('yellow') or 0,
                'red_cards':           c.get('red') or 0,
                'saves':               gk.get('saves') or 0,
                'goals_conceded':      gk.get('goals_conceded') or 0,
                'position_ppi':        pos_ppi,
                'overall_ppi':         None,
            })

    if player_profiles_to_upsert:
        supabase.table('players').upsert(player_profiles_to_upsert, on_conflict='player_id').execute()
    if player_stats_to_upsert:
        supabase.table('player_match_stats').upsert(player_stats_to_upsert, on_conflict='fixture_id,player_id').execute()

    print(f"  ✓ {len(player_stats_to_upsert)} player records saved")
    update_overall_ppi(fixture_id)

def update_overall_ppi(fixture_id):
    for pos in ['F', 'M', 'D', 'G']:
        all_ppis = supabase.table('player_match_stats').select('position_ppi').eq('position', pos).gt('minutes_played', 44).execute()
        ppis = [r['position_ppi'] for r in all_ppis.data if r.get('position_ppi')]
        if not ppis:
            continue

        avg = sum(ppis) / len(ppis)
        if avg == 0:
            continue

        match_players = supabase.table('player_match_stats').select('player_id, position_ppi').eq('fixture_id', fixture_id).eq('position', pos).execute()

        for row in match_players.data:
            if row.get('position_ppi'):
                if len(ppis) < 8:
                    overall = round(min(row['position_ppi'], 10.0), 2)
                else:
                    overall = round(min((row['position_ppi'] / avg) * 10, 12.0), 2)
                supabase.table('player_match_stats').update({'overall_ppi': overall}).eq('fixture_id', fixture_id).eq('player_id', row['player_id']).execute()

    print(f"  ✓ Overall PPI normalised")
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

    print(f"Finished matches: {len(finished)}")

    for match in finished:
        fid  = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        already = match_already_processed(fid)
        print(f"\n→ {home} vs {away}  (fixture {fid}) [{'refresh' if already else 'NEW'}]")

        stats = get_statistics(fid)
        save_match(match, stats)

        if not already:
            players = get_players(fid)
            events  = get_events(fid)
            lineups = get_lineups(fid)
            save_events(fid, events)
            save_players_and_stats(fid, players, lineups)
        else:
            players = get_players(fid)
            lineups = get_lineups(fid)
            save_players_and_stats(fid, players, lineups)

        print(f"✅ Done: {home} vs {away}")

    print(f"\n✅ Pipeline complete\n")


if __name__ == "__main__":
    run()
