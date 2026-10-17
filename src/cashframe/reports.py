"""Offline reports. Inputs are stored snapshots; this module never reads video or URLs.

The caller owns access control, expiry, locking and cache invalidation. A passport
hash covers its exact UTF-8 bytes; its ID derives from the manifest before adding
the ID (there is deliberately no self-referential checksum field).
"""
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from .event_labels import is_specific_type
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TITLE = 'Предварительный акт видеоаудита — требует подтверждения уполномоченным сотрудником'
UNSET = 'Не установлено / не сохранено'
NO_RULE = 'Нормативное основание не установлено'
PARAMETERS = ('sample_fps', 'refinement_fps', 'window_radius_sec', 'window_stride_sec',
              'event_threshold', 'video_threshold')
METADATA = ('size_bytes', 'duration_sec', 'width', 'height', 'frame_rate_mode',
            'average_fps', 'nominal_fps', 'codec')


@dataclass(frozen=True)
class ReportArtifacts:
    pdf_path: Path
    passport_path: Path
    passport_id: str
    passport_sha256: str


def _text(value):
    """Remove control characters, credential assignments and local paths."""
    if value is None:
        return UNSET
    text = str(value)
    text = re.sub(r'(?i)(?:[a-z]:[\\/]|file://|\\\\)[^\s<>"\']+', '[путь скрыт]', text)
    text = re.sub(r'(?<![\w:/])/(?:home|tmp|var|Users|private|srv|opt)/[^\s<>"\']+', '[путь скрыт]', text)
    text = re.sub(r'(?i)(?:x-api-key|api[_-]?key|authorization|password|token)\s*[:=]\s*\S+', '[секрет скрыт]', text)
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _hash(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{64}', value) else None


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _date(value):
    try:
        if isinstance(value, (float, int)):
            date = datetime.fromtimestamp(value, timezone.utc)
        else:
            date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if date.tzinfo is None:
                return None
        return date.astimezone(timezone.utc).isoformat().replace('+00:00', ' UTC')
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _versions(snapshot):
    snapshot = snapshot or {}
    versions = {}
    for group, fields in [('model', ('id', 'sha256', 'encoder_sha256')),
                          ('configuration', ('id', 'sha256')), ('code', ('version', 'sha256'))]:
        source = snapshot.get(group) or {}
        versions[group] = {key: _hash(source.get(key)) if key.endswith('sha256') else
                           _text(source[key]) if source.get(key) is not None else None for key in fields}
    for key in ('video_classifier_sha256', 'video_classifier_metadata_sha256', 'type_classifier_sha256'):
        if key in (snapshot.get('model') or {}):
            versions['model'][key] = _hash(snapshot['model'][key])
    versions['parameters'] = {key: _number((snapshot.get('parameters') or {}).get(key)) for key in PARAMETERS}
    return versions


def _human_confirmed(annotation):
    return (annotation.get('confirmed') is True and annotation.get('origin') == 'human'
            and is_specific_type(annotation.get('type'))
            and all(isinstance(annotation.get(k), str) and annotation[k].strip()
                    for k in ('reviewer', 'type', 'observed_fact')))


def _rule(annotation, rules):
    valid = (_human_confirmed(annotation) and annotation.get('visibility') == 'clear'
             and annotation.get('rule_status') == 'confirmed' and annotation.get('rule_applicability_confirmed') is True)
    if not valid:
        return None
    for rule in rules:
        if (rule.get('id') == annotation.get('rule_id') and rule.get('verified') is True
                and rule.get('applicability') and rule.get('visual_cues')):
            url = urlsplit(str(rule.get('url', '')))
            safe_url = urlunsplit((url.scheme, url.hostname or '', url.path, '', '')) if url.scheme in ('https', 'http') else None
            return {key: _text(rule.get(key)) for key in ('id', 'document', 'title', 'section', 'verified_on', 'applicability', 'limits')} | {'url': safe_url}
    return None


def _manifest(job, version_snapshot, rules, annotations):
    review = job.get('review') or {}
    metadata = review.get('metadata') or {}
    technical = {key: _text(metadata[key]) if key in ('frame_rate_mode', 'codec') and metadata.get(key) is not None
                 else _number(metadata.get(key)) for key in METADATA}
    if technical['duration_sec'] is None:
        technical['duration_sec'] = _number(review.get('duration_sec'))
    filename = str(job.get('filename') or '').replace('\\', '/').rsplit('/', 1)[-1]
    manifest = {'schema_version': 'cashframe.evidence-passport/1', 'job_id': _text(job.get('job_id')),
                'analysis_created_at': _date(job.get('created_at')), 'analysis_completed_at': _date(job.get('completed_at')),
                'source': {'filename': _text(filename) if filename else None, 'sha256': _hash(job.get('source_sha256')), **technical},
                'versions': _versions(version_snapshot if version_snapshot is not None else review.get('version_snapshot')),
                'verdict': _text(job['result'].get('verdict')), 'events': [], 'human_annotations': [],
                'limitations': [_text(item) for item in review.get('limitations', [])],
                'integrity_notice': 'SHA-256 подтверждает целостность файлов, а не юридическую достоверность вывода.'}
    images = []
    stored_events = review.get('events') or []
    for index, violation in enumerate(job['result'].get('violations', [])):
        stored = stored_events[index] if index < len(stored_events) else {}
        event = {'event_index': index, 'type': _text(violation.get('type')), 'type_confirmed': False,
                 'timecode_sec': _number(violation.get('timecode_sec')), 'start_sec': _number(stored.get('start_sec')),
                 'end_sec': _number(stored.get('end_sec')), 'model_description': _text(violation.get('description')),
                 'evidence_frames': []}
        event_images = []
        for frame in violation.get('evidence_frames', []):
            try:
                raw = base64.b64decode(frame['image_base64'], validate=True)
                with PILImage.open(BytesIO(raw)) as image:
                    if image.format != 'JPEG':
                        raise ValueError('Evidence must be a stored JPEG')
                    width, height = image.size
                    image.verify()
            except (KeyError, TypeError, binascii.Error, OSError) as exc:
                raise ValueError('Stored evidence JPEG is invalid') from exc
            timestamp = _number(frame.get('timestamp_sec'))
            if timestamp is None or timestamp < 0:
                raise ValueError('Stored evidence timestamp is invalid')
            point = event['timecode_sec']
            role = 'event' if point is not None and abs(timestamp - point) < 1e-6 else 'before' if point is not None and timestamp < point else 'after' if point is not None else 'unknown'
            event['evidence_frames'].append({'timestamp_sec': timestamp, 'sha256': sha256(raw).hexdigest(),
                                             'width': width, 'height': height, 'role': role})
            event_images.append(raw)
        images.append(event_images)
        manifest['events'].append(event)
    for annotation in annotations:
        row = {key: _text(annotation.get(key)) for key in ('origin', 'reviewer', 'type', 'observed_fact', 'visibility', 'saved_at', 'rule_status')}
        row.update({key: _number(annotation.get(key)) for key in ('event_index', 'start_sec', 'end_sec')})
        row['confirmed'] = _human_confirmed(annotation)
        row['rule_applicability_confirmed'] = annotation.get('rule_applicability_confirmed') is True
        row['rule'] = _rule(annotation, rules)
        manifest['human_annotations'].append(row)
    if not manifest['source']['sha256']:
        manifest['limitations'].append('SHA-256 исходного видео не сохранён; происхождение не проверено.')
    if any(value is None for group in ('model', 'configuration', 'code') for value in manifest['versions'][group].values()):
        manifest['limitations'].append('Часть версий или хешей модели, конфигурации и кода не сохранена.')
    if any(value is None for value in technical.values()):
        manifest['limitations'].append('Часть технических характеристик видео не сохранена.')
    manifest['limitations'] += ['Вывод модели требует проверки человеком. Отсутствие сигнала не гарантирует отсутствие нарушения.',
                               'Суммы, проводки, полномочия и личности людей по этому отчёту не устанавливаются.',
                               'Типы исходных событий модели не подтверждены; подтверждения человека приведены отдельно.']
    manifest['passport_id'] = 'EP-' + sha256(_canonical(manifest)).hexdigest()[:24]
    return manifest, images


def _render_pdf(path, manifest, images, passport_hash):
    font = 'CashframeUnicode'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(Path(__file__).parent / 'assets/fonts/DejaVuSans.ttf')))
    body = ParagraphStyle('AuditBody', fontName=font, fontSize=9, leading=13, textColor=colors.HexColor('#20303e'), spaceAfter=6, splitLongWords=True)
    heading = ParagraphStyle('AuditHeading', parent=body, fontSize=13, leading=17, spaceBefore=15, spaceAfter=8, keepWithNext=True, textColor=colors.HexColor('#006c68'))
    title = ParagraphStyle('AuditTitle', parent=heading, fontSize=19, leading=25, spaceBefore=0, spaceAfter=14)
    small = ParagraphStyle('AuditSmall', parent=body, fontSize=7.5, leading=10)
    caption = ParagraphStyle('AuditCaption', parent=small, alignment=TA_CENTER)
    def p(value, style=body):
        return Paragraph(escape(_text(value)).replace('\n', '<br/>'), style)
    def field(label, value):
        return p(f'{label}: {_text(value)}')
    def seconds(value):
        return f'{value:.6f} с' if value is not None else UNSET
    def section(label):
        story.append(p(label, heading))
    source = manifest['source']
    story = [p(TITLE, title), p('Локальный предварительный анализ. Требует независимой проверки уполномоченным сотрудником.', body),
             field('Номер отчёта', manifest['passport_id']), field('Идентификатор задания', manifest['job_id']),
             field('Дата начала анализа', manifest['analysis_created_at']), field('Дата завершения анализа', manifest['analysis_completed_at'])]
    section('Исходное видео')
    story += [field('Название', source['filename']), field('Размер, байт', source['size_bytes']), field('Длительность', seconds(source['duration_sec'])),
              field('Разрешение', f"{source['width']} × {source['height']}" if source['width'] and source['height'] else None),
              field('Средняя / номинальная частота кадров, FPS', f"{source['average_fps']} / {source['nominal_fps']}" if source['average_fps'] is not None else None),
              field('Режим частоты кадров', source['frame_rate_mode']), field('Кодек', source['codec'])]
    section('Результат модели')
    story += [p('События моделью не обнаружены.' if not manifest['events'] else 'Обнаружены сигналы возможных событий. Нарушения требуют подтверждения человеком.'),
              field('Исходный вердикт модели', manifest['verdict']), field('Количество обнаруженных событий', len(manifest['events'])), p(NO_RULE)]
    for index, event in enumerate(manifest['events']):
        section(f'Событие {index + 1}')
        story += [p('Тип не установлен. Исходная категория модели: ' + event['type']),
                  field('Интервал', f"{seconds(event['start_sec'])} - {seconds(event['end_sec'])}"),
                  field('Таймкод модели', seconds(event['timecode_sec'])), p('Описание модели (наблюдаемое действие человеком не подтверждено):'),
                  p(event['model_description'])]
        frames = event['evidence_frames']
        if not frames:
            story.append(p('Кадры доказательств не сохранены.'))
        for offset in range(0, len(frames), 3):
            cells = []
            for frame, raw in zip(frames[offset:offset+3], images[index][offset:offset+3]):
                scale = min(160 / frame['width'], 128 / frame['height'])
                label = {'before': 'До', 'event': 'Событие', 'after': 'После', 'unknown': 'Положение не установлено'}[frame['role']]
                cells.append([Image(BytesIO(raw), width=frame['width']*scale, height=frame['height']*scale),
                              p(f"{label} · {seconds(frame['timestamp_sec'])}", caption)])
            table = Table([cells], colWidths=[174] * len(cells), hAlign='LEFT')
            table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 4),
                                       ('RIGHTPADDING', (0, 0), (-1, -1), 4), ('BOX', (0, 0), (-1, -1), .4, colors.HexColor('#ccdcd9'))]))
            story += [table, Spacer(1, 7)]
        for role, name in [('before', 'До'), ('event', 'Событие'), ('after', 'После')]:
            if not any(frame['role'] == role for frame in frames):
                story.append(p(f'Кадр «{name}» с фактической временной отметкой не сохранён.', small))
        for frame in frames:
            story.append(p(f"JPEG {seconds(frame['timestamp_sec'])} SHA-256: {frame['sha256']}", small))
    section('Наблюдения и подтверждения человека')
    if not manifest['human_annotations']:
        story.append(p('Подтверждённые наблюдения человека не сохранены. ' + NO_RULE))
    for annotation in manifest['human_annotations']:
        story += [field('Проверяющий', annotation['reviewer']), field('Интервал наблюдения', f"{seconds(annotation['start_sec'])} - {seconds(annotation['end_sec'])}"),
                  field('Статус', 'Подтверждено человеком' if annotation['confirmed'] else 'Черновик / не подтверждено'),
                  field('Тип по разметке', annotation['type']), field('Видимость', annotation['visibility']), field('Наблюдаемое действие', annotation['observed_fact'])]
        rule = annotation['rule']
        if rule:
            story += [field('Нормативный документ', rule['document']), field('Пункт', rule['section']), field('Ссылка', rule['url']),
                      field('Условия применимости, подтверждены проверяющим', rule['applicability']), field('Ограничения нормы', rule['limits'])]
        else:
            story += [p(NO_RULE), field('Сохранённый статус основания', annotation['rule_status'])]
    section('Версии и параметры анализа')
    for group, label in [('model', 'Модель'), ('configuration', 'Конфигурация'), ('code', 'Приложение')]:
        for key, value in manifest['versions'][group].items():
            story.append(field(f'{label} / {key}', value))
    story.append(field('Параметры анализа', json.dumps(manifest['versions']['parameters'], ensure_ascii=False, sort_keys=True)))
    section('Evidence Passport и контрольные суммы')
    story += [field('Идентификатор паспорта', manifest['passport_id']), field('SHA-256 файла passport.json', passport_hash),
              field('SHA-256 исходного видео', source['sha256']), p('Паспорт сохранён рядом с PDF в структурированном JSON. Хеши JPEG указаны для фактически включённых байтов без перекодирования.'),
              p(manifest['integrity_notice'])]
    section('Ограничения анализа')
    story += [p(f'{i + 1}. {item}') for i, item in enumerate(manifest['limitations'])]
    section('Заключение уполномоченного проверяющего')
    story += [p('Заключение: __________________________________________________________'), Spacer(1, 18),
              p('_____________________________________________________________________'), Spacer(1, 18),
              p('Имя: _________________________   Дата: _________________________'), Spacer(1, 12),
              p('Подпись: _____________________')]
    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor('#63766f'))
        canvas.drawString(38, 24, 'Предварительный анализ · требуется проверка человеком')
        canvas.drawRightString(A4[0]-38, 24, str(doc.page))
        canvas.restoreState()
    document = SimpleDocTemplate(path, pagesize=A4, rightMargin=36, leftMargin=36,
                                 topMargin=36, bottomMargin=44, title=TITLE, author='Cashframe local audit', invariant=1)
    document.build(story, onFirstPage=page, onLaterPages=page)


def generate_report(job, output_dir, *, version_snapshot=None, rules=(), annotations=()):
    """Render a stored done-job snapshot into report.pdf and canonical passport.json.

    Accepted metadata lives in review.metadata, versions in review.version_snapshot.
    Unknown versions remain unknown; legacy jobs never acquire today's versions.
    Caller must serialize concurrent generation and retain the immutable snapshot.
    """
    if job.get('status') != 'done' or not isinstance(job.get('result'), dict):
        raise ValueError('Report requires a completed stored result')
    manifest, images = _manifest(job, version_snapshot, rules, annotations)
    raw = _canonical(manifest)
    passport_hash = sha256(raw).hexdigest()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path, passport_path = output_dir / 'report.pdf', output_dir / 'passport.json'
    # Build in memory first so a malformed document never replaces a valid pair.
    pdf = BytesIO()
    _render_pdf(pdf, manifest, images, passport_hash)
    passport_path.write_bytes(raw)
    pdf_path.write_bytes(pdf.getvalue())
    return ReportArtifacts(pdf_path, passport_path, manifest['passport_id'], passport_hash)
