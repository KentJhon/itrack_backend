# backend/routers/predict.py
import math
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Cookie

from utils.predict_core import (
    has_prophet,
    fetch_daily_series,
    forecast_with_prophet_df,
    forecast_with_moving_average,
    forecast_with_pretrained,
    get_current_stock,
    model_items,
)
from db import get_db
from security.jwt_tools import verify_token
from security.deps import COOKIE_NAME_AT
from routers.activity_logger import log_activity

router = APIRouter()


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


@router.get("/predict/model_items")
def predict_model_items():
    return model_items()


@router.get("/predict/forecast")
def predict_forecast(
    item_id: Optional[int] = Query(None, ge=1),
    horizon_days: int = Query(30, ge=7, le=365),
    item_name: Optional[str] = Query(None),
):
    if item_name is not None:
        fc = forecast_with_pretrained(item_name, horizon_days)
        current_stock = get_current_stock(item_id)  # 0 if None
        avg_daily = round(sum(r["yhat"] for r in fc) / len(fc), 2) if fc else 0.0
        total_next_30 = round(sum(r["yhat"] for r in fc[:30]), 2) if fc else 0.0
        safety_factor = 1.2
        target_cover = math.ceil(total_next_30 * safety_factor)
        recommended = max(0, target_cover - current_stock)
        return {
            "item": {"id": item_id, "name": item_name},
            "current_stock": current_stock,
            "forecast": fc,
            "summary": {
                "avg_daily": avg_daily,
                "total_next_30": total_next_30,
                "recommended_restock": int(recommended),
            },
        }

    if item_id is None:
        raise HTTPException(
            status_code=400,
            detail="Provide either item_name (pretrained) or item_id (DB-based).",
        )

    hist = fetch_daily_series(item_id)
    if has_prophet() and not hist.empty and hist["y"].sum() > 0:
        from prophet import Prophet
        m = Prophet(daily_seasonality=True, yearly_seasonality=True)  # type: ignore[call-arg]
        m.fit(hist.rename(columns={"ds": "ds", "y": "y"}))
        fc = forecast_with_prophet_df(m, horizon_days)
    else:
        fc = forecast_with_moving_average(hist, horizon_days)

    avg_daily = round(sum(r["yhat"] for r in fc) / len(fc), 2) if fc else 0.0
    total_next_30 = round(sum(r["yhat"] for r in fc[:30]), 2) if fc else 0.0
    current_stock = get_current_stock(item_id)

    safety_factor = 1.2
    target_cover = math.ceil(total_next_30 * safety_factor)
    recommended = max(0, target_cover - current_stock)

    return {
        "item": {"id": item_id},
        "current_stock": current_stock,
        "forecast": fc,
        "summary": {
            "avg_daily": avg_daily,
            "total_next_30": total_next_30,
            "recommended_restock": int(recommended),
        },
    }


@router.get("/predict/forecast_all")
def predict_forecast_all(
    horizon_days: int = Query(30, ge=7, le=365),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    """
    For a pretrained dict model, return a summary for every item.

    Logged as Predictive Restock.
    """
    items_meta = model_items()
    names = items_meta.get("items", [])
    if not names or (isinstance(names, list) and names == ["default_model"]):
        raise HTTPException(
            status_code=400,
            detail="Pretrained file is a single model; no per-item list available.",
        )

    out = []
    for name in names:
        fc = forecast_with_pretrained(name, horizon_days)
        avg_daily = round(sum(r["yhat"] for r in fc) / len(fc), 2) if fc else 0.0
        total_next_30 = round(sum(r["yhat"] for r in fc[:30]), 2) if fc else 0.0
        current_stock = 0  # unknown for pretrained; assume 0
        safety_factor = 1.2
        target_cover = math.ceil(total_next_30 * safety_factor)
        recommended = max(0, target_cover - current_stock)
        out.append(
            {
                "item_name": name,
                "summary": {
                    "avg_daily": avg_daily,
                    "total_next_30": total_next_30,
                    "recommended_restock": int(recommended),
                },
            }
        )
    out.sort(key=lambda r: r["summary"]["recommended_restock"], reverse=True)

    # 🔔 ACTIVITY
    actor_id = _actor_id_from_cookie(access_token)
    log_activity(
        actor_id,
        "Predictive Restock",
        "Ran forecast for all items (predict/forecast_all).",
    )

    return {"items": out}
