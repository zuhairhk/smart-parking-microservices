import asyncio
import json
import os
import random
from datetime import datetime, timezone

import aio_pika
from fastapi import FastAPI

APP_NAME = "sensor-service"
RABBIT_URL = os.getenv("RABBIT_URL", "amqp://guest:guest@rabbitmq:5672/")
EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "parking")
ROUTING_KEY = os.getenv("ROUTING_KEY", "spot.update")

# Simple simulation settings (override via env if you want)
ZONES = os.getenv("ZONES", "A,B").split(",")
SPOTS_PER_ZONE = int(os.getenv("SPOTS_PER_ZONE", "12"))
PUBLISH_EVERY_SECONDS = float(os.getenv("PUBLISH_EVERY_SECONDS", "1.0"))
FLIP_PROB = float(os.getenv("FLIP_PROB", "0.12"))  # probability a spot changes occupied/free per tick

app = FastAPI(title="Sensor Simulation Service", version="1.0")

_state = {}  # {(zone, spot_id): bool occupied}
_task: asyncio.Task | None = None

def _init_state():
    for z in ZONES:
        for i in range(1, SPOTS_PER_ZONE + 1):
            spot = f"{i:03d}"
            _state[(z, spot)] = random.random() < 0.35

async def publisher_loop():
    while True:
        try:
            print("Sensor connecting to RabbitMQ...")
            connection = await aio_pika.connect_robust(RABBIT_URL)
            async with connection:
                channel = await connection.channel()
                exchange = await channel.declare_exchange(
                    EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
                )
                print("Sensor connected to RabbitMQ, publishing...")
                while True:
                    # randomly flip some spots
                    for (z, s), occupied in list(_state.items()):
                        if random.random() < FLIP_PROB:
                            _state[(z, s)] = not occupied
                            msg = {
                                "zone": z,
                                "spot_id": s,
                                "occupied": _state[(z, s)],
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "source": APP_NAME,
                            }
                            body = json.dumps(msg).encode("utf-8")
                            await exchange.publish(
                                aio_pika.Message(body=body), routing_key=ROUTING_KEY
                            )
                    await asyncio.sleep(PUBLISH_EVERY_SECONDS)
        except Exception as e:
            print(f"Sensor publisher error: {e}, retrying in 2s...")
            await asyncio.sleep(2)

@app.on_event("startup")
async def startup():
    global _task
    _init_state()
    _task = asyncio.create_task(publisher_loop())

@app.on_event("shutdown")
async def shutdown():
    global _task
    if _task:
        _task.cancel()

@app.get("/health")
async def health():
    return {"ok": True, "service": APP_NAME, "zones": ZONES, "spots_per_zone": SPOTS_PER_ZONE}

@app.get("/state")
async def state():
    # returns current simulated state (for debugging)
    by_zone = {z: {} for z in ZONES}
    for (z, s), occ in _state.items():
        by_zone[z][s] = {"occupied": occ}
    return {"zones": by_zone}