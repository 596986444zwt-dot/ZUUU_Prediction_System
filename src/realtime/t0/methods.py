"""Two simultaneous challengers; full bias is never an automatic champion."""
from .contracts import BJT, utc, integer_center, require


def calculate(features, cutoff):
    outputs = []
    status = features['status']
    floor = features['Tmax_so_far']
    maximum = features['remaining_day_max']
    bias = features['current_bias']
    for method in ('LEVEL0', 'L1_A'):
        candidate = 'EXPERIMENTAL_SHADOW' if method == 'L1_A' and utc(cutoff).astimezone(BJT).hour < 12 else 'EXPERIMENTAL_ACTIVE_CANDIDATE'
        method_status = status if status != 'OK' else 'MISSING_BIAS_ALIGNMENT' if method == 'L1_A' and bias is None else 'OK'
        value = None
        if method_status == 'OK':
            value = max(floor, maximum + (bias if method == 'L1_A' else 0.))
            require(value >= floor, 'OBSERVED_HARD_FLOOR_VIOLATION')
        outputs.append(dict(method=method, prediction=value,
                            integer_prediction=integer_center(value) if value is not None else None,
                            status=method_status, candidate_status=candidate,
                            experiment_status='T0_EXPERIMENTAL', validation='FORWARD_VALIDATION'))
    return outputs
