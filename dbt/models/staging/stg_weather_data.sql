-- Staging model for raw weather data
-- Reads from dev.raw_weather_data inserted by the Airflow pipeline


{{config(
    materialized = 'table',
    unique_key = 'id'
)}}

with source as (

SELECT * 
FROM {{ source('dev', 'raw_weather_data') }}

),

de_dup as (
    select 
    *,
    row_number() over (partition by time order by inserted_at desc) as rn
    from source
)

select 
    id,
    city,
    temperature,
    weather_descriptions,
    wind_speed,
    time as weather_local_time,
    (inserted_at + (utc_offset || ' hours')::interval) as inserted_at_local
from de_dup
where rn = 1