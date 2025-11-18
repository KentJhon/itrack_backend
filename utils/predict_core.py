from typing import List, Dict, Any, Optional, Union
import os, math
import pandas as pd
from fastapi import HTTPException
import joblib

from db import get_db

# Prophet availability is optional
try:
    from prophet import Prophet
    _HAS_PROPHET = True
except Exception:
    Prophet = None  # type: ignore
    _HAS_PROPHET = False

_PRETRAINED: Optional[Union["Prophet", Dict[str, "Prophet"]]] = None
try:
    PKL_PATH = os.path.join(os.path.dirname(__file__), "..", "model.pkl")
    PKL_PATH = os.path.abspath(PKL_PATH)
    if os.path.exists(PKL_PATH):
        _PRETRAINED = joblib.load(PKL_PATH)
except Exception:
    _PRETRAINED = None

def has_prophet() -> bool:
    return _HAS_PROPHET

def is_single_model(obj) -> bool:
    if not _HAS_PROPHET:
        return False
    return hasattr(obj, "make_future_dataframe") and hasattr(obj, "predict")

def df_to_records(fc: pd.DataFrame) -> List[Dict[str, Any]]:
    out = fc[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    out["ds"] = pd.to_datetime(out["ds"]).dt.strftime("%Y-%m-%d")
    for c in ["yhat", "yhat_lower", "yhat_upper"]:
        out[c] = pd.to_numeric(out[c]).clip(lower=0).round(2)
    return out.to_dict(orient="records")

def fetch_daily_series(item_id: int) -> pd.DataFrame:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DATE(o.transaction_date) AS ds, SUM(ol.quantity) AS y
        FROM order_line ol
        JOIN `order` o ON o.order_id = ol.order_id
        WHERE ol.item_id = %s
        GROUP BY DATE(o.transaction_date)
        ORDER BY ds
        """,
        (item_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    df = pd.DataFrame(rows, columns=["ds", "y"])
    if not df.empty:
        df["ds"] = pd.to_datetime(df["ds"])
        df["y"] = pd.to_numeric(df["y"])
        full_range = pd.date_range(df["ds"].min(), df["ds"].max(), freq="D")
        df = (
            df.set_index("ds")
              .reindex(full_range, fill_value=0)
              .rename_axis("ds")
              .reset_index()
        )
    return df

def get_current_stock(item_id: Optional[int]) -> int:
    if item_id is None:
        return 0
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT stock_quantity FROM item WHERE item_id=%s", (item_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return int(row[0]) if row and row[0] is not None else 0

def forecast_with_prophet_df(m: "Prophet", horizon_days: int) -> List[Dict[str, Any]]:
    future = m.make_future_dataframe(periods=horizon_days, freq="D")
    fc = m.predict(future).tail(horizon_days)
    return df_to_records(fc)

def forecast_with_moving_average(df: pd.DataFrame, horizon_days: int, window: int = 14) -> List[Dict[str, Any]]:
    if df.empty:
        base = 0.0
        start_date = pd.Timestamp.today().normalize()
    else:
        tail = df.tail(window)
        base = float(tail["y"].mean()) if not tail.empty else float(df["y"].mean())
        start_date = pd.to_datetime(df["ds"].max()) + pd.Timedelta(days=1)

    dates = pd.date_range(start_date, periods=horizon_days, freq="D")
    fc = pd.DataFrame({
        "ds": dates,
        "yhat": base,
        "yhat_lower": base * 0.8,
        "yhat_upper": base * 1.2,
    })
    return df_to_records(fc)

def forecast_with_pretrained(item_name: Optional[str], horizon_days: int) -> List[Dict[str, Any]]:
    if _PRETRAINED is None:
        raise HTTPException(status_code=404, detail="No pretrained model available on server.")

    # dict[str, Prophet]
    if isinstance(_PRETRAINED, dict):
        if not item_name:
            raise HTTPException(status_code=400, detail="item_name is required for pretrained dict model.")
        if item_name not in _PRETRAINED:
            raise HTTPException(status_code=404, detail=f"Pretrained model '{item_name}' not found.")
        model = _PRETRAINED[item_name]
        if not is_single_model(model):
            raise HTTPException(status_code=500, detail=f"Stored object for '{item_name}' is not a valid Prophet model.")
        return forecast_with_prophet_df(model, horizon_days)

    # single model
    if is_single_model(_PRETRAINED):
        return forecast_with_prophet_df(_PRETRAINED, horizon_days)  # type: ignore[arg-type]

    raise HTTPException(status_code=500, detail="Pretrained object is not a valid Prophet model.")

def model_items():
    if _PRETRAINED is None:
        return {"items": []}
    if isinstance(_PRETRAINED, dict):
        return {"items": list(_PRETRAINED.keys())}
    if is_single_model(_PRETRAINED):
        return {"items": ["default_model"]}
    return {"items": []}
