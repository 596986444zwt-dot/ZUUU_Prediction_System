# PHASE10_ARCHITECTURE

Independent Windows CLI: bounded AWC/Open-Meteo adapters → immutable Phase10 SQLite raw/QC → frozen FEATURE_V1 operators → hash-verified fixed Ridge/LightGBM states → past-only Phase9 forward protocol → atomic snapshots → target settlement/evaluation → append-only state. No web/GUI/training/market layer. Shared old collectors are not executed because they write frozen databases.
