"""Frozen weak binary supervision; human action bounds are not legal event truth.

Only local exact input copies retain private review text. Metadata contains no
observations, reviewer names, raw types, video names or original filesystem paths.
Positive-video background always remains unknown, including after full playback.
"""
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .label_table import COLUMNS, validate_rows
from .review_bridge import COVERAGE_COLUMNS, validate_coverage
from .sequence_candidate import checked_split


SCHEMA_VERSION = 'cashframe.weak-reviewed-boundaries/1'
SUPERVISION = 'organizer_video_labels_with_human_action_boundaries'
INPUT_NAMES = ('events-review.csv', 'review-coverage.csv', 'manifest.json', 'split.json')
PARTITIONS = ('train', 'validation')


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _csv_rows(payload, columns):
    reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig'), newline=''))
    if reader.fieldnames != list(columns):
        raise ValueError('Weak review input has invalid ordered columns')
    return list(reader)


def _build_reviews(payloads):
    """Validate source facts and derive numeric weak events, without rewriting facts."""
    manifest = json.loads(payloads['manifest.json'].decode('utf-8-sig'))
    split = json.loads(payloads['split.json'].decode('utf-8-sig'))
    by_id = checked_split(manifest, split)
    membership = {gid: part for part in ('train', 'validation', 'test') for gid in split[part]}
    for group in manifest:
        duration = group.get('duration_sec')
        if (not re.fullmatch(r'[0-9a-f]{64}', group['sha256'])
                or isinstance(group.get('label'), bool) or group.get('label') not in (0, 1)
                or isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError('Invalid trusted source hash, label or duration')
    rows = validate_rows(_csv_rows(payloads['events-review.csv'], COLUMNS), manifest, split)
    coverage = validate_coverage(_csv_rows(payloads['review-coverage.csv'], COVERAGE_COLUMNS), manifest, split)
    reviews = {gid: {'events': [], 'coverage_complete': False} for gid in by_id}
    excluded = Counter()
    type_hashes = Counter()
    accepted_counts = Counter()
    for row in rows:
        digest = row['source_sha256']
        partition = membership[digest]
        interval = row['start_sec'] is not None
        if partition not in PARTITIONS and interval:
            raise ValueError('An annotated test/withheld interval cannot enter weak supervision')
        if not interval or row['annotation_status'] not in ('draft', 'confirmed'):
            excluded[row['annotation_status']] += 1
            continue
        if by_id[digest]['label'] != 1:
            raise ValueError('A weak event interval conflicts with its trusted negative source')
        if row['visibility'] != 'clear':
            excluded[row['annotation_status']] += 1
            continue
        if (not row['reviewer'].strip() or not any(character.isalnum() for character in row['reviewer'])
                or not row['observed_action'].strip()):
            raise ValueError('Weak action boundaries require a human reviewer and observed fact')
        if row['start_sec'] >= row['end_sec']:
            raise ValueError('Weak action boundaries require a nonempty real interval')
        reviews[digest]['events'].append({'start_sec': row['start_sec'], 'end_sec': row['end_sec'], 'type': None})
        accepted_counts[partition] += 1
        if row['type_id']:
            type_hashes[_digest(row['type_id'].encode('utf-8'))] += 1
    for row in coverage:
        digest = row['source_sha256']
        # Full playback of a positive source never verifies its background as
        # normal. Only an explicitly reviewed organizer-negative source may do so.
        if by_id[digest]['label'] == 0 and row['coverage_complete'] == 'yes':
            reviews[digest]['coverage_complete'] = True
    for gid, review in reviews.items():
        review['events'].sort(key=lambda event: (event['start_sec'], event['end_sec']))
        if membership[gid] in PARTITIONS and by_id[gid]['label'] == 1 and not review['events']:
            raise ValueError('Positive source lacks a clear human-reviewed action interval')
    facts = {
        'source_hashes': {part: list(split[part]) for part in PARTITIONS},
        'excluded_status_counts': dict(sorted(excluded.items())),
        'observed_type_hash_counts': dict(sorted(type_hashes.items())),
        'counts': {'weak_event_intervals': sum(accepted_counts.values()),
                   'intervals_by_partition': {part: accepted_counts[part] for part in PARTITIONS},
                   'positive_sources_by_partition': {part: sum(by_id[gid]['label'] == 1 for gid in split[part])
                                                     for part in PARTITIONS},
                   'fully_reviewed_negative_sources_by_partition': {
                       part: sum(reviews[gid]['coverage_complete'] for gid in split[part]) for part in PARTITIONS}}
    }
    return manifest, split, reviews, facts


def prepare_weak_review(master_path, coverage_path, manifest_path, split_path, output_dir):
    """Exclusively publish an exact-byte local snapshot and its weak-only contract.

    This does not change master statuses, manufacture confirmed rows, train a
    model, or treat an observed action as a legally confirmed violation.
    """
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError('Weak review snapshot already exists and cannot be overwritten')
    paths = dict(zip(INPUT_NAMES, map(Path, (master_path, coverage_path, manifest_path, split_path))))
    payloads = {name: path.read_bytes() for name, path in paths.items()}
    _, _, _, facts = _build_reviews(payloads)
    inputs = {name: {'sha256': _digest(payload), 'bytes': len(payload)} for name, payload in payloads.items()}
    metadata = {'schema_version': SCHEMA_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                'supervision': SUPERVISION, 'human_target_confirmation': False,
                'legal_applicability': False, 'classes': [], 'selection_partitions': list(PARTITIONS),
                'positive_coverage_policy': 'always_unknown', 'inputs': inputs, **facts}

    def unchanged():
        if any(_digest(path.read_bytes()) != inputs[name]['sha256'] for name, path in paths.items()):
            raise ValueError('Weak review inputs changed during preparation; snapshot not published')

    unchanged()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f'.{output.name}-', dir=output.parent) as staging:
        stage = Path(staging)
        for name, payload in payloads.items():
            with (stage / name).open('xb') as stream:
                stream.write(payload)
        with (stage / 'metadata.json').open('x', encoding='utf-8') as stream:
            json.dump(metadata, stream, ensure_ascii=False, allow_nan=False, indent=2)
        unchanged()
        if output.exists():
            raise FileExistsError('Weak review snapshot destination appeared during preparation')
        os.rename(stage, output)
    return metadata


