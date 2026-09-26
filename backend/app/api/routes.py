"""GET /routes: usual vs flood-safer route (docs/api-contracts.md)."""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/routes")
def routes(
    request: Request,
    from_lat: float = Query(..., ge=-90, le=90),
    from_lon: float = Query(..., ge=-180, le=180),
    to_lat: float | None = Query(None, ge=-90, le=90),
    to_lon: float | None = Query(None, ge=-180, le=180),
    parking_id: str | None = None,
    depart_at: datetime | None = None,
    arrive_by: datetime | None = None,
):
    state = request.app.state
    if parking_id is not None:
        if to_lat is not None or to_lon is not None:
            raise HTTPException(400, "Give either to_lat/to_lon or parking_id, not both")
        if parking_id not in state.store.parking.index:
            raise HTTPException(404, f"Unknown parking_id {parking_id!r}")
        point = state.store.parking.at[parking_id, "geometry"].representative_point()
        to_lat, to_lon = point.y, point.x
    elif to_lat is None or to_lon is None:
        raise HTTPException(400, "Give a destination: to_lat and to_lon, or parking_id")

    if depart_at and arrive_by:
        raise HTTPException(400, "Give depart_at or arrive_by, not both")
    for name, t in (("depart_at", depart_at), ("arrive_by", arrive_by)):
        if t is not None and t.tzinfo is None:
            raise HTTPException(400, f"{name} needs a timezone, e.g. 2026-09-26T13:00:00Z")

    try:
        result = state.planner.plan(from_lat, from_lon, to_lat, to_lon, depart_at, arrive_by)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if parking_id is not None:
        result["parking"] = state.parking.assess(parking_id)
        extra = state.parking.message(result["parking"])
        if extra:
            result["recommendation"]["message"] += " " + extra
    return result
