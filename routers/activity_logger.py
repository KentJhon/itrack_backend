# backend/activity_logger.py
import logging
from datetime import datetime
from typing import Any

from db import get_db


def log_activity(user_id: Any, action: str, description: str) -> None:
    """
    Insert a row into activity_logs.
    """

    try:
        if user_id is None:
            uid = 0
        else:
            uid = int(user_id)
    except (TypeError, ValueError):
        uid = 0

    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO activity_logs (user_id, action, description, timestamp)
            VALUES (%s, %s, %s, %s)
            """,
            (uid, action, description, datetime.now()),
        )
        conn.commit()
    except Exception as e:
        logging.exception("Failed to log activity: %s", e)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
