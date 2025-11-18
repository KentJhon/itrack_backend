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
from routers.activity_logs import router as activity_logs_router

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
# Base allowed origins (hard-coded)
base_origins = [
    "https://itrack-student-view.vercel.app",  # Vercel frontend
    "http://localhost:5173",                   # local dev (Vite)
]

# Extra origins from env (optional)
env_origins = os.getenv(
    "CORS_ALLOWED_ORIGINS",
    ""  # you can set more in Render if needed
)

extra_origins = [o.strip() for o in env_origins.split(",") if o.strip()]

origins = base_origins + extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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
