# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based fantasy baseball analytics application called "Waiver Wire Winner" that provides data-driven recommendations for fantasy baseball player pickups. The application:

- Fetches MLB game data and player statistics from multiple APIs (MLB Stats API, ESPN Fantasy API)
- Analyzes player performance metrics and rest patterns using custom fantasy scoring algorithms
- Identifies high-value, low-ownership players available on the waiver wire
- Sends automated email recommendations and social media posts
- Processes and stores historical data in Google BigQuery for analysis

## Architecture & Key Components

### Main Application (FastAPI)
- `main.py`: FastAPI web server with endpoints for nightly processing, manual backfills, and social media posting
- Deployed on Google Cloud Run (see `Procfile` for deployment configuration)

### Core Data Processing Pipeline
- `helpers/getLastNightGames.py`: Fetches previous night's completed games
- `helpers/getPlayerGameLogs.py`: Extracts detailed player statistics from game data  
- `helpers/writeBigQuery.py`: Writes processed data to Google BigQuery warehouse
- `helpers/getLikelyPitchers.py`: Identifies pitchers likely to play based on rest patterns and availability

### Analytics & Recommendations
- `helpers/waiver_wire_winner.py`: Legacy comprehensive fantasy analysis system using SQLite
- `helpers/todays_likely_pitchers.sql`: BigQuery SQL for identifying relief pitchers ready to play
- Custom fantasy scoring algorithms for both pitchers and batters
- Sharpe ratio calculations for consistent performer identification

### Communication & Output
- `helpers/sendEmail.py`: Automated email delivery of recommendations
- `helpers/postPicks.py` & `helpers/postTwitter.py`: Social media automation
- HTML-formatted reports with player recommendations

## Development Commands

### Environment Setup
```bash
# Install dependencies
pip3 install -r requirements.txt

# Run the FastAPI application locally
python3 main.py
# OR
uvicorn main:app --host 0.0.0.0 --port 8080
```

### API Endpoints
- `GET /` - Run nightly data processing pipeline
- `POST /post_picks` - Generate and post social media recommendations  
- `GET /run_backfill?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` - Backfill historical data
- `GET /callback_stub` - Placeholder callback endpoint

### Data Sources & Dependencies
- **MLB Stats API**: Primary source for game data and player statistics
- **ESPN Fantasy API**: Player ownership percentages and availability data
- **Google BigQuery**: Data warehouse for historical analysis (requires `GCP_SA_KEY` and `PROJECT_ID` environment variables)
- **Gmail SMTP**: Email delivery (requires email credentials in environment)

### Key Files
- `helpers/mlb_team_ids.csv`: MLB team ID mappings for roster verification
- `helpers/constants.py`: Shared configuration and constants
- `documentation/`: API documentation and reference materials

## Important Notes

- The application uses both a legacy SQLite-based system (`waiver_wire_winner.py`) and a modern BigQuery-based system
- Relief pitchers are the primary focus due to their unpredictable usage patterns
- Player availability is cross-referenced with active 26-man MLB rosters
- Fantasy scoring follows custom point systems defined in the codebase
- All date parameters should be in ISO format (YYYY-MM-DD)