def load_weak_review(snapshot_dir, manifest, split, partitions=PARTITIONS):
    """Load verified binary weak events; requesting withheld test data is forbidden."""
    if (not isinstance(partitions, (list, tuple)) or not partitions
            or len(set(partitions)) != len(partitions) or not set(partitions) <= set(PARTITIONS)):
        raise ValueError('Weak review supports only train/validation; test is withheld')
    directory = Path(snapshot_dir)
    metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
    if (not isinstance(metadata, dict) or metadata.get('schema_version') != SCHEMA_VERSION
            or metadata.get('supervision') != SUPERVISION
            or metadata.get('human_target_confirmation') is not False
            or metadata.get('legal_applicability') is not False
            or metadata.get('classes') != [] or metadata.get('selection_partitions') != list(PARTITIONS)
            or metadata.get('positive_coverage_policy') != 'always_unknown'
            or not isinstance(metadata.get('inputs'), dict) or set(metadata['inputs']) != set(INPUT_NAMES)):
        raise ValueError('Invalid weak-only supervision metadata contract')
    payloads = {}
    for name in INPUT_NAMES:
        record = metadata['inputs'][name]
        payload = (directory / name).read_bytes()
        if (not isinstance(record, dict) or set(record) != {'sha256', 'bytes'}
                or not isinstance(record['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', record['sha256'])
                or not isinstance(record['bytes'], int) or isinstance(record['bytes'], bool)
                or record['bytes'] != len(payload) or record['sha256'] != _digest(payload)):
            raise ValueError('Weak review snapshot input hash changed or is invalid')
        payloads[name] = payload
    frozen_manifest, frozen_split, reviews, facts = _build_reviews(payloads)
    if frozen_manifest != manifest or frozen_split != split:
        raise ValueError('Weak review snapshot differs from the trusted manifest or split')
    if any(metadata.get(name) != value for name, value in facts.items()):
        raise ValueError('Weak review metadata summary differs from its verified input facts')
    permitted = {gid for part in partitions for gid in split[part]}
    return {gid: review if gid in permitted else {'events': [], 'coverage_complete': False}
            for gid, review in reviews.items()}
