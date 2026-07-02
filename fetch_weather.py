import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DATASET_ID = "O-A0001-001"
API_URL = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{DATASET_ID}"
CSV_PATH = Path("weather_observation.csv")
DB_PATH = Path("weather.db")


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_authorization() -> str:
    load_env()
    token = os.getenv("CWA_API_KEY") or os.getenv("CWA_AUTHORIZATION") or os.getenv("Authorization")
    if not token:
        raise RuntimeError(
            "Missing CWA authorization. Add Authorization=your_key or CWA_API_KEY=your_key to .env."
        )
    return token


def request_json(token: str, limit: int | None = None) -> dict:
    params = {"Authorization": token, "format": "JSON"}
    if limit:
        params["limit"] = str(limit)

    url = f"{API_URL}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "weather-db-fetcher/1.0"})

    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8-sig"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CWA API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"連線 CWA API 失敗: {exc.reason}") from exc


def pick_value(data, *paths):
    for path in paths:
        value = data
        for part in path:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                value = None
                break
        if value not in (None, ""):
            return value
    return None


def weather_element_map(station: dict) -> dict:
    items = station.get("WeatherElement") or station.get("weatherElement") or {}
    if isinstance(items, dict):
        return items

    result = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("elementName") or item.get("ElementName")
            value = item.get("elementValue") or item.get("ElementValue")
            if name:
                result[name] = value
    return result


def rainfall_value(station: dict, elements: dict | None = None) -> object:
    elements = elements or {}
    value = pick_value(
        elements,
        ("Now", "Precipitation"),
        ("now", "precipitation"),
    )
    if value not in (None, ""):
        return value

    rainfall = station.get("RainfallElement") or station.get("rainfallElement") or {}
    return pick_value(
        rainfall,
        ("Now", "Precipitation"),
        ("now", "precipitation"),
        ("Past10Min", "Precipitation"),
        ("Past1hr", "Precipitation"),
    )


def coordinates(station: dict) -> tuple[object, object]:
    coords = pick_value(
        station,
        ("GeoInfo", "Coordinates"),
        ("geoInfo", "coordinates"),
    )

    if isinstance(coords, list) and coords:
        first = next(
            (
                item
                for item in coords
                if isinstance(item, dict)
                and str(item.get("CoordinateName") or item.get("coordinateName")).upper() == "WGS84"
            ),
            coords[0],
        )
        if isinstance(first, dict):
            return (
                first.get("StationLongitude") or first.get("stationLongitude"),
                first.get("StationLatitude") or first.get("stationLatitude"),
            )

    return (
        pick_value(station, ("GeoInfo", "StationLongitude"), ("geoInfo", "stationLongitude")),
        pick_value(station, ("GeoInfo", "StationLatitude"), ("geoInfo", "stationLatitude")),
    )


def payload_stations(payload: dict) -> list[dict]:
    stations = (
        pick_value(payload, ("records", "Station"))
        or pick_value(payload, ("records", "station"))
        or pick_value(payload, ("records", "location"))
        or pick_value(payload, ("cwaopendata", "dataset", "Station"))
        or []
    )
    if not isinstance(stations, list):
        raise RuntimeError("Unexpected CWA response shape: station list was not found.")
    return stations


def null_if_missing(value):
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.strip() in {"-99", "-999", "-99.0", "-999.0"}:
        return None
    return value


