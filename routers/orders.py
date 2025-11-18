from datetime import date
from fastapi import APIRouter, HTTPException
import mysql.connector

from db import get_db
from schemas import ORPayload

router = APIRouter(tags=["Orders"])


def _month_range(year: int, month: int):
    """
    Helper: given year & month, return (start_date, end_date) as Python date objects.
    end_date is the first day of the next month (exclusive).
    """
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1–12.")

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    return start, end


# =====================================================================
#  NORMAL POS TRANSACTIONS (EXCLUDES SOUVENIR / JOB ORDER TRANSACTIONS)
# =====================================================================
@router.get("/transactions")
def get_transactions():
    """
    Return all transactions EXCEPT those that contain any item whose
    category = 'Souvenir'.

    Normal POS: these rely on OR_number to set transaction_date.
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT
            o.order_id,
            o.OR_number,
            o.customer_name,
            o.total_price,
            o.transaction_date,
            u.username
        FROM `order` o
        JOIN `user` u ON o.user_id = u.user_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM order_line ol
            JOIN item i ON i.item_id = ol.item_id
            WHERE ol.order_id = o.order_id
              AND i.category = 'Souvenir'
        )
        ORDER BY o.transaction_date DESC, o.order_id DESC
        """
    )
    transactions = cursor.fetchall()

    cursor.close()
    conn.close()
    return {"transactions": transactions}


# =====================================================================
#  JOB ORDER TRANSACTIONS (ONLY ORDERS WITH SOUVENIR ITEMS)
# =====================================================================
@router.get("/job-orders/transactions")
def get_job_order_transactions():
    """
    Return ONLY transactions that contain at least one item whose
    category = 'Souvenir' (regardless of OR_number).

    For these, transaction_date will be set by /orders/{id}/set_joborder_date.
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT
            o.order_id,
            o.customer_name,
            o.total_price,
            o.transaction_date,
            u.username
        FROM `order` o
        JOIN `user` u ON o.user_id = u.user_id
        WHERE EXISTS (
            SELECT 1
            FROM order_line ol
            JOIN item i ON i.item_id = ol.item_id
            WHERE ol.order_id = o.order_id
              AND i.category = 'Souvenir'
        )
        ORDER BY o.transaction_date DESC, o.order_id DESC
        """
    )
    transactions = cursor.fetchall()

    cursor.close()
    conn.close()
    return {"transactions": transactions}


# =====================================================================
#  NORMAL POS: ADD OR (NON-SOUVENIR ORDERS ONLY)
# =====================================================================
@router.post("/orders/{order_id}/add_or")
def add_or(order_id: int, payload: ORPayload):
    """
    Normal POS flow (NON–Souvenir orders):

    - OR_number must be unique across orders (except this order)
      * if duplicate -> 400 "OR is not unique"
    - If OR was previously NULL AND the order is NOT a Souvenir order:
      * validate stock
      * deduct item.stock_quantity based on order_line

    - Always:
      * update OR_number
      * set transaction_date = NOW()

    NOTE:
    - Orders that contain 'Souvenir' items will NOT have stock deducted here;
      they are handled in set_joborder_date instead.
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # 0) Enforce OR uniqueness (only if OR_number is provided)
        if payload.OR_number:
            cursor.execute(
                """
                SELECT order_id
                FROM `order`
                WHERE OR_number = %s
                  AND order_id <> %s
                """,
                (payload.OR_number, order_id),
            )
            dup = cursor.fetchone()
            if dup:
                raise HTTPException(status_code=400, detail="OR is not unique")

        # 1) Lock order row
        cursor.execute(
            "SELECT * FROM `order` WHERE order_id = %s FOR UPDATE",
            (order_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")

        already_has_or = row.get("OR_number") is not None

        # 1b) Check if this order is a Souvenir order (has any Souvenir item)
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM order_line ol
            JOIN item i ON i.item_id = ol.item_id
            WHERE ol.order_id = %s
              AND i.category = 'Souvenir'
            """,
            (order_id,),
        )
        job_info = cursor.fetchone()
        is_souvenir_order = bool(job_info and job_info["cnt"] > 0)

        # 2) Get order lines + item stock, lock item rows
        cursor.execute(
            """
            SELECT
                ol.item_id,
                ol.quantity,
                i.stock_quantity
            FROM order_line ol
            JOIN item i ON i.item_id = ol.item_id
            WHERE ol.order_id = %s
            FOR UPDATE
            """,
            (order_id,),
        )
        lines = cursor.fetchall()

        # 3) If OR was NULL before AND this is NOT a Souvenir order,
        #    deduct stock now (normal POS behavior).
        if not already_has_or and not is_souvenir_order:
            # Validate stock
            for line in lines:
                if line["stock_quantity"] < line["quantity"]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Insufficient stock for item {line['item_id']}.",
                    )

            # Deduct stock
            for line in lines:
                cursor.execute(
                    """
                    UPDATE item
                    SET stock_quantity = stock_quantity - %s
                    WHERE item_id = %s
                    """,
                    (line["quantity"], line["item_id"]),
                )

        # 4) Update OR_number and transaction_date
        cursor.execute(
            """
            UPDATE `order`
            SET OR_number = %s,
                transaction_date = NOW()
            WHERE order_id = %s
            """,
            (payload.OR_number, order_id),
        )

        conn.commit()

        # 5) Return updated order summary
        cursor.execute(
            """
            SELECT
                o.order_id,
                o.OR_number,
                o.customer_name,
                o.total_price,
                o.transaction_date,
                u.username
            FROM `order` o
            JOIN `user` u ON o.user_id = u.user_id
            WHERE o.order_id = %s
            """,
            (order_id,),
        )
        updated = cursor.fetchone()

        return {"message": "OR updated", "order": updated}

    except HTTPException:
        conn.rollback()
        raise
    except mysql.connector.Error as err:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(err))
    finally:
        cursor.close()
        conn.close()


