-- Staging model for raw weather data
-- Reads from dev.raw_weather_data inserted by the Airflow pipeline
-- Applies deduplication and null/missing value handling

{{config(
    materialized = 'table',
    unique_key = 'id'
)}}

with source as (

    SELECT * 
    FROM {{ source('dev', 'raw_weather_data') }}

),

cleaned as (
    select
        id,
        -- Null handling: replace missing city with 'Unknown'
        COALESCE(NULLIF(TRIM(city), ''), 'Unknown')                      as city,
        -- Null handling: replace missing temperature with 0
        COALESCE(temperature, 0)                                          as temperature,
        -- Null handling: replace missing description with 'N/A'
        COALESCE(NULLIF(TRIM(weather_descriptions), ''), 'N/A')          as weather_descriptions,
        -- Null handling: replace missing wind_speed with 0
        COALESCE(wind_speed, 0)                                           as wind_speed,
        -- Null handling: fall back to inserted_at formatted as text if local time is missing
        COALESCE(time::text, to_char(inserted_at, 'YYYY-MM-DD HH24:MI')) as time,
        inserted_at,
        utc_offset,
        -- Data quality flag: mark rows that had nulls fixed
        CASE
            WHEN city IS NULL OR city = ''              THEN 'fixed: city null'
            WHEN temperature IS NULL                    THEN 'fixed: temperature null'
            WHEN wind_speed IS NULL                     THEN 'fixed: wind_speed null'
            WHEN time IS NULL                           THEN 'fixed: time null'
            ELSE 'clean'
        END                                                               as data_quality_flag
    from source
),

de_dup as (
    select
        *,
        row_number() over (partition by time order by inserted_at desc) as rn
    from cleaned
)

select
    id,
    city,
    temperature,
    weather_descriptions,
    wind_speed,
    time                                                                  as weather_local_time,
    (inserted_at + (utc_offset || ' hours')::interval)                   as inserted_at_local,
    data_quality_flag
from de_dup
where rn = 1