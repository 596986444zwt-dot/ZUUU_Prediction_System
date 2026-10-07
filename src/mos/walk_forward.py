"""Chronological immutable prediction attempts; evaluation labels attached last."""
import json
from .contracts import MODELS, VERSION
from .bias import select_bias

def evaluate(samples, created='AUDIT'):
    predictions, states = [], []
    history = []
    # Equal issue times cannot expose unsettled/current target errors to each other.
    for s in sorted(samples, key=lambda r: (r['issue_time_utc'], r['business_date_bjt'], r['horizon'])):
        for model in MODELS:
            bias, selected, fallback, components = select_bias(s, history, model)
            status = 'PREDICTED' if bias is not None else (
                'BLOCKED_LABEL_AVAILABILITY' if not any(r['settlement_verified'] for r in samples)
                else 'INSUFFICIENT_HISTORY')
            lineage = [{'date': r['business_date_bjt'], 'horizon': r['horizon'],
                        'settled_at_utc': r['settled_at_utc'], 'raw_error': r['raw_error']} for r in selected]
            state = dict(target_business_date=s['business_date_bjt'], horizon=s['horizon'], model=model,
                bias_value=bias, bias_window={'M2': '7_CALENDAR_DAYS', 'M3': '30_CALENDAR_DAYS'}.get(model, 'EXPANDING'),
                training_cutoff=s['issue_time_utc'], training_start=selected[0]['business_date_bjt'] if selected else None,
                training_end=selected[-1]['business_date_bjt'] if selected else None, training_n=len(selected),
                latest_settlement=max((r['settled_at_utc'] for r in selected), default=None),
                fallback_path=fallback, training_lineage_json=json.dumps(lineage, sort_keys=True),
                components_json=json.dumps(components, sort_keys=True), status=status)
            states.append(state)
            # Compute forecast before attaching current evaluation label.
            forecast = s['raw_ecmwf_tmax']-bias if bias is not None else None
            error = forecast-s['observed_tmax'] if forecast is not None else None
            predictions.append(dict(target_business_date=s['business_date_bjt'], horizon=s['horizon'], model=model,
                issue_time=s['issue_time_utc'], selected_ecmwf_run=s['selected_ecmwf_run_time_utc'],
                available_time=s['selected_ecmwf_source_available_time_utc'], availability_semantics=s['ecmwf_availability_semantics'],
                raw_ecmwf_tmax=s['raw_ecmwf_tmax'], mos_continuous_tmax=forecast,
                observed_tmax=s['observed_tmax'], raw_error=s['raw_error'], mos_error=error,
                absolute_raw_error=abs(s['raw_error']), absolute_mos_error=abs(error) if error is not None else None,
                bias_method=model, bias_value=bias, bias_window=state['bias_window'], season=s['season'],
                month=s['month'], year=s['year'], lead_start_hours=s['lead_start_hours'], lead_end_hours=s['lead_end_hours'],
                training_cutoff=state['training_cutoff'], training_start=state['training_start'], training_end=state['training_end'],
                training_n=state['training_n'], fallback_path=fallback, status=status,
                model_spec=model, model_version=VERSION, created_at=created))
        history.append(s)
    return predictions, states
