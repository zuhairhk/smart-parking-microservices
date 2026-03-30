# Distributed Smart Parking System

This project implements a **distributed smart parking system** using **cloud-style microservices**:
- Sensor Simulation Service (publishes spot availability updates)
- Parking Management Service (consumes updates; keeps real-time state per zone/spot)
- Reservation Service (reserve/release flow for drivers)
- Dynamic Pricing Service (computes price based on demand + time of day)
- Frontend Dashboard (live view + reserve/release)

Communication pattern:
- **Asynchronous messaging** (RabbitMQ topic exchange) for sensor updates → parking state.
- **REST APIs** for queries/commands (availability, reserve, release, pricing).

## 1) Prereqs
- Install **Docker Desktop** (Windows/Mac) OR Docker Engine + docker-compose (Linux).

## 2) Run the whole system
From the project root:

```bash
docker compose up --build
```

Then open:
- Dashboard: http://localhost:8080
- RabbitMQ management: http://localhost:15672  
  (username: `guest`, password: `guest`)
- Sensor service: http://localhost:8001/health
- Parking service: http://localhost:8002/health
- Reservation service: http://localhost:8003/health
- Pricing service: http://localhost:8004/health

**Important:** wait ~5–10 seconds after startup so sensor messages populate the Parking service, then click **Refresh**.

To stop:
```bash
docker compose down
```

## 3) Quick API tests (curl)
List zones (might be empty for a few seconds on first start):
```bash
curl http://localhost:8002/zones
```

See all spots in zone A:
```bash
curl http://localhost:8002/zones/A/spots
```

Reserve first available spot in zone A:
```bash
curl -X POST http://localhost:8003/reserve \
  -H "Content-Type: application/json" \
  -d '{"driver_id":"demo123","zone":"A"}'
```

Release (replace values with your output):
```bash
curl -X POST http://localhost:8003/release \
  -H "Content-Type: application/json" \
  -d '{"driver_id":"demo123","reservation_id":"<PASTE_ID>"}'
```

Get price for zone A:
```bash
curl http://localhost:8004/price/A
```

## 4) Instructions if you'd like to test yourself
1. Open the dashboard, pick zone A, look at availability table updating (Refresh / Auto-refresh).
2. View price changes as occupancy changes.
3. Reserve first available (shows reservation_id).
4. View when the reserved spot becomes unavailable.
5. Release reservation to show spot becomes available again.
6. Can also visit RabbitMQ UI to view messages flowing.

## 5) Notes
- Reservation TTL is 300s by default as we set in `docker-compose.yml`.
  
