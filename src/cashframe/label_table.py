"""Local human event review tables; source points never imply types or negatives.

Only the manifest and fixed split determine source membership. CSV provenance
flags are deliberately unsupported. Conversion targets taxonomy.train_types'
existing confirmed-human JSONL contract, not an inference-time answer lookup.
"""
from collections import Counter
import csv
import json
import math
from pathlib import Path
import re

from .data import canonical_id
from .event_labels import is_specific_type
from .taxonomy import _trusted_sources

COLUMNS = ('video_id', 'source_sha256', 'event_id', 'point_sec', 'start_sec',
           'end_sec', 'type_id', 'observed_action', 'visibility', 'rule_id',
           'annotation_status', 'reviewer', 'annotation_version')
STATUSES = ('unreviewed', 'draft', 'confirmed', 'rejected')
VISIBILITY = ('', 'clear', 'partial', 'insufficient', 'not_visible', 'unknown')


def _number(value, field):
    if value in ('', None):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{field} must be finite seconds or blank') from exc
    if isinstance(value, bool) or not math.isfinite(number):
        raise ValueError(f'{field} must be finite seconds or blank')
    return number


def validate_rows(rows, manifest, split):
    """Validate and normalize review rows without trusting client provenance."""
    trusted = _trusted_sources(manifest, split)
    accepted, ids = [], set()
    for index, original in enumerate(rows, 1):
        if set(original) != set(COLUMNS):
            raise ValueError(f'Row {index}: exact review-table columns required; provenance flags are forbidden')
        if any(not isinstance(original[key], (str, int, float, type(None))) for key in COLUMNS):
            raise ValueError(f'Row {index}: scalar cells required')
        row = {key: str(original[key]).strip() if original[key] is not None else '' for key in COLUMNS}
        # CSV must remain inert when opened by a spreadsheet application.
        if any(row[key].startswith(('=', '+', '-', '@')) for key in COLUMNS):
            raise ValueError(f'Row {index}: formula-like cells are not permitted')
        source = trusted.get(row['source_sha256'])
        if source is None:
            raise ValueError(f'Row {index}: unknown source hash')
        group, _ = source
        names = group.get('files') or [group.get('file', '')]
        if row['video_id'] not in {canonical_id(name) for name in names}:
            raise ValueError(f'Row {index}: video ID does not belong to source hash')
        event_id = row['event_id']
        if not re.fullmatch(r'[\w.-]{1,160}', event_id) or event_id in ids:
            raise ValueError(f'Row {index}: event IDs must be safe and globally unique')
        ids.add(event_id)
        duration = _number(group.get('duration_sec'), 'duration_sec')
        if duration is None or duration <= 0:
            raise ValueError('Trusted source duration must be positive')
        for key in ('point_sec', 'start_sec', 'end_sec'):
            row[key] = _number(original[key], key)
            if row[key] is not None and not 0 <= row[key] <= duration:
                raise ValueError(f'Row {index}: {key} outside source bounds')
        start, end, point = (row[k] for k in ('start_sec', 'end_sec', 'point_sec'))
        if (start is None) != (end is None) or (start is not None and start > end):
            raise ValueError(f'Row {index}: interval needs both ordered endpoints')
        if start is not None and point is not None and not start <= point <= end:
            raise ValueError(f'Row {index}: point outside reviewed interval')
        if row['annotation_status'] not in STATUSES or row['visibility'] not in VISIBILITY:
            raise ValueError(f'Row {index}: unsupported status or visibility')
        if not row['annotation_version']:
            raise ValueError(f'Row {index}: annotation version required')
        if row['type_id'] and not is_specific_type(row['type_id']):
            raise ValueError(f'Row {index}: generic binary/unknown label is not an action type')
        if row['annotation_status'] == 'unreviewed' and any(row[k] for k in ('type_id', 'observed_action', 'visibility', 'rule_id', 'reviewer')):
            raise ValueError(f'Row {index}: populated review facts require draft/confirmed/rejected status')
        if row['annotation_status'] == 'confirmed':
            if not all(row[k] for k in ('reviewer', 'observed_action', 'visibility')):
                raise ValueError(f'Row {index}: confirmation requires reviewer, observed action and visibility')
            if row['visibility'] != 'clear' and row['type_id']:
                raise ValueError(f'Row {index}: a type cannot be confirmed with insufficient visibility')
        if row['rule_id'] and (row['annotation_status'] != 'confirmed' or not row['type_id']
                               or row['visibility'] != 'clear'):
            raise ValueError(f'Row {index}: rule reference requires a human-confirmed clear action type; proposals stay separate')
        accepted.append(row)
    return accepted


def export_rows(manifest, split):
    """One unreviewed row per original point; never manufacture negative events."""
    rows = []
    _trusted_sources(manifest, split)
    for group in manifest:
        points = group.get('points_sec', [])
        if not points and group.get('point_sec') is not None:
            points = [group['point_sec']]
        for offset, point in enumerate(sorted(set(points)), 1):
            row = dict.fromkeys(COLUMNS, '')
            row.update(video_id=canonical_id(group['file']), source_sha256=group['sha256'],
                       event_id=f"{group['sha256']}-{offset}", point_sec=point,
                       annotation_status='unreviewed', annotation_version='1')
            rows.append(row)
    return validate_rows(rows, manifest, split)


