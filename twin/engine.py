"""TwinEngine: the MAVERIC twin behind a small, action-level interface.

It holds the live network state (per-cell ON/OFF and tilt), runs MAVERIC's own
predict -> attach -> CCO KPI pipeline for a tick, and answers shadow runs
(KPIs before and after a proposed action list) without touching live state.
"""

import copy
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from twin import paths  # noqa: F401  (puts MAVERIC on sys.path)
from twin.paths import BDT_MODEL_PATH, CONFIG_PATH, GENERATED_DIR, TOPOLOGY_PATH
from twin.vocabulary import TILT_SET

from apps.coverage_capacity_optimization.cco_engine import CcoEngine  # isort: skip
from radp.digital_twin.rf.bayesian.bayesian_engine import BayesianDigitalTwin  # isort: skip
from radp.digital_twin.utils.cell_selection import perform_attachment  # isort: skip

logger = logging.getLogger(__name__)

# The Energy Saving app uses -95 dBm, but the twin trained on MAVERIC's synthetic data predicts
# RSRP around -130 dBm (median), so -95 marks 99.6% of UEs weak and no action is visible.
# -135 dBm puts about a third of UEs below it at baseline; it is shown on the demo page.
WEAK_COVERAGE_DBM = float(os.environ.get("TWIN_WEAK_DBM", "-135"))
OVER_COVERAGE_DB = float(os.environ.get("TWIN_OVER_DB", "-65"))
DEFAULT_TILT = 12.0
# Load = users attached per cell, as MAVERIC's own load-balancing app measures it. A cell above this
# many users counts as overloaded (an assumption for the demo, shown on the page).
CELL_CAPACITY_UES = int(os.environ.get("TWIN_CELL_CAPACITY_UES", "150"))


@dataclass
class NetworkState:
    on: Dict[str, bool]
    tilt: Dict[str, float]
    tick: int = 3

    def copy(self) -> "NetworkState":
        return copy.deepcopy(self)


@dataclass
class Kpis:
    tick: int
    weak_coverage_pct: float
    over_coverage_pct: float
    energy_saving_pct: float
    active_cells: int
    mean_rsrp_dbm: Optional[float]
    ues_per_cell: Dict[str, int] = field(default_factory=dict)
    sim_seconds: float = 0.0
    max_cell_load: int = 0          # most users on any one cell
    overloaded_cells: int = 0       # cells above CELL_CAPACITY_UES
    excess_ues: int = 0             # users above capacity, summed over overloaded cells (shows slow drift)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class ActionError(ValueError):
    pass


