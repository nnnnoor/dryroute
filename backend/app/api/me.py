"""GET /me, PUT /me: the student's profile (docs/api-contracts.md)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.integrations.google_calendar import request_store, session, check_origin

router = APIRouter()
USER = "demo"  # SessionStore scopes this key to the connected browser


class HomeIn(BaseModel):
    label: str | None = Field(None, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ProfileIn(BaseModel):
    """Every field is optional: only the ones sent change. null clears name and preferred_parking_id."""
    name: str | None = Field(None, max_length=60)
    home: HomeIn | None = None
    preferred_parking_id: str | None = None
    arrival_buffer_minutes: int | None = Field(None, ge=0, le=120)


def _profile(request: Request, user: dict) -> dict:
    lot = user.get("preferred_parking_id")
    return {
        "user_id": user["user_id"],
        "name": user.get("name"),
        "home": user["home"],
        "preferred_parking_id": lot,
        "preferred_parking_name": request.app.state.store.parking.at[lot, "name"] if lot else None,
        "arrival_buffer_minutes": user["arrival_buffer_minutes"],
    }


@router.get("/me")
def me(request: Request):
    return _profile(request, request_store(request).get_user(USER))


@router.put("/me")
def update_me(body: ProfileIn, request: Request):
    check_origin(request)
    s = request.app.state
    store = request_store(request)
    changes = body.model_dump(exclude_unset=True)
    if "home" in changes and changes["home"] is None:
        raise HTTPException(400, "home can be changed but not removed")
    if changes.get("arrival_buffer_minutes", 0) is None:
        raise HTTPException(400, "arrival_buffer_minutes can be changed but not removed")
    if "name" in changes:
        changes["name"] = (changes["name"] or "").strip() or None
    lot = changes.get("preferred_parking_id")
    if lot is not None and lot not in s.store.parking.index:
        raise HTTPException(404, f"Unknown parking_id {lot!r}")

    user = {**store.get_user(USER), **changes}
    store.save_user(USER, user)
    current = session(request)
    if current and current.get("alert_service"):
        current["alert_service"].invalidate()
    elif not current:
        s.alerts.invalidate()
    return _profile(request, user)
