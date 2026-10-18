"""Versioned, local master-review to TCN bridge with independent human coverage.

The existing typed review table remains the source of event facts. Neither an
organizer point nor a confirmed event proves that the rest of a video was seen.
The CLI shares local/dataset/review-coverage.csv with the dataset-review UI;
standalone coverage templates must not replace that file after human UI review.
"""
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path

from .data import canonical_id, read_json, sha256_file
from .label_table import read_csv, validate_rows, write_csv
from .sequence_candidate import ANNOTATION_COLUMNS, checked_split


SCHEMA_VERSION = 'cashframe.sequence-review-bridge/1'
COVERAGE_COLUMNS = ('video_id', 'source_sha256', 'coverage_complete', 'reviewer',
                    'annotation_version')
PARTITIONS = ('train', 'validation')


def _sources(manifest, split):
    checked_split(manifest, split)
    membership = {gid: part for part in ('train', 'validation', 'test') for gid in split[part]}
    return {g['sha256']: (g, membership[g['group_id']]) for g in manifest}


def coverage_rows(manifest, split):
    """A blank whole-source coverage record for each train/validation source."""
    return [{'video_id': canonical_id(group['file']), 'source_sha256': digest,
             'coverage_complete': '', 'reviewer': '', 'annotation_version': '1'}
            for digest, (group, partition) in _sources(manifest, split).items()
            if partition in PARTITIONS]


def validate_coverage(rows, manifest, split):
    """Validate independently entered coverage; client partition flags are forbidden."""
    sources = _sources(manifest, split)
    normalized, seen = [], set()
    for index, original in enumerate(rows, 1):
        if set(original) != set(COVERAGE_COLUMNS):
            raise ValueError(f'Coverage row {index}: exact coverage columns required')
        if any(not isinstance(value, (str, type(None))) for value in original.values()):
            raise ValueError(f'Coverage row {index}: text cells required')
        row = {key: (original[key] or '').strip() for key in COVERAGE_COLUMNS}
        if any(value.startswith(('=', '+', '-', '@')) for value in row.values()):
            raise ValueError(f'Coverage row {index}: formula-like cells are forbidden')
        digest = row['source_sha256']
        if digest not in sources:
            raise ValueError(f'Coverage row {index}: unknown source hash')
        group, partition = sources[digest]
        if partition not in PARTITIONS:
            raise ValueError('Coverage handoff permits only train and validation sources; test is withheld')
        names = group.get('files') or [group['file']]
        if row['video_id'] not in {canonical_id(name) for name in names}:
            raise ValueError(f'Coverage row {index}: video does not belong to source hash')
        if digest in seen:
            raise ValueError('Coverage source hashes must be unique')
        seen.add(digest)
        if row['coverage_complete'] not in ('', 'yes', 'no'):
            raise ValueError('Coverage must be blank, yes or no')
        if not row['annotation_version']:
            raise ValueError('Coverage annotation version required')
        if row['coverage_complete'] == 'yes' and not row['reviewer']:
            raise ValueError('Complete coverage requires a separate human reviewer confirmation')
        normalized.append(row)
    return normalized


