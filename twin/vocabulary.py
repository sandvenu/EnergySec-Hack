"""The action vocabulary as MAVERIC's Energy Saving app defines it. Dependency-free, so the
gateway and rApps can use it without loading the twin."""

from typing import List, Optional

TILT_SET = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
OFF_INDEX = len(TILT_SET)  # RL agent's per-cell "OFF" action index


def rl_actions_to_vocabulary(action_indices, cell_ids: List[str], live: Optional[dict] = None) -> List[dict]:
    """Translate the RL agent's per-cell indices (0..10 tilt, 11 OFF) into action-vocabulary requests.

    `live` is the twin state dict {cell_id: {"on": bool, "tilt": float}}; only changes against it
    are emitted, so the framework sees real proposals.
    """
    out = []
    for idx, cell_id in zip([int(i) for i in action_indices], cell_ids):
        cur = live.get(cell_id) if live else None
        if idx == OFF_INDEX:
            if cur is None or cur["on"]:
                out.append({"name": "cell_off", "cell_id": cell_id})
            continue
        if cur is not None and not cur["on"]:
            out.append({"name": "cell_on", "cell_id": cell_id})
        tilt = TILT_SET[idx]
        if cur is None or cur["tilt"] != tilt:
            out.append({"name": "set_tilt", "cell_id": cell_id, "tilt": tilt})
    return out
