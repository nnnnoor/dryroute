"""The routing graph (data-pipeline graph.graphml), held in memory.

Edges are keyed like the pipeline: edge_id = f"{u}_{v}_{key}", identical to segments' edge_id and
Mongo _id. Never rebuild the graph here: a fresh OSM pull changes every edge_id.
"""
from pathlib import Path

import networkx as nx
import osmnx as ox


class RoadNetwork:
    def __init__(self, graphml: Path):
        G = ox.load_graphml(graphml)
        G = ox.add_edge_speeds(G)        # speed_kph from maxspeed, else highway-type averages
        G = ox.add_edge_travel_times(G)  # travel_time in seconds
        self.G: nx.MultiDiGraph = G
        self.edge_ids = {f"{u}_{v}_{k}" for u, v, k in G.edges(keys=True)}

    def check_matches(self, segment_ids) -> None:
        """Fail fast if the graph and the segment data come from different pipeline runs."""
        seg = set(segment_ids)
        if seg != self.edge_ids:
            raise RuntimeError(
                f"graph.graphml and segments disagree: {len(seg - self.edge_ids)} ids only in segments, "
                f"{len(self.edge_ids - seg)} only in the graph. Pull the latest data-pipeline outputs.")