def to_float(value):
    value = null_if_missing(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def station_rows(payload: dict) -> list[dict]:
    stations = payload_stations(payload)

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for station in stations:
        if not isinstance(station, dict):
            continue

        elements = weather_element_map(station)
        longitude, latitude = coordinates(station)

        rows.append(
            {
                "fetched_at": fetched_at,
                "station_id": station.get("StationId") or station.get("stationId"),
                "station_name": station.get("StationName") or station.get("stationName"),
                "county_name": pick_value(station, ("GeoInfo", "CountyName"), ("geoInfo", "countyName")),
                "town_name": pick_value(station, ("GeoInfo", "TownName"), ("geoInfo", "townName")),
                "longitude": to_float(longitude),
                "latitude": to_float(latitude),
                "altitude": to_float(pick_value(station, ("GeoInfo", "StationAltitude"), ("geoInfo", "stationAltitude"))),
                "observation_time": pick_value(station, ("ObsTime", "DateTime"), ("obsTime", "dateTime")),
                "weather": null_if_missing(elements.get("Weather")),
                "air_temperature": to_float(elements.get("AirTemperature")),
                "relative_humidity": to_float(elements.get("RelativeHumidity")),
                "wind_direction": to_float(elements.get("WindDirection")),
                "wind_speed": to_float(elements.get("WindSpeed")),
                "air_pressure": to_float(elements.get("AirPressure")),
                "precipitation": to_float(rainfall_value(station, elements)),
                "peak_gust_speed": to_float(pick_value(elements, ("GustInfo", "PeakGustSpeed"), ("gustInfo", "peakGustSpeed"))),
                "raw_json": json.dumps(station, ensure_ascii=False, sort_keys=True),
            }
        )
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        raise RuntimeError("No rows to write to CSV.")

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_sqlite(rows: list[dict], db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            DROP TABLE IF EXISTS weather_observations;
            DROP TABLE IF EXISTS predictions;
            DROP TABLE IF EXISTS forecasts;
            DROP TABLE IF EXISTS observations;
            DROP TABLE IF EXISTS stations;

            CREATE TABLE stations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT UNIQUE NOT NULL,
                station_name TEXT NOT NULL,
                county_name TEXT,
                town_name TEXT,
                latitude REAL,
                longitude REAL,
                altitude REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT NOT NULL,
                observation_time DATETIME NOT NULL,
                weather TEXT,
                air_temperature REAL,
                relative_humidity REAL,
                precipitation REAL,
                wind_speed REAL,
                wind_direction REAL,
                air_pressure REAL,
                peak_gust_speed REAL,
                raw_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(station_id, observation_time),
                FOREIGN KEY(station_id) REFERENCES stations(station_id)
            );

            CREATE TABLE forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                county_name TEXT,
                town_name TEXT,
                forecast_time DATETIME,
                element_name TEXT,
                element_value TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT NOT NULL,
                target_name TEXT NOT NULL,
                predict_time DATETIME NOT NULL,
                predicted_value REAL,
                model_name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(station_id) REFERENCES stations(station_id)
            );

            CREATE INDEX idx_stations_county ON stations(county_name);
            CREATE INDEX idx_observations_station_time ON observations(station_id, observation_time);
            """
        )

        conn.executemany(
            """
            INSERT INTO stations (
                station_id, station_name, county_name, town_name, latitude, longitude, altitude
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["station_id"],
                    row["station_name"],
                    row["county_name"],
                    row["town_name"],
                    row["latitude"],
                    row["longitude"],
                    row["altitude"],
                )
                for row in rows
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO observations (
                station_id, observation_time, weather, air_temperature, relative_humidity,
                precipitation, wind_speed, wind_direction, air_pressure, peak_gust_speed, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["station_id"],
                    row["observation_time"],
                    row["weather"],
                    row["air_temperature"],
                    row["relative_humidity"],
                    row["precipitation"],
                    row["wind_speed"],
                    row["wind_direction"],
                    row["air_pressure"],
                    row["peak_gust_speed"],
                    row["raw_json"],
                )
                for row in rows
            ],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch CWA O-A0001-001 weather data to CSV and SQLite.")
    parser.add_argument("--csv", default=str(CSV_PATH), help="CSV output path")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite database output path")
    parser.add_argument("--limit", type=int, help="Optional API row limit for quick tests")
    args = parser.parse_args()

    try:
        token = get_authorization()
        payload = request_json(token, args.limit)
        rows = station_rows(payload)
        write_csv(rows, Path(args.csv))
        write_sqlite(rows, Path(args.db))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Saved {len(rows)} rows to {args.csv} and {args.db}")
    print("SQLite tables: stations, observations, forecasts, predictions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
