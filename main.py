import os
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import datetime
# Import your helper modules as needed
# from helpers.data_processor import process_data
# from helpers.api_client import fetch_data
from helpers.getLastNightGames import get_last_night_games
from helpers.getPlayerGameLogs import get_player_game_logs
from helpers.sendEmail import send_email
from helpers.getGames import get_games
from helpers.writeBigQuery import write_to_big_query
from helpers.getLikelyPitchers import get_likely_pitchers
from helpers.postPicks import post_picks

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/")
async def run_script():
    try:
        # Your dataframe building logic goes here
        #df = build_dataframe()
        ## I SHOULD DELETE THIS FUNCTION AND PASS IN THE MOST RECENT DATES
        ## TO GETGAMES.PY - THEN HAVE A SEPARATE EMAIL SENDING FUNCTION

        last_nights_game_pks = get_last_night_games()
        print("Last Nights Games: ", last_nights_game_pks)
        game_logs = get_player_game_logs(last_nights_game_pks)
        print("Game logs created fine")
        write_to_big_query(game_logs, replace_or_append="append")
        print("Big Query writing was fine")
        send_email(game_logs)
        
        print("Nightly Game Script completed successfully")
        return {"message": "Nightly GameScript completed successfully"}
    except Exception as e:
        print(f"Script failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Script failed: {str(e)}")

@app.post("/post_picks")
async def post_to_social():
    try:
        likely_pitchers = get_likely_pitchers()
        post_picks(likely_pitchers)
    except Exception as e:
        print(f"Posting to socials failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Posting to socials failed: {str(e)}")
    
@app.get("/run_backfill")
async def run_backfill(start_date: str, end_date: str, background_tasks: BackgroundTasks):
    try:

        game_pks = get_games(start_date, end_date)
        game_logs = get_player_game_logs(game_pks)

        print("Game Logs Completed...")

        # This happens AFTER the response is sent
        background_tasks.add_task(write_to_big_query, game_logs, "replace")

        print("Running BigQuery drop in the background...")
        
        # Response sent immediately - no timeout!
        return {"message": "Game Logs processing started, writing to BigQuery in background"}

    except Exception as e:
        print(f"Script failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Script failed: {str(e)}")
    
class PlayerPick(BaseModel):
    playerName: str
    tier: int
    rank: int

class PickRequest(BaseModel):
    token: str
    picks: list[PlayerPick]

@app.get("/pick_pitchers", response_class=HTMLResponse)
async def pick_pitchers_form(request: Request):
    pitchers = []
    try:
        df = get_likely_pitchers()
        if df is not None and not df.empty:
            pitchers = df[['playerId', 'playerName', 'gamesRest', 'avgFantasyPts', 'boomFantasyPoints']].to_dict(orient='records')
    except Exception as e:
        print(f"Error loading pitchers: {e}")
    return templates.TemplateResponse("pick_pitchers.html", {"request": request, "pitchers": pitchers})

@app.post("/pick_pitchers")
async def pick_pitchers_submit(pick_request: PickRequest):
    form_token = os.environ.get("FORM_TOKEN")
    if not form_token or pick_request.token != form_token:
        raise HTTPException(status_code=403, detail="Invalid token")
    if not pick_request.picks:
        raise HTTPException(status_code=400, detail="No players selected")
    selected = sorted(
        [p.model_dump() for p in pick_request.picks],
        key=lambda x: (x["tier"], x["rank"])
    )
    return {"selected_players": selected}

@app.get("/callback_stub")
async def callback_stub():
    pass

def build_dataframe():
    # Your actual dataframe building code
    # This is where you'll call your helper files
    pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))