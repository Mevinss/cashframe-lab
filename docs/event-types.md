# Human-supervised visual event types

No confirmed event-type categories or independent different-camera footage are currently available. Existing binary violation labels and point timestamps cannot establish an event taxonomy. The optional trainer therefore cannot honestly produce a usable type model from the current dataset. It never invents categories or converts binary labels into action types.

`scripts/train_types.py` fits a small NumPy centroid baseline from **local confirmed human interval annotations**, using the existing frozen DINO cache. It performs no network calls and does not inspect videos or export frames. Keep annotations, features and model artifacts in the ignored local data directory.

```powershell
$env:PYTHONPATH = 'src'
.venv/Scripts/python.exe scripts/train_types.py --annotations local/annotations/confirmed.jsonl --manifest local/dataset/manifest.json --split local/dataset/split.json --features local/features --output local/experiment/types.npz
```

Adjust local paths to the existing dataset layout. The manifest and split must be the trusted original dataset files, never a split reconstructed from annotations or a submitted upload. An absent/empty annotation file exits with status 2 and an explanation; no artifact is created or replaced. The command also exits 2 when data, visibility or independent validation is insufficient. A fitted but weak model is saved with `active: false` for local research and cannot return types.

Accepted annotations contain `confirmed: true`, `origin: "human"`, nonempty `reviewer`, `observed_fact` and visual `type`, `visibility: "clear"`, `source_sha256`, and an interval `start_sec`/`end_sec` within the original source duration. Partial, unusable or unknown visibility is excluded. Placeholder and binary labels are excluded. Confirmation does not establish a legal rule: rule fields are not used in type fitting.

Actual partition membership comes from the source hash, manifest and trusted split; claimed `source_partition` and `training_eligible` fields are never trusted. Only original training and validation sources participate. Test, jury, external and unknown sources are excluded before their features are opened. Source aliases or overlap across partitions cause an error. Repeated intervals are deduplicated, conflicts are rejected, and independent counts are per source/type rather than per frame or annotation.

Every type requires at least three independent training sources and two independent validation sources; at least two visual types are required. The same cached feature encoder must have produced every sequence. Interval descriptors concatenate mean, standard deviation and mean absolute motion per second. The fixed baseline standardizes these descriptors, computes source-balanced class centroids, and derives class distance limits from training observations. It rejects distant events and nearest-centroid margins below 0.2. It does not tune using test sources.

Activation requires source-disjoint validation with macro F1, per-type precision, per-type recall and non-unknown coverage all at least 0.8. Validation metrics weight each source/type equally even when a source has several intervals. These are minimum engineering gates, **not evidence of 80% production accuracy**: small samples, taxonomy quality, incomplete event coverage and different-camera generalization remain limitations. Independent camera footage and an authorized evaluation are still required for any new-angle claim.

The single `.npz` artifact contains arrays and versioned JSON metadata: encoder configuration and its canonical SHA-256, feature version/dimension, human category names, independent counts, validation metrics, exclusion counts, acceptance gates and activation state. It contains no observed-fact text or frame data. Save it locally and retain the prior artifact when comparing experiments; training is an explicit command, never an inference side effect.

Runtime integration uses:

```python
from cashframe.taxonomy import classify_interval
candidate = classify_interval(artifact_path, times, values, start, end,
                              encoder_config=actual_encoder_config)
```

`predict_type(artifact_path, event_feature, encoder_config=...)` accepts a precomputed interval descriptor. Missing artifacts, omitted encoder compatibility evidence, inactive validation, ambiguous events and out-of-distribution features return `None` (unknown). Malformed artifacts, unsupported versions, incompatible encoders and invalid feature arrays raise `ValueError` so an integration can report the problem explicitly. Compatibility compares the exact canonical encoder configuration, including its weights/source hashes and sampling configuration.

A candidate contains a human-defined `type`, a centroid-margin `confidence` (not a calibrated probability), `origin: "automatic"`, `confirmed: false`, and model version. It cannot confirm an observed fact or choose a normative/legal rule. Human review and rule applicability confirmation remain separate steps.

`tests/test_taxonomy.py` uses separable **synthetic feature arrays only** to demonstrate train/predict mechanics, withheld-source exclusion, source independence, missing labels, visibility rejection, unknown output, compatibility errors and weak-validation refusal. Its fixture scores make no claim about real video performance.

The runtime reads `types.npz` from the selected model directory. For the original profile use `local/experiment/types.npz`; for the experimental profile use `local/experiment-v2/types.npz`. Keep the encoder configuration identical. No artifact has been installed on this machine because confirmed labels are absent.
