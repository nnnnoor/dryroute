# DryRoute API contract

**Status: draft v0.1.** Backend and frontend agree on this before building against it. Change a field
here first, then in code. Live, interactive docs for whatever is already built: `http://localhost:8000/docs`.

## Conventions

- **Base URL:** `http://localhost:8000` locally; the deployed URL goes in the frontend's `NEXT_PUBLIC_API_BASE_URL`.
- **Coordinates:** query params are always named (`from_lat`, `from_lon`), so order can't get mixed up.
  Geometry in responses is **GeoJSON, so `[lon, lat]`** (Leaflet wants `[lat, lon]`: flip when drawing).
- **Times:** ISO 8601 in **UTC** with `Z` (`2026-09-26T13:20:00Z`). The frontend converts to local time for display.
  Request params accept any ISO time with an offset.
- **Risk:** `risk_score` is 0–1 (higher = more flood-prone **given the current/forecast rain**). It's a relative
  ranking, not a probability. **Always color and decide by `risk_label`** (`low | medium | high`), never by the number.
  Live scores **and labels** come from the ML job (see [ML → backend](#ml--backend-risk_scores-collection)); the
  backend serves them as-is. Only when there's no ML label does the backend fall back to its own cutoffs
  (`low` < 0.35 ≤ `medium` < 0.6 ≤ `high` on the static score).
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
| `GET /calendar/events`, `GET /calendar/next-event` | Classes + when to leave | ✅ built (fake calendar; Google OAuth pending) |
| `GET /alerts`, `POST /alerts/{alert_id}/read`, `POST /alerts/refresh` | In-app alerts (poll every ~60 s) | ✅ built |
| `GET /dashboard` | Personal stats | planned |
| `POST /demo/scenario` | Switch to the demo storm scores | ✅ built |

---

### `GET /weather`

```json
{
  "scenario": "live",
  "computed_at": "2026-09-26T13:00:00Z",
  "stale": false,
  "model_version": "daily_report_v2",
  "rain": {"rain_mm": 35.0, "rain_lag1_mm": 12.0, "rain_prior3_mm": 24.0},
  "rain_level": "heavy"
}
```
The rain inputs of the ML run whose scores are being served. `scenario` is `"live"` or `"storm"` (demo).
`stale: true` means the latest run is too old (or missing) and roads fall back to the static baseline score.
`rain_level`: `none | light | moderate | heavy`, from the day's total `rain_mm` (< 1, < 10, < 25, ≥ 25 mm).
Rain fields are ML's daily inputs (Miami calendar days, mm; `rain_prior3_mm` includes yesterday).

### `GET /segments/risk`

Params: `west, south, east, north` (the visible map box, required), `min_label` (`low` = all streets, the
default; `medium`; `high`). Send `high` when zoomed out to the whole area to keep the response small.
Features are sorted riskiest first (by label, then score).
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
At most 5,000 features; `truncated: true` means zoom in or raise `min_label`.

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
    "eta_source": "live_traffic",
    "traffic_delay_minutes": 6.2,
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
- `eta_minutes`, `depart_at`, `arrive_at` include traffic when available. `eta_source`: `live_traffic` (leaving now),
  `predicted_traffic` (a future `depart_at` / `arrive_by`) or `free_flow` (speed limits only: traffic unavailable,
  so the time is optimistic). `traffic_delay_minutes`: minutes lost to traffic, `null` for `free_flow`.
  The backend always chooses the roads (flood-aware); traffic only changes the times.
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

`/calendar/next-event` returns the next **in-person** event (online ones are skipped) plus the trip plan.
Params: `from_lat, from_lon` (the student's current location; optional, both or neither; default: saved home).
```json
{
  "event_id": "fake0_20260928",
  "event_name": "COP 3530 Data Structures",
  "start_time": "2026-09-28T13:00:00Z",
  "end_time": "2026-09-28T14:15:00Z",
  "location": "PC 213",
  "location_point": {"lat": 25.755517, "lon": -80.373785},
  "recommended_departure": "2026-09-28T12:25:00Z",
  "leave_in_minutes": 85,
  "trip": {
    "compromised": true,
    "recommendation": {"action": "reroute_caution", "message": "The safer route avoids 6.9 km of flood-prone road but still crosses 1.2 km. Drive carefully (+1.4 min)."},
    "take": "safe",
    "eta_minutes": 21.1,
    "parking_id": "way/112762942",
    "parking_name": "Gold Parking Garage",
    "parking_hazardous": false,
    "walk_minutes": 3
  },
  "route_query": {"from_lat": 25.7617, "from_lon": -80.1918, "parking_id": "way/112762942", "arrive_by": "2026-09-28T12:47:00Z"}
}
```
- Returns **204 No Content** when no in-person event is coming up in the next 7 days.
- `location` is the location text as typed in the calendar. It's matched to an FIU building code ("PC 213",
  "PC213", "Graham Center (GC) 243"). `location_point` is `null` when no building matched (then
  `recommended_departure`, `leave_in_minutes`, `trip` and `route_query` are `null` too).
- `recommended_departure` = class start − 10 min buffer − walk from the lot − drive time of the route to take
  (`take`: `safe` when the recommendation is to reroute, else `usual`), rounded down to the minute.
  `leave_in_minutes` is negative when that time has passed.
- Parking: the student's preferred lot, else the nearest usable lot to the building. If no lot is within ~800 m
  (e.g. the Engineering Center), the trip goes to the building and the parking fields are `null`/`false`.
- Pass `route_query` straight to `GET /routes` to draw the trip.

`/calendar/events?hours=48` (1–168) returns a list of events with the fields `event_id, event_name, start_time,
end_time, location, location_point`.

### `GET /alerts`, `POST /alerts/{alert_id}/read`, `POST /alerts/refresh`

Poll `GET /alerts` about every 60 s and show unread ones as a banner/toast. Each poll rebuilds the alerts
(at most once a minute), so they follow the student's next class, the weather, the tide and the demo scenario.
```json
[{"alert_id": "al_3725145b3f",
  "type": "route_flood",
  "severity": "warning",
  "title": "Flooding likely on your route to COP 3530 Data Structures",
  "message": "The safer route avoids 6.9 km of flood-prone road but still crosses 1.2 km. Drive carefully (+1.4 min). Leave by 8:25 AM.",
  "source": "trip", "event_id": "fake0_20260928", "segment_id": "123_456_0", "parking_id": null,
  "read": false, "active": true,
  "created_at": "2026-09-28T11:00:00Z", "updated_at": "2026-09-28T11:00:00Z",
  "resolved_at": null, "expires_at": "2026-09-28T13:00:00Z"}]
```
| `type` | `source` | When | Links |
|---|---|---|---|
| `route_flood` | trip | next class (within 12 h): usual route is compromised | `event_id`, `segment_id` (worst street) |
| `parking_flood` | trip | the lot for that class is high hazard | `event_id`, `parking_id` |
| `leave_earlier` | trip | traffic adds ≥ 10 min (needs TomTom) | `event_id` |
| `closure` | trip | construction/closure on the route | `event_id` |
| `weather_warning` | nws | a National Weather Service flood alert is in effect | — |
| `tide` | tide | Biscayne Bay (Virginia Key) at/above the NWS flood level | — |

- **Sorted** most severe first, then most recently updated. `severity`: `info | warning | critical`.
- **Stable ids:** the same situation keeps its `alert_id`, so `read` sticks across rebuilds (unless the alert
  gets more severe, then it's unread again). `created_at` is when that situation started.
- **Resolved:** when the situation is over (storm passed, lot fine again) the alert drops off the list
  (`GET /alerts?include_resolved=true` still shows it with `active: false`, `resolved_at`).
  If a source couldn't be checked, its alerts stay as they were: "couldn't check" is never "all clear".
- `message` times are Miami local time ("Leave by 8:25 AM"); all timestamp fields are UTC as usual.
- `POST /alerts/{alert_id}/read` → `{"alert_id": "...", "read": true}` (404 for an unknown id).
- `POST /alerts/refresh` rebuilds right away and returns the list (e.g. right after `POST /demo/scenario` in the demo).

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
{"_id": "live|123_456_0", "street_id": "123_456_0", "scenario": "live", "run_id": "2026-09-26T13:00Z-live",
 "risk_score": 0.81, "risk_label": "high"}
```
- `street_id` as its own field (the backend also accepts it as the part of `_id` after the last `|`).
  Every street in `segments` gets a doc (bridges included; they can just be low).
- One set per scenario: `"live"` (real forecast) and `"storm"` (fixed heavy rain, e.g. 50 mm in 3 h, for the demo).
  Suggest `_id` = `"<scenario>|<street_id>"` so both sets fit in one collection.
- **`risk_label` is defined by ML** (decided 2026-09-26). ML's `risk_score` is a percentile ranking (a typical
  street scores ~0.5 even on a dry day), so fixed cutoffs don't work; ML decides which scores mean
  `low` / `medium` / `high` for each run. The backend uses the label for map colors, the safe route's
  high-risk penalty and "compromised". `risk_score` is only used to order roads and to weigh the safe route.
- A street with no `risk_label` (or a missing street) falls back to the backend's cutoffs.

**`risk_runs`**: one doc per completed run, written **after** all its scores, so the backend never reads a half-written set.

```json
{"_id": "2026-09-26T13:00Z-live", "scenario": "live", "computed_at": "2026-09-26T13:00:00Z",
 "model_version": "daily_report_v2",
 "rain": {"rain_mm": 35.0, "rain_lag1_mm": 12.0, "rain_prior3_mm": 24.0},
 "streets_scored": 23163}
```
The backend serves the newest run per scenario. If there is none, or the live run is more than 3 h old,
it falls back to the static `risk_score` in `segments` and reports `stale: true`.

**Open questions for ML:** where the job runs (a laptop is fine for the demo; the backend host could also
run it on a schedule), and whether it can score a future hour (for trips planned hours ahead).
Until then the backend uses the latest run for every trip time.
