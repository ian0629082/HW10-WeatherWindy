# CWA Weather Visualization Dashboard

This project fetches Taiwan CWA dataset `O-A0001-001`, saves it as CSV, imports it into SQLite, and serves a FastAPI dashboard API.

## Setup

Install dependencies:

```powershell
pip install -r backend\requirements.txt
```

Put your CWA authorization token in `.env`:

```env
Authorization=your_token_here
```

`CWA_AUTHORIZATION=your_token_here` is also supported.
`CWA_API_KEY=your_token_here` is supported too.

## Fetch Data

```powershell
python .\fetch_weather.py
```

Outputs:

- `weather_observation.csv`
- `weather.db`

SQLite tables:

- `stations`
- `observations`
- `forecasts`
- `predictions`

Quick test with fewer rows:

```powershell
python .\fetch_weather.py --limit 5
```

## Run API and Dashboard

```powershell
python -m uvicorn backend.app.main:app --reload
```

Open:

- Dashboard: <http://127.0.0.1:8000>
- API docs: <http://127.0.0.1:8000/docs>

Implemented endpoints:

- `GET /api/health`
- `GET /api/stations`
- `GET /api/stations/{station_id}/latest`
- `GET /api/stations/{station_id}/observations`
- `GET /api/map/stations/latest`
- `GET /api/summary`
- `GET /api/stations/{station_id}/predictions`

## Deploy to Vercel

This repo includes `api/index.py` and `vercel.json` for Vercel Python Serverless deployment.

Set this environment variable in Vercel:

```env
CWA_API_KEY=your_token_here
```

Vercel does not use the local `weather.db`. In production, the API fetches live CWA data and caches it briefly in memory.

## Dashboard Features

- County/city filter
- Station map
- Temperature, humidity, rainfall, wind speed, and rain probability cards
- Station popup with latest observation values
- Rain probability baseline using current rain, humidity, weather text, pressure, and wind speed

Rain probability is a transparent baseline (`heuristic-baseline-v1`). It is not a trained ML model yet because the current database only contains one observation time. After collecting historical observations, it can be replaced with a model such as Random Forest.
