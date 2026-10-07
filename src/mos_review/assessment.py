"""Descriptive candidate assessment; never feeds OOS forecasts or tunes weights."""
from src.mos.metrics import summarize, calculate
from .contracts import MODELS

def assess(metrics, slices):
    decisions = []
    for model in MODELS:
        primary = [r for r in metrics if r['model'] == model and r['horizon'] in ('T1','T2')]
        stability = [r for r in slices if r['model'] == model and r['horizon'] in ('T1','T2')
                     and r['slice_type'] in ('season','year') and r['n'] >= 30]
        eligible = (len(primary) == 2 and all(r['mae_improvement'] is not None and r['mae_improvement'] > 0 and r['rmse_improvement'] > 0 for r in primary)
                    and bool(stability) and all(r['mae_improvement'] >= -1e-12 for r in stability))
        decisions.append(dict(model=model, candidate_eligible=eligible,
            qualifying_slice_count=len(stability), worsening_slice_count=sum(r['mae_improvement'] < -1e-12 for r in stability),
            primary_mae_improvement=[r['mae_improvement'] for r in primary],
            primary_rmse_improvement=[r['rmse_improvement'] for r in primary]))
    # Predeclared simplest-first order; not an OOS rank or a historical selector.
    candidate = next((r['model'] for r in decisions if r['candidate_eligible']), 'NONE')
    return candidate, decisions
