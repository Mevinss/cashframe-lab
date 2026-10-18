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
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit
from .event_labels import is_specific_type
from .rules import is_verified_rule
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TITLE = 'Предварительный акт видеоаудита — требует подтверждения уполномоченным сотрудником'
UNSET = 'Не установлено / не сохранено'
NO_RULE = 'Нормативное основание не установлено'
PARAMETERS = ('sample_fps', 'refinement_fps', 'window_radius_sec', 'window_stride_sec',
              'event_threshold', 'video_threshold','threshold_roundoff_epsilon_multiplier')
METADATA = ('size_bytes', 'duration_sec', 'width', 'height', 'frame_rate_mode',
            'average_fps', 'nominal_fps', 'codec')

# Read-only per-call catalogs. Stored descriptions, rule documents and observations
# remain in their original language and are explicitly marked as source content.
_MESSAGES = '''title|Предварительный акт видеоаудита — требует подтверждения уполномоченным сотрудником|Бейнеаудиттің алдын ала актісі - уәкілетті қызметкердің растауы қажет|Preliminary video audit - requires confirmation by an authorized employee
unset|Не установлено / не сохранено|Анықталмаған / сақталмаған|Not established / not saved
no_rule|Нормативное основание не установлено|Нормативтік негіз анықталмаған|Regulatory basis not established
intro|Локальный предварительный анализ. Требует независимой проверки уполномоченным сотрудником.|Жергілікті алдын ала талдау. Уәкілетті қызметкердің тәуелсіз тексеруі қажет.|Local preliminary analysis. Requires independent review by an authorized employee.
violation|Обнаружены признаки возможных нарушений|Ықтимал бұзушылықтардың белгілері анықталды|Signs of possible violations detected
no_violation|Нарушение не обнаружено|Бұзушылық анықталмады|No violation detected
no_events|События моделью не обнаружены.|Модель оқиғаларды анықтамады.|The model detected no events.
review_notice|Вывод модели требует проверки человеком. Отсутствие сигнала не гарантирует отсутствие нарушения.|Модель қорытындысын адам тексеруі қажет. Сигналдың болмауы бұзушылықтың жоқтығына кепілдік бермейді.|The model result requires human review. No signal does not guarantee the absence of a violation.
integrity_notice|SHA-256 подтверждает целостность файлов, а не юридическую достоверность вывода.|SHA-256 файлдардың тұтастығын растайды, қорытындының заңдық дұрыстығын растамайды.|SHA-256 verifies file integrity, not the legal validity of the conclusion.
source_missing|SHA-256 исходного видео не сохранён; происхождение не проверено.|Бастапқы бейненің SHA-256 хеші сақталмаған; шығу тегі тексерілмеген.|Source video SHA-256 was not saved; provenance is unverified.
versions_missing|Часть версий или хешей модели, конфигурации и кода не сохранена.|Модельдің, конфигурацияның және кодтың кейбір нұсқалары немесе хештері сақталмаған.|Some model, configuration or code versions or hashes were not saved.
metadata_missing|Часть технических характеристик видео не сохранена.|Бейненің кейбір техникалық сипаттамалары сақталмаған.|Some technical video metadata was not saved.
invisible_limits|Суммы, проводки, полномочия и личности людей по этому отчёту не устанавливаются.|Бұл есеп бойынша сомалар, бухгалтерлік жазбалар, өкілеттіктер және адамдардың жеке басы анықталмайды.|This report does not establish amounts, ledger entries, authority or personal identities.
types_limit|Типы исходных событий модели не подтверждены; подтверждения человека приведены отдельно.|Модельдің бастапқы оқиға түрлері расталмаған; адамның растаулары бөлек берілген.|Original model event types are unconfirmed; human confirmations are listed separately.
source_boundary|Исходный текст ниже сохранён на языке оригинала.|Төмендегі бастапқы мәтін түпнұсқа тілінде сақталған.|Source text below is preserved in its original language.
report_id|Номер отчёта|Есеп нөмірі|Report number
job_id|Идентификатор задания|Тапсырма идентификаторы|Job identifier
created|Дата начала анализа|Талдаудың басталу күні|Analysis started
completed|Дата завершения анализа|Талдаудың аяқталу күні|Analysis completed
source|Исходное видео|Бастапқы бейне|Source video
filename|Название|Атауы|Filename
size|Размер, байт|Өлшемі, байт|Size, bytes
duration|Длительность|Ұзақтығы|Duration
resolution|Разрешение|Ажыратымдылығы|Resolution
fps|Средняя / номинальная частота кадров, FPS|Орташа / номиналды кадр жиілігі, FPS|Average / nominal frame rate, FPS
frame_rate_mode|Режим частоты кадров|Кадр жиілігінің режимі|Frame rate mode
codec|Кодек|Кодек|Codec
result|Результат модели|Модель нәтижесі|Model result
verdict|Исходный вердикт модели|Модельдің бастапқы қорытындысы|Original model verdict
event_count|Количество обнаруженных событий|Анықталған оқиғалар саны|Detected event count
event|Событие|Оқиға|Event
type_unknown|Тип не установлен. Исходная категория модели|Түрі анықталмаған. Модельдің бастапқы санаты|Type not established. Original model category
interval|Интервал|Аралық|Interval
timecode|Таймкод модели|Модель таймкоды|Model timecode
unresolved|Момент события не установлен|Оқиға сәті анықталмаған|Event time not established
model_description|Описание модели (наблюдаемое действие человеком не подтверждено):|Модель сипаттамасы (байқалған әрекетті адам растамаған):|Model description (observed action not confirmed by a human):
no_frames|Кадры доказательств не сохранены.|Дәлел кадрлары сақталмаған.|Evidence frames were not saved.
before|До|Дейін|Before
after|После|Кейін|After
unknown|Положение не установлено|Орны анықталмаған|Position not established
context|Контекст видео|Бейне контексті|Video context
frame_missing|Кадр «{name}» с фактической временной отметкой не сохранён.|Нақты уақыт белгісі бар «{name}» кадры сақталмаған.|The “{name}” frame with an actual timestamp was not saved.
human|Наблюдения и подтверждения человека|Адамның бақылаулары мен растаулары|Human observations and confirmations
no_human|Подтверждённые наблюдения человека не сохранены.|Адам растаған бақылаулар сақталмаған.|Confirmed human observations were not saved.
reviewer|Проверяющий|Тексеруші|Reviewer
observed_interval|Интервал наблюдения|Бақылау аралығы|Observation interval
status|Статус|Мәртебе|Status
confirmed|Подтверждено человеком|Адам растаған|Human confirmed
draft|Черновик / не подтверждено|Жоба / расталмаған|Draft / unconfirmed
type|Тип по разметке|Белгіленген түрі|Annotated type
visibility|Видимость|Көрінуі|Visibility
observed_fact|Наблюдаемое действие|Байқалған әрекет|Observed action
document|Нормативный документ|Нормативтік құжат|Regulatory document
section|Пункт|Тармақ|Section
url|Ссылка|Сілтеме|Source link
applicability|Условия применимости, подтверждены проверяющим|Тексеруші растаған қолдану шарттары|Applicability conditions confirmed by reviewer
rule_limits|Ограничения нормы|Норманың шектеулері|Rule limitations
rule_status|Сохранённый статус основания|Сақталған негіз мәртебесі|Stored basis status
versions|Версии и параметры анализа|Талдау нұсқалары мен параметрлері|Analysis versions and parameters
model|Модель|Модель|Model
configuration|Конфигурация|Конфигурация|Configuration
code|Приложение|Қолданба|Application
parameters|Параметры анализа|Талдау параметрлері|Analysis parameters
passport|Evidence Passport и контрольные суммы|Evidence Passport және бақылау хештері|Evidence Passport and checksums
passport_id|Идентификатор паспорта|Паспорт идентификаторы|Passport identifier
passport_hash|SHA-256 файла passport.json|passport.json файлының SHA-256 хеші|passport.json SHA-256
source_hash|SHA-256 исходного видео|Бастапқы бейненің SHA-256 хеші|Source video SHA-256
passport_notice|Паспорт сохранён рядом с PDF в структурированном JSON. Хеши JPEG указаны для фактически включённых байтов без перекодирования.|Паспорт PDF жанында құрылымдалған JSON пішімінде сақталған. JPEG хештері қайта кодталмаған нақты байттарға тиесілі.|The passport is stored beside the PDF as structured JSON. JPEG hashes cover the actual included bytes without re-encoding.
limitations|Ограничения анализа|Талдау шектеулері|Analysis limitations
human_conclusion|Заключение уполномоченного проверяющего|Уәкілетті тексерушінің қорытындысы|Authorized reviewer conclusion
conclusion|Заключение|Қорытынды|Conclusion
name|Имя|Аты-жөні|Name
date|Дата|Күні|Date
signature|Подпись|Қолы|Signature
footer|Предварительный анализ · требуется проверка человеком|Алдын ала талдау · адамның тексеруі қажет|Preliminary analysis · human review required
clear|Хорошо видно|Анық көрінеді|Clear
partial|Частично видно|Ішінара көрінеді|Partial
insufficient|Недостаточная видимость|Көрінуі жеткіліксіз|Insufficient visibility
not_visible|Не видно|Көрінбейді|Not visible
proposed|Предложено моделью|Модель ұсынған|Model proposed
not_established|Не установлено|Анықталмаған|Not established
not_checked|Не проверено|Тексерілмеген|Not checked
model_proposal|Предложено моделью|Модель ұсынған|Model proposed
localized|Локализовано моделью|Модель орнын анықтаған|Localized by model
localization_status|Статус локализации|Уақытты анықтау мәртебесі|Localization status
second|с|с|s
id|Идентификатор|Идентификатор|Identifier
sha256|SHA-256|SHA-256|SHA-256
encoder_sha256|SHA-256 энкодера|Энкодердің SHA-256 хеші|Encoder SHA-256
video_classifier_sha256|SHA-256 бинарной головы|Бинарлық бастың SHA-256 хеші|Video classifier SHA-256
video_classifier_metadata_sha256|SHA-256 метаданных бинарной головы|Бинарлық бас метадеректерінің SHA-256 хеші|Video classifier metadata SHA-256
type_classifier_sha256|SHA-256 классификатора типов|Түрлер жіктеуішінің SHA-256 хеші|Type classifier SHA-256
point_localizer_sha256|SHA-256 локализатора точек|Нүктелер локализаторының SHA-256 хеші|Point localizer SHA-256
point_localizer_metadata_sha256|SHA-256 метаданных локализатора точек|Нүктелер локализаторы метадеректерінің SHA-256 хеші|Point localizer metadata SHA-256
point_localizer_guard|Ограничитель локализатора точек|Нүктелер локализаторының шектеуі|Point localizer guard
point_localizer_score_kind|Характер оценки локализатора точек|Нүктелер локализаторы бағасының сипаты|Point localizer score kind
version|Версия|Нұсқа|Version
sample_fps|Частота выборки кадров|Кадрларды іріктеу жиілігі|Frame sampling rate
refinement_fps|Частота уточнения|Нақтылау жиілігі|Refinement sampling rate
window_radius_sec|Радиус окна, секунды|Терезе радиусы, секунд|Window radius, seconds
window_stride_sec|Шаг окна, секунды|Терезе қадамы, секунд|Window stride, seconds
event_threshold|Порог события|Оқиға шегі|Event threshold
video_threshold|Порог видео|Бейне шегі|Video threshold
threshold_roundoff_epsilon_multiplier|Допуск округления, множитель FP64 epsilon|Дөңгелектеу шегі, FP64 epsilon көбейткіші|Roundoff allowance, FP64 epsilon multiplier
window_aggregation|Вычисление оконных признаков|Терезелік белгілерді есептеу|Window feature aggregation
localization_guard|Ограничения локализации|Локализация шектеулері|Localization safeguards
constant|Постоянная|Тұрақты|Constant
variable|Переменная|Айнымалы|Variable
synthetic|Синтетический пример|Синтетикалық мысал|Synthetic example'''
_CATALOG = MappingProxyType({row[0]: MappingProxyType(dict(zip(('ru', 'kk', 'en'), row[1:])))
                            for row in (line.split('|') for line in _MESSAGES.splitlines())})


