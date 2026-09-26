# DryRoute 🌧️🗺️

**AI-powered navigation for students, built to predict flooded roads before you drive into one.**

DryRoute combines real-time weather data, elevation, stormwater infrastructure, and historical flood reports with AI to score flood risk at the road-segment level. It then plans safer commutes around that risk — synced to your class schedule via Google Calendar — so students get where they're going on time and dry.

---

## Table of Contents

- [Overview](#overview)
- [Core Features](#core-features)
- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [System Architecture](#system-architecture)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Team & Ownership](#team--ownership)
- [Project Status / Roadmap](#project-status--roadmap)
- [Contributing](#contributing)

---

## Overview

Flash flooding turns routine commutes into safety hazards, especially for students walking, biking, or driving to campus on tight schedules. DryRoute predicts which roads are likely to flood *before* a student leaves, and reroutes them around the risk — without making them late.

**Goal:** Make student transportation safer and more predictable by combining AI-powered flood prediction, personalized scheduling, and intelligent route planning.

## Core Features

| Feature | Description |
|---|---|
| 🌊 **Flood Prediction** | Analyzes weather forecasts, elevation, stormwater infrastructure, and historical flood data to predict which roads are at risk. |
| 🧭 **Smart Route Planning** | Identifies high-risk roads and recommends safer alternatives, factoring in travel time, traffic, and construction. |
| 📅 **Google Calendar Integration** | Reads upcoming classes/events to recommend departure times and plan routes that get students there on time. |
| 🚨 **Proactive Alerts** | Notifies users of potential flooding, road closures, and disruptions before they leave. |
| 🗺️ **Interactive Risk Map** | Displays road-level flood risk and lets users compare their usual route against safer alternatives. |
| 📊 **Personalized Dashboard** | Tracks time saved, high-risk segments avoided, trips planned, and alerts received. |
| ♿ **Accessibility & Safety** | Surfaces safer routes and accessible drop-off points for students with disabilities. |

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript |
| Backend | FastAPI (Python) |
| Database | MongoDB (Atlas) |
| ML | Python (pandas / scikit-learn) |
| Domain | GoDaddy |
| Hosting (frontend) | Vercel |
| Hosting (backend) | *TBD — see [docs/architecture.md](docs/architecture.md#hosting)* |
| Auth | Google OAuth 2.0 (Calendar API) |
| Mapping/Routing | *TBD — see integrations below* |
| Weather / Elevation / GIS | External APIs — see [Environment Variables](#environment-variables) |

> ⚠️ **Note:** Vercel serverless functions do not natively run a persistent FastAPI app well for this use case. The backend should be deployed separately (Render, Railway, or Fly.io are recommended for same-day setup) and the frontend on Vercel should call that backend URL. See `docs/architecture.md` for the finalized decision.

## Repository Structure

```
dryroute/
├── README.md
├── .env.example
├── docker-compose.yml
│
├── frontend/                  # React + TypeScript app
│   ├── package.json
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx
│   │   │   ├── map/
│   │   │   ├── dashboard/
│   │   │   └── settings/
│   │   ├── components/
│   │   │   ├── RouteMap.tsx
│   │   │   ├── RouteComparison.tsx
│   │   │   ├── FloodRiskLegend.tsx
│   │   │   └── CalendarEvents.tsx
│   │   ├── services/
│   │   │   └── api.ts
│   │   └── types/
│   └── public/
│
├── backend/                   # FastAPI app
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── api/
│   │   │   ├── routes.py
│   │   │   ├── calendar.py
│   │   │   ├── alerts.py
│   │   │   ├── reports.py
│   │   │   └── dashboard.py
│   │   ├── services/
│   │   │   ├── route_planner.py
│   │   │   ├── flood_risk.py
│   │   │   ├── traffic.py
│   │   │   ├── travel_time.py
│   │   │   └── calendar_sync.py
│   │   ├── db/
│   │   │   ├── mongo.py
│   │   │   └── schemas.py
│   │   └── integrations/
│   │       ├── weather.py
│   │       ├── elevation.py
│   │       ├── routing.py
│   │       └── google_calendar.py
│   └── tests/
│
├── ml/                         # Flood-risk model
│   ├── data/
│   │   ├── raw/
│   │   └── processed/
│   ├── notebooks/
│   │   └── exploration.ipynb
│   ├── src/
│   │   ├── features.py
│   │   ├── train.py
│   │   ├── predict.py
│   │   └── evaluate.py
│   └── models/
│       └── .gitkeep
│
├── data_pipeline/               # Standalone ETL scripts
│   ├── ingest_roads.py
│   ├── ingest_flood_reports.py
│   ├── ingest_weather.py
│   └── build_segments.py
│
└── docs/
    ├── architecture.md
    ├── api-contracts.md
    └── demo-script.md
```

## System Architecture

```
                     ┌────────────────────────┐
                     │   External Data Sources │
                     │  Weather · Elevation ·   │
                     │  GIS / Stormwater ·      │
                     │  Historical Flood Reports│
                     └───────────┬─────────────┘
                                 │
                                 ▼
                     ┌────────────────────────┐
                     │     data_pipeline/       │
                     │ ingest_*.py, build_segments.py │
                     │  → normalized road-segment set │
                     └───────────┬─────────────┘
                                 │
                                 ▼
                     ┌────────────────────────┐
                     │           ml/            │
                     │  features.py → train.py  │
                     │  → predict.py (risk score)│
                     └───────────┬─────────────┘
                                 │ risk score per segment
                                 ▼
                     ┌────────────────────────┐
                     │        MongoDB           │
                     │ road_segments, users,    │
                     │ routes, alerts, trips     │
                     └───────────┬─────────────┘
                                 │
                                 ▼
                     ┌────────────────────────┐        ┌───────────────────┐
                     │   FastAPI backend        │◄──────►│  Google Calendar   │
                     │ route_planner, alerts,   │        │  OAuth API         │
                     │ dashboard, calendar_sync │        └───────────────────┘
                     └───────────┬─────────────┘
                                 │ REST (JSON)
                                 ▼
                     ┌────────────────────────┐
                     │   React (TypeScript)     │
                     │ RouteMap, RouteComparison,│
                     │ FloodRiskLegend, Dashboard│
                     └────────────────────────┘
```

Full data flow, sequence diagrams, and the finalized hosting decision live in [`docs/architecture.md`](docs/architecture.md). A day-of execution plan and role breakdown is provided as the companion PDF: **DryRoute — Hackathon Plan & Role Breakdown**.

## Getting Started

### Prerequisites
- Node.js 18+
- Python 3.11+
- MongoDB Atlas cluster (or local `mongod`)
- Docker + Docker Compose (optional, for local full-stack spin-up)

### 1. Clone & configure environment
```bash
git clone <repo-url>
cd dryroute
cp .env.example .env   # fill in API keys — see Environment Variables below
```

### 2. Backend (FastAPI)
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend (React + TypeScript)
```bash
cd frontend
npm install
npm run dev
```

### 4. Full stack via Docker Compose
```bash
docker-compose up --build
```

### 5. ML pipeline (optional / offline)
```bash
cd ml
pip install -r requirements.txt
python src/train.py
python src/predict.py
```

## Environment Variables

Copy `.env.example` to `.env` and populate:

| Variable | Purpose |
|---|---|
| `MONGODB_URI` | MongoDB Atlas connection string |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Calendar OAuth |
| `WEATHER_API_KEY` | Weather forecast provider (e.g. NOAA / OpenWeather / Tomorrow.io) |
| `ELEVATION_API_KEY` | Elevation data (e.g. USGS / Google Elevation API / Open-Elevation) |
| `ROUTING_API_KEY` | Routing/mapping provider (e.g. Mapbox / Google Maps / OSRM self-hosted) |
| `NEXT_PUBLIC_API_BASE_URL` | Backend URL the frontend calls |
| `NEXT_PUBLIC_MAPS_API_KEY` | Client-side map rendering key |

See `docs/architecture.md` for the recommended provider per key and free-tier notes.

## Team & Ownership

| Area | Owner | Key Files |
|---|---|---|
| Data & GIS | *(assign)* | `data_pipeline/`, GIS layers |
| Machine Learning | *(assign)* | `ml/` |
| Backend, Routing & Calendar | *(assign)* | `backend/` |
| Frontend & Integration | *(assign)* | `frontend/` |

Full task-level breakdown and tonight's execution timeline are in the companion **Hackathon Plan & Role Breakdown** PDF.

## Project Status / Roadmap

- [ ] Road-segment dataset finalized (data & GIS)
- [ ] Baseline flood-risk scoring live (ML)
- [ ] FastAPI + MongoDB + Google OAuth wired up (backend)
- [ ] Route planning + travel-time calc working (backend)
- [ ] Map, dashboard, calendar UI connected to API (frontend)
- [ ] End-to-end demo scripted and rehearsed
- [ ] Deployed: frontend on Vercel, backend hosted, domain pointed via GoDaddy DNS

## Contributing

This is a hackathon project — coordinate through `docs/api-contracts.md` before changing any shared request/response shape so frontend and backend don't drift out of sync mid-build.
