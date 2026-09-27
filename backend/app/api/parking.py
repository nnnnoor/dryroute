"""GET /parking: every FIU lot/garage with its flood hazard right now (docs/api-contracts.md)."""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/parking")
def parking(request: Request):
    return request.app.state.parking.all_lots()
