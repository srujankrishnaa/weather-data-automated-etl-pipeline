{{
    config (
        materialized = 'table',
        unique_key = 'id'
    )
}}


select city,
       temperature,
       weather_descriptions,
       wind_speed,
       weather_local_time
 from {{ ref('stg_weather_data') }}