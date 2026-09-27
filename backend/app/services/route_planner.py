"""Usual vs flood-safer route on the road graph (docs/api-contracts.md, GET /routes).

Both routes avoid full closures and slow down through roadwork. The usual route is the fastest one,
like a normal navigation app. The safe route pays for flood risk: each edge costs
travel_time * (1 + route_risk_weight * risk), times route_high_risk_penalty if the edge is high risk.

The roads are always chosen here, from speed limits and flood risk. With a TomTom key, each chosen
route's drive time (ETA, leave-by) then comes from TomTom's traffic for that exact path.
"""
import hashlib
import math
from datetime import datetime, timedelta, timezone

import networkx as nx
import numpy as np

from app.config import Settings
from app.db.store import Store
from app.integrations.tomtom import TomTomTraffic
from app.services.flood_risk import LABEL_RANK, RiskService
from app.services.road_graph import RoadNetwork
from app.timeutil import iso_utc

M_PER_DEG_LAT = 110_540
M_PER_DEG_LON_EQUATOR = 111_320


def _distance(meters: float) -> str:
    return f"{meters / 1000:.1f} km" if meters >= 1000 else f"{round(meters)} m"


def worst_street(route: dict) -> str | None:
    """Named street with the most high-risk meters on the route."""
    by_name = {}
    for s in route["risk_segments"]:
        if s["risk_label"] == "high" and s["name"]:
            by_name[s["name"]] = by_name.get(s["name"], 0) + s["length_m"]
    return max(by_name, key=by_name.get) if by_name else None


