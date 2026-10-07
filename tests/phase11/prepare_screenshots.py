"""Synthetic anomalies are created ONLY under docs/phase11/fixtures."""
import json
import PySide6
from src.gui.adapters import now
from datetime import timedelta
from tests.phase11.fixtures import create,payload,change

root=create('anomaly')
payload(root,'probability_predictions','T1/P',{'pmf':[.2]*7})
change(root,'phase10_realtime_v1.db',"DELETE FROM realtime_prediction_snapshots WHERE record_id='T2'")
payload(root,'zuuu_normalized','obs',{'observation_time':(now()-timedelta(hours=5)).isoformat()})
payload(root,'ecmwf_raw_runs','run',{'run_time':(now()-timedelta(hours=25)).isoformat()})
payload(root,'engine_state','cycle',{'pid':99999999})
print(json.dumps({'PySide6':PySide6.__version__,'fixture_root':str(root)}))
