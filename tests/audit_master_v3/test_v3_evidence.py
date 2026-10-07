"""Auditor evidence consistency tests, not a claim the production guard passes."""
import json
import pytest
from src.audit.master_v3.common import OUT,TEMP,digest,snapshot

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))

def test_guard_uses_unique_fresh_fixtures():
    r=read('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json')
    paths=[x['fixture'] for x in r['cases']+[r['valid']]]
    assert len(paths)==len(set(paths)) and all(str(TEMP) in x for x in paths)

def test_guard_verdict_agrees_with_cases():
    r=read('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json')
    assert r['counts']['NEW_REFERENCE_GUARD_FAILURE_COUNT']==sum(x['status']=='FAIL' for x in r['cases'])
    assert (r['status']=='FAIL')==any(x['status']=='FAIL' for x in r['cases'])

def test_v2_conflict_reproduction_is_not_failed_write():
    x=read('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json')['v2_conflict']['fresh_reuse_reproduction']
    assert x['first_valid'] and x['before']==x['after'] and sum(x['before'].values())==4
    assert all(x['illegal_rejected']) and not x['second_valid']

@pytest.mark.parametrize('case',['missing_state','wrong_state_reference','wrong_metadata','state_payload_id_mismatch','probability_state_mismatch','forced_transaction_failure'])
def test_rejection_evidence_has_no_components(case):
    x=next(r for r in read('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json')['cases'] if r['case']==case)
    assert x['rejected'] and sum(x['persisted_components'].values())==0 and not x['orphan_count']

def test_valid_snapshot_has_four_complete_components():
    x=read('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json')['valid']
    assert x['accepted'] and x['component_links']==1 and sum(x['persisted_components'].values())==4

def test_forbidden_context_is_reported_not_hidden():
    x=read('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json')
    failed={r['case'] for r in x['cases'] if r['status']=='FAIL'}
    assert failed=={'state_prediction_horizon_mismatch','state_prediction_target_mismatch','registered_future_state'}
    assert x['status']=='FAIL' # Passing auditor test must not erase system finding.
