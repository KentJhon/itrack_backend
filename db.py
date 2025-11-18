import os
import mysql.connector
from mysql.connector import Error

def get_db():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT")),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
        )

        if conn.is_connected():
            return conn

        print("❌ Database connected but connection is not active.")
        return None

    except Error as e:
        print("❌ Database connection error:", e)
        return None
