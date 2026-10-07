# PHASE10_PREDICTION_CONTRACT

T1 Ridge / T2 LightGBM only. 102 sorted FEATURE_V1 names. Frozen fitted indices/medians/missing indicators/scaling/native NaNs are reused; no fitting. Last legal frozen fitted state is pinned per horizon at startup. All feature available times <= issue, no current target label. Whole selected target curve is one run/version, NULL retained. Snapshot is one transaction with feature/continuous/probability/metadata. Failed predictions generate NO_FORECAST errors rather than half OK snapshots.
