"""Train the MAVERIC twin in-process (no Docker/Kafka stack).

Steps mirror apps/energy_savings/main_app.py, but the Bayesian Digital Twin is trained
directly with the same settings as services/training/training_driver.py.

    python -m twin.train_twin --preprocess --bdt --rl --timesteps 25000
"""

import argparse
import logging
import time
from typing import Dict

import numpy as np
import pandas as pd

from twin import paths  # noqa: F401  (puts MAVERIC on sys.path)
from twin.paths import BDT_MODEL_PATH, CONFIG_PATH, GENERATED_DIR, RL_LOG_DIR, RL_MODEL_PATH, TOPOLOGY_PATH, TRAINING_DATA_PATH

from radp.digital_twin.rf.bayesian.bayesian_engine import BayesianDigitalTwin  # isort: skip

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("train_twin")

# Same bounds as MAVERIC services/training/training_driver.py
X_COLUMNS = ["cell_el_deg", "log_distance", "relative_bearing"]
Y_COLUMNS = ["avg_rsrp"]
X_MIN = {"cell_el_deg": -10, "cell_lat": -90, "cell_lon": -180, "log_distance": 0,
         "relative_bearing": 0, "relative_tilt": -90, "relative_tilt_squared": 0}
X_MAX = {"cell_el_deg": 50, "cell_lat": 90, "cell_lon": 180, "log_distance": 5,
         "relative_bearing": 360, "relative_tilt": 180, "relative_tilt_squared": 32400}


def preprocess(days):
    from apps.energy_savings.data_preprocessor import UEDataPreprocessor

    UEDataPreprocessor(base_data_dir=str(GENERATED_DIR)).run(days=days)


def train_bdt(iterations: int):
    topology_df = pd.read_csv(TOPOLOGY_PATH)
    training_df = pd.read_csv(TRAINING_DATA_PATH)
    per_cell = BayesianDigitalTwin.preprocess_ue_training_data(training_df, topology_df)
    model_map: Dict[str, BayesianDigitalTwin] = {}
    for cell_id, data in per_cell.items():
        t0 = time.time()
        model = BayesianDigitalTwin(data_in=[data], x_columns=X_COLUMNS, y_columns=Y_COLUMNS, x_min=X_MIN, x_max=X_MAX)
        losses = model.train_distributed_gpmodel(maxiter=iterations)
        model.predict_distributed_gpmodel([data.head(1)])
        model_map[cell_id] = model
        logger.info("BDT %s: %d rows, final loss %.4f, %.1fs", cell_id, len(data), losses[-1], time.time() - t0)
    BayesianDigitalTwin.save_model_map_to_pickle(model_file_path=str(BDT_MODEL_PATH), model_map=model_map)
    logger.info("Saved %d cell models to %s", len(model_map), BDT_MODEL_PATH)


def train_rl(days, timesteps: int):
    from apps.energy_savings.rl_trainer import run_rl_training

    run_rl_training(
        bdt_model_path=str(BDT_MODEL_PATH),
        base_ue_data_dir=str(GENERATED_DIR),
        training_days=days,
        topology_path=str(TOPOLOGY_PATH),
        config_path=str(CONFIG_PATH),
        rl_model_path=str(RL_MODEL_PATH),
        log_dir=str(RL_LOG_DIR),
        total_timesteps=timesteps,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preprocess", action="store_true")
    ap.add_argument("--bdt", action="store_true")
    ap.add_argument("--rl", action="store_true")
    ap.add_argument("--train-days", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--test-day", type=int, default=3)
    ap.add_argument("--bdt-iterations", type=int, default=100)
    ap.add_argument("--timesteps", type=int, default=25000)
    args = ap.parse_args()
    np.random.seed(7)

    for flag, fn in (
        (args.preprocess, lambda: preprocess(sorted(set(args.train_days + [args.test_day, 4])))),
        (args.bdt, lambda: train_bdt(args.bdt_iterations)),
        (args.rl, lambda: train_rl(args.train_days, args.timesteps)),
    ):
        if flag:
            t0 = time.time()
            fn()
            logger.info("Step done in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
