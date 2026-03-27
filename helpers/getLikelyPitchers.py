import pandas as pd
import requests
import pandas_gbq
import os
import json
from datetime import datetime
from google.oauth2 import service_account

# espn_base_url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{current_year}/players"         
#      - # segments/0/leagues/760081598"                                                                                      
#   27 +    espn_base_url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{current_year}/players"         
#   28   
#   29 -                                                                                                                      
#   29      espn_params = {
#   30 -        #"view": ["freeAgents"]                                                                                       
#   30          "scoringPeriodId": 0,
#   31 -        "view": ["players_wl"],                                                                                       
#   31 +        "view": ["kona_player_info"],                                                                                 
#   32          "seasonId": current_year
#   33      }
#   34   
#   35      headers = {'X-Fantasy-Filter': '{"filterActive":{"value":true}}'}
#   36 -    #espn_headers = {}                                                                                                
#   37 -    # player_ids = [3210625]                                                                                          
#   38 -    # headers["X-Fantasy-Filter"] = f'{{"filterIds":{{"value":{player_ids}}}}}'                                       
#   39 -    # espn_headers['X-Fantasy-Filter'] = f'{{"filterActive":{{"value":true}}}}'                                       
#   36   
#   37 -    cookies = {                                                                                                       
#   38 -        "swid": "{DEF635BE-7DB6-462F-86E1-B1945753DC0A}",                                                             
#   39 -        "espn_s2":                                                                                                    
#      -"AECxz1dvmhOuhXEzrfhG0A%2Bi3QEQhzRV3Ag04dTIXhxaY%2BZRdpwby6%2FfzWCy1RiNmyL5YygHOkQby9uAhvzJEmTdCSDgCHM%2FMXQJIhJZj6%2 
#      -FmrYFtXR%2BzETzGdrbcaNdxFqQUAmxJYukjs8pQh7M48285dcNeLDOsE0AbmxDp3C2Ygwia6mt%2F%2Bn%2FeyEHfOTr%2FT9vC6GYC5XRY%2B83PMr8 
#      -ommx9tHgO808KGYULKTIsq%2FIecwki8vrB7xZUqjk7KQi%2F60073kYgM5rYGqsjVnCObnefMwJvVPhgT%2Fkxgm2gp1K%2FVg%3D%3D"            
#   40 -    }                                                                                                                 
#   41 -                                                                                                                      
#   37      try:
#   38 -        # Make the API request                                                                                        
#   39 -        espn_response = requests.get(                                                                                 
#   40 -            espn_base_url,                                                                                            
#   41 -            params=espn_params,                                                                                       
#   42 -            headers=headers,                                                                                          
#   43 -            #cookies=cookies                                                                                          
#   44 -        )                                                                                                             
#   38 +        espn_response = requests.get(espn_base_url, params=espn_params, headers=headers)                              
#   39          espn_response.raise_for_status()
#   40 -                                                                                                                      
#   41 -        # Extract data from response                                                                                  
#   40          espn_data = espn_response.json()

# ESPN stat ID to scoring category mapping
ESPN_PITCHING_STAT_MAP = {
    '34': 'outs',
    '37': 'hits',
    '39': 'baseOnBalls',
    '42': 'hitByPitch',
    '45': 'earnedRuns',
    '48': 'strikeOuts',
    '50': 'wildPitches',
    '51': 'balks',
    '53': 'wins',
    '54': 'losses',
    '57': 'saves',
    '58': 'blownSaves',
    '60': 'holds',
}

PITCHING_POINT_SYSTEM = {
    'outs': 1,
    'earnedRuns': -3,
    'wins': 6,
    'losses': -3,
    'saves': 17,
    'blownSaves': -4,
    'strikeOuts': 5,
    'hits': -1,
    'baseOnBalls': -1,
    'hitByPitch': -1,
    'wildPitches': -1,
    'balks': -7,
    'holds': 8,
}


def _calculate_projected_score(espn_stats):
    """Calculate projected fantasy score from ESPN stat projections."""
    score = 0
    for espn_id, category in ESPN_PITCHING_STAT_MAP.items():
        value = espn_stats.get(espn_id, 0)
        score += value * PITCHING_POINT_SYSTEM.get(category, 0)
    return score


