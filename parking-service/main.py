import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional

import aio_pika
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_NAME = "parking-service"
RABBIT_URL = os.getenv("RABBIT_URL", "amqp://guest:guest@rabbitmq:5672/")
EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "parking")
ROUTING_KEY = os.getenv("ROUTING_KEY", "spot.update")

# Reserve TTL for demo (seconds)
RESERVATION_TTL_SECONDS = int(os.getenv("RESERVATION_TTL_SECONDS", "300"))

app = FastAPI(title="Parking Management Service", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# State
# key: (zone, spot_id)
# value: dict with occupied(bool), reserved_by(str|None), reserved_until(iso|None), last_update(ts)
_spots: Dict[Tuple[str, str], dict] = {}
_lock = asyncio.Lock()

def _ensure_spot(zone: str, spot_id: str):
    key = (zone, spot_id)
    if key not in _spots:
        _spots[key] = {
            "zone": zone,
            "spot_id": spot_id,
            "occupied": False,
            "reserved_by": None,
            "reserved_until": None,
            "last_update": None,
        }

def _utc_now():
    return datetime.now(timezone.utc)

async def _expire_reservations():
    while True:
        async with _lock:
            now = _utc_now()
            for k, v in _spots.items():
                if v["reserved_until"]:
                    try:
                        until = datetime.fromisoformat(v["reserved_until"])
                    except Exception:
                        until = None
                    if until and now >= until:
                        v["reserved_by"] = None
                        v["reserved_until"] = None
        await asyncio.sleep(2.0)

async def consumer_loop():
    import asyncio

    while True:
        try:
            print("Connecting to RabbitMQ...")

            connection = await aio_pika.connect_robust(RABBIT_URL)
            channel = await connection.channel()

            exchange = await channel.declare_exchange(
                EXCHANGE_NAME,
                aio_pika.ExchangeType.TOPIC,
                durable=True
            )

            queue = await channel.declare_queue("parking_updates", durable=True)
            await queue.bind(exchange, routing_key=ROUTING_KEY)

            print("Connected to RabbitMQ, consuming messages...")

            async with queue.iterator() as qiter:
                async for message in qiter:
                    async with message.process():
                        try:
                            payload = json.loads(message.body.decode("utf-8"))
                            zone = payload["zone"]
                            spot_id = payload["spot_id"]
                            occupied = bool(payload["occupied"])
                            ts = payload.get("ts") or _utc_now().isoformat()
                        except Exception:
                            continue

                        async with _lock:
                            _ensure_spot(zone, spot_id)
                            v = _spots[(zone, spot_id)]
                            v["occupied"] = occupied
                            v["last_update"] = ts

        except Exception as e:
            print("RabbitMQ connection failed:", e)
            print("Retrying in 2 seconds...")
            await asyncio.sleep(2)

@app.on_event("startup")
async def startup():
    asyncio.create_task(consumer_loop())
    asyncio.create_task(_expire_reservations())

@app.get("/health")
async def health():
    return {"ok": True, "service": APP_NAME}

def _is_reserved_active(v: dict) -> bool:
    if not v["reserved_until"] or not v["reserved_by"]:
        return False
    try:
        until = datetime.fromisoformat(v["reserved_until"])
    except Exception:
        return False
    return _utc_now() < until

@app.get("/zones")
async def zones():
    async with _lock:
        zones = sorted({z for (z, _s) in _spots.keys()})
    return {"zones": zones}

@app.get("/zones/{zone}/spots")
async def zone_spots(zone: str):
    async with _lock:
        out = []
        for (z, s), v in _spots.items():
            if z == zone:
                vv = v.copy()
                vv["reserved_active"] = _is_reserved_active(v)
                out.append(vv)
    if not out:
        raise HTTPException(status_code=404, detail="Zone not found (no spots seen yet). Wait a few seconds for sensors.")
    return {"zone": zone, "spots": sorted(out, key=lambda x: x["spot_id"])}

@app.get("/zones/{zone}/available")
async def zone_available(zone: str):
    async with _lock:
        candidates = []
        for (z, _s), v in _spots.items():
            if z != zone:
                continue
            reserved = _is_reserved_active(v)
            if (not v["occupied"]) and (not reserved):
                candidates.append(v)
    return {"zone": zone, "available": sorted([{"spot_id": v["spot_id"]} for v in candidates], key=lambda x: x["spot_id"])}

class ReserveRequest(BaseModel):
    driver_id: str

@app.post("/zones/{zone}/spots/{spot_id}/reserve")
async def reserve_spot(zone: str, spot_id: str, req: ReserveRequest):
    async with _lock:
        _ensure_spot(zone, spot_id)
        v = _spots[(zone, spot_id)]

        # cannot reserve if occupied
        if v["occupied"]:
            raise HTTPException(status_code=409, detail="Spot is currently occupied")

        # cannot reserve if already reserved
        if _is_reserved_active(v):
            raise HTTPException(status_code=409, detail="Spot is already reserved")

        until = _utc_now() + timedelta(seconds=RESERVATION_TTL_SECONDS)
        v["reserved_by"] = req.driver_id
        v["reserved_until"] = until.isoformat()
        return {"ok": True, "zone": zone, "spot_id": spot_id, "reserved_by": req.driver_id, "reserved_until": v["reserved_until"]}

@app.post("/zones/{zone}/spots/{spot_id}/release")
async def release_spot(zone: str, spot_id: str, req: ReserveRequest):
    async with _lock:
        _ensure_spot(zone, spot_id)
        v = _spots[(zone, spot_id)]
        if v["reserved_by"] != req.driver_id:
            raise HTTPException(status_code=403, detail="Only the reserving driver can release this spot")
        v["reserved_by"] = None
        v["reserved_until"] = None
        return {"ok": True, "zone": zone, "spot_id": spot_id, "released_by": req.driver_id}

@app.get("/stats/{zone}")
async def zone_stats(zone: str):
    async with _lock:
        total = 0
        occupied = 0
        reserved = 0
        for (z, _s), v in _spots.items():
            if z != zone:
                continue
            total += 1
            if v["occupied"]:
                occupied += 1
            if _is_reserved_active(v):
                reserved += 1
        if total == 0:
            raise HTTPException(status_code=404, detail="Zone not found")
    free = total - occupied
    available = total - occupied - reserved
    occ_rate = (occupied / total) if total else 0.0
    return {"zone": zone, "total": total, "occupied": occupied, "free": free, "reserved_active": reserved, "available": available, "occupancy_rate": occ_rate}
