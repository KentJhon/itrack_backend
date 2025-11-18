import os
import mysql.connector
from mysql.connector import Error

def get_db():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "itrack"),
        )
        return conn
    except Error as e:
        print("❌ Database connection error:", e)
        return None
