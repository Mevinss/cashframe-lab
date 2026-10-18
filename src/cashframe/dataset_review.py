"""Opt-in, authenticated local dataset review; never an inference label lookup."""
import csv
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import uuid

from .data import canonical_id, sha256_file
from .label_table import COLUMNS, export_rows, validate_rows
from .review_bridge import validate_coverage
from .taxonomy import _trusted_sources


COVERAGE_COLUMNS = ('video_id', 'source_sha256', 'coverage_complete', 'reviewer', 'annotation_version')
_ROOT_LOCKS = {}
_ROOT_LOCKS_GUARD = threading.Lock()


class ReviewConflict(ValueError):
    pass


class ReviewInputError(ValueError):
    def __init__(self,field,message):
        super().__init__(message)
        self.field=field


def _csv_bytes(columns, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


class DatasetReviewStore:
    def __init__(self, root):
        self.root = Path(root)
        self.dataset = self.root / 'local/dataset'
        self.table = self.dataset / 'events-review.csv'
        self.coverage = self.dataset / 'review-coverage.csv'
        with _ROOT_LOCKS_GUARD:
            self.lock = _ROOT_LOCKS.setdefault(str(self.root.resolve()), threading.RLock())

    @contextmanager
    def _guard(self):
        with self.lock:
            self.dataset.mkdir(parents=True, exist_ok=True)
            with (self.dataset/'.review.lock').open('a+b') as handle:
                handle.seek(0, 2)
                if not handle.tell():
                    handle.write(b'0');handle.flush()
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:yield
                finally:
                    handle.seek(0)
                    if os.name == 'nt':msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self):
        paths = (self.dataset/'manifest.json', self.dataset/'split.json', self.table, self.coverage)
        raw = {path: path.read_bytes() if path.exists() else b'' for path in paths}
        manifest = json.loads(raw[paths[0]].decode('utf-8-sig'))
        split = json.loads(raw[paths[1]].decode('utf-8-sig'))
        trusted = _trusted_sources(manifest, split)
        if raw[self.table]:
            reader = csv.DictReader(io.StringIO(raw[self.table].decode('utf-8-sig'), newline=''))
            if reader.fieldnames != list(COLUMNS):raise ValueError('Review columns differ from contract')
            rows = validate_rows(list(reader), manifest, split)
        else:rows = export_rows(manifest, split)
        coverage = {}
        if raw[self.coverage]:
            reader = csv.DictReader(io.StringIO(raw[self.coverage].decode('utf-8-sig'), newline=''))
            if reader.fieldnames != list(COVERAGE_COLUMNS):raise ValueError('Coverage columns differ from contract')
            coverage = {row['source_sha256']: row for row in validate_coverage(list(reader), manifest, split)}
        if any((path.read_bytes() if path.exists() else b'') != data for path, data in raw.items()):
            raise ReviewConflict('Review files changed while loading')
        revision = hashlib.sha256(b''.join(raw[path]+b'\0' for path in paths)).hexdigest()
        return manifest, split, trusted, rows, coverage, revision

    def snapshot(self):
        with self._guard():
            return self._snapshot()

    def _snapshot(self):
        _, _, trusted, rows, coverage, revision = self._read()
        sources = []
        for digest, (group, partition) in trusted.items():
            if partition not in ('train', 'validation'):
                continue
            events = [row for row in rows if row['source_sha256'] == digest]
            sources.append(dict(source_sha256=digest, video_id=canonical_id(group['file']),
                                filename=group['file'], duration_sec=group['duration_sec'],
                                label=group['label'], points_sec=group.get('points_sec', []),
                                partition=partition, events=events,
                                coverage_complete=coverage.get(digest, {}).get('coverage_complete') == 'yes'))
        return dict(revision=revision, sources=sources,
                        observed_intervals=sum(row['annotation_status'] in ('draft','confirmed') and row['start_sec'] is not None
                                               for source in sources for row in source['events']),
                    confirmed_intervals=sum(row['annotation_status']=='confirmed' and row['start_sec'] is not None
                                            for source in sources for row in source['events']),
                    confirmed_types=sum(row['annotation_status']=='confirmed' and bool(row['type_id'])
                                        for source in sources for row in source['events']))

    def _source(self, digest, trusted):
        source = trusted.get(digest)
        if source is None or source[1] not in ('train', 'validation'):
            raise KeyError('Unknown review source')
        return source[0]

    def video(self, digest):
        with self._guard():
            _, _, trusted, _, _, _ = self._read()
            group = self._source(digest, trusted)
            path = Path(group['path']).resolve(strict=True)
            if path.name != group['file'] or path.suffix.lower() not in ('.mov', '.mp4') or not path.is_file():
                raise ValueError('Source path does not match the trusted manifest')
            if sha256_file(path) != digest:
                raise ValueError('Source video changed after its manifest was prepared')
            return path

    def save(self, digest, payload):
        with self._guard():
            manifest, split, trusted, rows, coverage, revision = self._read()
            group = self._source(digest, trusted)
            if not isinstance(payload, dict) or set(payload) != {'revision', 'reviewer', 'coverage_complete', 'events'}:
                raise ValueError('Exact review fields required')
            if payload['revision'] != revision:
                raise ReviewConflict('Review changed; reload before saving')
            reviewer, complete, events = payload['reviewer'], payload['coverage_complete'], payload['events']
            if (not isinstance(reviewer,str) or not 0<len(reviewer.strip())<=100
                    or not any(character.isalnum() for character in reviewer)
                    or reviewer.strip().startswith(('=','+','-','@'))):
                raise ReviewInputError('reviewer','Введите имя или обозначение проверяющего, например «Проверяющий 1».')
            if (not isinstance(complete, bool) or not isinstance(events, list) or len(events) > 100):
                raise ValueError('A human reviewer, boolean coverage and event list are required')
            if group['label'] == 0 and events:
                raise ValueError('Events conflict with the source negative label; resolve the label discrepancy first')
            if group['label'] == 1 and complete and not events:
                raise ValueError('Positive video needs reviewed intervals before declaring full coverage')
            version = uuid.uuid4().hex
            replacements = []
            organizer_rows = {row['event_id']: row for row in export_rows(manifest, split)}
            # CSV workflows may have reviewed a point row in place. Keep its
            # human facts under a separate stable ID and restore the source point.
            imported_ids = {row['event_id']: f"{digest}-review-import-{hashlib.sha256(row['event_id'].encode()).hexdigest()[:16]}"
                            for row in rows if row['source_sha256'] == digest
                            and row['annotation_status'] in ('draft','confirmed') and row['event_id'] in organizer_rows}
            rows = [dict(row,event_id=imported_ids[row['event_id']]) if row['event_id'] in imported_ids else row for row in rows]
            rows += [organizer_rows[event_id] for event_id in imported_ids]
            existing = {row['event_id']: row for row in rows if row['source_sha256'] == digest}
            organizer_ids = set(organizer_rows)
            submitted_ids = set()
            for i, event in enumerate(events, 1):
                if (not isinstance(event, dict) or set(event) - {'event_id', 'start_sec', 'end_sec', 'type_id', 'observed_action', 'visibility', 'rule_id','target_confirmed'}
                        or not {'start_sec', 'end_sec', 'type_id', 'observed_action', 'visibility'} <= set(event)):
                    raise ValueError('Event fields are invalid')
                if isinstance(event['start_sec'], bool) or isinstance(event['end_sec'], bool):
                    raise ValueError('Interval endpoints must be seconds')
                start, end = float(event['start_sec']), float(event['end_sec'])
                if not start < end:
                    raise ValueError('Event must have a nonempty interval')
                target_confirmed=event.get('target_confirmed',False)
                if not isinstance(target_confirmed,bool):raise ValueError('Target-event confirmation must be boolean')
                event_id = event.get('event_id')
                event_id = imported_ids.get(event_id,event_id)
                if event_id is not None and (event_id not in existing or event_id in submitted_ids):
                    raise ValueError('An edited event must belong to this source and occur once')
                if event_id is not None and (event_id in organizer_ids or existing[event_id]['annotation_status'] not in ('draft','confirmed')):
                    raise ValueError('Original point facts and unfinished review records cannot be replaced')
                event_id = event_id or f'{digest}-review-{uuid.uuid4().hex}'
                submitted_ids.add(event_id)
                row = dict.fromkeys(COLUMNS, '')
                point = next((point for point in group.get('points_sec', []) if start <= point <= end), None)
                row.update(video_id=canonical_id(group['file']), source_sha256=digest,
                           event_id=event_id, point_sec=point,
                           start_sec=start, end_sec=end, type_id=event['type_id'],
                           observed_action=event['observed_action'], visibility=event['visibility'],
                           rule_id=event.get('rule_id') or '', reviewer=reviewer.strip(),
                           annotation_status='confirmed' if target_confirmed else 'draft', annotation_version=version)
                if not isinstance(row['observed_action'], str) or len(row['observed_action']) > 3000:
                    raise ValueError('Observed action must be short text')
                if not isinstance(row['type_id'], str) or len(row['type_id']) > 160:
                    raise ValueError('Action type must be short text')
                replacements.append(row)
            retained = []
            for old in rows:
                if old['event_id'] in submitted_ids:
                    continue
                if old['source_sha256'] == digest and old['annotation_status'] in ('draft','confirmed'):
                    # Removal is explicit review history, never deletion of original facts.
                    old = dict(old, annotation_status='rejected', rule_id='', reviewer=reviewer.strip(), annotation_version=version)
                retained.append(old)
            # Original organizer point rows remain unchanged alongside human intervals.
            new_rows = validate_rows(retained + replacements, manifest, split)
            coverage[digest] = dict(video_id=canonical_id(group['file']), source_sha256=digest,
                                    coverage_complete='yes' if complete else 'no', reviewer=reviewer.strip(), annotation_version=version)
            checked_coverage=validate_coverage(list(coverage.values()), manifest, split)
            contents = {self.table: _csv_bytes(COLUMNS, new_rows),
                        self.coverage: _csv_bytes(COVERAGE_COLUMNS, checked_coverage)}
            if self._read()[-1] != revision:
                raise ReviewConflict('Review changed outside this editor before commit')
            # Keep the previous exact bytes before replacing either file.
            backup = self.root/'local/annotations/dataset-revisions'/version
            backup.mkdir(parents=True)
            for path in contents:
                if path.exists():
                    (backup/path.name).write_bytes(path.read_bytes())
            (backup/'change.json').write_text(json.dumps(dict(source_sha256=digest, prior_revision=revision,
                                               reviewer=reviewer.strip(), annotation_version=version,
                                               imported_event_ids=imported_ids)), encoding='utf-8')
            previous = {p: p.read_bytes() if p.exists() else None for p in contents}
            if self._read()[-1] != revision:
                raise ReviewConflict('Review changed while preparing its backup')
            replaced = []
            try:
                for path, data in contents.items():
                    temporary = path.with_name(path.name+'.'+version+'.tmp')
                    temporary.write_bytes(data)
                    os.replace(temporary, path)
                    replaced.append(path)
            except OSError:
                for path in replaced:
                    if previous[path] is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(previous[path])
                raise
            return self._snapshot()
