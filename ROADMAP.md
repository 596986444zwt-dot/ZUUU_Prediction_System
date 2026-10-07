# ZUUU Prediction System Roadmap

中文说明：下列 Completed 表示已存在实现或阶段成果，不代表生产验收、校准或性能认证。In Progress 是需要持续评价的工作，不是实时进度报告。Next / Research Ideas 需要数据、维护者审查和独立验证，不承诺交付日期或效果。

Completed means implementation or a phase deliverable exists, not production acceptance or verified calibration/performance. In Progress lists ongoing evaluation priorities, not live telemetry. Proposed work is subject to data availability, maintainer review, and independent validation.

## Completed

- Historical ZUUU Ground Truth processing
- ECMWF Forecast Archive implementation
- DATA_V1
- Raw ECMWF Baseline
- Statistical MOS
- Feature Engineering
- T+1 ML: Ridge formal candidate implementation
- T+2 ML: LightGBM formal candidate implementation
- Probability framework with explicit per-output status
- Real-Time Engine implementation
- T0 Experimental Forward Validation: Level0 + L1_A, no formal Champion
- Desktop GUI
- GitHub open-source release under MIT
- Initial public-test CI, Issue/PR templates, and bilingual documentation

## In Progress

- Phase10 operational soak and independent acceptance
- T0 forward-validation data accumulation
- ECMWF source availability monitoring
- Probability calibration evaluation
- GUI operational refinement

## Next

- T0 30 valid-day evaluation with an explicit valid-day denominator
- T0 60 / 90 / 180-day milestones; sample thresholds never imply automatic promotion
- Additional probability calibration research using past-only walk-forward / OOS evaluation
- Model stability monitoring
- Challenger model research with frozen comparison baselines
- Expand automated CI with isolated fixtures and secret/large-file checks
- Packaging and dependency-lock improvements
- Server/web deployment evaluation; no automatic migration of production state

## Research Ideas

- Additional public meteorological sources with provenance and licensing checks
- ECMWF availability-delay statistics separating run, request, receipt, and first successful capture
- T0 peak-time modeling
- Correlated trajectory simulation
- Isotonic, Platt, and Beta calibration applicability and evaluation
- Ensemble research
- Additional ML models

## How to participate

Choose an [open Issue](https://github.com/596986444zwt-dot/ZUUU_Prediction_System/issues), describe scope and validation before large changes, and follow [CONTRIBUTING.md](CONTRIBUTING.md). Research proposals must record data windows, as-of rules, leakage controls, walk-forward design, baselines, and metrics. No roadmap item authorizes changes to a production worker, frozen model, database, or experimental status.
