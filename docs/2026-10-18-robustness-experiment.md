# Cached-descriptor regularization experiment, 2026-10-18

The additional training run did **not** establish an improvement over the active frozen v2 classifier. Its selected candidate matches v2's validation accuracy of 62.5%, still producing three false positives among four negative validation videos. The predeclared promotion gate failed. The active model, its settings, source data and annotations remain unchanged; the candidate is retained only as a local research artifact.

## Inputs and limits

The fixed source split has 24 training groups (11 negative, 13 positive), eight validation groups (4/4), and eight previously examined test groups (4/4). Exact source hashes remain disjoint. These are the existing 40 source groups from 42 files, with cached frozen DINOv2 features; no new independent recordings, camera identities, action-type labels, or interval annotations were added. Exact source separation does not establish camera or scene independence.

The active comparison is `local/experiment-v2`, including its actual frozen classifier and threshold. The older temporal-descriptor v3 experiment reached 50% validation accuracy and was not substituted for this baseline. This run loads only training and validation feature files. It never opens test feature files, uses test labels for selection, downloads external data, re-extracts video features, or sends private video material or descriptions to a remote service.

Descriptor noise and same-class interpolation are regularizers. They do not simulate a different camera angle, create independent samples, or prove robustness to real visual changes. Video-level binary supervision does not establish action types or temporal localization quality.

## Frozen protocol

The local protocol was saved before fitting. Exactly 18 candidates were declared: three descriptors (`mean`, `mean_std`, `robust`), two linear-kernel ridge penalties (10 and 100), and three regularizers (none, standardized Gaussian descriptor noise with sigma 0.1, or same-class mixup using Beta(2,2)). Each augmented source gets one synthetic copy. Real and synthetic rows each receive half the source's original anchor weight, preserving original total class/anchor mass instead of silently weakening the ridge penalty as the row count grows. Mixup uses distinct fitting sources from the same class.

Four deterministic source-group folds use seed 42. Every fold first separates its fitting and held groups, fits normalization on original fitting descriptors only, then generates synthetic descriptors exclusively from that fitting set. No synthetic row enters a held fold or validation. Every original video contributes one descriptor irrespective of duration.

Architecture selection uses training out-of-fold balanced accuracy, then F1, recall and declared order. Each candidate's threshold is optimized on its training out-of-fold scores. These are selection statistics, not an unbiased nested-CV evaluation. Only the winning architecture is refit on all 24 training sources and evaluated on validation. Its final threshold is calibrated there using balanced accuracy, F1, recall and the higher threshold as tie-breakers. Validation does not select among architectures.

Eligibility requires out-of-fold balanced accuracy at least as high as the matched active control, strictly higher validation accuracy, and no reduction in validation recall or specificity. This runner has no test-evaluation or active-replacement path, regardless of eligibility.

## Results

The selected architecture is the **unaugmented mean descriptor with ridge 100**. Noise at the same setting tied its OOF metrics and lost by the predeclared order; the best mixup setting did not improve on the active control's OOF metrics. Consequently this experiment supplies no demonstrated augmentation benefit.

Confusion counts are TN / FP / FN / TP. Training resubstitution uses each model's final validation-calibrated threshold; training OOF uses each candidate's training-OOF-calibrated threshold. Neither is independent accuracy.

| Evaluation | Accuracy | Balanced accuracy | Precision | Recall | Specificity | F1 | TN / FP / FN / TP |
|---|---:|---:|---:|---:|---:|---:|---|
| Active v2, training resubstitution | 62.50% | 59.09% | 59.09% | 100% | 18.18% | 74.29% | 2 / 9 / 0 / 13 |
| Active v2 control, matched training OOF | 70.83% | 71.68% | 80.00% | 61.54% | 81.82% | 69.57% | 9 / 2 / 5 / 8 |
| V4 candidate, training resubstitution | 58.33% | 54.55% | 56.52% | 100% | 9.09% | 72.22% | 1 / 10 / 0 / 13 |
| V4 candidate, training OOF | 75.00% | 75.52% | 81.82% | 69.23% | 81.82% | 75.00% | 9 / 2 / 4 / 9 |
| Active v2, frozen validation | 62.50% | 62.50% | 57.14% | 100% | 25.00% | 72.73% | 1 / 3 / 0 / 4 |
| V4 candidate, validation at training OOF threshold | 37.50% | 37.50% | 33.33% | 25.00% | 50.00% | 28.57% | 2 / 2 / 3 / 1 |
| V4 candidate, validation after threshold calibration | 62.50% | 62.50% | 57.14% | 100% | 25.00% | 72.73% | 1 / 3 / 0 / 4 |

The candidate's OOF threshold is `0.004340585988718928`; its final validation threshold is `-0.07182600834830427`. These are uncalibrated decision values, not probabilities. The loss from 75% training OOF to 37.5% validation accuracy at the OOF threshold, followed by recovery through validation calibration, shows threshold instability. The lower resubstitution accuracy after calibration further cautions against interpreting the OOF gain as a real improvement.

The four eligibility gates returned pass / **fail** / pass / pass: training OOF nonregression, strict validation accuracy improvement, validation recall nonregression, validation specificity nonregression. No test score was produced. Previously published v2 test results remain historical exploratory reuse, not a new test for this candidate. One validation video changes accuracy by 12.5 percentage points, and the validation set has already informed earlier experiments; additional tuning against it would increase overfitting risk.

## Reproduction and verification

Run from the repository root into a fresh, unused local directory:

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONIOENCODING = 'utf-8'
.venv-isolated/Scripts/python.exe scripts/experiment_v4.py --output local/experiments/v4-robust-2026-10-18/another-reproduction
```

The first run took 3.405 seconds with 65.0 MB maximum RSS observed at cooperative checkpoints. An exact reproduction took 3.605 seconds with 64.1 MB. The default budget is 120 seconds and 512 MB, checked between loads/fits; it is not an operating-system hard limit, a continuously sampled peak, or full video-to-result timing. No further candidates were added after the result.

The original run lives in ignored `local/experiments/v4-robust-2026-10-18`; an exact reproduction lives in its `reproduction` subdirectory. Protocols, source hashes and IDs, per-source scores, folds, encoder metadata and model weights stay local. Candidate specification, thresholds, every OOF score, validation scores, normalization, reference rows and coefficients reproduced exactly. Hashes of every active v2 artifact still match their pre-run values. Existing output directories are rejected.

Six new tests were written and observed failing before implementation, then passed. They cover same-class/source-limited mixup and mass preservation, deterministic noise and train-only normalization, missing test features with an unusable test label, unchanged active bytes, source-disjoint folds, validation mutations leaving all training selection/weights unchanged, duplicate-source rejection and budget exhaustion. The complete repository suite passed **160 tests in 22.705 seconds**. A pre-existing Starlette/httpx deprecation warning remains; there were no test failures.

The outcome supports keeping active v2 unchanged and obtaining independently reviewed recordings before making a stronger accuracy or camera-robustness claim.
