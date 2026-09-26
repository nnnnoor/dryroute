"""Flood risk endpoints: /weather, /segments/risk, /demo/scenario (docs/api-contracts.md)."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


@router.get("/weather")
def weather(request: Request):
    return request.app.state.risk.weather()


@router.get("/segments/risk")
def segments_risk(
    request: Request,
    west: float = Query(..., ge=-180, le=180),
    south: float = Query(..., ge=-90, le=90),
    east: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-90, le=90),
    min_risk: float = Query(0.0, ge=0, le=1),
):
    if west >= east or south >= north:
        raise HTTPException(400, "Box must have west < east and south < north")
    features, truncated = request.app.state.risk.streets_in_box(west, south, east, north, min_risk)
    return {"type": "FeatureCollection", "truncated": truncated, "features": features}


class ScenarioIn(BaseModel):
    scenario: Literal["live", "storm"]


@router.post("/demo/scenario")
def set_scenario(body: ScenarioIn, request: Request):
    risk = request.app.state.risk
    risk.scenario = body.scenario
    return risk.weather()
