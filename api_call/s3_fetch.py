"""
s3_fetch.py — Fetch hourly weather observations from NOAA ISD via public S3.

Data source: s3://noaa-isd-pds/data/YEAR/USAF-WBAN-YEAR.gz
Format: ISD (Integrated Surface Database) fixed-width ASCII
Docs: s3://noaa-isd-pds/isd-format-document.pdf (--no-sign-request)

Byte positions verified against live JFK data 2025-01-01:
  - METAR reported 10°C and 14kt → ISD decoded +0100 (10.0°C) and 0072 (7.2 m/s = 25.9 km/h) ✅
"""

import gzip
import io
from datetime import datetime, timezone

import boto3
from botocore import UNSIGNED
from botocore.config import Config

# ---------------------------------------------------------------------------
# Station configuration — USAF-WBAN format, verified active Aug 2025
# ---------------------------------------------------------------------------
STATIONS = {
    "New York JFK":          "744860-94789",
    "New York Central Park": "725053-94728",
}

# ---------------------------------------------------------------------------
# ISD mandatory data — byte positions (0-indexed, Python slice notation)
# All positions include the 4-char record-length prefix at pos 0-3
# ---------------------------------------------------------------------------
DATE_START, DATE_END       = 15, 23   # YYYYMMDD
TIME_START, TIME_END       = 23, 27   # HHMM UTC
WND_SPEED_START, WND_SPEED_END = 65, 69   # 4 digits, tenths of m/s
TMP_START, TMP_END         = 87, 92   # +TTTT (signed tenths of °C) + quality flag

# ---------------------------------------------------------------------------
# NOAA sentinel values — missing data is encoded as these magic numbers
# MUST filter these before inserting or dbt not_null tests will fail
# ---------------------------------------------------------------------------
SENTINEL_TMP   = 9999   # if abs(value) >= this, temperature is missing
SENTINEL_WIND  = 9999   # if value >= this, wind speed is missing
MIN_LINE_LEN   = 93     # lines shorter than this lack the mandatory data section


def _get_s3_key(station_id: str, year: int) -> str:
    """Build the S3 object key for a given station and year."""
    return f"data/{year}/{station_id}-{year}.gz"


def _parse_line(line: str) -> dict | None:
    """
    Parse one ISD fixed-width record.
    Returns a dict with raw values, or None if the line is too short.
    """
    if len(line) < MIN_LINE_LEN:
        return None

    obs_date  = line[DATE_START:DATE_END]    # e.g. "20250101"
    obs_time  = line[TIME_START:TIME_END]    # e.g. "0051"
    wnd_raw   = line[WND_SPEED_START:WND_SPEED_END]  # e.g. "0072"
    tmp_field = line[TMP_START:TMP_END]              # e.g. "+0100" (5 chars incl. sign)

    try:
        tmp_raw = int(tmp_field)   # signed integer, tenths of °C
        wnd_val = int(wnd_raw)     # unsigned integer, tenths of m/s
    except ValueError:
        return None

    return {
        "obs_date": obs_date,
        "obs_time": obs_time,
        "tmp_raw":  tmp_raw,
        "wnd_val":  wnd_val,
    }


def _validate_row(parsed: dict, station_name: str) -> None:
    """
    Raise ValueError if NOAA sentinel values are present.
    Sentinels mean the instrument reported no data — do not insert.
    """
    if abs(parsed["tmp_raw"]) >= SENTINEL_TMP:
        raise ValueError(
            f"[{station_name}] Missing temperature — sentinel detected: {parsed['tmp_raw']}"
        )
    if parsed["wnd_val"] >= SENTINEL_WIND:
        raise ValueError(
            f"[{station_name}] Missing wind speed — sentinel detected: {parsed['wnd_val']}"
        )


def _sanity_check(temp_c: float, wind_kmh: float, station_name: str) -> None:
    """Log a warning (but don't crash) for physically impossible values."""
    if not (-80 <= temp_c <= 60):
        print(f"WARNING [{station_name}]: unusual temperature {temp_c}°C")
    if not (0 <= wind_kmh <= 400):
        print(f"WARNING [{station_name}]: unusual wind speed {wind_kmh} km/h")