def validate_language(language):
    if language not in ('ru', 'kk', 'en'):
        raise ValueError('Report language must be ru, kk or en')
    return language


def _translator(language):
    validate_language(language)
    return lambda key: _CATALOG[key][language]


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
    for key in ('video_classifier_sha256', 'video_classifier_metadata_sha256', 'type_classifier_sha256',
                'point_localizer_sha256', 'point_localizer_metadata_sha256'):
        if key in (snapshot.get('model') or {}):
            versions['model'][key] = _hash(snapshot['model'][key])
    versions['parameters'] = {key: _number((snapshot.get('parameters') or {}).get(key)) for key in PARAMETERS}
    for key in ('point_localizer_guard', 'localization_guard', 'point_localizer_score_kind','window_aggregation'):
        value = (snapshot.get('parameters') or {}).get(key)
        if value is not None:
            if key.endswith('_guard') and isinstance(value,dict):
                versions['parameters'][key]={name:_number(value[name]) for name in (
                    'min_score','min_range','min_prominence','tail_fraction','max_coverage_fraction',
                    'max_events','flat_range') if name in value}
            else:versions['parameters'][key] = _text(value)
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
        if rule.get('id') == annotation.get('rule_id') and is_verified_rule(rule):
            url = urlsplit(str(rule.get('url', '')))
            safe_url = urlunsplit((url.scheme, url.hostname or '', url.path, '', '')) if url.scheme in ('https', 'http') else None
            return {key: _text(rule.get(key)) for key in ('id', 'document', 'title', 'section', 'verified_on', 'applicability', 'limits')} | {'url': safe_url}
    return None


