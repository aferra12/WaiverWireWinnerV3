import requests
import pandas as pd
import json

from .getGameDetails import get_game_details

def get_player_game_logs(game_pks: list) -> pd.DataFrame:
    """
    Extract player game logs from MLB Stats API for specified games.
    
    Args:
        game_pks (list): List of MLB game IDs to retrieve data for
        
    Returns:
        pandas.DataFrame: DataFrame containing player statistics
    """

    # Grab all of the game details first, will be joined to the player logs later
    try:
        game_details = get_game_details(game_pks)
    except requests.exceptions.RequestException as e:
        print(f"Error retrieving data for game {game_pks}: {e}")

    # Track player game logs
    player_data = []
    
    for game_pk in game_pks:

        try:
            # MLB Stats API endpoint for game details with boxscore
            url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    
            response = requests.get(url)
            data = response.json()
            
            # Process both home and away teams
            for team_type in ['home', 'away']:
                team_name = data['teams'][team_type]['team']['name']
                # team_id = data['teams'][team_type]['team']['id']
                
                # Process each player on the team
                for player_id, player_info in data['teams'][team_type]['players'].items():

                    # with open(f'player_info_{player_id}.json', 'w', encoding='utf-8') as f:
                    #     json.dump(player_info, f, ensure_ascii=False, indent=4)

                    # I've decided that I'm just going to make one massive table for both pitchers, hitters, and game data #
                    # SHOULD I DO BATTERS HERE TOO WITH IS_PITCHER FLAG? PROBABLY #

                    player_record = {
                            'gamePk': game_pk,
                            'playerId': player_id.replace('ID', ''),
                            'playerName': player_info['person']['fullName'],
                            'teamName': team_name,
                            'isHome': True if team_type == 'home' else False
                    }

                    # Check if player is a pitcher (position code "1")
                    if "position" in player_info and player_info["position"]["code"] == "1":
                        player_record['isPitcher'] = True
                        
                        ### STOPPED HERE LAST TIME. NEED TO ADD IN FiELDS TO PULL ###
                        # Initialize player record with basic info
                        # player_record = {
                        #     'game_pk': game_pk,
                        #     'player_id': player_id.replace('ID', ''),
                        #     'player_name': player_info['person']['fullName'],
                        #     'team_name': team_name,
                        #     'is_home': True if team_type == 'home' else False
                        #     # 'did_start': None,
                        #     # 'did_pitch': None,  # Default value
                        #     # 'num_pitches': None,
                        #     # 'num_strikes': None,
                        #     # IMPLEMENT ~API~ Calls to Statcast to Grab these and parse out of CSV
                        #     # Run Value: https://baseballsavant.mlb.com/leaderboard/swing-take?year=2025&team=&leverage=Neutral&group=Pitcher&type=All&sub_type=null&min=10&csv=True
                        #     # xwOBA: https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year=2025&position=&team=&filterType=bip&min=1
                        #     # res = requests.get(url, timeout=None).content
                        #     # data = pd.read_csv(io.StringIO(res.decode('utf-8')))
                        #     # data = sanitize_statcast_columns(data)
                        #     #'run_value': None,
                        #     #'xwOBA': None
                        # }

                        # Check if player has pitching stats for this game
                        if 'stats' in player_info and 'pitching' in player_info['stats'] and player_info['stats']['pitching']:
                            player_record['didPlay'] = True
                            # need a check here that if didPlay is false, isStarter is also false
                            player_record['isStarter'] = True if player_info['stats']['pitching']['gamesStarted'] == 1 else False
                            
                            # Extract game pitching stats
                            pitching_stats = player_info['stats']['pitching']

                            # add quality starts
                            player_record['qualityStart'] = 1 if pitching_stats['outs'] >= 18 and pitching_stats['earnedRuns'] <= 3 else 0
                            
                            # Add relevant pitching stats to player record
                            pitching_fields = [
                                'hits', 'runs', 'earnedRuns', 'baseOnBalls', 'strikeOuts',
                                'homeRuns', 'pitchesThrown', 'strikes', 'balls', 'battersFaced',
                                'outs', 'completeGames', 'shutouts', 'holds', 'saves', 'blownSaves',
                                'inheritedRunners', 'inheritedRunnersScored', 'wildPitches', 'hitBatsmen',
                                'balks', 'wins', 'losses', 'pickoffs'
                            ]
                            
                            for stat in pitching_fields:
                                player_record[stat] = pitching_stats.get(stat, None)
                            
                            # adding in Hilltopper and ESPN points
                            player_record['hilltopperPts'] = (
                                player_record['outs'] +
                                player_record['earnedRuns'] * -3 +
                                player_record['wins'] * 6 +
                                player_record['losses'] * -3 +
                                player_record['saves'] * 14 +
                                player_record['blownSaves'] * -4 +
                                player_record['strikeOuts'] * 5 +
                                player_record['hits'] * -1 +
                                player_record['baseOnBalls'] * -1 +
                                player_record['hitBatsmen'] * -1 +
                                player_record['wildPitches'] * -1 +
                                player_record['balks'] * -7 +
                                player_record['pickoffs'] * 7 +
                                player_record['qualityStart'] * 8 +
                                player_record['holds'] * 7
                            )

                            player_record['espnPts'] = (
                                player_record['outs'] +
                                player_record['earnedRuns'] * -2 +
                                player_record['wins'] * 2 +
                                player_record['losses'] * -2 +
                                player_record['saves'] * 2 +
                                player_record['strikeOuts'] +
                                player_record['hits'] * -1 +
                                player_record['baseOnBalls'] * -1 +
                                player_record['holds'] * 2
                            )
                            
                        else:
                            player_record['didPlay'] = False

                        # Add player record to dataset
                        player_data.append(player_record)

                    else:
                        player_record['isPitcher'] = False

                        if 'stats' in player_info and 'batting' in player_info['stats'] and player_info['stats']['batting']:
                            player_record['didPlay'] = True
                            player_record['isStarter'] = False if player_info['gameStatus']['isSubstitute'] == True else True
                            
                            # Extract game pitching stats
                            batting_stats = player_info['stats']['batting']

                            player_record['sacrifices'] = batting_stats['sacBunts'] + batting_stats['sacFlies']
                            player_record['singles'] = batting_stats['hits'] - batting_stats['doubles'] - batting_stats['triples'] - batting_stats['homeRuns']
                            
                            # Add relevant pitching stats to player record
                            batting_fields = [
                                'doubles', 'triples', 'homeRuns', 'baseOnBalls',
                                'runs', 'rbi', 'stolenBases', 'strikeOuts', 'intentionalWalks',
                                'hitByPitch', 'caughtStealing', 'groundIntoDoublePlay', 'plateAppearances'
                            ]
                            
                            for stat in batting_fields:
                                player_record[stat] = batting_stats.get(stat, None)
                            
                            # adding in Hilltopper and ESPN points
                            player_record['hilltopperPts'] = (
                                player_record['singles'] * 2 +
                                player_record['doubles'] * 5 +
                                player_record['triples'] * 10 +
                                player_record['homeRuns'] * 14 +
                                player_record['baseOnBalls'] * 1 +
                                player_record['runs'] * 2 +
                                player_record['rbi'] * 4 +
                                player_record['stolenBases'] * 10 +
                                player_record['strikeOuts'] * -1 +
                                player_record['intentionalWalks'] * 7 +
                                player_record['hitByPitch'] * 1 +
                                player_record['caughtStealing'] * -2 +
                                player_record['groundIntoDoublePlay'] * -1 +
                                player_record['sacrifices'] * 1
                            )

                            player_record['espnPts'] = (
                                player_record['singles'] +
                                player_record['doubles'] * 2 +
                                player_record['triples'] * 3 +
                                player_record['homeRuns'] * 4 +
                                player_record['baseOnBalls'] +
                                player_record['runs'] +
                                player_record['rbi'] +
                                player_record['stolenBases'] +
                                player_record['strikeOuts'] * -1
                            )

                        else:
                            player_record['didPlay'] = False

                        # Add player record to dataset
                        player_data.append(player_record)
            
        except requests.exceptions.RequestException as e:
            print(f"Error retrieving data for game {game_pk}: {e}")

    # Convert to DataFrame
    df = pd.DataFrame(player_data)
    
    # Convert numeric columns from strings to appropriate types
    numeric_columns = ['hits', 'runs', 'earnedRuns', 'baseOnBalls', 'strikeOuts',
                       'homeRuns', 'pitchesThrown', 'strikes', 'balls', 'battersFaced',
                       'outs', 'completeGames', 'shutouts', 'holds', 'saves', 'blownSaves',
                       'qualityStart', 'inheritedRunners', 'inheritedRunnersScored', 'wildPitches',
                       'hitBatsmen', 'balks', 'wins', 'losses', 'sacrifices', 'singles', 'doubles',
                       'triples', 'homeRuns', 'baseOnBalls', 'runs', 'rbi', 'stolenBases', 'strikeOuts',
                       'intentionalWalks', 'hitByPitch', 'caughtStealing', 'groundIntoDoublePlay',
                       'plateAppearances', 'hilltopperPts', 'espnPts', 'pickoffs', 'gamePk', 'gameDuration']
    
    df['isStarter'] = pd.to_numeric(df['isStarter'], errors='coerce').astype('bool')

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')

    df = pd.merge(df, game_details, on='gamePk', how='left')

    return df

# if __name__ == "__main__":
#     # Test with a sample game
#     game_pks = [718780]
#     pitcher_df = get_player_game_logs(game_pks)
#     print(pitcher_df)
    
#     # Display the results
#     # print(f"Found {len(pitcher_df)} pitcher records")
#     # print(f"Pitchers who pitched: {pitcher_df['did_p'].sum()}")
    
#     # # Show pitchers who actually pitched in this game
#     # print("\nPitchers who pitched in this game:")
#     # if 'inningsPitched' in pitcher_df.columns:
#     #     print(pitcher_df[pitcher_df['did_pitch']][['player_name', 'team', 'inningsPitched', 'earnedRuns', 'strikeOuts', 'baseOnBalls']])
#     # else:
#     #     print(pitcher_df[pitcher_df['did_pitch']][['player_name', 'team']])
    
#     # Save to CSV for further analysis
#     pitcher_df.to_csv(f"pitcher_game_logs_{game_pks[0]}.csv", index=False)