class RoutePlanner:
    def __init__(self, network: RoadNetwork, store: Store, risk: RiskService, settings: Settings,
                 traffic: TomTomTraffic | None = None):
        self.G = network.G
        self.store = store
        self.risk = risk
        self.settings = settings
        self.traffic = traffic  # None: drive times from speed limits only

        # Snap only to nodes in the largest strongly connected component, so any two snapped points
        # are routable (the bbox cut leaves some one-way dead ends at the edges).
        scc = max(nx.strongly_connected_components(self.G), key=len)
        self._nodes = np.array(list(scc))
        self._node_lon = np.array([self.G.nodes[n]["x"] for n in self._nodes])
        self._node_lat = np.array([self.G.nodes[n]["y"] for n in self._nodes])

        self._keys = list(self.G.edges(keys=True))
        self._edge_ids = [f"{u}_{v}_{k}" for u, v, k in self._keys]
        self._travel_time = np.array([self.G.edges[k]["travel_time"] for k in self._keys])

    # ---------------------------------------------------------------- snapping
    def nearest_node(self, lat: float, lon: float) -> tuple[int, float]:
        """(node id, distance in m) of the closest routable node."""
        dx = (self._node_lon - lon) * M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat))
        dy = (self._node_lat - lat) * M_PER_DEG_LAT
        d2 = dx * dx + dy * dy
        i = int(np.argmin(d2))
        return int(self._nodes[i]), math.sqrt(d2[i])

    # ---------------------------------------------------------------- planning
    def plan(self, from_lat, from_lon, to_lat, to_lon,
             depart_at: datetime | None = None, arrive_by: datetime | None = None) -> dict:
        origin, d_from = self.nearest_node(from_lat, from_lon)
        dest, d_to = self.nearest_node(to_lat, to_lon)
        if origin == dest:
            raise ValueError("Start and destination are at the same place on the map")

        risk = self.risk.edge_risk().reindex(self._edge_ids).to_numpy()
        labels = self.risk.edge_labels().reindex(self._edge_ids).to_numpy()
        closures = self.store.active_closures()
        closed = {c["edge_id"] for c in closures if c["full_closure"]}
        slowed = {c["edge_id"] for c in closures if not c["full_closure"]}

        base = self._travel_time * np.array(
            [self.settings.route_roadwork_factor if e in slowed else 1.0 for e in self._edge_ids])
        safe = base * (1 + self.settings.route_risk_weight * risk)
        safe = np.where(labels == "high", safe * self.settings.route_high_risk_penalty, safe)

        usual_path = self._shortest(origin, dest, self._costs(base, closed))
        safe_path = self._shortest(origin, dest, self._costs(safe, closed))

        ctx = {"risk": dict(zip(self._edge_ids, risk)), "label": dict(zip(self._edge_ids, labels)),
               "time": dict(zip(self._edge_ids, base)),
               "closures": {c["edge_id"]: c for c in closures}}
        usual = self._describe(usual_path, ctx, depart_at, arrive_by)
        safe_route = self._describe(safe_path, ctx, depart_at, arrive_by)

        high = lambda r: {s["segment_id"] for s in r["risk_segments"] if s["risk_label"] == "high"}
        # Both routes already avoid full closures, so only flood risk can compromise the usual one
        compromised = usual["high_risk_m"] >= self.settings.route_compromised_min_m
        same_route = usual["route_id"] == safe_route["route_id"]
        comparison = {
            "extra_minutes": round(safe_route["eta_minutes"] - usual["eta_minutes"], 1),
            "high_risk_m_avoided": max(0, usual["high_risk_m"] - safe_route["high_risk_m"]),
            # high-risk segments on the usual route that the safe route skips
            "high_risk_segments_avoided": len(high(usual) - high(safe_route)),
        }
        return {
            "compromised": compromised,
            "recommendation": self._recommend(compromised, same_route, usual, safe_route, comparison),
            "coverage": self._coverage(d_from, d_to),
            "weather": self.risk.weather(),
            "usual": usual,
            "safe": safe_route,
            "same_route": same_route,
            "comparison": comparison,
            "parking": None,
        }

    def _recommend(self, compromised, same_route, usual, safe, comparison) -> dict:
        """What the student should do: one action for the frontend's logic, one sentence to show."""
        if not compromised:
            return {"action": "safe", "message": "No high flood-risk roads on your usual route."}
        if same_route:
            return {"action": "no_alternative",
                    "message": f"No safer route: your trip has to cross {_distance(usual['high_risk_m'])} of "
                               f"flood-prone road. Consider leaving later."}
        extra = comparison["extra_minutes"]
        extra_text = f"+{extra:g} min" if extra > 0 else "no extra time"
        if safe["high_risk_m"] < self.settings.route_compromised_min_m:
            street = worst_street(usual)
            where = f"on {street}" if street else "on your usual route"
            return {"action": "reroute",
                    "message": f"High flood risk {where}. Take the safer route ({extra_text})."}
        return {"action": "reroute_caution",
                "message": f"The safer route avoids {_distance(comparison['high_risk_m_avoided'])} of flood-prone "
                           f"road but still crosses {_distance(safe['high_risk_m'])}. Drive carefully ({extra_text})."}

    def _costs(self, cost: np.ndarray, closed: set[str]) -> dict:
        """(u, v, key) -> cost, without fully closed edges."""
        return {k: c for k, e, c in zip(self._keys, self._edge_ids, cost) if e not in closed}

    def _shortest(self, origin, dest, costs) -> list[str]:
        """Cheapest path as a list of edge_ids (picks the cheapest parallel edge between two nodes)."""
        def weight(u, v, keyed):
            vals = [costs[(u, v, k)] for k in keyed if (u, v, k) in costs]
            return min(vals) if vals else None  # None hides the edge (all parallels closed)

        nodes = nx.shortest_path(self.G, origin, dest, weight=weight)
        edges = []
        for u, v in zip(nodes[:-1], nodes[1:]):
            k = min((k for k in self.G[u][v] if (u, v, k) in costs), key=lambda k: costs[(u, v, k)])
            edges.append(f"{u}_{v}_{k}")
        return edges

    def _describe(self, edges, ctx, depart_at, arrive_by) -> dict:
        segs = self.store.segments
        coords = []
        for e in edges:
            pts = [[round(x, 6), round(y, 6)] for x, y in segs.at[e, "geometry"].coords]
            coords.extend(pts[1:] if coords and coords[-1] == pts[0] else pts)

        route_id = "r_" + hashlib.sha1("|".join(edges).encode()).hexdigest()[:8]
        free_flow = sum(ctx["time"][e] for e in edges)
        seconds, traffic = free_flow, None
        if self.traffic:
            # For arrive_by, ask about the departure our own estimate implies (one call, close enough)
            guess = arrive_by - timedelta(seconds=free_flow) if arrive_by else depart_at
            traffic = self.traffic.travel_times(route_id, coords, guess)
            if traffic:
                seconds = traffic["traffic_s"]
        if arrive_by:
            arrive = arrive_by
            depart = arrive_by - timedelta(seconds=seconds)
        else:
            depart = depart_at or datetime.now(timezone.utc)
            arrive = depart + timedelta(seconds=seconds)

        risk_segments = []
        for e in edges:
            score = ctx["risk"][e]
            label = ctx["label"][e]
            if label != "low":
                name = segs.at[e, "name"]
                risk_segments.append({
                    "segment_id": e,
                    "name": name if isinstance(name, str) else None,
                    "risk_score": round(float(score), 3),
                    "risk_label": label,
                    "length_m": round(float(segs.at[e, "length"])),
                })
        max_risk = max((ctx["risk"][e] for e in edges), default=0.0)
        worst_label = max((ctx["label"][e] for e in edges), key=LABEL_RANK.get, default="low")

        on_route = [ctx["closures"][e] for e in edges if e in ctx["closures"]]
        closures = list({c["closure_id"]: {"closure_id": c["closure_id"], "name": c["name"], "kind": c["kind"],
                                           "full_closure": c["full_closure"]} for c in on_route}.values())
        return {
            "route_id": route_id,
            "geometry": {"type": "LineString", "coordinates": coords},
            "distance_m": round(float(sum(segs.at[e, "length"] for e in edges))),
            "eta_minutes": round(seconds / 60, 1),
            # "live_traffic" (now), "predicted_traffic" (future departure) or "free_flow" (speed limits)
            "eta_source": ("live_traffic" if traffic["live"] else "predicted_traffic") if traffic else "free_flow",
            "traffic_delay_minutes": round(traffic["delay_s"] / 60, 1) if traffic else None,
            "depart_at": iso_utc(depart),
            "arrive_at": iso_utc(arrive),
            "max_risk": round(float(max_risk), 3),
            "risk_label": worst_label,
            "high_risk_m": sum(s["length_m"] for s in risk_segments if s["risk_label"] == "high"),
            "risk_segments": risk_segments,
            "closures": closures,
        }

    def _coverage(self, d_from: float, d_to: float) -> dict:
        limit = self.settings.coverage_snap_m
        notes = []
        if d_from > limit:
            notes.append(f"Start is outside the mapped area; the route begins at the nearest mapped road "
                         f"({d_from / 1000:.1f} km away).")
        if d_to > limit:
            notes.append(f"Destination is outside the mapped area; the route ends at the nearest mapped road "
                         f"({d_to / 1000:.1f} km away).")
        return {"origin_in_area": d_from <= limit, "destination_in_area": d_to <= limit,
                "note": " ".join(notes) or None}
