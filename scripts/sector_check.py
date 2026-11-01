import logging, warnings; warnings.filterwarnings("ignore"); logging.disable(logging.INFO)
import numpy as np, pandas as pd
from twin.engine import TwinEngine
from radp.digital_twin.rf.bayesian.bayesian_engine import BayesianDigitalTwin
eng = TwinEngine(); ue = eng.ue_per_tick[3].head(200)
cfg = eng.site_config
P = {}
for c in ["cell_5_0","cell_5_1","cell_5_2","cell_3_1"]:
    f = BayesianDigitalTwin.create_prediction_frames(site_config_df=cfg[cfg.cell_id==c], prediction_frame_template=ue)[c]
    eng.models[c].predict_distributed_gpmodel(prediction_dfs=[f]); P[c] = f["rxpower_dbm"].values
    print(c, "pred mean", P[c].mean().round(2), "std", P[c].std().round(2), "cols", [k for k in f.columns if k not in ue.columns][:12])
print("5_0 vs 5_1 abs diff mean", np.abs(P["cell_5_0"]-P["cell_5_1"]).mean().round(2))
for t in [0, 6, 12, 20]:
    f = BayesianDigitalTwin.create_prediction_frames(site_config_df=cfg[cfg.cell_id=="cell_5_0"].assign(cell_el_deg=t), prediction_frame_template=ue)["cell_5_0"]
    eng.models["cell_5_0"].predict_distributed_gpmodel(prediction_dfs=[f]); print("tilt", t, f["rxpower_dbm"].mean().round(2))