def sequence_rows(rows, coverage, manifest, split):
    """Return existing TCN-schema rows plus a provenance record per output row.

    Unreviewed, point-only and insufficient-visibility facts never become TCN
    events. A clear human-confirmed interval may retain an unknown event type
    for binary TCN training; the type-training export has a stricter contract.
    Test rows may exist in the master but never enter this handoff.
    Coverage gets its own row so its reviewer cannot be confused with an event's.
    """
    sources = _sources(manifest, split)
    rows = validate_rows(rows, manifest, split)
    coverage = {row['source_sha256']: row for row in validate_coverage(coverage, manifest, split)}
    events = [{'source_sha256': row['source_sha256'], 'event_id': row['event_id'],
               'start_sec': row['start_sec'], 'end_sec': row['end_sec'],
               'type': row['type_id'], 'visibility': row['visibility'],
               'reviewer': row['reviewer'], 'annotation_version': row['annotation_version']}
              for row in rows if sources[row['source_sha256']][1] in PARTITIONS
              and row['annotation_status'] == 'confirmed' and row['visibility'] == 'clear'
              and row['start_sec'] is not None]
    exported_ids = {event['event_id'] for event in events}
    master_by_id = {row['event_id']: row for row in rows}
    for event in events:
        group, _ = sources[event['source_sha256']]
        if event['start_sec'] >= event['end_sec']:
            raise ValueError('TCN events require a positive-duration human interval')
        if group['label'] != 1:
            raise ValueError('Confirmed event conflicts with trusted negative source label')
    for digest in sources:
        local = sorted((event for event in events if event['source_sha256'] == digest),
                       key=lambda event: event['start_sec'])
        for index, first in enumerate(local):
            if any(first['end_sec'] > second['start_sec'] and first['type'] != second['type']
                   for second in local[index + 1:]):
                raise ValueError('Overlapping events have conflicting human types')
    output, provenance = [], []
    for digest, (group, partition) in sources.items():
        if partition not in PARTITIONS:
            continue
        base = dict.fromkeys(ANNOTATION_COLUMNS, '')
        base.update(source_sha256=digest, source_file=group['file'], organizer_label=group['label'])
        full = coverage.get(digest)
        requested = full['coverage_complete'] if full else ''
        unresolved = [row['event_id'] for row in rows if row['source_sha256'] == digest
                      and row['start_sec'] is not None and row['event_id'] not in exported_ids]
        # The TCN schema cannot mask individual uncertain intervals. Preserve all
        # background as unknown when any annotated interval was omitted, even if
        # a human completed playback. Merely retained organizer points are inert.
        downgraded = requested == 'yes' and bool(unresolved)
        effective = 'no' if downgraded else requested
        output.append(base | {'coverage_complete': effective,
                              'reviewer': full['reviewer'] if full else ''})
        common = {'source_sha256': digest, 'source_partition': partition,
                  'training_eligible': partition == 'train'}
        provenance.append(common | {'output_row': len(output), 'kind': 'coverage',
                                     'coverage_complete': effective, 'coverage_requested': requested,
                                     'coverage_downgrade_reason': 'unexported_annotated_intervals' if downgraded else None,
                                     'unexported_event_ids': unresolved,
                                     'reviewer': full['reviewer'] if full else '',
                                     'annotation_version': full['annotation_version'] if full else None})
        for event in events:
            if event['source_sha256'] != digest:
                continue
            master = master_by_id[event['event_id']]
            points = group.get('points_sec') or ([group['point_sec']] if group.get('point_sec') is not None else [])
            point = master['point_sec'] if master['point_sec'] in points else ''
            output.append(base | {'organizer_point_sec': point, 'start_sec': event['start_sec'],
                                  'end_sec': event['end_sec'], 'event_type': event['type'],
                                  'visibility': event['visibility'], 'reviewer': event['reviewer'],
                                  'confirmed': 'yes'})
            provenance.append(common | {'output_row': len(output), 'kind': 'event',
                                         'event_id': event['event_id'],
                                         'annotation_version': event['annotation_version'],
                                         'reviewer': event['reviewer'], 'origin': 'human'})
    return output, provenance


def _read_coverage(path, manifest, split):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(COVERAGE_COLUMNS):
            raise ValueError('Exact ordered coverage columns required')
        return validate_coverage(list(reader), manifest, split)


def _new_csv(path):
    path = Path(path)
    if path.suffix.lower() != '.csv':
        raise ValueError('Use a separate CSV output; original workbooks are read-only')
    if path.exists():
        raise FileExistsError(f'Refusing to overwrite {path}')
    return path


