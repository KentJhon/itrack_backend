# backend/routers/predictive.py
from fastapi import APIRouter, HTTPException, Query, Cookie
from fastapi.responses import FileResponse
import os
import pandas as pd

from typing import Optional

from db import get_db
from security.jwt_tools import verify_token
from security.deps import COOKIE_NAME_AT
from routers.activity_logger import log_activity

from services.predictive_service import (
    DATA_FILE,
    ITEM_MODELS,
    load_history_from_excel,
    to_monthly,
    eligible_items,
    train_models_for_eligible_items,
    list_cached_models,
    forecast_next_6_months_for_itemname,
    forecast_next_month_safe,
    recommended_restock_plan,
    export_month_plan,
    all_items_summary,
)

router = APIRouter(prefix="/predictive", tags=["Predictive"])


def _actor_id_from_cookie(access_token: Optional[str]) -> Optional[int]:
    if not access_token:
        return None
    try:
        claims = verify_token(access_token)
        if claims.get("type") == "access":
            return int(claims["sub"])
    except Exception:
        return None
    return None


def _get_stock_from_db() -> pd.DataFrame:
    """
    Get current stock from MySQL `item` table and return DataFrame:
      [item_name, stock_quantity]
    Matching to CSV/Excel happens by item_name (case-insensitive).
    """
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT name AS item_name, stock_quantity FROM item")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return pd.DataFrame(columns=["item_name", "stock_quantity"])

    df = pd.DataFrame(rows)
    df["item_name"] = df["item_name"].astype(str).str.strip()
    return df


# ----------------------- TRAIN / VALIDATE -----------------------

@router.api_route("/train", methods=["GET", "POST"])
def train_validate_excel():
    try:
        df = load_history_from_excel()
        monthly = to_monthly(df)
        elig = eligible_items(monthly)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load data: {e}")

    return {
        "status": "ok",
        "rows_loaded": int(len(df)),
        "unique_items": int(df["item_name"].nunique()),
        "date_min": str(df["date"].min()),
        "date_max": str(df["date"].max()),
        "eligible_items_count": len(elig),
        "eligible_items_sample": elig[:10],
        "data_file": str(DATA_FILE),
    }


@router.api_route("/train/all", methods=["GET", "POST"])
def train_all_models():
    try:
        df = load_history_from_excel()
        trained, skipped = train_models_for_eligible_items(df)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Training failed: {e}")

    return {
        "status": "ok",
        "trained": trained,
        "trained_count": len(trained),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "cache_size": len(ITEM_MODELS),
    }


@router.get("/models")
def list_models():
    names = list_cached_models()
    return {"count": len(names), "items": names}


# ----------------------- 6-MONTH FORECAST -----------------------