# =====================================================================
#  JOB ORDER FINALIZE: SOUVENIR ONLY
# =====================================================================
@router.post("/orders/{order_id}/set_joborder_date")
def set_joborder_date(order_id: int):
    """
    SOUVENIR FLOW ONLY (JOB ORDER):

    - Applies ONLY if the order has at least one item with category = 'Souvenir'
    - Set transaction_date = NOW() IF it's currently NULL
    - Does NOT require OR_number
    - When transaction_date was NULL (first time):
        * validate stock for Souvenir items
        * deduct stock_quantity for those items

    Called immediately after Job Order "Save & Print".
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # 1) Lock order row to ensure it exists
        cursor.execute(
            """
            SELECT order_id, transaction_date
            FROM `order`
            WHERE order_id = %s
            FOR UPDATE
            """,
            (order_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")

        had_date_before = row["transaction_date"] is not None

        # 2) Check it has at least one 'Souvenir' item
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM order_line ol
            JOIN item i ON i.item_id = ol.item_id
            WHERE ol.order_id = %s
              AND i.category = 'Souvenir'
            """,
            (order_id,),
        )
        info = cursor.fetchone()
        if not info or info["cnt"] == 0:
            raise HTTPException(
                status_code=400,
                detail="Order does not contain any 'Souvenir' items",
            )

        # 3) If this is the FIRST time we finalize this Souvenir Order
        #    (transaction_date was NULL), deduct stock for Souvenir items.
        if not had_date_before:
            cursor.execute(
                """
                SELECT
                    ol.item_id,
                    ol.quantity,
                    i.stock_quantity
                FROM order_line ol
                JOIN item i ON i.item_id = ol.item_id
                WHERE ol.order_id = %s
                  AND i.category = 'Souvenir'
                FOR UPDATE
                """,
                (order_id,),
            )
            job_lines = cursor.fetchall()

            # Validate stock
            for line in job_lines:
                if line["stock_quantity"] < line["quantity"]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Insufficient stock for item {line['item_id']}.",
                    )

            # Deduct stock
            for line in job_lines:
                cursor.execute(
                    """
                    UPDATE item
                    SET stock_quantity = stock_quantity - %s
                    WHERE item_id = %s
                    """,
                    (line["quantity"], line["item_id"]),
                )

        # 4) Update transaction_date ONLY if it's currently NULL
        cursor.execute(
            """
            UPDATE `order`
            SET transaction_date = COALESCE(transaction_date, NOW())
            WHERE order_id = %s
            """,
            (order_id,),
        )

        conn.commit()

        # 5) Return updated order summary
        cursor.execute(
            """
            SELECT
                o.order_id,
                o.OR_number,
                o.customer_name,
                o.total_price,
                o.transaction_date,
                u.username
            FROM `order` o
            JOIN `user` u ON o.user_id = u.user_id
            WHERE o.order_id = %s
            """,
            (order_id,),
        )
        updated = cursor.fetchone()

        return {"message": "Souvenir Job Order finalized", "order": updated}

    except HTTPException:
        conn.rollback()
        raise
    except mysql.connector.Error as err:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(err))
    finally:
        cursor.close()
        conn.close()


# =====================================================================
#  DELETE ORDER (ANY CATEGORY)
# =====================================================================
@router.delete("/orders/{order_id}")
def delete_order(order_id: int):
    """
    Delete an order (REGARDLESS of category).
    """
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT order_id FROM `order` WHERE order_id = %s",
            (order_id,),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Order not found")

        cursor.execute(
            "DELETE FROM `order` WHERE order_id = %s",
            (order_id,),
        )
        conn.commit()

    except mysql.connector.Error as err:
        raise HTTPException(status_code=500, detail=str(err))
    finally:
        cursor.close()
        conn.close()

    return {"message": "Order deleted"}


