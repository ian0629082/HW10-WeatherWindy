from pathlib import Path
import os
import sqlite3
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fetch_weather import get_authorization, load_env, request_json, station_rows


ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "weather.db"
FRONTEND_DIR = ROOT_DIR / "frontend"
LIVE_CACHE_SECONDS = 600
_live_cache: dict[str, object] = {"expires_at": 0.0, "rows": []}


app = FastAPI(title="CWA Weather Dashboard API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def db_available() -> bool:
    return DB_PATH.exists()


def connect() -> sqlite3.Connection:
    if not db_available():
        raise HTTPException(status_code=500, detail="weather.db not found and CWA live fallback failed.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def rows(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def live_weather_rows() -> list[dict]:
    now = time.time()
    if now < float(_live_cache["expires_at"]):
        return list(_live_cache["rows"])

    token = get_authorization()
    payload = request_json(token)
    records = station_rows(payload)
    _live_cache["rows"] = records
    _live_cache["expires_at"] = now + LIVE_CACHE_SECONDS
    return list(records)


def latest_source_rows() -> list[dict]:
    if db_available():
        return rows(
            """
            SELECT s.station_id, s.station_name, s.county_name, s.town_name,
                   s.latitude, s.longitude, s.latitude AS lat, s.longitude AS lon,
                   o.observation_time, o.weather, o.air_temperature, o.relative_humidity,
                   o.precipitation, o.wind_speed, o.wind_direction, o.air_pressure,
                   o.peak_gust_speed
            FROM stations s
            JOIN observations o ON o.station_id = s.station_id
            WHERE s.latitude IS NOT NULL
              AND s.longitude IS NOT NULL
              AND o.observation_time = (
                  SELECT MAX(o2.observation_time)
                  FROM observations o2
                  WHERE o2.station_id = s.station_id
              )
            ORDER BY s.county_name, s.town_name, s.station_name
            """
        )

    records = []
    for record in live_weather_rows():
        item = dict(record)
        item["lat"] = item.get("latitude")
        item["lon"] = item.get("longitude")
        records.append(item)
    return records


def values(records: list[dict], key: str) -> list[float]:
    return [record[key] for record in records if record.get(key) is not None]


def rain_probability(record: dict) -> float:
    """Transparent baseline until enough time-series data exists for ML."""
    weather = str(record.get("weather") or "")
    humidity = record.get("relative_humidity")
    precipitation = record.get("precipitation")
    pressure = record.get("air_pressure")
    wind_speed = record.get("wind_speed")

    score = 8.0
    if precipitation is not None and precipitation > 0:
        score += 55.0
    if any(word in weather for word in ("\u96e8", "\u96f7", "\u9663\u96e8", "\u8c6a\u96e8")):
        score += 35.0
    if any(word in weather for word in ("\u9670", "\u591a\u96f2")):
        score += 8.0
    if humidity is not None:
        score += max(0.0, min(30.0, (humidity - 62.0) * 0.9))
    if pressure is not None:
        score += max(0.0, min(12.0, (1008.0 - pressure) * 0.8))
    if wind_speed is not None:
        score += max(0.0, min(8.0, (wind_speed - 4.0) * 1.5))

    return round(max(0.0, min(100.0, score)), 1)


def with_rain_probability(records: list[dict] | dict | None):
    if records is None:
        return None
    if isinstance(records, dict):
        records["rain_probability"] = rain_probability(records)
        records["rain_probability_model"] = "heuristic-baseline-v1"
        return records
    for record in records:
        record["rain_probability"] = rain_probability(record)
        record["rain_probability_model"] = "heuristic-baseline-v1"
    return records


@app.get("/")
def dashboard():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        return {"message": "CWA Weather Dashboard API", "docs": "/docs"}
    return FileResponse(index_path)


@app.get("/api/config")
def config():
    load_env()
    windy_api_key = os.getenv("VITE_WINDY_API_KEY") or os.getenv("WINDY_API_KEY") or ""
    return {"windy_api_key": windy_api_key}


@app.get("/api/health")
def health():
    if db_available():
        counts = one(
            """
            SELECT
                (SELECT COUNT(*) FROM stations) AS stations,
                (SELECT COUNT(*) FROM observations) AS observations
            """
        )
        return {"status": "ok", "source": "sqlite", **counts}

    records = live_weather_rows()
    return {"status": "ok", "source": "cwa-live", "stations": len(records), "observations": len(records)}


@app.get("/api/stations")
def stations(
    county: str | None = None,
    q: str | None = Query(default=None, description="Search station, county, or town name"),
    limit: int = Query(default=1000, ge=1, le=2000),
):
    if not db_available():
        records = latest_source_rows()
        if county:
            records = [record for record in records if record.get("county_name") == county]
        if q:
            records = [
                record
                for record in records
                if q in str(record.get("station_name") or "")
                or q in str(record.get("county_name") or "")
                or q in str(record.get("town_name") or "")
            ]
        return records[:limit]

    like = f"%{q}%" if q else None
    return rows(
        """
        SELECT station_id, station_name, county_name, town_name, latitude, longitude, altitude
        FROM stations
        WHERE (? IS NULL OR county_name = ?)
          AND (? IS NULL OR station_name LIKE ? OR county_name LIKE ? OR town_name LIKE ?)
        ORDER BY county_name, town_name, station_name
        LIMIT ?
        """,
        (county, county, q, like, like, like, limit),
    )


@app.get("/api/stations/{station_id}/latest")
def station_latest(station_id: str):
    if not db_available():
        result = next((record for record in latest_source_rows() if record.get("station_id") == station_id), None)
        if not result:
            raise HTTPException(status_code=404, detail="Station not found")
        return with_rain_probability(result)

    result = one(
        """
        SELECT s.station_id, s.station_name, s.county_name, s.town_name, s.latitude, s.longitude,
               o.observation_time, o.weather, o.air_temperature, o.relative_humidity,
               o.precipitation, o.wind_speed, o.wind_direction, o.air_pressure, o.peak_gust_speed
        FROM stations s
        JOIN observations o ON o.station_id = s.station_id
        WHERE s.station_id = ?
        ORDER BY o.observation_time DESC
        LIMIT 1
        """,
        (station_id,),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Station not found")
    return with_rain_probability(result)


@app.get("/api/stations/{station_id}/observations")
def station_observations(
    station_id: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(default=200, ge=1, le=5000),
):
    if not db_available():
        result = next((record for record in latest_source_rows() if record.get("station_id") == station_id), None)
        return with_rain_probability([result] if result else [])

    return with_rain_probability(
        rows(
            """
            SELECT station_id, observation_time, weather, air_temperature, relative_humidity,
                   precipitation, wind_speed, wind_direction, air_pressure, peak_gust_speed
            FROM observations
            WHERE station_id = ?
              AND (? IS NULL OR observation_time >= ?)
              AND (? IS NULL OR observation_time <= ?)
            ORDER BY observation_time
            LIMIT ?
            """,
            (station_id, start, start, end, end, limit),
        )
    )


@app.get("/api/map/stations/latest")
def map_stations_latest():
    return with_rain_probability(
        [
            record
            for record in latest_source_rows()
            if record.get("lat") is not None and record.get("lon") is not None
        ]
    )


@app.get("/api/summary")
def summary():
    records = latest_source_rows()
    probabilities = [rain_probability(record) for record in records]
    times = sorted(record.get("observation_time") for record in records if record.get("observation_time"))
    temperatures = values(records, "air_temperature")
    humidities = values(records, "relative_humidity")
    rainfalls = values(records, "precipitation")
    winds = values(records, "wind_speed")

    return {
        "station_count": len(records),
        "avg_temperature": round(sum(temperatures) / len(temperatures), 1) if temperatures else None,
        "avg_humidity": round(sum(humidities) / len(humidities), 1) if humidities else None,
        "total_precipitation": round(sum(rainfalls), 1) if rainfalls else None,
        "avg_wind_speed": round(sum(winds) / len(winds), 1) if winds else None,
        "latest_observation_time": times[-1] if times else None,
        "avg_rain_probability": round(sum(probabilities) / len(probabilities), 1) if probabilities else None,
        "rain_probability_model": "heuristic-baseline-v1",
    }


@app.get("/api/stations/{station_id}/predictions")
def station_predictions(station_id: str, target: str = "air_temperature"):
    latest = next((record for record in latest_source_rows() if record.get("station_id") == station_id), None)
    if not latest:
        raise HTTPException(status_code=404, detail="No data for station or target")

    if target == "rain_probability":
        return {
            "station_id": station_id,
            "target_name": target,
            "model_name": "heuristic-baseline-v1",
            "predict_time": latest["observation_time"],
            "predicted_value": rain_probability(latest),
            "unit": "%",
            "note": "Uses current rain, humidity, weather text, pressure, and wind speed.",
        }

    if target not in {"air_temperature", "relative_humidity", "precipitation", "wind_speed", "air_pressure"}:
        raise HTTPException(status_code=404, detail="No data for station or target")
    if latest.get(target) is None:
        raise HTTPException(status_code=404, detail="No data for station or target")

    return {
        "station_id": station_id,
        "target_name": target,
        "model_name": "latest-value-baseline",
        "predict_time": latest["observation_time"],
        "predicted_value": latest[target],
        "note": "Baseline placeholder for the ML phase.",
    }
