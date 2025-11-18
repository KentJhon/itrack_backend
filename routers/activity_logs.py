from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from db import get_db

router = APIRouter(prefix="/activity-logs", tags=["Activity Logs"])


@router.get("")
def list_activity_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    """
    Paginated + filterable list of activity logs for ActivityLog.jsx.
    """
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        where = []
        params = []

        if user_id is not None:
            where.append("al.user_id = %s")
            params.append(user_id)

        if action:
            where.append("al.action = %s")
            params.append(action)

        if search:
            where.append("al.description LIKE %s")
            params.append(f"%{search}%")

        if date_from:
            where.append("DATE(al.timestamp) >= %s")
            params.append(date_from)

        if date_to:
            where.append("DATE(al.timestamp) <= %s")
            params.append(date_to)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        # total
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM activity_logs al {where_sql}",
            params,
        )
        total = cur.fetchone()["cnt"]

        offset = (page - 1) * page_size

        # rows
        cur.execute(
            f"""
            SELECT
                al.log_id   AS id,
                al.user_id  AS user_id,
                u.username  AS user_name,
                al.action   AS action,
                al.description,
                al.timestamp
            FROM activity_logs al
            LEFT JOIN user u ON u.user_id = al.user_id
            {where_sql}
            ORDER BY al.timestamp DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        )
        rows = cur.fetchall()

        return {
            "data": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/highlights")
def list_highlight_activity_logs(
    limit: int = Query(20, ge=1, le=100),
):
    """
    Lightweight feed used by dashboard widgets.
    Only returns authentication events, inventory add/delete/stock changes,
    and predictive restock runs.
    """
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            """
            SELECT
                al.log_id      AS id,
                al.user_id     AS user_id,
                u.username     AS user_name,
                al.action      AS action,
                al.description AS description,
                al.timestamp   AS timestamp
            FROM activity_logs al
            LEFT JOIN user u ON u.user_id = al.user_id
            WHERE (
                al.action IN (%s, %s, %s)
                OR (al.action = %s AND al.description LIKE %s)
                OR (al.action = %s AND al.description LIKE %s)
                OR (al.action = %s AND al.description LIKE %s)
            )
            ORDER BY al.timestamp DESC
            LIMIT %s
            """,
            [
                "Login",
                "Logout",
                "Predictive Restock",
                "Create",
                "Added inventory item%",
                "Delete",
                "Deleted inventory item%",
                "Update",
                "Updated inventory item%",
                limit,
            ],
        )
        rows = cur.fetchall()

        activities = []
        for row in rows:
            user_display = row["user_name"]
            if not user_display:
                if row["user_id"] is not None and row["user_id"] != 0:
                    user_display = f"User #{row['user_id']}"
                else:
                    user_display = "System"

            activities.append(
                {
                    "id": row["id"],
                    "user": user_display,
                    "action": row["action"],
                    "description": row["description"],
                    "timestamp": row["timestamp"],
                }
            )

        return {"activities": activities}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