def _write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_bridge(master_path, coverage_path, manifest_path, split_path, output_path):
    """Exclusively create a derived CSV and a hash-bound, versioned JSON sidecar."""
    output = _new_csv(output_path)
    sidecar = output.with_suffix('.bridge.json')
    if sidecar.exists():
        raise FileExistsError(sidecar)
    input_paths = [('master', master_path), ('coverage', coverage_path),
                   ('manifest', manifest_path), ('split', split_path)]
    inputs = {name: {'path': str(Path(path).resolve()), 'sha256': sha256_file(path)}
              for name, path in input_paths}
    manifest, split = read_json(manifest_path), read_json(split_path)
    master = read_csv(master_path, manifest, split)
    coverage = _read_coverage(coverage_path, manifest, split)
    rows, provenance = sequence_rows(master, coverage, manifest, split)
    sources = _sources(manifest, split)
    event_hashes = {row['source_sha256'] for row in rows if row['confirmed'] == 'yes'}
    complete = {row['source_sha256'] for row in rows if row['coverage_complete'] == 'yes'}
    result = {'status': 'derived_review_written', 'confirmed_events': sum(row['confirmed'] == 'yes' for row in rows),
              'fully_reviewed_sources': len(complete),
              'coverage_downgraded_sources': [canonical_id(sources[row['source_sha256']][0]['file'])
                                               for row in provenance if row.get('coverage_downgrade_reason')],
              'missing_positive_intervals': [canonical_id(group['file']) for digest, (group, partition) in sources.items()
                                             if partition in PARTITIONS and group['label'] == 1 and digest not in event_hashes],
              'missing_negative_coverage': [canonical_id(group['file']) for digest, (group, partition) in sources.items()
                                            if partition in PARTITIONS and group['label'] == 0 and digest not in complete],
              'model_training': 'not_run'}
    metadata = {'schema_version': SCHEMA_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                'selection_partitions': list(PARTITIONS), 'provenance_authority': 'manifest + fixed source split',
                'coverage_authority': 'separate explicit whole-source human coverage confirmation',
                'inputs': inputs,
                'excluded_master_rows': dict(Counter(sources[row['source_sha256']][1] for row in master
                                                     if sources[row['source_sha256']][1] not in PARTITIONS)),
                'rows': provenance, 'summary': result}
    if any(sha256_file(path) != inputs[name]['sha256'] for name, path in input_paths):
        raise ValueError('Review inputs changed during conversion; save stable versions and retry')
    _write_csv(output, rows, ANNOTATION_COLUMNS)
    metadata['output'] = {'path': str(output.resolve()), 'sha256': sha256_file(output),
                          'columns': list(ANNOTATION_COLUMNS)}
    with sidecar.open('x', encoding='utf-8') as stream:
        json.dump(metadata, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return result


def prepare_review(master_path, manifest_path, split_path, table_path, coverage_path):
    """Copy unreviewed original train/validation points and blank coverage records."""
    table, cover = _new_csv(table_path), _new_csv(coverage_path)
    readme = table.with_suffix('.README.md')
    if table.resolve() == cover.resolve() or readme.exists():
        raise ValueError('Review, coverage and documentation destinations must be new and separate')
    manifest, split = read_json(manifest_path), read_json(split_path)
    sources = _sources(manifest, split)
    rows = [row for row in read_csv(master_path, manifest, split)
            if sources[row['source_sha256']][1] in PARTITIONS and row['annotation_status'] == 'unreviewed'
            and row['point_sec'] in (sources[row['source_sha256']][0].get('points_sec') or [])]
    write_csv(table, rows, manifest, split)
    _write_csv(cover, coverage_rows(manifest, split), COVERAGE_COLUMNS)
    instructions = '''# Локальный просмотр train/validation

Это рабочая копия исходных точек из master events-review.csv. Test исключён.
Excel открыт только для чтения. Начало, конец, тип и полнота просмотра не угаданы.
Видео и кадры просматриваются локально; не отправляйте их внешним AI.
Канонический файл покрытия — local/dataset/review-coverage.csv, общий с UI.
После сохранений в UI не используйте отдельный шаблон quality-sprint-coverage.csv
вместо него. prepare откажется перезаписывать существующий coverage-файл.

1. У точки найдите фактические границы события. Заполните start_sec/end_sec,
   observed_action, visibility (clear/partial/insufficient/not_visible/unknown),
   reviewer и annotation_version. Сначала статус draft, после проверки confirmed.
2. type_id — только конкретная подтверждённая наблюдаемая категория. Пересчёт денег,
   передача документа или открытие ящика сами по себе не подтверждают нарушение.
   Если тип неизвестен, оставьте type_id пустым: это отказ от классификации типа.
   Для бинарного TCN confirmed + clear + реальный интервал всё равно означают
   подтверждённое целевое событие. Не подтверждайте так обычное нейтральное действие.
   Если само событие не установлено, оставьте draft/rejected; если видимость
   недостаточна, укажите её честно. Пустой тип не делает событие отрицательным.
   Правовые основания здесь не навязываются; rule_id можно оставить пустым.
3. Дополнительное событие — отдельная строка с новым безопасным event_id. Сохраните
   video_id/source_sha256. Не заменяйте исходную точку границами придуманного интервала.
4. Полнота просмотра подтверждается ОТДЕЛЬНО в coverage CSV: coverage_complete=yes
   только после просмотра всего видео и внесения всех событий. Обязательны reviewer
   и annotation_version. Пусто/no означает, что непроверенный фон остаётся неизвестным.
   Для нормальных видео не создавайте фиктивные события: достаточно полного просмотра.
5. Согласуйте правки с master events-review.csv по event_id. Машинные предложения
   держите отдельно. Производные таблицы пересоздавайте новым именем после правок.
6. Проверка master: scripts/event_labels_table.py validate --table <master.csv>.
   Конвертация: scripts/sequence_review_bridge.py convert --table <master.csv>
   --coverage local/dataset/review-coverage.csv --output <new-derived.csv>.
   Этот coverage-путь используется CLI по умолчанию. Нужен PYTHONPATH=src.
   Команда пишет versioned .bridge.json с SHA-256 входов и provenance каждой строки.
   Если входные файлы меняются во время конвертации, экспорт отменяется.
   Она не обучает модель. Оригинальные интервалы и полное покрытие обязательны
   перед обучением TCN; наличие одного события не подтверждает просмотр остального.

## Исходные точки (видео | секунды | часть)

'''
    instructions += '\n'.join(f"- {row['video_id']} | {row['point_sec']:g} | {sources[row['source_sha256']][1]}"
                               for row in rows)
    instructions += '\n\n## Полный просмотр нормальных источников (видео | длительность | часть)\n\n'
    instructions += '\n'.join(f"- {canonical_id(group['file'])} | {group['duration_sec']:g} с | {partition}"
                               for group, partition in sources.values() if partition in PARTITIONS and group['label'] == 0)
    readme.write_text(instructions + '\n', encoding='utf-8')
    return {'status': 'review_handoff_ready', 'copied_points': len(rows),
            'coverage_sources': len(coverage_rows(manifest, split)), 'test_sources_exported': 0}
