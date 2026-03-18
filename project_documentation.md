# Weather Data Pipeline — Full User Manual

## Table of Contents
1. [Overview](#1-overview)
2. [Prerequisites & WSL2 Setup](#2-prerequisites--wsl2-setup)
3. [Install Docker in WSL](#3-install-docker-in-wsl)
4. [Clone & Configure the Project](#4-clone--configure-the-project)
5. [Understanding the API & Testing It](#5-understanding-the-api--testing-it)
6. [Start the Pipeline](#6-start-the-pipeline)
7. [Verifying PostgreSQL Data](#7-verifying-postgresql-data)
8. [Airflow — DAG Overview & Monitoring](#8-airflow--dag-overview--monitoring)
9. [dbt — Transformations & Data Quality](#9-dbt--transformations--data-quality)
10. [Superset — Connecting & Building Dashboards](#10-superset--connecting--building-dashboards)
11. [Troubleshooting Guide](#11-troubleshooting-guide)
12. [Useful Commands Reference](#12-useful-commands-reference)

---

## 1. Overview

An end-to-end, fully automated data pipeline that:
- Reads hourly weather observations from the **NOAA ISD public dataset on AWS S3** (no API key required)
- Inserts raw data into **PostgreSQL** with a dedup guard
- Transforms and models it using **dbt** (staging → mart layer, with null handling)
- Validates data quality with **26 dbt tests** after every run
- Orchestrates every step automatically with **Apache Airflow 3.0** on an hourly schedule
- Visualises it with **Apache Superset** auto-refreshing dashboards

Everything runs in Docker containers, spun up with a single `docker-compose up` command.

**Data Flow:**
```
NOAA ISD — AWS Public S3
(s3://noaa-isd-pds/data/YEAR/STATION-YEAR.gz)
    │  every hour (Airflow schedules)
    ▼
① ingest_data_task  (PythonOperator)
    │  s3_fetch.py → boto3 anonymous S3 read
    │  ISD fixed-width parse → sentinel validation → dedup guard
    ▼
  dev.raw_weather_data  (Postgres)
  [New York JFK + New York Central Park]
    │
    ▼
② transform_data_task (dbt run)
    │  Null coalescing + dedup + data_quality_flag
    ├──  dev.stg_weather_data          (cleaned + deduplicated)
    ├──  dev.daily_average             (aggregated per city/day)
    └──  dev.weather_analytics_reports (reporting layer)
    │
    ▼
③ validate_data_task (dbt test)
    │  26 tests: not_null, unique, accepted_values
    │  ✅ PASS=26 / WARN=0 / ERROR=0
    ▼
  Apache Superset Dashboard
  (auto-refresh every hour)
```

**Tech Stack:**

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.x | Data ingestion scripts |
| boto3 | latest | Anonymous S3 access (NOAA public bucket, no credentials) |
| Apache Airflow | 3.0.0 | Pipeline orchestration |
| dbt-postgres | 1.9.latest | Data transformation |
| PostgreSQL | 14.17 | Data warehouse |
| Apache Superset | 3.0.0-py310 | BI dashboards |
| Redis | 7 | Superset cache backend |
| Docker + Compose | latest | Container infrastructure |

---

## 2. Prerequisites & WSL2 Setup

### Enable WSL2 on Windows

**Option A — PowerShell (classic method)**

Open **PowerShell as Administrator** and run:

```powershell
# Enable WSL feature
wsl --install

# Set WSL2 as default
wsl --set-default-version 2

# Install Ubuntu 24.04
wsl --install -d Ubuntu-24.04
```

Restart your computer when prompted. After restart, open **Ubuntu** from the Start menu. It will ask you to create a UNIX username and password — remember these, you'll need them for `sudo`.

---

**Option B — VS Code (recommended if you already use VS Code)**

This is the method used when building this project:

1. Install the **Remote Development** extension pack in VS Code
2. Click the **blue `><` icon** in the bottom-left corner of VS Code
3. Select **"Connect to WSL with Distro..."**
4. Choose **Ubuntu-24.04** (it will install it automatically if not present)
5. VS Code reconnects and you're now inside Linux — open the integrated terminal and you're ready

> [!TIP]
> The VS Code method gives you full IDE features (file explorer, extensions, Git integration) running natively inside WSL2, which is the best development experience for this project.

### Verify WSL2 is Running

```bash
wsl --list --verbose
# Should show Ubuntu-24.04 with VERSION 2
```

### Update Ubuntu packages

```bash
sudo apt update && sudo apt upgrade -y
```

---

## 3. Install Docker in WSL

```bash
# Download and run the official Docker install script
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add your user to the docker group (so you don't need sudo every time)
sudo usermod -aG docker $USER
newgrp docker

# Verify Docker works
docker --version
docker run hello-world
```

> [!NOTE]
> If you have **Docker Desktop for Windows**, make sure WSL2 integration is enabled:
> Docker Desktop → Settings → Resources → WSL Integration → Enable for Ubuntu-24.04

### Fix Docker Socket Permissions (WSL-specific)

> ⚠️ Run this **once per WSL session** after every system restart:

```bash
sudo chmod 666 /var/run/docker.sock
```

This is required so the Airflow container can spin up Docker containers (for dbt) without `PermissionError`.

---

## 4. Clone & Configure the Project

### Clone the Repo

```bash
git clone https://github.com/srujankrishnaa/weather-data-automated-etl-pipeline.git
cd weather-data-automated-etl-pipeline
```

### Set Up the Superset Environment File

```bash
cp docker/.env.example docker/.env
```

Open `docker/.env` and change `SUPERSET_SECRET_KEY` to any random string:
```bash
SUPERSET_SECRET_KEY=my_super_secret_random_key_123
```

### Set Your Weatherstack API Key

Get a free API key from [weatherstack.com](https://weatherstack.com) (free tier = 100 calls/month).

Open `api_call/request_call.py` and replace the placeholder:
```python
api_key = "YOUR_WEATHERSTACK_API_KEY"
```

---

## 5. Understanding the Data Source

### What is NOAA ISD?

NOAA ISD (Integrated Surface Database) is a global archive of hourly surface weather observations maintained by the US National Oceanic and Atmospheric Administration. It's available as a **free public dataset on AWS S3** — no account or API key needed.

```
S3 bucket:  s3://noaa-isd-pds/
Path:       data/YEAR/USAF-WBAN-YEAR.gz
Example:    data/2025/744860-94789-2025.gz  ← JFK Airport 2025
```

Each file is a gzip-compressed fixed-width text file with one row per observation (roughly hourly).

### Stations used in this pipeline

| Station | USAF | WBAN | S3 Key |
|---|---|---|---|
| New York JFK Airport | 744860 | 94789 | `744860-94789` |
| New York Central Park | 725053 | 94728 | `725053-94728` |

### Key ISD Fixed-Width Field Positions (0-indexed)

| Field | Bytes | Format | Example | Decoded |
|---|---|---|---|---|
| Date | 15–22 | YYYYMMDD | `20250827` | Aug 27, 2025 |
| Time | 23–26 | HHMM UTC | `0351` | 03:51 UTC |
| Wind speed | 65–68 | tenths of m/s | `0041` | 4.1 m/s = 14.8 km/h |
| Temperature | 87–91 | signed tenths of °C | `+0206` | 20.6°C |

### Sentinel values (missing data indicators)

NOAA uses magic numbers for missing data — **these must be rejected before inserting**:
- Temperature `>= 9999` → skip row
- Wind speed `>= 9999` → skip row

`s3_fetch.py` handles all of this automatically.

### Test the data source manually

```bash
# Stream the JFK 2025 file and show first 3 lines
curl -s "https://noaa-isd-pds.s3.amazonaws.com/data/2025/744860-94789-2025.gz" | gunzip | head -3

# Run the standalone fetch script inside the Airflow container
docker exec airflow_container python /opt/airflow/api_call/s3_fetch.py
```

---

## 6. Start the Pipeline

### Start Everything

```bash
cd ~/weather-data-automated-etl-pipeline
sudo chmod 666 /var/run/docker.sock   # Required on WSL after restart
docker-compose up
```

First run downloads images (~2–3 GB). Subsequent starts take ~30 seconds.

### Service URLs

| Service | URL | Notes |
|---|---|---|
| **Airflow UI** | http://localhost:8000 | Username: `admin` |
| **Superset UI** | http://localhost:8088 | `admin` / `admin` |
| **PostgreSQL** | `localhost:5000` | `db_user` / `db_password` |

### Get Airflow Password

Airflow auto-generates a password on first start:

```bash
docker exec airflow_container cat /opt/airflow/simple_auth_manager_passwords.json.generated
```

### Stop Everything

```bash
docker-compose down          # Stop containers — data is preserved ✅
docker-compose down -v       # Stop + delete ALL volumes ⚠️ (full wipe)
```

---

## 7. Verifying PostgreSQL Data

Connect to Postgres inside the container:

```bash
docker exec -it postgres_container psql -U db_user -d db
```

Useful queries:

```sql
-- See raw ingested data (most recent first)
SELECT * FROM dev.raw_weather_data ORDER BY inserted_at DESC LIMIT 10;

-- See deduplicated staging data with data quality flags
SELECT * FROM dev.stg_weather_data LIMIT 10;

-- Check how many rows had nulls fixed vs clean
SELECT data_quality_flag, COUNT(*) as count
FROM dev.stg_weather_data
GROUP BY data_quality_flag
ORDER BY count DESC;

-- See daily aggregated averages per city
SELECT * FROM dev.daily_average;

-- See reporting table
SELECT * FROM dev.weather_analytics_reports;

-- View all tables in the dev schema
\dt dev.*
```

---

## 8. Airflow — DAG Overview & Monitoring

### The DAG: `weather-api-dbt-orchestrator`

File: `airflow/dags/orchestrator.py`

The DAG has **three tasks** chained sequentially (`task1 >> task2 >> task3`):

1. **`ingest_data_task`** (`PythonOperator`)
   - Calls `insert_records.main()` which imports `s3_fetch.fetch_data()`
   - `s3_fetch` connects to `s3://noaa-isd-pds/` anonymously via boto3
   - Tries current year first, falls back to prior year if not yet published
   - Parses ISD fixed-width format, validates sentinels, converts units
   - Dedup guard prevents re-inserting the same station + hour
   - Runs in ~20–40 seconds (streams ~10 MB file per station)

2. **`transform_data_task`** (`BashOperator`)
   - Runs `docker run ... ghcr.io/dbt-labs/dbt-postgres:1.9.latest run`
   - Applies null coalescing, deduplication, and builds all 3 dbt models
   - Runs in ~10–15 seconds

3. **`validate_data_task`** (`BashOperator`)
   - Runs `docker run ... ghcr.io/dbt-labs/dbt-postgres:1.9.latest test`
   - Executes all 26 dbt schema tests across source, staging, and mart layers
   - Fails the DAG run visibly if any data quality check fails
   - Runs in ~3–5 seconds

Schedule: every **1 hour** (`schedule=timedelta(hours=1)`)

### Monitoring in the UI

1. Go to **http://localhost:8000** → login
2. **Dags** list → find `weather-api-dbt-orchestrator` — toggle should be ON (blue)
3. Click the DAG → **Runs** tab → each row is one 5-min execution
4. Click a run → **Task Instances** → all three tasks show ✅ **success** when working

### Manual Trigger

```bash
docker exec airflow_container airflow dags trigger weather-api-dbt-orchestrator
```

### Check DAG Parse Status

```bash
docker logs airflow_container 2>&1 | grep "dags-folder  orchestrator"
# Look for: # DAGs  # Errors
# Good:    1         0
```

---

## 9. dbt — Transformations & Data Quality

### Models Overview

```
dbt/models/
├── staging/
│   ├── sources.yml         → Registers source + not_null/unique tests on raw_weather_data
│   ├── schema.yml          → Column-level tests for stg_weather_data
│   └── stg_weather_data.sql → Null handling, deduplication → staging table
└── mart/
    ├── schema.yml                        → Column-level tests for mart models
    ├── daily_average.sql                 → AVG(temperature), AVG(wind_speed) per city/day
    └── weather_analytics_reports.sql     → Key columns for reporting
```

### Null & Missing Value Handling (`stg_weather_data.sql`)

Before deduplication, all nullable columns are coalesced to safe defaults:

| Column | Strategy |
|---|---|
| `city` | `COALESCE(NULLIF(TRIM(city), ''), 'Unknown')` |
| `temperature` | `COALESCE(temperature, 0)` |
| `wind_speed` | `COALESCE(wind_speed, 0)` |
| `weather_descriptions` | `COALESCE(NULLIF(TRIM(...), ''), 'N/A')` |
| `time` | `COALESCE(time::text, to_char(inserted_at, 'YYYY-MM-DD HH24:MI'))` |

Every row is tagged with a `data_quality_flag` column:
- `'clean'` — no nulls were found
- `'fixed: city null'`, `'fixed: temperature null'` etc. — describes what was fixed

This lets you monitor data quality trends over time directly in Superset.

### Deduplication Logic

After cleaning, rows are deduplicated using `ROW_NUMBER()` — keeping only the latest record per `time` window:

```sql
de_dup AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY time ORDER BY inserted_at DESC) AS rn
    FROM cleaned
)
SELECT ... FROM de_dup WHERE rn = 1
```

### Data Quality Tests (26 total — defined in schema.yml files)

| Layer | File | Tests |
|---|---|---|
| Source | `staging/sources.yml` | `not_null` on 6 columns, `unique` on `id` |
| Staging | `staging/schema.yml` | `not_null` on all columns, `unique` on `id`, `accepted_values` on `data_quality_flag` |
| Mart | `mart/schema.yml` | `not_null` on all columns of `daily_average` + `weather_analytics_reports` |

Tests run automatically after every `dbt run` via `validate_data_task` in Airflow.

### Run dbt Manually

```bash
# Run all models
docker run --rm --network data_project_my_network \
  -v /home/srujan/repos/data_project/dbt:/usr/app -w /usr/app \
  -e DBT_PROFILES_DIR=/usr/app \
  ghcr.io/dbt-labs/dbt-postgres:1.9.latest run

# Full refresh (drops + recreates all tables)
docker run --rm --network data_project_my_network \
  -v /home/srujan/repos/data_project/dbt:/usr/app -w /usr/app \
  -e DBT_PROFILES_DIR=/usr/app \
  ghcr.io/dbt-labs/dbt-postgres:1.9.latest run --full-refresh

# Run data quality tests
docker run --rm --network data_project_my_network \
  -v /home/srujan/repos/data_project/dbt:/usr/app -w /usr/app \
  -e DBT_PROFILES_DIR=/usr/app \
  ghcr.io/dbt-labs/dbt-postgres:1.9.latest test
# Expected: PASS=26 WARN=0 ERROR=0
```

---

## 10. Superset — Connecting & Building Dashboards

### Login

Go to **http://localhost:8088** → `admin` / `admin`

### Connect Superset to PostgreSQL (one-time setup)

1. **Settings** (top right) → **Database Connections** → **+ Database**
2. Select **PostgreSQL**
3. Fill in:
   - **Host**: `db`
   - **Port**: `5432`
   - **Database name**: `db`
   - **Username**: `db_user`
   - **Password**: `db_password`
4. Click **Test Connection** → should show ✅ Connected
5. Click **Connect**

### Add Datasets

1. **Datasets** → **+ Dataset**
2. Select your Postgres connection → Schema: `dev`
3. Add these tables:
   - `stg_weather_data`
   - `daily_average`
   - `weather_analytics_reports`

### Build a Chart

1. **Charts** → **+ Chart**
2. Select dataset → choose chart type (e.g. **Line Chart** for temperature over time)
3. Configure metrics: `AVG(temperature)`, `AVG(wind_speed)`
4. Time column: `weather_local_time`
5. **Save**

### Create Dashboard with Auto-Refresh

1. **Dashboards** → **+ Dashboard**
2. Drag your charts onto the canvas
3. **⋮** menu → **Set auto-refresh interval** → **5 minutes**
4. **Save** the dashboard

The dashboard will now automatically refresh every 5 minutes — matching the Airflow schedule.

---

## 11. Troubleshooting Guide

### ❌ DAG disappears from Airflow UI after running a few times

**Cause:** Docker socket `PermissionError` causes the dag-processor to fail after parsing.
**Fix:**
```bash
sudo chmod 666 /var/run/docker.sock
```
Then in the Airflow UI, click **Clear** on the failed task to re-run.

### ❌ Superset "Invalid login"

**Cause:** The `superset-init` container may have fallen back to SQLite (if `DATABASE_DIALECT` was misconfigured) or volumes were wiped.
**Fix:** Re-run init:
```bash
docker-compose up superset-init
```
Login: `admin` / `admin`

### ❌ `sqlite3.OperationalError: no such table: user_attribute`

**Cause:** The `DATABASE_DIALECT` in `docker/.env` has a comma instead of a plus — `postgresql,psycopg2` → SQLAlchemy fails silently and falls back to SQLite.
**Fix:** Ensure `docker/.env` has:
```
DATABASE_DIALECT=postgresql+psycopg2
```
Then restart: `docker-compose up superset superset-init`

### ❌ `docker-compose up` fails — Postgres unhealthy

**Cause:** Port 5000 or 5432 already in use, or previous data volume is corrupted.
**Fix:**
```bash
docker-compose down -v      # Wipe volumes
docker-compose up           # Fresh start
```

### ❌ `transform_data_task` fails — Permission denied on docker.sock

**Cause:** Docker socket ownership resets on WSL restart.
**Fix:**
```bash
sudo chmod 666 /var/run/docker.sock
```
Then clear and retry the task in the Airflow UI.

### ❌ Airflow DAG parse timeout — `SIGKILL` after 600 seconds

**Cause:** Heavy 3rd-party operator imports at parse time (e.g. `DockerOperator`) take too long in WSL2.
**Fix:** Use `BashOperator` running `docker run ...` instead — no library imports at parse time. Parse time drops from 600s → 10s. *(Already implemented in this project.)*

### ❌ dbt fails — duplicate records in staging

**Cause:** The staging model ran before the deduplication CTE was implemented.
**Fix:**
```bash
docker exec dbt_container dbt run --full-refresh --select stg_weather_data
```

### ❌ Old DAG IDs still showing in Airflow UI

```bash
docker exec airflow_container airflow dags delete old-dag-id -y
docker exec airflow_container airflow dags reserialize
```

### ❌ Superset logs show `HTTP Error 429` during init

**Cause:** `SUPERSET_LOAD_EXAMPLES=yes` tries to download sample data from the internet — rate limited.
**Fix:** Ensure `docker-compose.yaml` has:
```yaml
environment:
  SUPERSET_LOAD_EXAMPLES: "no"
```

---

## 12. Useful Commands Reference

### Container Management
```bash
docker-compose up                          # Start all services (foreground)
docker-compose up -d                       # Start all services (background)
docker-compose up -d --no-deps af          # Restart only Airflow
docker-compose up -d superset              # Restart only Superset
docker-compose down                        # Stop — data preserved
docker-compose down -v                     # Stop + wipe all volumes
docker ps                                  # List running containers
docker logs airflow_container --tail 30    # View Airflow logs
```

### Airflow CLI (inside container)
```bash
docker exec -it airflow_container bash
airflow dags list                          # List all known DAGs
airflow dags list-import-errors            # Show why a DAG failed to parse
airflow dags trigger weather-api-dbt-orchestrator  # Manually trigger a run
airflow dags reserialize                   # Force re-parse all DAGs
airflow dags delete <dag_id> -y            # Delete a DAG from metadata DB
```

### PostgreSQL CLI
```bash
docker exec -it postgres_container psql -U db_user -d db
\dt dev.*                                  # List tables in dev schema
\d dev.raw_weather_data                    # Show table schema
SELECT COUNT(*) FROM dev.raw_weather_data; # Count raw rows
```

### dbt CLI
```bash
docker exec dbt_container dbt run              # Run all models
docker exec dbt_container dbt run --full-refresh  # Drop + recreate all tables
docker exec dbt_container dbt test             # Run data tests
docker exec dbt_container dbt compile          # Compile without running
```

### Airflow Password
```bash
docker exec airflow_container cat /opt/airflow/simple_auth_manager_passwords.json.generated
```