def _manifest(job, version_snapshot, rules, annotations, language='ru'):
    t = _translator(language)
    def tx(value):
        return t('unset') if value is None else _text(value)
    review = job.get('review') or {}
    metadata = review.get('metadata') or {}
    technical = {key: tx(metadata[key]) if key in ('frame_rate_mode', 'codec') and metadata.get(key) is not None
                 else _number(metadata.get(key)) for key in METADATA}
    if technical['duration_sec'] is None:
        technical['duration_sec'] = _number(review.get('duration_sec'))
    filename = str(job.get('filename') or '').replace('\\', '/').rsplit('/', 1)[-1]
    verdict = job['result'].get('verdict')
    if verdict not in ('violation', 'no_violation'):
        raise ValueError('Stored result must contain a binary verdict')
    manifest = {'schema_version': 'cashframe.evidence-passport/1', 'language': language,
                'title': t('title'), 'verdict_label': t(verdict), 'unknown_value_label': t('unset'),
                'source_content_notice': t('source_boundary'), 'job_id': tx(job.get('job_id')),
                'analysis_created_at': _date(job.get('created_at')), 'analysis_completed_at': _date(job.get('completed_at')),
                'source': {'filename': tx(filename) if filename else None, 'sha256': _hash(job.get('source_sha256')), **technical},
                'versions': _versions(version_snapshot if version_snapshot is not None else review.get('version_snapshot')),
                'verdict': tx(job['result'].get('verdict')), 'events': [], 'human_annotations': [],
                'limitations': [], 'source_limitations': [tx(item) for item in review.get('limitations', [])],
                'integrity_notice': t('integrity_notice')}
    images = []
    stored_events = review.get('events') or []
    for index, violation in enumerate(job['result'].get('violations', [])):
        stored = stored_events[index] if index < len(stored_events) else {}
        event = {'event_index': index, 'type': tx(violation.get('type')), 'type_confirmed': False,
                 'localization_status':tx(stored.get('localization_status','model_proposal')),
                 'timecode_sec': _number(violation.get('timecode_sec')), 'start_sec': _number(stored.get('start_sec')),
                 'end_sec': _number(stored.get('end_sec')), 'model_description': tx(violation.get('description')),
                 'evidence_frames': []}
        status = event['localization_status']
        event['localization_status_label'] = t(status) if status in _CATALOG else t('unset')
        event['timecode_role'] = 'context' if status == 'unresolved' else 'model_proposal'
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
            if event['localization_status']=='unresolved':role='context'
            event['evidence_frames'].append({'timestamp_sec': timestamp, 'sha256': sha256(raw).hexdigest(),
                                             'width': width, 'height': height, 'role': role, 'role_label': t(role)})
            event_images.append(raw)
        images.append(event_images)
        manifest['events'].append(event)
    for annotation in annotations:
        row = {key: tx(annotation.get(key)) for key in ('origin', 'reviewer', 'type', 'observed_fact', 'visibility', 'saved_at', 'rule_status')}
        row.update({key: _number(annotation.get(key)) for key in ('event_index', 'start_sec', 'end_sec')})
        row['confirmed'] = _human_confirmed(annotation)
        row['status_label'] = t('confirmed' if row['confirmed'] else 'draft')
        row['visibility_label'] = t(row['visibility']) if row['visibility'] in _CATALOG else t('unset')
        row['rule_applicability_confirmed'] = annotation.get('rule_applicability_confirmed') is True
        row['rule'] = _rule(annotation, rules)
        row['rule_status_label'] = (t('confirmed') if row['rule'] else
                                    t('proposed') if row['rule_status'] == 'proposed' else t('not_established'))
        if row['rule']:
            row['rule'] = {key: t('unset') if value == UNSET else value for key, value in row['rule'].items()}
        manifest['human_annotations'].append(row)
    if not manifest['source']['sha256']:
        manifest['limitations'].append(t('source_missing'))
    if any(value is None for group in ('model', 'configuration', 'code') for value in manifest['versions'][group].values()):
        manifest['limitations'].append(t('versions_missing'))
    if any(value is None for value in technical.values()):
        manifest['limitations'].append(t('metadata_missing'))
    manifest['limitations'] += [t(key) for key in ('review_notice', 'invisible_limits', 'types_limit')]
    manifest['labels'] = {key: t(key) for key in _CATALOG}
    manifest['passport_id'] = 'EP-' + sha256(_canonical(manifest)).hexdigest()[:24]
    return manifest, images


