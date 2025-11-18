from fastapi import APIRouter, HTTPException
from db import get_db

router = APIRouter(tags=["Dashboard"])


@router.get("/dashboard/top-items")
def get_top_items(year: int, month: int | None = None):
    """
    Returns TOP 3 most sold items.
    If month is None → yearly
    If month is provided → monthly
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    if month:
        cursor.execute("""
            SELECT i.name, SUM(ol.quantity) AS total_sold
            FROM order_line ol
            JOIN `order` o ON o.order_id = ol.order_id
            JOIN item i ON i.item_id = ol.item_id
            WHERE YEAR(o.transaction_date) = %s
              AND MONTH(o.transaction_date) = %s
            GROUP BY i.item_id
            ORDER BY total_sold DESC
            LIMIT 3
        """, (year, month))
    else:
        cursor.execute("""
            SELECT i.name, SUM(ol.quantity) AS total_sold
            FROM order_line ol
            JOIN `order` o ON o.order_id = ol.order_id
            JOIN item i ON i.item_id = ol.item_id
            WHERE YEAR(o.transaction_date) = %s
            GROUP BY i.item_id
            ORDER BY total_sold DESC
            LIMIT 3
        """, (year,))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return {"top_items": rows}

@router.get("/dashboard/sales")
def get_sales(year: int):
    """
    Returns monthly sales totals for given year.
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT MONTH(transaction_date) AS month, 
               SUM(total_price) AS total
        FROM `order`
        WHERE YEAR(transaction_date) = %s
        GROUP BY MONTH(transaction_date)
        ORDER BY MONTH(transaction_date)
    """, (year,))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    # convert to full 12 months (fill missing with zero)
    monthly = [{"month": m, "total": 0} for m in range(1,13)]
    for row in rows:
        monthly[row["month"]-1]["total"] = float(row["total"])

    return {"sales": monthly}