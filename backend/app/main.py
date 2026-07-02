from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "weather.db"
FRONTEND_DIR = ROOT_DIR / "frontend"


app = FastAPI(title="CWA Weather Dashboard API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail="weather.db not found. Run fetch_weather.py first.")
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
    if any(word in weather for word in ("雨", "雷", "陣雨", "豪雨")):
        score += 35.0
    if any(word in weather for word in ("陰", "多雲")):
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


@app.get("/api/health")
def health():
    counts = one(
        """
        SELECT
            (SELECT COUNT(*) FROM stations) AS stations,
            (SELECT COUNT(*) FROM observations) AS observations
        """
    )
    return {"status": "ok", **counts}


@app.get("/api/stations")
def stations(
    county: str | None = None,
    q: str | None = Query(default=None, description="Search station, county, or town name"),
    limit: int = Query(default=1000, ge=1, le=2000),
):
    sql = """
        SELECT station_id, station_name, county_name, town_name, latitude, longitude, altitude
        FROM stations
        WHERE (? IS NULL OR county_name = ?)
          AND (? IS NULL OR station_name LIKE ? OR county_name LIKE ? OR town_name LIKE ?)
        ORDER BY county_name, town_name, station_name
        LIMIT ?
    """
    like = f"%{q}%" if q else None
    return rows(sql, (county, county, q, like, like, like, limit))


@app.get("/api/stations/{station_id}/latest")
def station_latest(station_id: str):
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
    return with_rain_probability(rows(
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
    ))


@app.get("/api/map/stations/latest")
def map_stations_latest():
    return with_rain_probability(rows(
        """
        SELECT s.station_id, s.station_name, s.county_name, s.town_name,
               s.latitude AS lat, s.longitude AS lon,
               o.observation_time, o.weather, o.air_temperature, o.relative_humidity,
               o.precipitation, o.wind_speed, o.wind_direction, o.air_pressure
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
    ))


@app.get("/api/summary")
def summary():
    result = one(
        """
        SELECT
            COUNT(*) AS station_count,
            ROUND(AVG(air_temperature), 1) AS avg_temperature,
            ROUND(AVG(relative_humidity), 1) AS avg_humidity,
            ROUND(SUM(precipitation), 1) AS total_precipitation,
            ROUND(AVG(wind_speed), 1) AS avg_wind_speed,
            MAX(observation_time) AS latest_observation_time
        FROM observations
        """
    )
    latest = rows(
        """
        SELECT weather, relative_humidity, precipitation, wind_speed, air_pressure
        FROM observations
        WHERE observation_time = (SELECT MAX(observation_time) FROM observations)
        """
    )
    probabilities = [rain_probability(record) for record in latest]
    result["avg_rain_probability"] = round(sum(probabilities) / len(probabilities), 1) if probabilities else None
    result["rain_probability_model"] = "heuristic-baseline-v1"
    return result


@app.get("/api/stations/{station_id}/predictions")
def station_predictions(station_id: str, target: str = "air_temperature"):
    if target == "rain_probability":
        latest = one(
            """
            SELECT station_id, observation_time, weather, relative_humidity,
                   precipitation, wind_speed, air_pressure
            FROM observations
            WHERE station_id = ?
            ORDER BY observation_time DESC
            LIMIT 1
            """,
            (station_id,),
        )
        if not latest:
            raise HTTPException(status_code=404, detail="No data for station or target")
        return {
            "station_id": station_id,
            "target_name": target,
            "model_name": "heuristic-baseline-v1",
            "predict_time": latest["observation_time"],
            "predicted_value": rain_probability(latest),
            "unit": "%",
            "note": "Uses current rain, humidity, weather text, pressure, and wind speed.",
        }

    latest = one(
        f"""
        SELECT station_id, observation_time, {target} AS value
        FROM observations
        WHERE station_id = ? AND {target} IS NOT NULL
        ORDER BY observation_time DESC
        LIMIT 1
        """,
        (station_id,),
    ) if target in {"air_temperature", "relative_humidity", "precipitation", "wind_speed", "air_pressure"} else None
    if not latest:
        raise HTTPException(status_code=404, detail="No data for station or target")
    return {
        "station_id": station_id,
        "target_name": target,
        "model_name": "latest-value-baseline",
        "predict_time": latest["observation_time"],
        "predicted_value": latest["value"],
        "note": "Baseline placeholder for the ML phase.",
    }