@router.get("/forecast/item")
def forecast_one_item(
    item_name: str = Query(..., description="Exact item name from the 'Items' column"),
):
    try:
        hist = load_history_from_excel()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data load failed: {e}")

    stock_df = _get_stock_from_db()
    stock_map = {
        n.casefold(): int(q)
        for n, q in zip(stock_df["item_name"], stock_df["stock_quantity"])
    }
    current_stock = stock_map.get(item_name.casefold(), 0)

    try:
        monthly = forecast_next_6_months_for_itemname(hist, item_name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    plan = recommended_restock_plan(monthly, current_stock)

    return {
        "item_name": item_name,
        "current_stock": int(current_stock),
        "monthly_forecast": monthly.to_dict(orient="records"),
        "restock_plan": plan.to_dict(orient="records"),
        "total_6mo_forecast": int(round(float(monthly["forecast_qty"].sum()))),
        "total_recommended_restock": int(plan["recommended_restock"].sum())
        if not plan.empty
        else 0,
    }


@router.get("/forecast/all")
def forecast_all_items():
    try:
        hist = load_history_from_excel()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data load failed: {e}")

    stock_df = _get_stock_from_db()
    table = all_items_summary(hist, stock_df)

    return {"count": int(len(table)), "rows": table.to_dict(orient="records")}


# ----------------------- EXPORT 6-MONTH PLAN -----------------------

@router.get("/export")
def export_item_plan(
    item_name: str,
    filetype: str = Query("csv", pattern="^(csv|xlsx)$"),
):
    try:
        hist = load_history_from_excel()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data load failed: {e}")

    stock_df = _get_stock_from_db()
    stock_map = {
        n.casefold(): int(q)
        for n, q in zip(stock_df["item_name"], stock_df["stock_quantity"])
    }
    current_stock = stock_map.get(item_name.casefold(), 0)

    try:
        monthly = forecast_next_6_months_for_itemname(hist, item_name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    plan = recommended_restock_plan(monthly, current_stock)
    path = export_month_plan(item_name, plan, filetype=filetype)

    media_type = (
        "text/csv"
        if filetype == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(path, media_type=media_type, filename=os.path.basename(path))


# ----------------------- NEXT-MONTH FORECAST ONLY -----------------------

@router.get("/next_month/item")
def next_month_one_item(
    item_name: str = Query(..., description="Exact item name from the 'Items' column"),
):
    try:
        hist = load_history_from_excel()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data load failed: {e}")

    stock_df = _get_stock_from_db()
    stock_map = {
        n.casefold(): int(q)
        for n, q in zip(stock_df["item_name"], stock_df["stock_quantity"])
    }
    current_stock = stock_map.get(item_name.casefold(), 0)

    try:
        pred = forecast_next_month_safe(hist, item_name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "item_name": item_name,
        "next_month_forecast": int(pred),
        "current_stock": int(current_stock),
    }


@router.get("/next_month/all")
def next_month_all_items(
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    """
    Predict next month's issuance for ALL items.

    This is your Predictive Restock feature – we log it.
    """
    try:
        hist_raw = load_history_from_excel()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data load failed: {e}")

    stock_df = _get_stock_from_db()
    if stock_df.empty:
        # still log that someone tried
        actor_id = _actor_id_from_cookie(access_token)
        log_activity(
            actor_id,
            "Predictive Restock",
            "Ran forecast for all items (no stock rows).",
        )
        return {"count": 0, "rows": []}

    stock_df = stock_df.copy()
    stock_df["key"] = stock_df["item_name"].astype(str).str.strip().str.casefold()

    db_key_to_name = {k: n for k, n in zip(stock_df["key"], stock_df["item_name"])}
    db_key_to_stock = {k: int(q) for k, q in zip(stock_df["key"], stock_df["stock_quantity"])}

    def map_to_db_name(raw: str) -> str | None:
        if raw is None:
            return None
        key = str(raw).strip().casefold()
        if key in db_key_to_name:
            return db_key_to_name[key]
        return None

    hist = hist_raw.copy()
    hist["canonical_name"] = hist["item_name"].apply(map_to_db_name)
    hist = hist.dropna(subset=["canonical_name"])

    if hist.empty:
        actor_id = _actor_id_from_cookie(access_token)
        log_activity(
            actor_id,
            "Predictive Restock",
            "Ran forecast for all items (no matching history).",
        )
        return {"count": 0, "rows": []}

    hist["item_name"] = hist["canonical_name"]
    hist = (
        hist.groupby(["date", "item_name"], as_index=False)["quantity"]
        .sum()
        .reset_index(drop=True)
    )

    stock_map = {
        n.strip().casefold(): int(q)
        for n, q in zip(stock_df["item_name"], stock_df["stock_quantity"])
    }

    rows = []
    for name in sorted(hist["item_name"].unique().tolist(), key=str.casefold):
        try:
            pred = forecast_next_month_safe(hist, name)
        except Exception:
            continue
        current = int(stock_map.get(name.strip().casefold(), 0))
        rows.append(
            {
                "item_name": name,
                "current_stock": current,
                "next_month_forecast": int(pred),
            }
        )

    rows.sort(key=lambda r: r["next_month_forecast"], reverse=True)

    # 🔔 ACTIVITY
    actor_id = _actor_id_from_cookie(access_token)
    log_activity(
        actor_id,
        "Predictive Restock",
        "Ran forecast for all items (predictive/next_month/all).",
    )

    return {"count": len(rows), "rows": rows}
