"""File locations for the twin. Importing this module puts MAVERIC on sys.path."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAVERIC_ROOT = Path(os.environ.get("MAVERIC_ROOT", ROOT.parent / "maveric-1.0.0" / "maveric-1.0.0"))
if str(MAVERIC_ROOT) not in sys.path:
    sys.path.insert(0, str(MAVERIC_ROOT))

DATA_ROOT = Path(os.environ.get("TWIN_DATA", ROOT / "twin_data" / "energy_saving_data"))
STATIC_DIR = DATA_ROOT / "data"
GENERATED_DIR = DATA_ROOT / "generated_data"
TOPOLOGY_PATH = STATIC_DIR / "topology.csv"
CONFIG_PATH = STATIC_DIR / "config.csv"
TRAINING_DATA_PATH = STATIC_DIR / "dummy_ue_training_data.csv"

MODEL_DIR = Path(os.environ.get("TWIN_MODELS", ROOT / "twin_data" / "models"))
BDT_MODEL_PATH = MODEL_DIR / "bdt_model_map.pickle"
RL_MODEL_PATH = MODEL_DIR / "energy_saver_agent.zip"
RL_LOG_DIR = MODEL_DIR / "rl_training_logs"
