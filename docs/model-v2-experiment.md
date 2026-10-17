# Video model v2: bounded local experiment

Status: experimental candidate available for explicit opt-in. The eight test videos were already examined during v1 development, so their reuse is exploratory. This experiment does not establish 85–90% accuracy, confirmed action types, localization quality, or performance on another camera.

## Data and supervision audit

- 42 source files reduce to 40 exact SHA-256 source groups: two duplicate groups each have one extra file. The fixed split remains 24 training groups (11 negative, 13 positive), eight validation groups (4/4), and eight test groups (4/4).
- No exact source overlaps partitions. This checks source identity, not visual near-duplicates or scene independence. No camera IDs or independently verified camera split are available.
- Durations range from 11.63 to 142.53 seconds. Cached frozen DINOv2 features are available for all 40 groups. No video frames, images, scene descriptions, or feature vectors were transmitted to external AI.
- All positive groups have point annotations, and all recorded points fall within their videos. A point marks weak temporal supervision; it does not establish a complete action interval or a confirmed type.
- Training uses 1,062 supervised windows from 11 negative videos and only 39 point-near positive windows from 13 positive videos. Other positive-video windows remain unlabeled. V1 balances total loss per class/video; the raw window counts alone therefore do not prove class-loss imbalance.
- The selected v1 classifier uses the maximum point score over the video, without its optional bag head. On validation it predicts every video positive. Negative videos average 73.1% of windows above its event threshold, compared with 32.3% for positive videos. Max aggregation increases susceptibility to isolated false signals, but these observed scores show that aggregation alone does not explain the failure. No visual cause is inferred.

## Frozen selection protocol

The protocol and candidate grid were saved locally before any v2 test scoring. All work used cached features and NumPy on CPU; no model downloads or additional training libraries were needed.

Exactly 27 candidates were declared: three video descriptors (window mean; mean plus standard deviation; mean, standard deviation, 10th and 90th percentiles), each with three linear-kernel ridge penalties (0.1, 1, 10) and six RBF combinations (bandwidth 0.25, 1, 4; ridge 0.1, 1). Windows retain the existing mean, standard deviation and temporal difference features. Every video contributes once to the video classifier, irrespective of its window count.

Four deterministic, stratified source-group folds within the 24 training groups produce out-of-fold scores. Each fold fits standardization, kernel references and coefficients using its fitting groups only. Class weights give each binary class equal total weight. Architecture selection uses training OOF balanced accuracy, then F1, recall and declared order. OOF thresholds are optimized on those OOF scores; these are model-selection statistics, not an unbiased nested-CV estimate.

After selecting one architecture, it is refit on the same 24 training groups. Only its final decision threshold is calibrated on the eight validation groups, using balanced accuracy, then F1, recall and the higher threshold. Validation labels never choose among architectures. No training or selection routine accepts test features or labels. A synthetic integration test proves selection works when test features are absent and its label is deliberately unusable.

The winner is the mean-window descriptor with a linear kernel and ridge penalty 10. Its descriptor has 1,152 values and 24 training reference rows. The frozen decision threshold is `-0.35479454157951407`; scores are uncalibrated decision values, not probabilities. Training includes no validation or test groups.

## Results

Confusion counts below use TN / FP / FN / TP. Accuracy and balanced accuracy coincide on validation and test because those splits are balanced.

| Evaluation | Accuracy | Precision | Recall | Specificity | F1 | TN / FP / FN / TP |
|---|---:|---:|---:|---:|---:|---|
| Training OOF, selected candidate/OOF threshold | 70.8% | 80.0% | 61.5% | 81.8% | 69.6% | 9 / 2 / 5 / 8 |
| Validation, before threshold calibration | 37.5% | 33.3% | 25.0% | 50.0% | 28.6% | 2 / 2 / 3 / 1 |
| Validation, calibrated threshold | 62.5% | 57.1% | 100% | 25.0% | 72.7% | 1 / 3 / 0 / 4 |
| Already-viewed test, frozen v2 | 62.5% | 57.1% | 100% | 25.0% | 72.7% | 1 / 3 / 0 / 4 |
| Already-viewed test, v1 / constant-positive | 50.0% | 50.0% | 100% | 0% | 66.7% | 0 / 4 / 0 / 4 |
| Already-viewed test, constant-negative | 50.0% | 0% | 0% | 100% | 0% | 4 / 0 / 4 / 0 |

