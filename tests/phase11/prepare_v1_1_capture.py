"""Independent synthetic review case; all writes stay in TEST_FIXTURE."""
from datetime import timedelta
import json
from src.gui.adapters import now
from tests.phase11.fixtures import create,payload,change
from tests.phase11.soak_fixture import seed

def main():
    root=create('v1_1_review_anomaly',12)
    payload(root,'probability_predictions','T1/P',{'pmf':[.2]*7})
    change(root,'phase10_realtime_v1.db',"DELETE FROM realtime_prediction_snapshots WHERE record_id='T2'")
    payload(root,'zuuu_normalized','obs',{'observation_time':(now()-timedelta(hours=5)).isoformat()})
    payload(root,'ecmwf_raw_runs','run',{'run_time':(now()-timedelta(hours=30)).isoformat()})
    payload(root,'engine_state','cycle',{'pid':99999999})
    folder=seed(root,hours=73,findings=['ACTIVE_HIGH'])
    path=folder/'evidence/LATEST_OBSERVER_STATUS.json';body=json.loads(path.read_text());body['sample_time']=(now()-timedelta(minutes=10)).isoformat();path.write_text(json.dumps(body))
    print(root)

if __name__=='__main__':main()
