# backend/routers/items.py
from typing import Optional

from fastapi import APIRouter, Form, Cookie
import mysql.connector

from db import get_db
from security.jwt_tools import verify_token
from security.deps import COOKIE_NAME_AT
from routers.activity_logger import log_activity

router = APIRouter(prefix="/items", tags=["Items"])


def _actor_id_from_cookie(access_token: str | None) -> Optional[int]:
    if not access_token:
        return None
    try:
        claims = verify_token(access_token)
        if claims.get("type") == "access":
            return int(claims["sub"])
    except Exception:
        return None
    return None


# ✅ READ: Fetch all items
@router.get("/")
def get_items():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM item")
    items = cursor.fetchall()
    cursor.close()
    conn.close()
    return items


# ✅ CREATE: Add a new item
@router.post("/")
def add_item(
    name: str = Form(...),
    unit: str = Form(...),
    category: str = Form(...),
    price: float = Form(...),
    stock_quantity: int = Form(...),
    reorder_level: int = Form(...),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO item (name, unit, category, price, stock_quantity, reorder_level)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (name, unit, category, price, stock_quantity, reorder_level),
        )
        conn.commit()
        item_id = cursor.lastrowid
    except mysql.connector.Error:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    actor_id = _actor_id_from_cookie(access_token)
    log_activity(
        actor_id,
        "Create",
        f"Added inventory item #{item_id} ({name}), category={category}, stock={stock_quantity}.",
    )

    return {"message": "Item added successfully", "item_id": item_id}


# ✅ UPDATE: Modify an existing item (including stock changes via form)
@router.put("/{item_id}")
def update_item(
    item_id: int,
    name: str = Form(...),
    unit: str = Form(...),
    category: str = Form(...),
    price: float = Form(...),
    stock_quantity: int = Form(...),
    reorder_level: int = Form(...),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE item
            SET name=%s, unit=%s, category=%s, price=%s, stock_quantity=%s, reorder_level=%s
            WHERE item_id=%s
            """,
            (name, unit, category, price, stock_quantity, reorder_level, item_id),
        )
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    actor_id = _actor_id_from_cookie(access_token)
    log_activity(
        actor_id,
        "Update",
        f"Updated inventory item #{item_id} ({name}), category={category}, stock={stock_quantity}.",
    )

    return {"message": "Item updated successfully"}


# ✅ DELETE: Remove an item
@router.delete("/{item_id}")
def delete_item(
    item_id: int,
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM item WHERE item_id=%s", (item_id,))
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    actor_id = _actor_id_from_cookie(access_token)
    log_activity(
        actor_id,
        "Delete",
        f"Deleted inventory item #{item_id}.",
    )

    return {"message": "Item deleted successfully"}


# ✅ ADD STOCK ONLY: Adjust stock and log it
@router.post("/{item_id}/add_stock")
def add_stock(
    item_id: int,
    added_qty: int = Form(...),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    """
    Increment stock_quantity for an existing item.
    This is for 'add stock' operations (e.g., new delivery).
    """
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # Get current item info
        cursor.execute(
            "SELECT name, stock_quantity FROM item WHERE item_id = %s",
            (item_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise mysql.connector.Error(f"Item #{item_id} not found")

        old_stock = int(row["stock_quantity"])
        new_stock = old_stock + added_qty

        # Update stock
        cursor.execute(
            """
            UPDATE item
            SET stock_quantity = %s
            WHERE item_id = %s
            """,
            (new_stock, item_id),
        )
        conn.commit()

    except mysql.connector.Error:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    actor_id = _actor_id_from_cookie(access_token)
    log_activity(
        actor_id,
        "Update",  # or "Transaction" / "Stock Change" if you prefer
        f"Added {added_qty} units to item #{item_id} ({row['name']}), stock {old_stock} → {new_stock}.",
    )

    return {
        "message": "Stock updated successfully",
        "item_id": item_id,
        "old_stock": old_stock,
        "new_stock": new_stock,
    }
