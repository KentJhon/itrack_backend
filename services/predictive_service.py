# backend/services/predictive_service.py
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from prophet import Prophet

# -----------------------------------
# Paths (change filename if needed)
# -----------------------------------
DATA_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "sales_history.csv"
)  # supports .csv/.xlsx/.xls
EXPORT_DIR = Path(__file__).resolve().parents[1] / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------
# Simple in-memory model cache
# -----------------------------------
ITEM_MODELS: Dict[str, Prophet] = {}  # key: item_name (lowercase), value: trained Prophet


# -----------------------------------
# Readers (CSV/XLSX/XLS)
# -----------------------------------
def _read_excel_with_engine(path: Path) -> pd.DataFrame:
    """
    Read dataset whether it's CSV, XLS, or XLSX.
    Automatically picks the right reader.
    """
    ext = path.suffix.lower()

    if ext == ".csv":
        return pd.read_csv(path)
    elif ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        # pip install openpyxl
        return pd.read_excel(path, engine="openpyxl")
    elif ext == ".xls":
        # pip install xlrd==1.2.0
        return pd.read_excel(path, engine="xlrd")
    else:
        raise ValueError(
            f"Unsupported or missing extension '{ext}'. "
            f"Rename your file to .csv, .xlsx, or .xls. File: {path}"
        )


# -----------------------------------
# Robust date parser
# -----------------------------------
def _parse_dates_safely(s: pd.Series) -> pd.Series:
    """
    Robust date parsing:
    - try month-first (US style)
    - if many NaT, try day-first and keep the better parse
    - handles already-datetime columns
    """
    if pd.api.types.is_datetime64_any_dtype(s):
        return s

    d1 = pd.to_datetime(s, errors="coerce", infer_datetime_format=True, dayfirst=False)
    frac_nat = d1.isna().mean()
    if frac_nat > 0.2:
        d2 = pd.to_datetime(s, errors="coerce", infer_datetime_format=True, dayfirst=True)
        return d2 if d2.isna().mean() < frac_nat else d1
    return d1


# -----------------------------------
# Load & clean history (Items, Date, Issuances)
# Returns daily rows but we will aggregate to MONTH later.
# -----------------------------------
def load_history_from_excel(
    path: Path = DATA_FILE,
    items_col: str = "Items",
    date_col: str = "Date",
    qty_col: str = "Issuances",
) -> pd.DataFrame:
    """
    Returns a clean DataFrame with columns:
      [date (datetime.date), item_name (str), quantity (int)]
    """
    if not path.exists():
        raise FileNotFoundError(f"Sales history file not found: {path}")

    df = _read_excel_with_engine(path)

    # normalize headers (case/whitespace agnostic)
    norm = {c: str(c).strip().lower() for c in df.columns}
    rev = {v: k for k, v in norm.items()}

    def col(name: str) -> str:
        k = name.strip().lower()
        if k not in rev:
            raise ValueError(f"Column '{name}' not found in data. Got columns: {list(df.columns)}")
        return rev[k]

    items_c = col(items_col)
    date_c = col(date_col)
    qty_c = col(qty_col)

    df = df[[items_c, date_c, qty_c]].copy()
    df.rename(columns={items_c: "item_name", date_c: "date", qty_c: "quantity"}, inplace=True)

    # clean types
    df["item_name"] = df["item_name"].astype(str).str.strip()
    df["date"] = _parse_dates_safely(df["date"])
    df = df.dropna(subset=["date"])
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).astype(float)

    # ensure date-only column, then group
    df["date"] = df["date"].dt.date
    df = df.groupby(["date", "item_name"], as_index=False)["quantity"].sum()

    return df  # columns: date, item_name, quantity


