"""Report installed type-classifier capability without reading private reviews.

Presence of an archive does not mean it passed the runtime quality gates.
This status is separate from binary readiness and never supplies a verdict.
"""
import json
from pathlib import Path

import numpy as np

from .data import read_json, sha256_file
from .taxonomy import _load_artifact


PRIORITY_CLASS_IDS = (
    'leave_before_service_end',
    'cash_receipt_count_without_client_presence',
    'prior_cash_outside_designated_storage',
)


def classifier_capabilities(model_dir, encoder_config=None):
    """Inspect the same fixed path, encoder binding and gates as the pipeline.

    Raw class names and human observations are intentionally not exposed here.
    Training support counts are source/type pairs, not unique source totals.
    Priority IDs describe the proposed scope, not learned classifier support.
    """
    model_dir = Path(model_dir)
    artifact = model_dir / 'types.npz'
    result = {'status': 'absent', 'class_count': 0, 'model_version': None,
              'artifact_sha256': None, 'training_support': None,
              'score_kind': 'uncalibrated_centroid_margin',
              'normative_classification': 'not_validated',
              'priority_class_ids': list(PRIORITY_CLASS_IDS),
              'priority_classes_trained': False}
    if not artifact.exists():
        return result
    try:
        before = sha256_file(artifact)
        result['artifact_sha256'] = before
        if encoder_config is None:
            encoder_config = read_json(model_dir / 'config.json').get('encoder')
        if not isinstance(encoder_config, dict) or not encoder_config:
            raise ValueError('Actual runtime encoder metadata is required')
        # This rechecks arrays, provenance, quality and encoder compatibility.
        loaded = _load_artifact(artifact, encoder_config)
        with np.load(artifact, allow_pickle=False) as archive:
            metadata = json.loads(str(archive['metadata'].item()))
        if sha256_file(artifact) != before:
            raise ValueError('Type artifact changed while inspecting')
        result.update(status='active' if loaded is not None else 'inactive',
                      class_count=len(metadata['labels']), model_version=metadata['version'],
                      training_support={
                          'train_source_type_pairs': sum(c['train_groups'] for c in metadata['counts'].values()),
                          'validation_source_type_pairs': sum(c['validation_groups'] for c in metadata['counts'].values()),
                      })
        # Even an accepted visual-action head does not establish legal classes.
        return result
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        result.update(status='invalid', class_count=0, model_version=None,
                      training_support=None)
        return result
