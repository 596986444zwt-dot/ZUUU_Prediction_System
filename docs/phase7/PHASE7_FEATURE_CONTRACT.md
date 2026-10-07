# FEATURE_V1 / FEATURE_UNIT_CONTRACT_V1 / FEATURE_REGISTRY_V1

{
  "version": "FEATURE_V1",
  "formal_horizons": [
    "T1",
    "T2"
  ],
  "T0": "LEGACY_T0_REFERENCE not materialized; production Intraday DEFERRED / BLOCKED_FOR_HISTORICAL_REPLAY",
  "intraday": "HISTORICAL_ZUUU_INGEST_NOT_OBSERVED",
  "meteostat": "TRAINING BLOCKED",
  "eligibility": "GROUND_TRUTH_ELIGIBILITY_RULE_V1; D+2 00:00 BJT; conservative eligibility, not observed receipt",
  "missing": "NULL; no imputation, interpolation or sample deletion; per-feature mask and reason",
  "season": {
    "DJF": [
      12,
      1,
      2
    ],
    "MAM": [
      3,
      4,
      5
    ],
    "JJA": [
      6,
      7,
      8
    ],
    "SON": [
      9,
      10,
      11
    ]
  },
  "periods": {
    "morning": [
      6,
      7,
      8,
      9,
      10,
      11
    ],
    "afternoon": [
      12,
      13,
      14,
      15,
      16,
      17
    ],
    "evening": [
      18,
      19,
      20,
      21,
      22,
      23
    ]
  },
  "tie": "earliest BJT hour for tied extrema/changes",
  "radiation": "source hourly backward-interval mean W/m2; sum*3600/1e6 MJ/m2; source target-hour labels preserved",
  "precipitation": "source hourly preceding-interval mm; sum across target-day hour labels; not a new interval alignment",
  "availability": "ECMWF estimated dissemination inherited; max of input availability; deterministic EPOCH means computable, not receipt",
  "selection": "physical definitions and source quality only; no target correlation or OOS feature selection",
  "phase6": "M6 benchmark-derived feature only; PHASE6_MOS_CANDIDATE remains NONE",
  "revisions": "exact run offsets 6/12/24h; prev latest earlier canonical same-model AVAILABLE run with availability<=issue; no search forward, no splice; full target-day curve required"
}

Every registry formula/operator, unit, required input fields, hours, availability and missing rule is frozen in DB and CSV. ACCEPTED/CONDITIONAL are materialized, with truthful NULL masks; conditional features require Phase8 explicit missingness policy. No selected features based on labels or target correlation. Weather source units checked against frozen raw hourly_units; wind km/h /3.6 to m/s; native RH used, no derived RH.

Historical MOS fields are copied from Phase6, not refitted. M1=M5 duplicate intentionally reported. M6 remains benchmark-derived and Candidate NONE.

X: training_feature_view has only sample_id + registered feature columns. Y: training_label_view. sample metadata: training_sample_view. Optional combined phase7_feature_matrix is explicitly a labeled audit projection, never automatic X. View training_feature_mask_view preserves missing vs zero.

Calendar/EPOCH marks timeless computability, not historical publication. Run-based time features inherit selected-run availability; peak lead also depends on forecast temperature. Solar is the frozen deterministic NOAA projection at 30.576,103.950.

Solar radiation uses hourly backward means: energy=sum W/m2*3600/1e6. Hour labels 00..23 are inherited; sums represent those labeled intervals (first may start previous day at 23), not a newly re-aligned exact midnight daily accumulation. Precipitation has the same source hour-label convention. Source documentation: [Open-Meteo historical forecast variables](https://open-meteo.com/en/docs/historical-forecast-api), [Forecast hourly variable definitions](https://open-meteo.com/en/docs). Only existing frozen individual run data are used; no new API data fetched.

Partial feature validity requires all necessary input hours; no partial means or imputation. Some slope/peak definitions have legitimate structural NULL. Revision 24h can be structurally unavailable for T2 because older runs cover only 72h. Exact named offsets do not seek nearest future or alternative run.

T0 fixed-issue is LEGACY_T0_REFERENCE, not materialized in formal T1/T2 matrices. Intraday contract is separately deferred. Changes after freeze require FEATURE_V2.