# =====================================================================
#  NORMAL MONTHLY REPORT (NON-SOUVENIR)
# =====================================================================
@router.get("/monthly-report")
def monthly_report(year: int, month: int):
    """
    NORMAL MONTHLY REPORT (NON–SOUVENIR)

    - transaction_date in given month
    - OR_number IS NOT NULL (OR is the deciding factor)
    - EXCLUDES any order that has a Souvenir item
    - Each row is one order_line
    """
    start_date, end_date = _month_range(year, month)

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                o.order_id,
                o.OR_number AS or_number,
                o.customer_name AS payer,
                o.transaction_date AS date,
                ol.quantity AS qty_sold,
                COALESCE(i.unit, 'pcs') AS unit,
                i.name AS description,
                i.price AS unit_cost,
                (ol.quantity * i.price) AS total_cost
            FROM `order` o
            JOIN order_line ol ON ol.order_id = o.order_id
            JOIN item i ON i.item_id = ol.item_id
            WHERE o.transaction_date >= %s
              AND o.transaction_date < %s
              AND o.OR_number IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM order_line ol2
                  JOIN item i2 ON i2.item_id = ol2.item_id
                  WHERE ol2.order_id = o.order_id
                    AND i2.category = 'Souvenir'
              )
            ORDER BY o.transaction_date, o.order_id, ol.order_line_id
            """,
            (start_date, end_date),
        )
        rows = cursor.fetchall()
        return {"rows": rows}

    except mysql.connector.Error as err:
        raise HTTPException(status_code=500, detail=str(err))
    finally:
        cursor.close()
        conn.close()


# =====================================================================
#  JOB ORDER MONTHLY REPORT (SOUVENIR ONLY)
# =====================================================================
@router.get("/monthly-report/job-orders")
def monthly_report_job_orders(year: int, month: int):
    """
    SOUVENIR JOB ORDER MONTHLY REPORT

    - transaction_date in given month
    - Order MUST contain at least one item with category = 'Souvenir'
    - NO OR_number requirement
    - Each row is one order_line where item.category = 'Souvenir'
    """
    start_date, end_date = _month_range(year, month)

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                o.order_id,
                o.customer_name AS payer,
                o.transaction_date AS date,
                ol.quantity AS qty_sold,
                COALESCE(i.unit, 'pcs') AS unit,
                i.name AS description,
                i.price AS unit_cost,
                (ol.quantity * i.price) AS total_cost
            FROM `order` o
            JOIN order_line ol ON ol.order_id = o.order_id
            JOIN item i ON i.item_id = ol.item_id
            WHERE o.transaction_date >= %s
              AND o.transaction_date < %s
              AND EXISTS (
                  SELECT 1
                  FROM order_line ol2
                  JOIN item i2 ON i2.item_id = ol2.item_id
                  WHERE ol2.order_id = o.order_id
                    AND i2.category = 'Souvenir'
              )
              AND i.category = 'Souvenir'
            ORDER BY o.transaction_date, o.order_id, ol.order_line_id
            """,
            (start_date, end_date),
        )
        rows = cursor.fetchall()
        return {"rows": rows}

    except mysql.connector.Error as err:
        raise HTTPException(status_code=500, detail=str(err))
    finally:
        cursor.close()
        conn.close()
# ----------------- NEW DASHBOARD ENDPOINT -----------------
# This is what your Dashboard will use.
@router.get("/dashboard")
def get_dashboard_stats():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 1) Total revenue from completed transactions (have OR_number)
        cursor.execute("""
            SELECT COALESCE(SUM(total_price), 0) AS total_revenue
            FROM `order`
            WHERE OR_number IS NOT NULL
        """)
        rev_row = cursor.fetchone() or {}
        total_revenue = float(rev_row.get("total_revenue") or 0)

        # 2) Total items sold (sum of quantities from order_line)
        cursor.execute("""
            SELECT COUNT(*) AS total_items_sold
            FROM `order`
            WHERE OR_number IS NOT NULL
        """)
        items_row = cursor.fetchone() or {}
        total_items_sold = int(items_row.get("total_items_sold") or 0)

        # 3) Most sold items (top 5)
        cursor.execute("""
            SELECT i.item_id, i.name, SUM(ol.quantity) AS total_sold
            FROM order_line ol
            JOIN `order` o ON o.order_id = ol.order_id
            JOIN item i ON i.item_id = ol.item_id
            WHERE o.OR_number IS NOT NULL
            GROUP BY i.item_id, i.name
            ORDER BY total_sold DESC
            LIMIT 5
        """)
        top_items = cursor.fetchall()

        return {
            "total_revenue": total_revenue,
            "total_items_sold": total_items_sold,
            "top_items": top_items,
        }
    except mysql.connector.Error as err:
        raise HTTPException(status_code=500, detail=str(err))
    finally:
        cursor.close()
        conn.close()