def _render_pdf(path, manifest, images, passport_hash):
    t = _translator(manifest['language'])
    font = 'CashframeUnicode'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(Path(__file__).parent / 'assets/fonts/DejaVuSans.ttf')))
    body = ParagraphStyle('AuditBody', fontName=font, fontSize=9, leading=13, textColor=colors.HexColor('#20303e'), spaceAfter=6, splitLongWords=True)
    heading = ParagraphStyle('AuditHeading', parent=body, fontSize=13, leading=17, spaceBefore=15, spaceAfter=8, keepWithNext=True, textColor=colors.HexColor('#006c68'))
    title = ParagraphStyle('AuditTitle', parent=heading, fontSize=19, leading=25, spaceBefore=0, spaceAfter=14)
    small = ParagraphStyle('AuditSmall', parent=body, fontSize=7.5, leading=10)
    metadata_style = ParagraphStyle('AuditMetadata', parent=body, fontSize=8.25, leading=11, spaceAfter=2)
    caption = ParagraphStyle('AuditCaption', parent=small, alignment=TA_CENTER)
    def display(value):
        return t('unset') if value is None or value == UNSET else _text(value)
    def p(value, style=body):
        return Paragraph(escape(display(value)).replace('\n', '<br/>'), style)
    def field(key, value, style=body):
        return p(f'{t(key)}: {display(value)}', style)
    def seconds(value):
        return f"{value:.6f} {t('second')}" if value is not None else t('unset')
    def section(key):
        story.append(p(t(key), heading))
    def source_text(value):
        story.extend([p(t('source_boundary'), small), p(value)])
    source = manifest['source']
    story = [p(t('title'), title), p(t('intro')), field('report_id', manifest['passport_id']),
             field('job_id', manifest['job_id']), field('created', manifest['analysis_created_at']),
             field('completed', manifest['analysis_completed_at'])]
    section('source')
    mode = source['frame_rate_mode']
    story += [field('filename', source['filename']), field('size', source['size_bytes']), field('duration', seconds(source['duration_sec'])),
              field('resolution', f"{source['width']} × {source['height']}" if source['width'] and source['height'] else None),
              field('fps', f"{display(source['average_fps'])} / {display(source['nominal_fps'])}"),
              field('frame_rate_mode', t(mode) if mode in _CATALOG else mode), field('codec', source['codec'])]
    section('result')
    # The principal verdict always comes from the stored API verdict, even when
    # events are absent or present inconsistently. Counts never determine it.
    story += [p(manifest['verdict_label'], heading), field('verdict', manifest['verdict']),
              field('event_count', len(manifest['events']))]
    if not manifest['events']:
        story.append(p(t('no_events')))
    story.append(p(t('no_rule')))
    for index, event in enumerate(manifest['events']):
        story.append(p(f"{t('event')} {index + 1}", heading))
        story += [field('type_unknown', event['type']), field('localization_status', event['localization_status_label'])]
        if event['localization_status'] == 'unresolved':
            story.append(p(t('unresolved')))
        else:
            story += [field('interval', f"{seconds(event['start_sec'])} - {seconds(event['end_sec'])}"),
                      field('timecode', seconds(event['timecode_sec']))]
        story.append(p(t('model_description')))
        source_text(event['model_description'])
        frames = event['evidence_frames']
        if not frames:
            story.append(p(t('no_frames')))
        for offset in range(0, len(frames), 3):
            cells = []
            for frame, raw in zip(frames[offset:offset+3], images[index][offset:offset+3]):
                scale = min(160 / frame['width'], 128 / frame['height'])
                cells.append([Image(BytesIO(raw), width=frame['width']*scale, height=frame['height']*scale),
                              p(f"{frame['role_label']} · {seconds(frame['timestamp_sec'])}", caption)])
            table = Table([cells], colWidths=[174] * len(cells), hAlign='LEFT')
            table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 4),
                                       ('RIGHTPADDING', (0, 0), (-1, -1), 4), ('BOX', (0, 0), (-1, -1), .4, colors.HexColor('#ccdcd9'))]))
            story += [table, Spacer(1, 7)]
        for role in ([] if event['localization_status'] == 'unresolved' else ['before', 'event', 'after']):
            if not any(frame['role'] == role for frame in frames):
                story.append(p(t('frame_missing').format(name=t(role)), small))
        for frame in frames:
            story.append(p(f"JPEG {seconds(frame['timestamp_sec'])} SHA-256: {frame['sha256']}", small))
    section('human')
    if not manifest['human_annotations']:
        story.append(p(t('no_human') + ' ' + t('no_rule')))
    for annotation in manifest['human_annotations']:
        story += [field('reviewer', annotation['reviewer']), field('observed_interval', f"{seconds(annotation['start_sec'])} - {seconds(annotation['end_sec'])}"),
                  field('status', annotation['status_label']), field('visibility', annotation['visibility_label'])]
        story.append(p(t('source_boundary'), small))
        story += [field('type', annotation['type']), field('observed_fact', annotation['observed_fact'])]
        rule = annotation['rule']
        if rule:
            story += [field('document', rule['document']), field('section', rule['section']), field('url', rule['url']),
                      field('applicability', rule['applicability']), field('rule_limits', rule['limits'])]
        else:
            story += [p(t('no_rule')), field('rule_status', annotation['rule_status_label'])]
    section('versions')
    for group in ('model', 'configuration', 'code'):
        for key, value in manifest['versions'][group].items():
            story.append(p(f"{t(group)} / {t(key)}: {display(value)}", metadata_style))
    story.append(p(t('parameters'), small))
    for key, value in manifest['versions']['parameters'].items():
        story.append(field(key, value, metadata_style))
    section('passport')
    story += [field('passport_id', manifest['passport_id']), field('passport_hash', passport_hash),
              field('source_hash', source['sha256']), p(t('passport_notice')), p(manifest['integrity_notice'])]
    section('limitations')
    story += [p(f'{i + 1}. {item}') for i, item in enumerate(manifest['limitations'])]
    if manifest['source_limitations']:
        story.append(p(t('source_boundary'), small))
        story += [p(item) for item in manifest['source_limitations']]
    story.append(KeepTogether([p(t('human_conclusion'), heading),
              p(t('conclusion') + ': __________________________________________________'), Spacer(1, 6),
              p('_____________________________________________________________________'), Spacer(1, 6),
              p(t('name') + ': _________________________   ' + t('date') + ': _________________________'), Spacer(1, 6),
              p(t('signature') + ': _____________________')]))
    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor('#63766f'))
        canvas.drawString(38, 24, t('footer'))
        canvas.drawRightString(A4[0]-38, 24, str(doc.page))
        canvas.restoreState()
    document = SimpleDocTemplate(path, pagesize=A4, rightMargin=36, leftMargin=36,
                                 topMargin=36, bottomMargin=44, title=t('title'), author='Local video audit', invariant=1)
    document.build(story, onFirstPage=page, onLaterPages=page)


def generate_report(job, output_dir, *, version_snapshot=None, rules=(), annotations=(), language='ru'):
    """Render a stored done-job snapshot into report.pdf and canonical passport.json.

    Accepted metadata lives in review.metadata, versions in review.version_snapshot.
    Unknown versions remain unknown; legacy jobs never acquire today's versions.
    Caller must serialize concurrent generation and retain the immutable snapshot.
    """
    if job.get('status') != 'done' or not isinstance(job.get('result'), dict):
        raise ValueError('Report requires a completed stored result')
    manifest, images = _manifest(job, version_snapshot, rules, annotations, language)
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
