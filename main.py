import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from routers.auth import router as auth_router
from routers.users import router as users_router
from routers.items import router as items_router
from routers.predict import router as predict_router
from routers.orders import router as orders_router
from routers.sales import router as sales_router
from routers.predictive import router as predictive_router
from routers.reports import router as reports_router
from routers.dashboard import router as dashboard_router
from routers.activity_logs import router as activity_logs_router  # 👈 FIXED

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# ----------------------------------------------------------
# Root endpoint (Render health check / quick online test)
# ----------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok"}

# ----------------------------------------------------------
# CORS
# ----------------------------------------------------------
allowed = os.getenv(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------
# Routers
# ----------------------------------------------------------
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(items_router)
app.include_router(predict_router)
app.include_router(orders_router)
app.include_router(sales_router)
app.include_router(predictive_router)
app.include_router(dashboard_router)
app.include_router(reports_router)
app.include_router(activity_logs_router)