class TwinEngine:
    def __init__(self, test_day: int = 3, ue_sample: Optional[int] = None):
        topology = pd.read_csv(TOPOLOGY_PATH)
        config = pd.read_csv(CONFIG_PATH)
        site = pd.merge(topology, config, on="cell_id", how="left")
        site["cell_el_deg"] = site["cell_el_deg"].fillna(DEFAULT_TILT)
        site["hTx"], site["hRx"] = 25.0, 1.5
        self.site_config = site
        self.cell_ids: List[str] = site["cell_id"].tolist()
        self.models: Dict[str, BayesianDigitalTwin] = BayesianDigitalTwin.load_model_map_from_pickle(str(BDT_MODEL_PATH))
        self.base_ue_per_tick = self._load_ue_data(test_day, ue_sample)
        self.ue_per_tick = dict(self.base_ue_per_tick)
        self.event: Optional[dict] = None
        self.default_state = NetworkState(
            on={c: True for c in self.cell_ids},
            tilt={c: float(t) for c, t in zip(site["cell_id"], site["cell_el_deg"])},
        )
        self.live = self.default_state.copy()
        self._lock = threading.Lock()
        logger.info("TwinEngine ready: %d cells, %d cell models, test day %d", len(self.cell_ids), len(self.models), test_day)

    @staticmethod
    def _load_ue_data(day: int, sample: Optional[int]) -> Dict[int, pd.DataFrame]:
        out = {}
        day_dir = GENERATED_DIR / f"Day_{day}" / "ue_data_gym_ready"
        for tick in range(24):
            f = day_dir / f"generated_ue_data_for_cco_{tick}.csv"
            if f.exists():
                df = pd.read_csv(f)
                out[tick] = df.sample(n=sample, random_state=tick) if sample and len(df) > sample else df
        if not out:
            raise FileNotFoundError(f"No gym-ready UE data in {day_dir}; run python -m twin.train_twin --preprocess")
        return out

    # ---- topology for the demo page ------------------------------------------------
    def topology(self) -> List[dict]:
        cols = ["cell_id", "site_id", "cell_lat", "cell_lon", "cell_az_deg"]
        return self.site_config[cols].to_dict(orient="records")

    # ---- actions ---------------------------------------------------------------------
    def validate(self, action: dict) -> None:
        name, cell = action.get("name"), action.get("cell_id")
        if name == "read_kpi":
            return
        if cell not in self.cell_ids:
            raise ActionError(f"unknown cell_id {cell!r}")
        if name == "set_tilt":
            tilt = action.get("tilt")
            if tilt not in TILT_SET:
                raise ActionError(f"tilt must be one of {TILT_SET}, got {tilt!r}")
        elif name not in ("cell_off", "cell_on"):
            raise ActionError(f"unknown action {name!r}")

    def _apply_to(self, state: NetworkState, actions: List[dict]) -> NetworkState:
        new = state.copy()
        for a in actions:
            self.validate(a)
            if a["name"] == "cell_off":
                new.on[a["cell_id"]] = False
            elif a["name"] == "cell_on":
                new.on[a["cell_id"]] = True
            elif a["name"] == "set_tilt":
                new.tilt[a["cell_id"]] = float(a["tilt"])
        return new

    # ---- simulation ------------------------------------------------------------------
    def simulate(self, state: NetworkState, tick: Optional[int] = None, with_points: bool = False):
        tick = state.tick if tick is None else tick
        t0 = time.time()
        ue = self.ue_per_tick[tick]
        active = [c for c in self.cell_ids if state.on[c]]
        cfg = self.site_config.copy()
        cfg["cell_el_deg"] = cfg["cell_id"].map(state.tilt)
        frames = []
        for cell_id in active:
            row = cfg[cfg["cell_id"] == cell_id]
            pred = BayesianDigitalTwin.create_prediction_frames(site_config_df=row, prediction_frame_template=ue)[cell_id]
            self.models[cell_id].predict_distributed_gpmodel(prediction_dfs=[pred])
            frames.append(pred)
        n_cells = len(self.cell_ids)
        energy = 100.0 * (1 - len(active) / n_cells)
        if not frames:
            k = Kpis(tick, 100.0, 0.0, energy, 0, None, {}, time.time() - t0)
            return (k, []) if with_points else k
        rf = perform_attachment(pd.concat(frames, ignore_index=True), cfg[cfg["cell_id"].isin(active)])
        cov = CcoEngine.rf_to_coverage_dataframe(
            rf, weak_coverage_threshold=WEAK_COVERAGE_DBM, over_coverage_threshold=OVER_COVERAGE_DB
        )
        weak, over = CcoEngine.get_weak_over_coverage_percentages(cov)
        counts = rf["cell_id"].value_counts().reindex(active, fill_value=0).astype(int).to_dict()
        k = Kpis(tick, round(float(weak), 2), round(float(over), 2), round(energy, 2), len(active),
                 round(float(np.mean(rf["rsrp_dbm"])), 2), counts, round(time.time() - t0, 3),
                 max(counts.values()), sum(v > CELL_CAPACITY_UES for v in counts.values()),
                 sum(max(0, v - CELL_CAPACITY_UES) for v in counts.values()))
        if not with_points:
            return k
        pts = rf[["loc_x", "loc_y", "cell_id", "rsrp_dbm"]].round(5).to_dict(orient="records")
        return k, pts

    # ---- framework-facing API --------------------------------------------------------
    def kpis(self, tick: Optional[int] = None) -> Kpis:
        with self._lock:
            state = self.live.copy()
        return self.simulate(state, tick)

    def shadow(self, actions: List[dict], tick: Optional[int] = None) -> dict:
        """KPIs of the live state vs the live state with `actions` applied. Live state is untouched."""
        with self._lock:
            before_state = self.live.copy()
        after_state = self._apply_to(before_state, actions)
        t0 = time.time()
        before = self.simulate(before_state, tick)
        after = self.simulate(after_state, tick)
        delta = {k: round(getattr(after, k) - getattr(before, k), 2)
                 for k in ("weak_coverage_pct", "over_coverage_pct", "energy_saving_pct", "max_cell_load",
                           "overloaded_cells", "excess_ues")}
        return {"before": before.as_dict(), "after": after.as_dict(), "delta": delta,
                "seconds": round(time.time() - t0, 3)}

    def apply(self, actions: List[dict]) -> dict:
        with self._lock:
            self.live = self._apply_to(self.live, actions)
            state = self.live.copy()
        return {"state": self.state_dict(state), "kpis": self.simulate(state).as_dict()}

    def set_tick(self, tick: int) -> None:
        if tick not in self.ue_per_tick:
            raise ActionError(f"no UE data for tick {tick}")
        with self._lock:
            self.live.tick = tick

    def reset(self) -> None:
        with self._lock:
            tick = self.live.tick
            self.live = self.default_state.copy()
            self.live.tick = tick

    # ---- planned events ----------------------------------------------------------------
    def add_event(self, name: str, lon: float, lat: float, ues: int, sigma_deg: float, ticks: List[int],
                  seed: int = 1) -> dict:
        """A planned event (concert, match): a crowd of extra users around a point for the given hours."""
        rng = np.random.default_rng(seed)
        with self._lock:
            self.ue_per_tick = dict(self.base_ue_per_tick)
            for t in ticks:
                if t not in self.base_ue_per_tick:
                    raise ActionError(f"no UE data for tick {t}")
                base = self.base_ue_per_tick[t]
                crowd = pd.DataFrame({"mock_ue_id": np.arange(100_000, 100_000 + ues),
                                      "loc_x": rng.normal(lon, sigma_deg, ues),
                                      "loc_y": rng.normal(lat, sigma_deg * 0.75, ues), "tick": t})
                self.ue_per_tick[t] = pd.concat([base, crowd[[c for c in base.columns if c in crowd.columns]]],
                                                ignore_index=True)
            self.event = {"name": name, "lon": lon, "lat": lat, "ues": ues, "ticks": list(ticks)}
            return dict(self.event)

    def clear_event(self) -> None:
        with self._lock:
            self.ue_per_tick = dict(self.base_ue_per_tick)
            self.event = None

    def state_dict(self, state: Optional[NetworkState] = None) -> dict:
        s = state or self.live
        return {"tick": s.tick, "cells": {c: {"on": s.on[c], "tilt": s.tilt[c]} for c in self.cell_ids},
                "event": self.event, "cell_capacity_ues": CELL_CAPACITY_UES}

    def map_view(self) -> dict:
        with self._lock:
            state = self.live.copy()
        k, pts = self.simulate(state, with_points=True)
        return {"state": self.state_dict(state), "kpis": k.as_dict(), "ues": pts}