# -----------------------------------
# Monthly aggregation & eligibility
# -----------------------------------
def to_monthly(history_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert daily history to monthly totals per item.
    Returns columns: ['item_name', 'month', 'y', 'ds'] where:
      - 'month' is pandas.Period('M')
      - 'ds' is Month Start timestamp (required by Prophet)
      - 'y' is monthly quantity
    """
    df = history_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M")  # IMPORTANT: just "M", not "MS"
    monthly = (
        df.groupby(["item_name", "month"], as_index=False)["quantity"].sum()
        .rename(columns={"quantity": "y"})
        .sort_values(["item_name", "month"])
        .reset_index(drop=True)
    )
    monthly["ds"] = monthly["month"].dt.to_timestamp(how="start")  # month start
    return monthly  # item_name, month, y, ds


def eligible_items(monthly_df: pd.DataFrame, min_months: int = 12, min_sum: int = 10) -> List[str]:
    """
    Implements your Colab rule:
      eligible if months >= 12 OR total y >= 10
    Returns a list of item_name strings (original casing).
    """
    agg = monthly_df.groupby("item_name")["y"].agg(count="count", total="sum").reset_index()
    elig = agg[(agg["count"] >= min_months) | (agg["total"] >= min_sum)]
    return elig["item_name"].tolist()


# -----------------------------------
# Prophet model utilities (monthly)
# -----------------------------------
def _fit_monthly_prophet(monthly_item_df: pd.DataFrame) -> Prophet:
    """
    Train Prophet on MONTHLY data for a single item.
    Expects columns ['ds', 'y'].

    We keep settings mild to avoid "exploding" forecasts:
      - yearly seasonality only
      - multiplicative seasonality (good for scale changes)
      - moderate changepoint_prior_scale
    """
    # Ensure one row per month (in case of duplicates)
    monthly_item_df = (
        monthly_item_df
        .groupby(pd.Grouper(key="ds", freq="MS"))["y"]
        .sum()
        .reset_index()
    )

    m = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.2,
    )
    m.fit(monthly_item_df[["ds", "y"]])
    return m


def train_models_for_eligible_items(history_df: pd.DataFrame) -> Tuple[List[str], List[str]]:
    """
    Train and cache (in-memory) Prophet models for all ELIGIBLE items, per your rule.
    Returns (trained_items, skipped_items).
    """
    monthly = to_monthly(history_df)
    names = eligible_items(monthly)

    trained, skipped = [], []
    for name in names:
        item_df = monthly.loc[monthly["item_name"].str.casefold() == name.casefold()].copy()
        # Guard: Prophet needs >= 2 non-NaN rows
        if item_df["y"].dropna().shape[0] < 2:
            skipped.append(name)
            continue

        model = _fit_monthly_prophet(item_df[["ds", "y"]])
        ITEM_MODELS[name.casefold()] = model
        trained.append(name)

    return trained, skipped


def list_cached_models() -> List[str]:
    """
    Return list of item names with a cached Prophet model.
    """
    # We stored keys as lowercase; just return them as-is
    return sorted(ITEM_MODELS.keys())


# -----------------------------------
# SAFE FALLBACK LOGIC
# -----------------------------------
def fallback_next_month(item_df: pd.DataFrame) -> int:
    """
    Safe fallback when Prophet is unreliable due to limited data.

    Rules:
    - Use avg of last 3 non-zero months
    - If < 3 non-zero months → use last non-zero month
    - Always return int ≥ 0
    """
    recent = item_df[item_df["y"] > 0].tail(3)["y"].tolist()

    if len(recent) == 0:
        return 0
    if len(recent) < 3:
        return int(round(recent[-1]))

    return int(round(sum(recent) / len(recent)))


def forecast_next_month_safe(history_df: pd.DataFrame, item_name: str) -> int:
    """
    Returns ONLY next month's forecast (integer).
    Uses Prophet only if data is rich enough (>= 12 months).
    Otherwise uses a safe moving-average fallback.

    This is what powers /predictive/next_month endpoints.
    """
    monthly = to_monthly(history_df)
    item_df = monthly.loc[monthly["item_name"].str.casefold() == item_name.casefold()].copy()

    if item_df.empty:
        return 0

    # Count available months
    n_months = item_df["y"].dropna().shape[0]

    # If insufficient history → FALLBACK
    if n_months < 12:
        return fallback_next_month(item_df)

    # Try Prophet for richer histories
    key = item_name.casefold()
    model = ITEM_MODELS.get(key)

    try:
        if model is None:
            model = _fit_monthly_prophet(item_df[["ds", "y"]])
            ITEM_MODELS[key] = model

        future = model.make_future_dataframe(periods=1, freq="MS", include_history=False)
        fc = model.predict(future)

        # yhat might be float; clip & round
        next_month_pred = max(0, int(round(float(fc["yhat"].iloc[-1]))))
        return next_month_pred

    except Exception:
        # Any failure → safe fallback
        return fallback_next_month(item_df)


# -----------------------------------
# 6-MONTH FORECAST (used by /predictive/forecast/item + /forecast/all)
# -----------------------------------
def forecast_next_6_months_for_itemname(history_df: pd.DataFrame, item_name: str) -> pd.DataFrame:
    """
    Output monthly forecast DF: [month(YYYY-MM), forecast_qty] for next 6 months.

    Logic:
    - If item has >= 12 months of data → use Prophet over 6 months
    - If < 12 months → use the same fallback (moving average) for *each* of the next 6 months.
    """
    monthly = to_monthly(history_df)
    item_df = monthly.loc[monthly["item_name"].str.casefold() == item_name.casefold()].copy()
    if item_df.empty:
        raise ValueError(f"No history found for item: {item_name}")

    n_months = item_df["y"].dropna().shape[0]

    # Determine the start month for forecasting: the month after the last observed
    last_month = item_df["month"].max()  # Period('M')

    # If not enough history → fallback repeated for 6 months
    if n_months < 12:
        base = fallback_next_month(item_df)
        rows = []
        current_month = last_month
        for _ in range(6):
            current_month = current_month + 1  # next month
            rows.append({"month": str(current_month), "forecast_qty": int(base)})
        return pd.DataFrame(rows)

    # Use Prophet for 6 months when history is rich
    key = item_name.casefold()
    model = ITEM_MODELS.get(key)

    if model is None:
        if item_df["y"].dropna().shape[0] < 2:
            # Can't reasonably fit a time series model
            raise ValueError(f"Insufficient data to train a model for item: {item_name}")

        model = _fit_monthly_prophet(item_df[["ds", "y"]])
        ITEM_MODELS[key] = model

    future = model.make_future_dataframe(periods=6, freq="MS", include_history=False)
    fc = model.predict(future)[["ds", "yhat"]].copy()
    fc["month"] = fc["ds"].dt.to_period("M")
    fc["forecast_qty"] = (
        fc["yhat"]
        .fillna(0.0)
        .clip(lower=0.0)
        .round(0)
        .astype(int)
    )

    monthly_forecast = fc[["month", "forecast_qty"]].copy()
    monthly_forecast["month"] = monthly_forecast["month"].astype(str)
    return monthly_forecast


# -----------------------------------
# Restock plan + export (6 months)
# -----------------------------------
def recommended_restock_plan(monthly_fc: pd.DataFrame, current_stock: int) -> pd.DataFrame:
    """
    Simulate month-by-month usage and compute restock to keep stock >= 0.
    Returns: [month, forecast_qty, start_stock, recommended_restock, end_stock]
    """
    stock = int(current_stock)
    rows = []
    for _, r in monthly_fc.iterrows():
        mth = r["month"]
        need = float(r["forecast_qty"])
        end_stock = stock - need
        restock = 0
        if end_stock < 0:
            restock = math.ceil(-end_stock)
            end_stock = 0
        rows.append(
            {
                "month": mth,
                "forecast_qty": int(round(need)),
                "start_stock": int(stock),
                "recommended_restock": int(restock),
                "end_stock": int(end_stock),
            }
        )
        stock = end_stock
    return pd.DataFrame(rows)


def export_month_plan(item_name: str, plan_df: pd.DataFrame, filetype: str = "csv") -> str:
    """
    Save per-item 6-month plan to /exports as CSV or XLSX. Returns the file path.
    """
    safe = item_name.replace("/", "-").replace("\\", "-").replace(" ", "_")
    if filetype.lower() == "csv":
        out = EXPORT_DIR / f"{safe}_six_month_plan.csv"
        plan_df.to_csv(out, index=False)
    else:
        out = EXPORT_DIR / f"{safe}_six_month_plan.xlsx"
        with pd.ExcelWriter(out, engine="openpyxl") as w:  # requires openpyxl
            plan_df.to_excel(w, index=False, sheet_name="ForecastPlan")
    return str(out)


def all_items_summary(history_df: pd.DataFrame, stock_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one row per item_name:
      [item_name, current_stock, total_6mo_forecast, first_month_restock, total_recommended_restock]
    If an item isn't in stock_df, assume current_stock=0.
    Uses the same 6-month forecast function above (with fallback for sparse items).
    """
    stock_map = {
        str(n).strip().casefold(): int(q)
        for n, q in zip(stock_df["item_name"], stock_df["stock_quantity"])
    }
    rows = []
    for name in sorted(history_df["item_name"].unique().tolist(), key=str.casefold):
        try:
            monthly = forecast_next_6_months_for_itemname(history_df, name)
        except Exception:
            # skip items that fail for any reason
            continue

        current = int(stock_map.get(name.casefold(), 0))
        plan = recommended_restock_plan(monthly, current)

        total_fc = float(monthly["forecast_qty"].sum())
        total_restock = int(plan["recommended_restock"].sum()) if not plan.empty else 0
        first_restock = int(plan.iloc[0]["recommended_restock"]) if not plan.empty else 0

        rows.append(
            {
                "item_name": name,
                "current_stock": current,
                "total_6mo_forecast": int(round(total_fc)),
                "first_month_restock": first_restock,
                "total_recommended_restock": total_restock,
            }
        )

    return pd.DataFrame(rows)