# backend/routers/sales.py

from fastapi import APIRouter, HTTPException
from db import get_db
from schemas import SaleCreateIn

router = APIRouter(prefix="/api/sales", tags=["Sales"])


@router.get("/catalog")
def get_catalog():
    """Minimal item list for selects."""
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        """
        SELECT item_id, name, price, stock_quantity
        FROM item
        ORDER BY name
        """
    )
    rows = cur.fetchall()

    cur.close()
    conn.close()
    return rows


@router.post("/")
def create_sale(payload: SaleCreateIn):
    """
    Create a POS sale (used by normal sales and Job Orders):

    - Inserts into `order` + `order_line`
    - Validates stock (but does NOT deduct yet)
    - DOES NOT set transaction_date (left as NULL)

      * Normal sales: date set in /orders/{order_id}/add_or
      * Job Orders:   date set in /orders/{order_id}/set_joborder_date
    """
    # Validate payload
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided.")

    if any(i.quantity <= 0 for i in payload.items):
        raise HTTPException(status_code=400, detail="Quantities must be positive.")

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # 1) Validate stock & compute total (but do NOT deduct yet)
        total = 0.0
        for it in payload.items:
            cur.execute(
                """
                SELECT item_id, price, stock_quantity
                FROM item
                WHERE item_id = %s
                FOR UPDATE
                """,
                (it.item_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Item {it.item_id} not found.",
                )

            if row["stock_quantity"] < it.quantity:
                raise HTTPException(
                    status_code=409,
                    detail=f"Insufficient stock for item {it.item_id}.",
                )

            total += float(row["price"]) * it.quantity

        # 2) Validate user_id
        cur.execute(
            "SELECT user_id FROM `user` WHERE user_id = %s",
            (payload.user_id,),
        )
        if not cur.fetchone():
            raise HTTPException(
                status_code=400, detail=f"Invalid user_id {payload.user_id}"
            )

        # 3) Insert order header (transaction_date = NULL)
        #    Use backticks for `order` (reserved word)
        cur.execute(
            """
            INSERT INTO `order` (user_id, total_price, OR_number, customer_name, transaction_date)
            VALUES (%s, %s, %s, %s, NULL)
            """,
            (payload.user_id, total, payload.OR_number, payload.customer_name),
        )
        order_id = cur.lastrowid

        # 4) Insert lines (NO stock update here)
        for it in payload.items:
            cur.execute(
                """
                INSERT INTO order_line (order_id, item_id, quantity)
                VALUES (%s, %s, %s)
                """,
                (order_id, it.item_id, it.quantity),
            )

        conn.commit()

        return {
            "sale_id": order_id,
            "total_price": round(total, 2),
            "items": [i.dict() for i in payload.items],
        }

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        # This is what becomes your 500
        raise HTTPException(status_code=500, detail=f"Server error: {e}")
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


@router.get("/{sale_id}")
def get_sale(sale_id: int):
    """Fetch a sale header + lines."""
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    # Quote `order` because it's reserved
    cur.execute(
        "SELECT * FROM `order` WHERE order_id = %s",
        (sale_id,),
    )
    order = cur.fetchone()

    if not order:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Sale not found.")

    cur.execute(
        """
        SELECT
            ol.order_line_id,
            ol.item_id,
            i.name,
            i.price,
            ol.quantity
        FROM order_line ol
        JOIN item i ON i.item_id = ol.item_id
        WHERE ol.order_id = %s
        """,
        (sale_id,),
    )
    lines = cur.fetchall()

    cur.close()
    conn.close()

    return {"order": order, "lines": lines}
