"""Use frozen Phase6 values unchanged, retain every training label edge."""
import json
from .contracts import utc, require

def compute(definition,sample,states,predictions,history):
    key=sample['business_date_bjt'],sample['horizon'],definition['operation']
    state=states[key];prediction=predictions[key];days=json.loads(state['training_dates_json'])
    labels=[history[(d,sample['horizon'])] for d in days]
    issue=utc(sample['issue_time_utc'])
    require(state['training_cutoff']==sample['issue_time_utc'],'Frozen Phase6 cutoff changed')
    for l in labels:
        require(l['business_date_bjt']<sample['business_date_bjt'] and utc(l['label_eligibility_time_bjt'])<=issue and utc(l['day_end_utc'])<issue and utc(l['issue_time_utc'])<issue,'Historical label leakage')
    value=prediction['mos_continuous_tmax'] if definition['feature_name']=='m6_corrected_temperature_c' else state['bias_value']
    return value,None if value is not None else state['status'],state,labels