V2 removes one false positive on validation and one on the reused test while retaining all four positive detections. Three of four negative test videos still trigger a false alert. One video changes accuracy by 12.5 percentage points. The validation result at the training threshold shows substantial threshold instability. These results justify an experimental opt-in for review, not a production accuracy or robustness claim. No further candidate tuning followed the test result.

The new whole-video decision does not validate the old point head's peak or proposed evidence frames. Human review must still confirm event relevance, intervals, types and any rule applicability.

## Reproduction and local artifacts

Run from the repository root in PowerShell. The original frozen experiment is preserved; rerunning selection into its directory is rejected. To reproduce selection in a fresh, unused local directory:

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONIOENCODING = 'utf-8'
.venv/Scripts/python.exe scripts/experiment_v2.py --phase select --output local/experiments/v2-reproduction --max-seconds 60 --max-rss-mb 512
```

The reproduction directory already exists on this machine from verification. Use another fresh output name for another reproduction. Verification produced exactly the same selected specification, threshold, normalization arrays, reference rows and coefficients; it did not rescore the test.

The first evaluation was run only after `selection-frozen.json` and `selected.npz` existed:

```powershell
.venv/Scripts/python.exe scripts/experiment_v2.py --phase evaluate
```

A second evaluation into the same experiment directory is rejected; inspect the stored local report instead. Selection and evaluation check manifest/split/encoder metadata hashes, and evaluation verifies all training/validation feature hashes. `load_frozen` verifies the classifier artifact hash. Test feature hashes and the frozen selection hash are saved with the evaluation.

Private files stay under ignored `local/experiments/v2`: the predeclared protocol, frozen selection with folds and training diagnostics, classifier weights, and `test-report.json` with per-source predictions/errors and constant baselines. No source filenames, group IDs, private examples or weights are included in this aggregate document.

The initial selection took 4.49 seconds at 62.3 MB process RSS; exact reproduction took 4.58 seconds at 62.0 MB. Cached test scoring and audit checks took 0.144 seconds at 42.0 MB. Current commands default to a 60-second, 512-MB cooperative experiment budget checked between feature loads and fold fits; this is not an operating-system hard memory limit. Stored feature-extraction measurements range from 4.57 to 57.23 seconds and 123.9 to 335.7 MB RSS. Those older extraction measurements and the cached experiment do not replace full offline end-to-end timing under the application's 300-second job limit.

## Explicit opt-in and rollback

Prepare a separate model directory, preserving the live base and its configuration:

```powershell
.venv/Scripts/python.exe scripts/enable_v2.py --base local/experiment --candidate local/experiments/v2 --output local/experiment-v2
```

This directory already exists locally. The command refuses an existing target, checks the exact encoder configuration, clones old artifacts, retains `config-v1.json`, and adds both v2 files. The clone declares `video_classifier='selected.npz'`, `video_classifier_metadata='selection-frozen.json'` and experimental status. It never overwrites `local/experiment`.

Select this directory with `scripts/start.ps1 -ModelDirectory local/experiment-v2`; the runtime integration is implemented. Returning to `local/experiment` restores the base behavior. Keep the base point classifier for interval proposals and apply the selected v2 classifier only to the whole-video decision. Include both new files in every model-version evidence snapshot.

The runtime interface is:

```python
from cashframe.research import load_frozen, score_videos
video_model, threshold, metadata = load_frozen(model_dir, expected_encoder=config['encoder'])
score = float(score_videos(video_model, [windows])[0])
positive = score >= threshold
```

`windows` is the existing `window_features` output. Exact encoder metadata is stored at `metadata['encoder']`; a mismatch is rejected. Loading and scoring need only NumPy. Runtime integration must identify this as an experimental whole-video classifier and preserve the old point head's uncertainty.

## Verification

Twelve research tests cover source duplication rejection, reproducible source folds, confusion metrics, forbidden test calibration, nonfinite scores, training-only normalization, test-free selection, model hash corruption, opt-in preservation, encoder compatibility and cooperative budget exhaustion. The integrated full suite passed 85 tests after fixes to report API and a retention test that now controls wall time instead of racing a short expiration interval. Final integration evidence is recorded in acceptance-v2.json; research tests do not establish real-world model quality.
