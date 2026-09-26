# DryRoute API contract

**Status: draft v0.1.** Backend and frontend agree on this before building against it. Change a field
here first, then in code. Live, interactive docs for whatever is already built: `http://localhost:8000/docs`.

## Conventions

- **Base URL:** `http://localhost:8000` locally; the deployed URL goes in the frontend's `NEXT_PUBLIC_API_BASE_URL`.
- **Coordinates:** query params are always named (`from_lat`, `from_lon`), so order can't get mixed up.
  Geometry in responses is **GeoJSON, so `[lon, lat]`** (Leaflet wants `[lat, lon]`: flip when drawing).
- **Times:** ISO 8601 in **UTC** with `Z` (`2026-09-26T13:20:00Z`). The frontend converts to local time for display.
  Request params accept any ISO time with an offset.
- **Risk:** `risk_score` is 0–1 (higher = more likely to flood **given the current/forecast rain**). Labels:
  `low` < 0.35 ≤ `medium` < 0.6 ≤ `high`. Thresholds may be tuned; always color by `risk_label`, not the number.
  Live scores come from the ML job (see [ML → backend](#ml--backend-risk_scores-collection)); the backend serves them as-is.
- **IDs:** `segment_id` is a road edge id like `"99309740_99334400_0"`. `parking_id` is an OSM id like `"way/112768036"`.
- **Users:** until Google login is wired up, every call acts as one demo user. Later, login sets a session cookie
  (send requests with `credentials: "include"`); no endpoint shapes change.
- **Errors:** `{"detail": "message"}` with 400 / 404 / 422. A trip outside the mapped area is **not** an error: see `coverage`.
- **Mapped area:** roughly FIU (west) to Brickell (east), SW 40th St-ish (south) to the airport (north).
  Commutes starting further out get a partial route and a `coverage.note`.

## Endpoints

| Endpoint | What | Status |
|---|---|---|
| `GET /health` | Server up + data loaded | ✅ built |
| `GET /weather` | Rain used for risk right now | ✅ built |
| `GET /segments/risk` | Road risk in a map box, for coloring the map | ✅ built |
| `GET /routes` | Usual vs flood-safer route, + parking check | ✅ built |
| `GET /parking` | FIU lots/garages with flood hazard | ✅ built |
| `GET /calendar/events`, `GET /calendar/next-event` | Classes + when to leave | planned |
| `GET /alerts`, `POST /alerts/{alert_id}/read` | In-app alerts (poll every ~60 s) | planned |
| `GET /dashboard` | Personal stats | planned |
| `POST /demo/scenario` | Switch to the demo storm scores | ✅ built |

---

### `GET /weather`

```json
{
  "scenario": "live",
  "computed_at": "2026-09-26T13:00:00Z",
  "stale": false,
  "model_version": "rules-v1",
  "rain": {"rain_mm_next_3h": 18.4, "rain_mm_last_24h": 41.0, "max_hourly_mm": 9.2},
  "rain_level": "heavy"
}
```
The rain inputs of the ML run whose scores are being served. `scenario` is `"live"` or `"storm"` (demo).
`stale: true` means the latest run is too old (or missing) and roads fall back to the static baseline score.
`rain_level`: `none | light | moderate | heavy`.

### `GET /segments/risk`

Params: `west, south, east, north` (the visible map box, required), `min_risk` (default 0; send ~0.6 when
zoomed out to the whole area: about 4,300 streets, 1.5 MB).
Returns a GeoJSON FeatureCollection. The two directions of a two-way street are merged into one feature.

```json
{
  "type": "FeatureCollection",
  "truncated": false,
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "LineString", "coordinates": [[-80.1935, 25.7612], [-80.1929, 25.7615]]},
      "properties": {
        "segment_id": "123_456_0",
        "name": "Southwest 9th Street",
        "risk_score": 0.81,
        "risk_label": "high",
        "closed": false
      }
    }
  ]
}
```
At most 5,000 features; `truncated: true` means zoom in or raise `min_risk`.

### `GET /routes`

Params: `from_lat, from_lon` (required), then **either** `to_lat, to_lon` **or** `parking_id` (drive to that
FIU lot). Optional: `depart_at` or `arrive_by` (default: leave now).

```json
{
  "compromised": true,
  "recommendation": {
    "action": "reroute_caution",
    "message": "The safer route avoids 6.9 km of flood-prone road but still crosses 1.2 km. Drive carefully (+1.4 min)."
  },
  "coverage": {"origin_in_area": true, "destination_in_area": true, "note": null},
  "weather": { "...": "same as GET /weather" },
  "usual": {
    "route_id": "r_8f2c",
    "geometry": {"type": "LineString", "coordinates": [[-80.19, 25.76], [-80.37, 25.75]]},
    "distance_m": 21430,
    "eta_minutes": 27.5,
    "depart_at": "2026-09-26T12:25:00Z",
    "arrive_at": "2026-09-26T12:52:30Z",
    "max_risk": 0.81,
    "risk_label": "high",
    "high_risk_m": 2416,
    "risk_segments": [
      {"segment_id": "123_456_0", "name": "Southwest 9th Street", "risk_score": 0.81, "risk_label": "high", "length_m": 140}
    ],
    "closures": [
      {"closure_id": "fake_001", "name": "SW 8th St lane closure", "kind": "roadwork", "full_closure": false}
    ]
  },
  "safe": { "...": "same shape as usual" },
  "same_route": false,
  "comparison": {"extra_minutes": 1.2, "high_risk_m_avoided": 2113, "high_risk_segments_avoided": 15},
  "parking": null
}
```
- `compromised`: the usual route has at least 200 m of high-risk road.
- `recommendation`: what to tell the student. Use `action` for logic (colors, which route to highlight) and show
  `message` as-is:

  | `action` | When | Example `message` |
  |---|---|---|
  | `safe` | not compromised | "No flooding expected on your usual route." |
  | `reroute` | compromised; the safe route has < 200 m of high-risk road | "Flooding likely on Southwest 67th Avenue. Take the safer route (+0.1 min)." |
  | `reroute_caution` | compromised; the safe route is better but still has ≥ 200 m | "The safer route avoids 1.9 km of flood-prone road but still crosses 1.6 km. Drive carefully (+1.2 min)." |
  | `no_alternative` | compromised; no different route exists | "No safer route: your trip has to cross 3.9 km of flood-prone road. Consider leaving later." |
- `max_risk` / `risk_label`: the riskiest road on the route. `high_risk_m`: meters of high-risk road on it.
- `closures`: closures on the route. Both routes already avoid full closures and slow down through roadwork.
- `risk_segments`: only `medium`/`high` road pieces, in driving order (one entry per piece, so a long street can appear several times).
- `same_route: true` means the usual route is already the safest; `safe` then equals `usual`.
- `comparison.high_risk_segments_avoided`: high-risk pieces of the usual route that the safe route skips.
- `parking` is filled when `parking_id` is given:

```json
"parking": {
  "planned": {"parking_id": "way/112768036", "name": "North of University Towers Parking Lot", "type": "surface",
              "hazard_score": 0.74, "hazard_label": "high", "center": [-80.3763, 25.7551]},
  "hazardous": true,
  "alternatives": [
    {"parking_id": "way/513972395", "name": "Parkview Parking Deck", "type": "garage",
     "hazard_score": 0.193, "hazard_label": "low", "center": [-80.377255, 25.754714], "distance_from_planned_m": 103}
  ]
}
```
- `hazard_score`: the lot's flood exposure scaled by the current rain (dry day → every lot is low).
- `hazardous`: the planned lot is `high`. Then `alternatives` lists up to 3 `low` lots, **garages first**
  (upper decks stay dry), then closest to the planned lot. Otherwise `alternatives` is `[]`.
  Unnamed, loading-area, staff, compound and private lots are never suggested.
- When hazardous, `recommendation.message` also ends with e.g. "W10 Parking Lot may flood. Park at Panther
  Parking Garage instead (426 m away)."
- The route still goes to the planned lot. To draw the route to an alternative, call `/routes` again with its `parking_id`.

### `GET /parking`

No params. Returns every FIU lot/garage with its hazard right now (for the map):
```json
[{"parking_id": "way/112768036", "name": "…", "type": "garage | surface | street_side",
  "hazard_score": 0.74, "hazard_label": "high", "center": [-80.3763, 25.7551],
  "geometry": {"type": "Polygon", "coordinates": [[…]]}}]
```

### `GET /calendar/events` and `GET /calendar/next-event`

`/calendar/next-event` returns the next event plus the trip plan:
```json
{
  "event_id": "abc",
  "event_name": "COP 3530",
  "start_time": "2026-09-26T13:00:00Z",
  "end_time": "2026-09-26T14:15:00Z",
  "location": "PC 213",
  "location_point": {"lat": 25.7563, "lon": -80.3736},
  "recommended_departure": "2026-09-26T12:20:00Z",
  "trip": {"compromised": true, "eta_minutes": 30.9, "parking_hazardous": false},
  "route_query": {"from_lat": 25.76, "from_lon": -80.19, "parking_id": "way/…", "arrive_by": "2026-09-26T12:55:00Z"}
}
```
- Returns **204 No Content** when nothing is coming up.
- `location` is the event's location text as typed in the calendar; `location_point` is `null` when it couldn't be
  resolved to a place (then `recommended_departure`, `trip` and `route_query` are `null` too).
- Pass `route_query` straight to `GET /routes` to draw the trip.

`/calendar/events?hours=48` returns a list of events with the fields `event_id, event_name, start_time, end_time,
location, location_point`.

### `GET /alerts`, `POST /alerts/{alert_id}/read`

```json
[{"alert_id": "al_12", "created_at": "2026-09-26T11:50:00Z",
  "type": "route_flood | parking_flood | closure | leave_earlier",
  "severity": "info | warning | critical",
  "title": "Flooding likely on your route to COP 3530",
  "message": "SW 9th St is high risk. Leave by 12:15 and take the safer route (+3 min).",
  "event_id": "abc", "segment_id": "123_456_0", "parking_id": null, "read": false}]
```
Newest first. Poll about every 60 s and show unread ones as a banner/toast.

### `GET /dashboard`

```json
{"trips_planned": 12, "time_saved_minutes": 34, "risky_segments_avoided": 19, "alerts_received": 7}
```

### `POST /demo/scenario`

Body `{"scenario": "live" | "storm"}`. Everything after this serves that scenario's scores (see below);
`"live"` returns to the real forecast. Lets the demo show flooding on a dry day.

---

## ML → backend: `risk_scores` collection

**Status: proposal for the ML owner.** ML's scheduled job computes live risk and writes it to Mongo
(db `flood`); the backend only reads. The job runs about hourly.

**`risk_scores`**: one doc per street (`street_id` from segments; the model works at street level, see
`data-pipeline/ML_HANDOFF.md`). The backend applies it to both directions of the street.

```json
{"_id": "123_456_0", "scenario": "live", "run_id": "2026-09-26T13:00Z-live",
 "risk_score": 0.81, "risk_label": "high"}
```
- `_id` = `street_id`. Every street in `segments` gets a doc (bridges included; they can just be low).
- One set per scenario: `"live"` (real forecast) and `"storm"` (fixed heavy rain, e.g. 50 mm in 3 h, for the demo).
  Suggest `_id` = `"<scenario>|<street_id>"` so both sets fit in one collection.
- `risk_label` uses the thresholds in Conventions above.

**`risk_runs`**: one doc per completed run, written **after** all its scores, so the backend never reads a half-written set.

```json
{"_id": "2026-09-26T13:00Z-live", "scenario": "live", "computed_at": "2026-09-26T13:00:00Z",
 "model_version": "rules-v1",
 "rain": {"rain_mm_next_3h": 18.4, "rain_mm_last_24h": 41.0, "max_hourly_mm": 9.2},
 "streets_scored": 23163}
```
The backend serves the newest run per scenario. If there is none, or the live run is more than 3 h old,
it falls back to the static `risk_score` in `segments` and reports `stale: true`.

**Open questions for ML:** where the job runs (a laptop is fine for the demo; the backend host could also
run it on a schedule), and whether it can score a future hour (for trips planned hours ahead).
Until then the backend uses the latest run for every trip time.
