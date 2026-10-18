# Передача независимого исследовательского вклада

Репозиторий: Mevinss/cashframe-lab, рабочая ветка `feature/my-part`.
Оригинальные 93 tracked-файла команды сохранены без изменений. Вклад изолирован
под `research/`, production frontend/API/pipeline/config/models не заменены.

## Что реализовано и переиспользовано

- `event_localization/`: стандартная библиотека, sanitized JSON, maximum-cardinality
  one-to-one assignment с глобальной оптимизацией IoU/нормализованного point error,
  deterministic content ties, localized PR/F1, условные IoU/MAE, unresolved и evidence
  timing. Для points не придумываются интервалы. Negative/empty выделены отдельно.
  Модуль проверен повторно; менять работающую реализацию ради дублирования не стали.
- `ui-candidate/`: профессиональное адаптивное рабочее пространство, Демо и явный
  Реальный режим, MOV upload/readiness/polling/results/review/JPEG/timeline,
  authenticated video Blob preview, manual annotation, PDF/Passport с SHA-256.
  Сетевой сбой не заменяется synthetic data. Новые улучшения: preview browser cap
  128 МиБ, Blob без лишней полной конкатенации буфера, проверка длины/усечения,
  cleanup чтения, корректная busy notification и отказ от устаревшего review,
  расширенное отклонение placeholder types, более строгие MIME/Range проверки
  и очистка несохранённой human-review формы при смене события (подтверждение
  предыдущего события не переносится автоматически).
- `rule_evidence/`: factual human checklist для RK231-20, RK231-22, RK231-19-6.
  Единственный публичный каталог `ui-candidate/rule-catalog.json` используют
  Python-движок и JS. Unknown/not_visible/automatic/unconfirmed не считаются
  выполненными условиями. Юридический verdict всегда отсутствует.
- `model_quality/`: переиспользует `cashframe.research.binary_metrics`; добавляет
  FP/FN tradeoff, paired исправления/регрессии, exact paired test, Wilson intervals.
  Нет дублирования интервал-метрик или автоматического продвижения моделей.
- `model_improvement/`: четыре predeclared negative-reference persistence MIL
  кандидата с fold-fitted negative statistics и регуляризованной video-level head.
  Это другой descriptor, чем mean/std/quantile/kernel/pyramid/dynamics V2/V3.
  Реальный запуск защищён authorization gate, пишет только в новый `local/...`,
  не принимает test data для выбора, не меняет V2.

Переиспользованы read-only существующие schema, annotation validator, binary
metrics, group folds, window features, frozen V2 loader, data preparation и
DINO extraction contracts. Backend в runtime остаётся оригинальным.

## Архитектура

```text
Browser :8001
  ├─ Demo state → synthetic events and mock observations, no backend API
  ├─ Public factual checklist → in-memory assessment, no annotation POST
  └─ Real API adapter → /bridge/allowlisted routes
        → bounded streaming localhost bridge → original FastAPI :8000
          → existing worker/models/reports/annotations (unchanged)

Offline research
  ├─ sanitized temporal exports → event_localization
  ├─ confirmed factual answers → rule_evidence
  └─ authorized prepared train/validation cache → candidate + paired comparison
        all private artifacts only under ignored local/
```

Мост: фиксированное назначение 127.0.0.1:8000, static/API allowlists, host/origin/
Sec-Fetch-Site/custom-header checks, никаких arbitrary URL/CORS/redirects.
Ключ только в transient памяти вкладки и X-API-Key; cookies приватного Codespaces
URL не пересылаются в FastAPI. Upload ≤101 МиБ с overhead, annotation JSON ≤16 КиБ,
64 КиБ streaming blocks, четыре upstream-запроса, socket/total deadlines,
no-store/no body/path/key logs. Конкретные лимиты перечислены в UI README.
Публичных портов не требуется. Мост не является production security boundary
для недоверенных внешних клиентов; Codespaces forwarding должен быть Private.

## Реальные данные и ML-результат

Пользователь явно ответил: **«Нет, реальные данные не обрабатывать»**.
Обработка/распаковка датасета, DINO extraction на банковских видео, реальные
labels/embeddings и real-data обучение не выполнялись. При проверке наличия
`local/open.zip`, `local/dataset/manifest.json`, `local/features/metadata.json`
отсутствовали. При их последующем появлении разрешение автоматически не возникает.
Production модели/thresholds не заменены; реальный эксперимент заблокирован.

| Проверка | V2 reference | Новый кандидат |
| --- | --- | --- |
| Реальная train/validation оценка в этой работе | Не выполнялась | Не выполнялась |
| Accuracy / precision / recall / F1 / specificity / balanced accuracy | Не измерены | Не измерены |
| FP / FN на реальных данных | Не измерены | Не измерены |
| Улучшение | V2 сохранён как reference | Не продемонстрировано |

Synthetic classifier tests обучают только игрушечные NumPy массивы для проверки
алгоритма. Их результат не выдаётся за метрики банковской модели. У команды уже
были OOF/validation/test отчёты V1/V2/V3; эта работа не превращает повторно
просмотренную validation/test в независимую оценку и не пересчитывает их.
Нет заявлений о 85–90%, новом ракурсе, confirmed type accuracy или visual relevance.

Протокол будущей разрешённой работы: fresh ignored local output, фиксированные
source-group splits/encoder, четыре training-group folds; architecture и threshold
по train OOF, validation один раз descriptive против фиксированного V2 без retuning.
Test files/labels не участвуют. Recall floor .75 — экспериментальная настройка,
не требование банка. Снижение FP всегда сравнивается с ростом FN. Production
promotion выключен независимо от exploratory validation результата.

## Фактический checklist и нормы

