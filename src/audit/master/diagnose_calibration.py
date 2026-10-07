import json
import math
import numpy as np
from .common import *
from .probability_audit import calibration_q

def run():
    c=snapshot(ROOT/'database/phase9_probability_v1.db');mass={r['record_id']:np.frombuffer(r['pmf'],dtype='<f8') for r in c.execute('SELECT * FROM phase9_probability_mass')};pred={r['record_id']:json.loads(r['payload_json']) for r in c.execute('SELECT * FROM phase9_probability_prediction')};out=[]
    for row in c.execute('SELECT * FROM phase9_calibration_registry'):
        r=json.loads(row['payload_json']);ids=r['calibration_ids']
        if not ids:continue
        loss=[float(np.mean([-math.log(calibration_q(mass[x],pred[x]['actual'],a)) for x in ids])) for a in (1.,.8,1.2)];chosen=(1.,.8,1.2)[min(range(3),key=lambda j:(loss[j],j))]
        if chosen!=r['alpha']:out.append(dict(id=r['record_id'],saved_alpha=r['alpha'],independent_alpha=chosen,saved_losses=r['selection_scores'],independent_losses=loss,loss_differences=[a-b for a,b in zip(loss,r['selection_scores'])]))
    output('CALIBRATION_DIAGNOSTIC.json',out);print('DIFFERENCES',len(out));print(json.dumps(out[:5],indent=2));c.close()

if __name__=='__main__':run()
