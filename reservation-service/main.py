import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_NAME = "reservation-service"
PARKING_BASE_URL = os.getenv("PARKING_BASE_URL", "http://parking-service:8000")

app = FastAPI(title="Reservation Service", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# In-memory reservations for demo
# reservation_id -> record
_reservations: Dict[str, dict] = {}

def _utc_now():
    return datetime.now(timezone.utc).isoformat()

class ReserveRequest(BaseModel):
    driver_id: str
    zone: str

@app.get("/health")
async def health():
    return {"ok": True, "service": APP_NAME}

@app.post("/reserve")
async def reserve(req: ReserveRequest):
    async with httpx.AsyncClient(timeout=8.0) as client:
        # get available spots
        avail = await client.get(f"{PARKING_BASE_URL}/zones/{req.zone}/available")
        if avail.status_code != 200:
            raise HTTPException(status_code=502, detail="Could not query parking availability")
        available = avail.json().get("available", [])
        if not available:
            raise HTTPException(status_code=409, detail="No available spots in this zone right now")

        spot_id = available[0]["spot_id"]

        # reserve on parking-service
        resp = await client.post(
            f"{PARKING_BASE_URL}/zones/{req.zone}/spots/{spot_id}/reserve",
            json={"driver_id": req.driver_id},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Failed to reserve: {resp.text}")

        data = resp.json()
        reservation_id = str(uuid.uuid4())
        record = {
            "reservation_id": reservation_id,
            "driver_id": req.driver_id,
            "zone": req.zone,
            "spot_id": spot_id,
            "reserved_until": data.get("reserved_until"),
            "created_at": _utc_now(),
        }
        _reservations[reservation_id] = record
        return record

class ReleaseRequest(BaseModel):
    reservation_id: str
    driver_id: str

@app.post("/release")
async def release(req: ReleaseRequest):
    record = _reservations.get(req.reservation_id)
    if not record:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if record["driver_id"] != req.driver_id:
        raise HTTPException(status_code=403, detail="Only the reserving driver can release this reservation")

    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(
            f"{PARKING_BASE_URL}/zones/{record['zone']}/spots/{record['spot_id']}/release",
            json={"driver_id": req.driver_id},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Failed to release: {resp.text}")

    _reservations.pop(req.reservation_id, None)
    return {"ok": True, "released": True, "reservation_id": req.reservation_id}

@app.get("/reservations")
async def list_reservations():
    return {"reservations": list(_reservations.values())}
