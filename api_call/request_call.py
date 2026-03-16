import requests

api_key = "YOUR_WEATHERSTACK_API_KEY"  # Get yours at https://weatherstack.com
api_url = f"http://api.weatherstack.com/current?access_key={api_key}&query=New York"

def fetch_data():
    print("Fetching weather data from Weatherstack API...")
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        print("API response received successfully.")

        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        raise

#fetch_data()

def mock_data():
    return {'request': {'type': 'City', 'query': 'New York, United States of America', 'language': 'en', 'unit': 'm'}, 'location': {'name': 'New York', 'country': 'United States of America', 'region': 'New York', 'lat': '40.714', 'lon': '-74.006', 'timezone_id': 'America/New_York', 'localtime': '2026-03-14 08:03', 'localtime_epoch': 1773475380, 'utc_offset': '-4.0'}, 'current': {'observation_time': '12:03 PM', 'temperature': 7, 'weather_code': 113, 'weather_icons': ['https://cdn.worldweatheronline.com/images/wsymbols01_png_64/wsymbol_0001_sunny.png'], 'weather_descriptions': ['Sunny'], 'astro': {'sunrise': '07:09 AM', 'sunset': '07:02 PM', 'moonrise': '04:58 AM', 'moonset': '02:19 PM', 'moon_phase': 'Waning Crescent', 'moon_illumination': 26}, 'air_quality': {'co': '205.85', 'no2': '5.95', 'o3': '84', 'so2': '3.85', 'pm2_5': '2.25', 'pm10': '2.25', 'us-epa-index': '1', 'gb-defra-index': '1'}, 'wind_speed': 34, 'wind_degree': 256, 'wind_dir': 'WSW', 'pressure': 1010, 'precip': 0, 'humidity': 51, 'cloudcover': 0, 'feelslike': 2, 'uv_index': 0, 'visibility': 16, 'is_day': 'yes'}}