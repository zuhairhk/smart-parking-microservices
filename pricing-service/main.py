import os
from datetime import datetime, timezone
import math

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_NAME = "pricing-service"
PARKING_BASE_URL = os.getenv("PARKING_BASE_URL", "http://parking-service:8000")

# Simple pricing model (demo-friendly)
# base_price * (1 + demand_factor) * time_factor
BASE_PRICE = float(os.getenv("BASE_PRICE", "3.00"))  # dollars
MAX_MULTIPLIER = float(os.getenv("MAX_MULTIPLIER", "3.0"))

app = FastAPI(title="Dynamic Pricing Service", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def _local_hour_approx_utc():
    # In containers we treat UTC as "time of day" for the demo.
    return datetime.now(timezone.utc).hour

def _time_factor(hour: int) -> float:
    # morning peak (7-10), evening peak (16-19)
    if 7 <= hour <= 10:
        return 1.35
    if 16 <= hour <= 19:
        return 1.45
    if 0 <= hour <= 5:
        return 0.90
    return 1.00

@app.get("/health")
async def health():
    return {"ok": True, "service": APP_NAME}

@app.get("/price/{zone}")
async def price(zone: str):
    async with httpx.AsyncClient(timeout=8.0) as client:
        stats = await client.get(f"{PARKING_BASE_URL}/stats/{zone}")
        if stats.status_code != 200:
            raise HTTPException(status_code=502, detail="Could not query parking stats")
        st = stats.json()

    occ = float(st.get("occupancy_rate", 0.0))
    # demand factor ramps up as occupancy increases (nonlinear)
    demand_factor = (occ ** 2) * 1.8  # tuned for demo
    hour = _local_hour_approx_utc()
    tf = _time_factor(hour)

    multiplier = 1.0 + demand_factor
    multiplier *= tf
    multiplier = min(multiplier, MAX_MULTIPLIER)

    price = round(BASE_PRICE * multiplier, 2)
    return {
        "zone": zone,
        "base_price": BASE_PRICE,
        "occupancy_rate": occ,
        "time_hour_utc": hour,
        "time_factor": tf,
        "multiplier": round(multiplier, 3),
        "price": price,
    }