Используются только documented registry references из `config/rules.json` и
`docs/rules.md`: пункты 20, 22 и подпункт 6 пункта 19 акта НБРК №231.
[Официальный источник](https://adilet.zan.kz/rus/docs/V1900019680) при текущем
чтении вернул JS-оболочку, поэтому **новой независимой проверки правового текста
нет**. Поле verified исходного реестра — факт документации команды, не новое
заключение этого инструмента. Ни один новый нормативный факт не добавлен.

UI-кнопка «Открыть фактический чек-лист» доступна отдельно от model output.
Ответы человека, ясность видимости, контекст и фактические наблюдения проверяются
раздельно. Ненаблюдаемая передача документа не доказывает его отсутствия; выход из
кадра не равен уходу с рабочего места. Даже supported checklist — не юридическое
подтверждение применимости и не автоматическое заполнение annotation формы.
Смена Демо/Реального режима сбрасывает ответы; отправки в backend нет.

## Запуск

Из корня репозитория:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python research/ui-candidate/preview.py
```

Codespaces: Ports → Forward a Port → 8001 → Port Visibility → Private → Open in
Browser. Порт 8000 оставить Private, его сервер не перезапускался этой работой.
UI по умолчанию Демо; реальный режим включается вручную и принимает ключ пользователя
без чтения `local/api-key.txt`. Readiness кнопка вызывает штатный synthetic model
probe, поэтому не нажимать её для pure code-only проверки. В этой работе production
API не вызывался. Ранее установленные V1/V2 артефакты прошли readiness в предыдущем
этапе; это не текущий real-video inference или оценка качества.

Для существующего REAL V2 backend (запускает оператор; не входит в smoke-тесты):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m cashframe.launcher --host 127.0.0.1 --port 8000 --model-dir local/experiment-v2
```

## Выполненные тесты

- 109 исходных Python-регрессий команды — PASS.
- 6 исходных frontend-тестов команды — PASS.
- 29 localization synthetic tests — PASS, включая global optimum и permutations.
- 5 model_improvement synthetic tests — PASS: determinism, train-only reference,
  authorization before file access, budgets/finite inputs и complete prepared runner
  на синтетическом cache с недоступным test feature/label.
- 3 model_quality tests — PASS: FP/FN tradeoff, paired uncertainty, invalid input.
- 5 rule_evidence tests — PASS, включая Python/JS parity на комбинациях ответов.
- 13 UI Python tests — PASS: allowlist, traversal, auth/origin, sizes, timeouts,
  Range, document hashes, schema/JPEG и synthetic MOV → bridge → отдельный FastAPI.
- 9 UI demo JS tests, 21 adapter/session JS tests, 3 checklist JS tests — PASS.
- JS syntax, Python AST, secret-pattern и staged-path/type/size audits — выполнены
  перед commit. Фактический commit SHA и push status сообщаются после Git-операций,
  не вписываются самоссылкой в этот файл.

Итого: 164 Python + 39 JavaScript tests, все только synthetic/technical. Командный
frontend runner также запускался через `node --test` (один test file, шесть тестов).
Новый technical E2E создаёт однотонный MOV FFmpeg, изолированные SQLite/jobs и случайный
ключ в temporary directory, отдельные loopback HTTP-порты, deterministic analyzer
stub, реальные JPEG, оригинальные PDF/Passport и synthetic manual annotation.
Ни localhost:8000 production, ни private keys/данные/модели в нём не используются.
Preview 409 намеренно проверяет, что completed result не теряется.

Полноценная browser/visual automation недоступна. Остались ручные desktop/mobile,
keyboard/dialog/focus и память на целевых устройствах. Unit/HTTP тесты не доказывают
удобство в реальном браузере или статистическую состоятельность модели.

Повторение исследовательских тестов:

```bash
for suite in event_localization model_improvement model_quality rule_evidence ui-candidate; do
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. .venv/bin/python -m unittest \
    discover -s "research/$suite" -p 'test_*.py' -v || exit 1
done
node research/ui-candidate/test_demo.mjs
node research/ui-candidate/test_api.mjs
node research/ui-candidate/test_rule_checklist.mjs
```

## Передача следующему участнику

1. Рассмотреть feature branch без замены `web/` или `src/cashframe/`; каждый модуль
   можно обсуждать отдельно. Никакого автоматического merge/main rollout.
2. Провести browser review и отдельно согласовать production integration.
3. Уточнить новый разрешённый контур данных. Текущее запрещение real-data processing
   сохраняется до нового явного разрешения. Не запускать CLI authorization flag
   по одному факту наличия архива.
4. После разрешения проверить и подготовить источники штатными инструментами,
   сохранить исходное source-group split и provenance; read-only metadata/cache
   checks описаны в model_improvement README. Автоподготовки архива здесь нет.
5. Заморозить один эксперимент до просмотра новой независимой выборки и привлекать
   независимую человеческую разметку/другие камеры. Не путать видео-детекцию,
   event localization, тип, evidence timing, visual relevance и применимость нормы.
6. Сохранить модели V1/V2 отдельно. Candidate NPZ этого исследования не совместим
   с production loader автоматически и не должен копироваться в experiment-v2.
7. Все bank data, weights, source IDs, feature caches, private annotations и ключи
   только в ignored local storage. В Git входят только reviewed research code/docs
   и явно синтетические fixtures; `.vscode/` не входит в commit.

## Состав вклада

Полный состав: `event_localization/` (4 файла), `ui-candidate/` (19 файлов),
`model_improvement/` (3 файла), `model_quality/` (3 файла), `rule_evidence/` (3 файла)
и этот HANDOFF — 33 файла. Commit включает ранее созданные независимые модули;
оригинальные tracked team files не входят. Секрет из IDE не использовался и не
копировался в исходники, fixtures, команды или Git. Нет real-data metrics/artifacts.
