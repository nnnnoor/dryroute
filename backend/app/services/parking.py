"""FIU parking flood hazard and safer alternatives (docs/api-contracts.md, GET /parking and /routes parking).

The pipeline's parking_score (0-1) is static: how exposed a lot is when it rains hard (ponding, low ground,
flood-prone access roads). The backend scales it by the rain level of the ML run being served, so a
low-lying lot is fine on a dry day and hazardous in a storm.
"""
import math

from app.config import Settings
from app.db.store import Store
from app.services.flood_risk import RiskService, risk_label
from app.services.route_planner import M_PER_DEG_LAT, M_PER_DEG_LON_EQUATOR


class ParkingService:
    def __init__(self, store: Store, risk: RiskService, settings: Settings):
        self.store = store
        self.risk = risk
        self.settings = settings
        lots = store.parking
        points = lots.geometry.representative_point()  # inside the lot even for odd shapes
        self._center = {pid: (p.x, p.y) for pid, p in zip(lots.index, points)}
        blocked = settings.parking_not_suggested
        self._suggestable = {
            pid for pid, name, access in zip(lots.index, lots["name"], lots["access"])
            if not any(word in name for word in blocked) and access != "private"}

    def rain_factor(self) -> float:
        level = self.risk.weather()["rain_level"]
        return self.settings.parking_rain_factor.get(level, 1.0)

    def lot(self, parking_id: str, factor: float) -> dict:
        row = self.store.parking.loc[parking_id]
        hazard = float(row["parking_score"]) * factor
        lon, lat = self._center[parking_id]
        return {
            "parking_id": parking_id,
            "name": row["name"],
            "type": row["type"],
            "hazard_score": round(hazard, 3),
            "hazard_label": risk_label(hazard, self.settings),
            "center": [round(lon, 6), round(lat, 6)],
        }

    def all_lots(self) -> list[dict]:
        factor = self.rain_factor()
        return [{**self.lot(pid, factor), "geometry": geom.__geo_interface__}
                for pid, geom in zip(self.store.parking.index, self.store.parking.geometry)]

    def assess(self, parking_id: str) -> dict:
        """The planned lot's hazard and, if it's high, safer lots nearby (garages first, then closest)."""
        factor = self.rain_factor()
        planned = self.lot(parking_id, factor)
        hazardous = planned["hazard_label"] == "high"
        alternatives = []
        if hazardous:
            for pid in self._suggestable - {parking_id}:
                alt = self.lot(pid, factor)
                if alt["hazard_label"] == "low":
                    alt["distance_from_planned_m"] = round(self._distance(parking_id, pid))
                    alternatives.append(alt)
            alternatives.sort(key=lambda a: (a["type"] != "garage", a["distance_from_planned_m"]))
            alternatives = alternatives[:self.settings.parking_alternatives]
        return {"planned": planned, "hazardous": hazardous, "alternatives": alternatives}

    def message(self, assessment: dict) -> str | None:
        """Sentence to add to the trip recommendation, or None when the lot is fine."""
        if not assessment["hazardous"]:
            return None
        name = assessment["planned"]["name"]
        if not assessment["alternatives"]:
            return f"{name} may flood, and no nearby lot is clearly safer."
        alt = assessment["alternatives"][0]
        return f"{name} may flood. Park at {alt['name']} instead ({alt['distance_from_planned_m']} m away)."

    def _distance(self, a: str, b: str) -> float:
        (lon1, lat1), (lon2, lat2) = self._center[a], self._center[b]
        dx = (lon2 - lon1) * M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat1))
        dy = (lat2 - lat1) * M_PER_DEG_LAT
        return math.hypot(dx, dy)
