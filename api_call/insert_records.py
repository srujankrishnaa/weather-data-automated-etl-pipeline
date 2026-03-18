import psycopg2
import os
from s3_fetch import fetch_data

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", 5000))

def connect_to_db():

    print("Connecting to the PostgreSQL database...")
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname="db",
            user="db_user",
            password="db_password"
        )
        return conn
    except psycopg2.Error as e:
        print(f"Database connection failed: {e}")
        raise

def create_table(conn):
    print("Creating table if not exist...")
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE SCHEMA IF NOT EXISTS dev;
            CREATE TABLE IF NOT EXISTS dev.raw_weather_data (
                id SERIAL PRIMARY KEY,
                city TEXT,
                temperature FLOAT,
                weather_descriptions TEXT,
                wind_speed FLOAT,
                time TIMESTAMP,
                inserted_at TIMESTAMP DEFAULT NOW(),
                utc_offset TEXT
            );
        """)
        conn.commit()
        print("Table created successfully.")
    except psycopg2.Error as e:
        print(f"Table creation failed: {e}")
        raise
        

def insert_records(conn, data):
    weather  = data['current']
    location = data['location']
    cursor   = conn.cursor()

    # Dedup guard — skip if this exact station + time already ingested
    cursor.execute("""
        SELECT 1 FROM dev.raw_weather_data
        WHERE city = %s AND time = %s
    """, (location['name'], location['localtime']))

    if cursor.fetchone():
        print(f"Already ingested {location['name']} at {location['localtime']} — skipping.")
        return

    cursor.execute("""
        INSERT INTO dev.raw_weather_data (
            city,
            temperature,
            weather_descriptions,
            wind_speed,
            time,
            inserted_at,
            utc_offset
        ) VALUES (%s, %s, %s, %s, %s, NOW(), %s)
    """, (
        location['name'],
        weather['temperature'],
        weather['weather_descriptions'][0],
        weather['wind_speed'],
        location['localtime'],
        location['utc_offset']
    ))
    conn.commit()
    print(f"Inserted {location['name']} at {location['localtime']}")


def main():
    conn = None
    try:
        conn = connect_to_db()
        create_table(conn)

        # s3_fetch.fetch_data() returns a list — one dict per station
        data_list = fetch_data()
        if not data_list:
            print("No data returned from NOAA S3 — skipping insert.")
            return

        for data in data_list:
            try:
                insert_records(conn, data)
            except Exception as e:
                print(f"Error inserting {data['location']['name']}: {e}")
                conn.rollback()   # keep connection alive for next station

    except Exception as e:
        print(f"Fatal error during execution: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")