def _get_active_roster_and_todays_teams():
    """Fetch active 26-man rosters and teams playing today."""
    mlb_team_ids = pd.read_csv("helpers/mlb_team_ids.csv")

    active_players = []
    for _, team in mlb_team_ids.iterrows():
        team_id = team['id']
        player_response = requests.get(f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster")
        roster_data = player_response.json()
        for player in roster_data.get("roster", []):
            person = player.get("person", {})
            active_players.append({"id": person.get("id"), "team_id": team_id})

    active_players_df = pd.DataFrame(active_players)

    schedule_response = requests.get("https://statsapi.mlb.com/api/v1/schedule", params={
        "sportId": 1,
        "date": datetime.now().strftime("%Y-%m-%d")
    })
    schedule_data = schedule_response.json()
    dates = schedule_data.get("dates", [])

    if not dates:
        print("No games scheduled for today")
        return active_players_df, pd.DataFrame()

    teams_playing = []
    for game in dates[0].get("games", []):
        away_team = game.get("teams", {}).get("away", {}).get("team", {})
        home_team = game.get("teams", {}).get("home", {}).get("team", {})
        teams_playing.append({"team_id": away_team.get("id")})
        teams_playing.append({"team_id": home_team.get("id")})

    return active_players_df, pd.DataFrame(teams_playing)


def _espn_projection_fallback(espn_data, current_year):
    """Fallback: rank low-owned pitchers by projected fantasy score from ESPN."""
    print("BigQuery returned no results, falling back to ESPN projections")

    parsed_data = []
    for player in espn_data:
        percent_owned = player.get('ownership', {}).get('percentOwned')
        if percent_owned is None or percent_owned >= 7.5:
            continue
        if player.get('defaultPositionId') not in (1, 11) or player.get('proTeamId', 0) == 0:
            continue

        # Find season projections (statSourceId=1)
        proj_stats = {}
        for s in player.get('stats', []):
            if s.get('statSourceId') == 1 and s.get('seasonId') == current_year and s.get('statSplitTypeId') == 0:
                proj_stats = s.get('stats', {})
                break

        if not proj_stats:
            continue

        projected_score = _calculate_projected_score(proj_stats)
        projected_outs = proj_stats.get('34', 0)
        projected_ip = projected_outs / 3 if projected_outs else 0

        # Normalize to per-game score (assume ~1 IP per relief appearance)
        avg_pts = projected_score / projected_ip if projected_ip > 0 else 0

        parsed_data.append({
            'playerId': player.get('id'),
            'playerName': player.get('fullName'),
            'gamesRest': 0,
            'avgFantasyPts': round(avg_pts, 1),
            'boomFantasyPoints': round(avg_pts * 1.5, 1),
        })

    if not parsed_data:
        return pd.DataFrame()

    return pd.DataFrame(parsed_data).sort_values(by='avgFantasyPts', ascending=False)


def get_likely_pitchers():

    # Run the query against the BQ Table to get ALL Likely Pitchers Back
    creds_json = os.environ['GCP_SA_KEY']
    creds_dict = json.loads(creds_json)

    credentials = service_account.Credentials.from_service_account_info(creds_dict)

    with open('helpers/todays_likely_pitchers.sql', 'r') as f:
        sql_query = f.read()

    sql_pitchers = pandas_gbq.read_gbq(
        sql_query,
        project_id=os.environ['PROJECT_ID'],
        credentials=credentials
    )

    current_year = datetime.now().year
    espn_base_url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{current_year}/players"

    espn_params = {
        "scoringPeriodId": 0,
        "view": ["kona_player_info"],
        "seasonId": current_year
    }

    headers = {'X-Fantasy-Filter': '{"filterActive":{"value":true}}'}

    try:
        espn_response = requests.get(espn_base_url, params=espn_params, headers=headers)
        espn_response.raise_for_status()
        espn_data = espn_response.json()

        # Parse ownership data for filtering
        parsed_data = []
        for player in espn_data:
            parsed_data.append({
                'id': player.get('id'),
                'player_name': player.get('fullName'),
                'position_id': player.get('defaultPositionId'),
                'team': player.get('proTeamId'),
                'percent_owned': player.get('ownership', {}).get('percentOwned')
            })

        espn_player_data = pd.DataFrame(parsed_data)
        low_owned_players = espn_player_data[espn_player_data['percent_owned'] < 7.5]
        low_owned_pitchers = low_owned_players[low_owned_players['position_id'].isin([1, 11]) & low_owned_players['team'] != 0]

        # Try BQ-based approach first
        if not sql_pitchers.empty:
            merged_df = sql_pitchers.merge(low_owned_pitchers, left_on='playerName', right_on='player_name')
            likely_pitchers_df = merged_df[['playerId', 'playerName', 'gamesRest', 'avgFantasyPts', 'boomFantasyPoints']]
            likely_pitchers_df = likely_pitchers_df.sort_values(by='boomFantasyPoints', ascending=False)
        else:
            likely_pitchers_df = pd.DataFrame()

        # Fallback to ESPN projections if BQ merge yielded nothing
        if likely_pitchers_df.empty:
            likely_pitchers_df = _espn_projection_fallback(espn_data, current_year)

        if likely_pitchers_df.empty:
            return pd.DataFrame()

        # Filter by active 26-man roster and today's games
        active_players_df, playing_today = _get_active_roster_and_todays_teams()

        if playing_today.empty:
            return pd.DataFrame()

        active_likely_pitchers = likely_pitchers_df.merge(active_players_df, left_on='playerId', right_on='id')
        final_pitcher_picks = active_likely_pitchers.merge(playing_today, left_on='team_id', right_on='team_id')

        return final_pitcher_picks

    except Exception as e:
        print(f"Error in get_likely_pitchers: {e}")