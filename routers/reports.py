import logging
from fastapi import APIRouter, HTTPException, Query
from db import get_db

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/monthly")
def get_monthly_report(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
):
    """
    Monthly report based on ORDER + ORDER_LINE + ITEM.

    Rules:
    - Always filter by YEAR/MONTH of transaction_date.
    - Include ALL categories.
    - For NON-Souvenir items: OR_number must be real (NOT NULL, NOT '-').
    - For Souvenir items: OR_number is NOT required.
      -> In the result, Souvenir rows will ALWAYS show OR_number as '-'.
    Each row = one order_line.
    """
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            """
            SELECT
                DATE(o.transaction_date)      AS date,
                o.customer_name               AS payer,
                ol.quantity                   AS qty_sold,
                COALESCE(i.unit, 'pcs')       AS unit,
                i.name                        AS description,
                i.price                       AS unit_cost,
                (i.price * ol.quantity)       AS total_cost,
                CASE
                    WHEN i.category = 'Souvenir' THEN '-'
                    ELSE o.OR_number
                END                           AS or_number
            FROM `order` o
            JOIN order_line ol ON ol.order_id = o.order_id
            JOIN item i        ON i.item_id = ol.item_id
            WHERE YEAR(o.transaction_date) = %s
              AND MONTH(o.transaction_date) = %s
              AND (
                    (o.OR_number IS NOT NULL AND o.OR_number <> '-')
                    OR i.category = 'Souvenir'
                  )
            ORDER BY o.transaction_date ASC,
                     o.order_id,
                     ol.order_line_id
            """,
            (year, month),
        )
        rows = cur.fetchall()
        return {"rows": rows}
    except Exception as e:
        logging.exception("Error fetching monthly report")
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()
