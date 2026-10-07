from pathlib import Path
from src.data_v1.contracts import PRODUCTION, AUXILIARY, PRODUCTION_SHA, AUXILIARY_SHA

ROOT = Path(__file__).resolve().parents[2]
PHASE4 = ROOT / 'database/phase4_data_v1.db'
OUTPUT = ROOT / 'database/phase5_ecmwf_raw_baseline_v1.db'
PHASE4_SHA = '9d1901aa0f6c9c3106450cb9d41d885797745f087a80c497cff082f67094a892'
PHASE4_SEMANTIC = '4eea080168f102f30314b0b8a64123af533923fd7f4b7e7c6e9312b5582835dd'
VERSION = 'ECMWF_RAW_BASELINE_V1'
SOURCES = {'production': (PRODUCTION, PRODUCTION_SHA), 'auxiliary': (AUXILIARY, AUXILIARY_SHA), 'phase4': (PHASE4, PHASE4_SHA)}
CONTRACT = {
    'version': VERSION, 'prediction': 'max of 24 frozen same-vintage hourly temperatures; no rounding',
    'error': 'forecast - observed; positive is warm', 'std': 'population ddof=0',
    'percentile': 'type 7 linear interpolation at (N-1)*p',
    'r2': '1-SSE/SST; NULL for N<2 or constant observed target',
    'pearson': 'centered covariance / sqrt(centered sums of squares); NULL if undefined',
    'hits': 'inclusive absolute error <= 0.5, 1.0, 2.0 degrees C',
    'empty_groups': 'N=0; hit counts=0; all other metrics NULL',
    'season': 'copied from frozen Phase4 Solar rows',
    'bins': ['<15', '[15,20)', '[20,25)', '[25,30)', '[30,35)', '>=35'],
    'source_phase4_semantic_sha256': PHASE4_SEMANTIC,
    'T0': 'late-day / same-day benchmark under frozen 21:00 BJT issue semantics',
    'T1_T2': 'primary forward forecast benchmarks',
    'meteostat_training_admission': 'BLOCKED', 'integer_diagnostics': 'NONE',
}

def temperature_bin(value):
    return CONTRACT['bins'][next((i for i, edge in enumerate((15, 20, 25, 30, 35)) if value < edge), 5)]
