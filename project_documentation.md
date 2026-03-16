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
9. [dbt — Transformations Explained](#9-dbt--transformations-explained)
10. [Superset — Connecting & Building Dashboards](#10-superset--connecting--building-dashboards)
11. [Troubleshooting Guide](#11-troubleshooting-guide)
12. [Useful Commands Reference](#12-useful-commands-reference)

---

## 1. Overview

An end-to-end, fully automated data pipeline that:
- Fetches live weather data from the **Weatherstack API** every 5 minutes
- Inserts raw data into **PostgreSQL**
- Transforms and models it using **dbt** (staging → mart layer)
- Orchestrates every step automatically with **Apache Airflow 3.0**
- Visualises it with **Apache Superset** auto-refreshing dashboards

Everything runs in Docker containers, spun up with a single `docker-compose up` command.

**Data Flow:**
```
Weatherstack API
    │  (every 5 min, triggered by Airflow)
    ▼
ingest_data_task  →  dev.raw_weather_data  (Postgres)
    │
    ▼
transform_data_task (dbt run)
    ├──  dev.stg_weather_data          (deduplicated)
    ├──  dev.daily_average             (aggregated)
    └──  dev.weather_analytics_reports (reporting)
             │
             ▼
      Apache Superset Dashboard
      (auto-refresh every 5 min)
```

**Tech Stack:**

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.x | API ingestion scripts |
| Apache Airflow | 3.0.0 | Pipeline orchestration |
| dbt-postgres | 1.9.latest | Data transformation |
| PostgreSQL | 14.17 | Data warehouse |
| Apache Superset | 3.0.0-py310 | BI dashboards |
| Redis | 7 | Superset cache backend |
| Docker + Compose | latest | Container infrastructure |

---

## 2. Prerequisites & WSL2 Setup

### Enable WSL2 on Windows

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

## 5. Understanding the API & Testing It

### What the API returns

The Weatherstack API returns a JSON object for a given city. Example response fields used:

```json
{
  "location": { "name": "New York", "localtime": "2026-03-16 14:00" },
  "current": {
    "temperature": 12,
    "weather_descriptions": ["Partly cloudy"],
    "wind_speed": 20,
    "humidity": 65
  }
}
```

### Test the API locally

```bash
# Activate virtual env (if running outside Docker)
cd api_call
python -c "from request_call import fetch_data; print(fetch_data())"
```

### Test inserting into Postgres

```bash
# With Docker running:
docker exec airflow_container python /opt/airflow/api_call/insert_records.py
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

-- See deduplicated staging data
SELECT * FROM dev.stg_weather_data LIMIT 10;

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

The DAG has two tasks chained sequentially (`task1 >> task2`):

1. **`ingest_data_task`** (`PythonOperator`)
   - Lazily imports `insert_records.main()` inside the callable (avoids parse-time imports)
   - Calls Weatherstack API, inserts one row into `dev.raw_weather_data`
   - Runs in ~3–5 seconds

2. **`transform_data_task`** (`BashOperator`)
   - Runs `docker run ... ghcr.io/dbt-labs/dbt-postgres:1.9.latest run`
   - Mounts the local `./dbt` directory into the container
   - Executes all dbt models: staging → mart
   - Runs in ~10–15 seconds

Schedule: every **5 minutes** (`schedule=timedelta(minutes=5)`)

### Monitoring in the UI

1. Go to **http://localhost:8000** → login
2. **Dags** list → find `weather-api-dbt-orchestrator` — toggle should be ON (blue)
3. Click the DAG → **Runs** tab → each row is one 5-min execution
4. Click a run → **Task Instances** → both tasks show ✅ **success** when working

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

## 9. dbt — Transformations Explained

### Models Overview

```
dbt/models/
├── staging/
│   ├── sources.yml             → Registers dev.raw_weather_data as a dbt source
│   └── stg_weather_data.sql   → Reads raw data, deduplicates using ROW_NUMBER()
└── mart/
    ├── daily_average.sql             → AVG(temperature), AVG(wind_speed) per city/day
    └── weather_analytics_reports.sql → Key columns for reporting (city, temp, wind, time)
```

### Deduplication Logic (`stg_weather_data.sql`)

```sql
WITH source AS (
    SELECT * FROM {{ source('dev', 'raw_weather_data') }}
),
de_dup AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY weather_local_time ORDER BY inserted_at DESC) AS rn
    FROM source
)
SELECT * FROM de_dup WHERE rn = 1
```

### Run dbt Manually

```bash
# Run all models
docker exec dbt_container dbt run

# Full refresh (drops and recreates tables)
docker exec dbt_container dbt run --full-refresh

# Test models
docker exec dbt_container dbt test
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
