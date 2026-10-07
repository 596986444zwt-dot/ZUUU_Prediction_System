## What changed

Describe the final behavior and link the related Issue.

## Why

Explain the concrete problem and who benefits.

## Testing

Commands, environment, results, and tests not run with reasons. Use isolated fixtures; never run unchecked tests against production state.

## Data leakage check

For research: data window, as-of rule, receipt/settlement eligibility, preprocessing/fit/calibration windows, walk-forward/OOS method, comparison baseline, common samples, and metrics. Mark not applicable for documentation-only changes.

## Production impact

Describe any operational impact. Do not stop workers, alter Soak, write runtime data, or automatically promote/deploy models.

## T0 / T+1 / T+2 impact

Address each horizon separately, including Experimental/candidate/formal and calibrated/uncalibrated status. Do not claim unsupported accuracy.

## Documentation updated

List changes to Chinese/English README, protocols, or Roadmap, or explain why none are needed.

## Checklist

- [ ] No secrets, credentials, Cookies, private keys, or sensitive personal information.
- [ ] No runtime DB or backups.
- [ ] No raw data or private evidence bundles.
- [ ] No future leakage; as-of and label eligibility are respected.
- [ ] Tests pass; omitted validation and limitations are documented.
- [ ] No unauthorized model-status change or claim of T0 Champion/calibrated probability.
- [ ] No production worker, Soak, or forward-validation data changes.
- [ ] Chinese and English documentation reflect the same behavior where applicable.
