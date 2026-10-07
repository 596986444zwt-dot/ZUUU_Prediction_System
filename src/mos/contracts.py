from src.baseline.contracts import ROOT, SOURCES as BASE_SOURCES, OUTPUT as PHASE5

OUTPUT = ROOT / 'database/phase6_statistical_mos_v1.db'
REPORTS = ROOT / 'docs/phase6'
VERSION = 'STATISTICAL_MOS_V1'
SOURCES = {**BASE_SOURCES, 'phase5': (PHASE5, '851122d90d2202f6259d26d6ca08e35960ef295ad624a9464d020c38e6c507f5')}
PHASE5_SEMANTIC = '4f31fa80c704eed5100b1add4d37cebf120b1e56fe1c4e2ba65a140d9d8fd353'
MODELS = ('M1', 'M2', 'M3', 'M4', 'M5', 'M6')
CONTRACT = {
    'version': VERSION, 'error': 'forecast - observed', 'prediction': 'raw - past_bias; continuous; no rounding',
    'minimum_history': 7, 'minimum_group_history': 7,
    'M1': 'horizon expanding mean error', 'M2': '7 calendar days ending at last closed BJT day before issue; minimum 7; otherwise M1',
    'M3': '30 calendar days ending at last closed BJT day before issue; minimum 7; otherwise M1',
    'M4': 'same meteorological season and horizon; minimum 7; otherwise M1',
    'M5': 'horizon-specific expanding correction; identical to M1, no invented continuous daily lead',
    'M6': 'fixed equal weights of legal M1/M3/M4 biases, including their documented fallbacks; no selection',
    'season': {'DJF': [12, 1, 2], 'MAM': [3, 4, 5], 'JJA': [6, 7, 8], 'SON': [9, 10, 11]},
    'training_gate': 'same horizon; target day fully ended; exact final label settlement evidence verified; settled_at < issue; history issue < issue',
    'label_availability': 'UNKNOWN unless historical settlement of exact final TARGET_V1 label is evidenced; do not substitute event/day-end/import/freeze time',
    'T0': 'LEGACY / HISTORICAL fixed 21:00 BJT benchmark; not production Intraday T0',
    'ECMWF': 'frozen run and ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED semantics preserved',
    'trailing_anchor': 'last fully ended BJT natural day before issue, not target date',
    'integer_diagnostic': 'DIAGNOSTIC ONLY; floor(x+0.5), ties toward positive infinity; never used by MOS',
    'candidate_rule': 'descriptive Phase6 candidate only if T1/T2 MAE and RMSE improve and all >=30-sample season/year slices have nonnegative MAE improvement; OOS screening is not past-time model selection',
    'forbidden_inputs': ['Meteostat', 'historical Intraday observations', 'Phase7 weather features'],
}

def season(month):
    return next(k for k, months in CONTRACT['season'].items() if month in months)