def _load_raw_text(s3_client, station_id: str, year: int) -> str:
    """Stream, decompress, and decode one station's annual ISD file from S3."""
    key = _get_s3_key(station_id, year)
    print(f"Fetching s3://noaa-isd-pds/{key} ...")
    response   = s3_client.get_object(Bucket="noaa-isd-pds", Key=key)
    compressed = response["Body"].read()
    return gzip.decompress(compressed).decode("ascii", errors="replace")


def fetch_station_data(station_name: str, station_id: str) -> dict:
    """
    Fetch the most recent valid observation for one station from NOAA ISD S3.

    Strategy:
      1. Try the current year's file first.
      2. If it doesn't exist (NOAA publishing delay — typically weeks behind),
         fall back to the previous year's file.
      3. Parse all lines in the file and pick the LAST valid observation
         that passes sentinel checks. This works regardless of whether
         today's date is in the file (annual files are read-to-end).

    Returns a dict compatible with insert_records.insert_records().
    """
    s3 = boto3.client(
        "s3",
        config=Config(signature_version=UNSIGNED),
        region_name="us-east-1",
    )

    # --- Year fallback (NOAA ISD lag: current calendar year may not be published yet) ---
    now_utc  = datetime.now(timezone.utc)
    raw_text = None
    used_year = None
    for year_offset in (0, -1, -2):
        year = now_utc.year + year_offset
        try:
            raw_text  = _load_raw_text(s3, station_id, year)
            used_year = year
            print(f"  → Using {year} ISD data for {station_name}")
            break
        except Exception as e:
            print(f"  ↩ {year} not available ({type(e).__name__}), trying previous year...")

    if raw_text is None:
        raise ValueError(
            f"[{station_name}] Could not find ISD data for {station_id} "
            f"in years {now_utc.year} to {now_utc.year - 2}"
        )

    # --- Parse every line and collect those that pass sentinel validation ---
    valid_obs = []
    for line in raw_text.splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        try:
            _validate_row(parsed, station_name)
        except ValueError:
            continue   # skip rows with missing-data sentinels
        valid_obs.append(parsed)

    if not valid_obs:
        raise ValueError(f"[{station_name}] No valid (non-sentinel) observations in {used_year} file")

    # Sort by date+time — take the MOST RECENT observation in the file
    valid_obs.sort(key=lambda r: r["obs_date"] + r["obs_time"])
    best = valid_obs[-1]

    # --- Unit conversion ---
    temperature_c = best["tmp_raw"] / 10.0                      # tenths of °C → °C
    wind_kmh      = round((best["wnd_val"] / 10.0) * 3.6, 1)   # tenths of m/s → km/h

    _sanity_check(temperature_c, wind_kmh, station_name)

    # --- Build obs datetime (ISD is always UTC) ---
    obs_dt = datetime.strptime(
        f"{best['obs_date']}{best['obs_time']}", "%Y%m%d%H%M"
    ).replace(tzinfo=timezone.utc)

    print(
        f"✅ {station_name}: {temperature_c}°C  |  {wind_kmh} km/h  |  "
        f"obs={best['obs_date']} {best['obs_time']} UTC  |  "
        f"data_age={used_year}"
    )

    return {
        "location": {
            "name":       station_name,
            "localtime":  obs_dt.strftime("%Y-%m-%d %H:%M"),
            "utc_offset": "0",
        },
        "current": {
            "temperature":          temperature_c,
            "weather_descriptions": [f"NOAA ISD {used_year}"],
            "wind_speed":           wind_kmh,
        },
    }



def fetch_data() -> list[dict]:
    """
    Fetch observations for all configured stations.
    Returns a list of observation dicts — one per station.
    Called by insert_records.main().
    """
    results = []
    for name, station_id in STATIONS.items():
        try:
            obs = fetch_station_data(name, station_id)
            results.append(obs)
        except Exception as e:
            print(f"❌ Failed to fetch {name}: {e}")
    return results


# ---------------------------------------------------------------------------
# Quick standalone test — run directly inside the Airflow container:
#   docker exec airflow_container python /opt/airflow/api_call/s3_fetch.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    results = fetch_data()
    for r in results:
        loc = r["location"]
        cur = r["current"]
        print(
            f"\nStation : {loc['name']}"
            f"\nTime    : {loc['localtime']} UTC"
            f"\nTemp    : {cur['temperature']}°C"
            f"\nWind    : {cur['wind_speed']} km/h"
        )