def write_csv(path, rows, manifest, split):
    rows = validate_rows(rows, manifest, split)
    path = Path(path)
    if path.suffix.lower() != '.csv':
        raise ValueError('Review export must use a separate .csv file; workbooks are read-only')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return summary(rows, manifest, split)


def read_csv(path, manifest, split):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(COLUMNS):
            raise ValueError('Exact ordered review-table columns required')
        return validate_rows(list(reader), manifest, split)


def confirmed_annotations(rows, manifest, split):
    """Convert confirmed, typed, clear intervals; validation is metrics-only.

    Test/external rows never enter the training handoff. A confirmed abstention
    (blank type) remains a valid review record but cannot train a classifier.
    Rule IDs are review data only and never establish applicability by themselves.
    """
    trusted = _trusted_sources(manifest, split)
    result = []
    for row in validate_rows(rows, manifest, split):
        partition = trusted[row['source_sha256']][1]
        if (partition not in ('train', 'validation') or row['annotation_status'] != 'confirmed'
                or not row['type_id'] or row['visibility'] != 'clear' or row['start_sec'] is None):
            continue
        result.append({'source_sha256': row['source_sha256'], 'event_id': row['event_id'],
                       'source_partition': partition, 'training_eligible': partition == 'train',
                       'origin': 'human', 'confirmed': True, 'reviewer': row['reviewer'],
                       'type': row['type_id'], 'observed_fact': row['observed_action'],
                       'visibility': row['visibility'], 'start_sec': row['start_sec'],
                       'end_sec': row['end_sec'], 'annotation_version': row['annotation_version']})
    return result


def summary(rows, manifest, split):
    normalized = validate_rows(rows, manifest, split)
    converted = confirmed_annotations(normalized, manifest, split)
    trusted = _trusted_sources(manifest, split)
    return {'rows': len(normalized), 'sources_with_points': len({r['source_sha256'] for r in normalized}),
            'source_groups': len(manifest), 'status_counts': dict(Counter(r['annotation_status'] for r in normalized)),
            'partition_counts': dict(Counter(trusted[r['source_sha256']][1] for r in normalized)),
            'confirmed_train_intervals': sum(r['training_eligible'] for r in converted),
            'confirmed_validation_intervals': sum(not r['training_eligible'] for r in converted),
            'type_classifier_status': 'not_trained', 'frames_exported': 0}


README = '''# Local human event review

Original open_labels.xlsx is read-only and is never opened for writing. This CSV
copies only original manifest point times. Blank intervals, action types, actions,
visibility and rules mean unknown; a point does not label other frames negative.
Multiple globally unique event_id rows may refer to one video/source.

1. Keep model suggestions in a separate file, never in confirmed ground truth.
2. Review the source locally; add extra event rows with unique IDs if necessary.
3. Use draft while reviewing. Enter actual observed_action, visibility, reviewer
   and annotation_version. Set confirmed only after human review. A blank type
   records abstention when action/visibility is insufficient. Do not invent rules.
4. Type-training handoff requires a specific confirmed type, clear visibility,
   and both observed interval endpoints. Original points alone cannot train types.
5. Run scripts/event_labels_table.py validate, then convert to JSONL and pass that
   file to scripts/train_types.py --annotations with the SAME manifest and split.
   Conversion derives provenance; CSV source_partition/training_eligible columns
   are rejected. Train rows alone fit the model. Validation rows evaluate it;
   test/external rows never enter the handoff. The trainer rechecks membership.
6. A rule_id is a reviewer reference, not confirmation of legal applicability;
   the separate verified rule registry and applicability checks remain required.

No confirmed action taxonomy exists in the original labels. Exporting this table
does not train a classifier. At least two visual types, each with three independent
train and two validation source groups, are required by the existing trainer.
CSV is UTF-8 BOM. Formula-like cells starting =, +, - or @ are rejected. Import as
text; do not execute formulas from annotation cells. Use a separate .csv file.
'''


def write_documentation(path):
    path = Path(path)
    schema = {'schema_version': 'cashframe.event-review/1', 'columns': list(COLUMNS),
              'annotation_status': list(STATUSES), 'visibility': list(VISIBILITY),
              'unknown': 'empty cell', 'time_unit': 'seconds', 'multiple_events_per_source': True,
              'provenance_authority': 'trusted manifest + fixed split; never CSV flags',
              'human_confirmation_fields': ['reviewer', 'observed_action', 'visibility', 'annotation_version'],
              'type_training_requires': ['confirmed', 'specific type_id', 'clear visibility', 'start_sec', 'end_sec', 'train source'],
              'legal_applicability': 'rule_id alone never confirms applicability'}
    path.with_suffix('.schema.json').write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding='utf-8')
    path.with_suffix('.README.md').write_text(README, encoding='utf-8')
