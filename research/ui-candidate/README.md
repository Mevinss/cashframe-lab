# Альтернативный UI: Демо + локальный FastAPI

Изолированный HTML/CSS/vanilla JS интерфейс. Все изменения находятся только в
`research/ui-candidate/`; оригинальные frontend/backend/модели не изменены.
Нет внешних ресурсов, CDN, аналитики или внешних AI-сервисов.

## Запуск и доступ

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python research/ui-candidate/preview.py
```

UI: `http://127.0.0.1:8001`. Мост всегда направлен только на
`127.0.0.1:8000`; адрес нельзя переопределить запросом, URL или аргументом запуска.
Backend надо запускать отдельно штатным способом. Preview не запускает,
не останавливает и не перенастраивает backend. Остановка только preview — Ctrl+C.
В Codespaces: **Ports → Forward a Port → 8001 → Port Visibility → Private →
Open in Browser**. Порт 8000 также должен оставаться Private; публичный доступ
не требуется. Не запускайте unrestricted server из корня репозитория.

## Два режима

**Демо** включено по умолчанию. Все данные синтетические и явно помечены.
Работают сценарии positive/negative/pending/failed/unresolved/empty, события,
учебная шкала, макеты evidence и mock-разметка в RAM. API-запросов нет, ключ
не нужен; документы недоступны. Выбор MOV не читает его содержимое.

**Реальный backend** включается только явным выбором режима в шапке. Здесь нет
демо-событий, SVG вместо evidence или подмены ошибок синтетическим результатом.
Ключ вводится вручную в password-поле и переносится только в память вкладки;
поле очищается после принятия значения. Нет localStorage/sessionStorage/IndexedDB,
ключа в URL, логах, исходниках или файлах. Он передаётся только заголовком
`X-API-Key`. Отключение, смена режима или перезагрузка очищает клиентские данные,
отменяет ожидание и освобождает Blob URLs. Отмена HTTP не отменяет задание backend.

Кнопка «Проверить готовность» явно выполняет GET readiness. Этот endpoint
в существующем backend может запускать **пробное вычисление модели** и писать
runtime-лог. Он не вызывается автоматически при открытии страницы или выборе
реального режима. В тестах используется только mock. Readiness не проверяет
пользовательский ключ: его проверяют защищённые endpoints.

Для разрешённого MOV (до 100 МиБ) нажмите «Отправить MOV и начать анализ».
Сначала проверяется readiness; при 503 файл **не отправляется**. Общий `ready:false`
не сообщает точную причину: это могут быть отсутствующие артефакты, FFmpeg или
серверный ключ. UI не скачивает модели и не создаёт артефакты. Артефакты encoder/experiment/experiment-v2 впоследствии были приватно установлены;
штатная readiness V1/V2 прошла в предыдущем этапе. Текущий UI не подменяет фактическую
готовность историческим результатом: readiness проверяется по явному действию.

## Реальные контракты

Адаптер `api.mjs` сверялся read-only с `api.py`, `schemas.py`, `annotations.py`,
`config.py`, `worker.py`, `events.py`, `web/app.js`. Синтетический fixture проверяется
настоящим `CompletedResult`, а synthetic annotation — `validate_annotation`.
Тесты не создают FastAPI app, не открывают модель и не вызывают его startup/cleanup.

| Метод адаптера | Endpoint backend |
| --- | --- |
| checkReadiness | GET `/health/ready`, 200/503 |
| analyzeVideo | POST `/api/v1/analyze`, multipart `file`, ответ `{job_id}` |
| getJob | GET `/api/v1/jobs/{id}`, processing/done/failed |
| getReview | GET `/api/v1/jobs/{id}/review`, stage + review metadata |
| getRules | GET `/api/v1/rules` |
| getAnnotations / saveAnnotation | GET / POST `/api/v1/jobs/{id}/annotations` |
| getVideoPreview | GET `/api/v1/jobs/{id}/video` |
| downloadReport | GET `/api/v1/jobs/{id}/report.pdf` |
| downloadPassport | GET `/api/v1/jobs/{id}/passport` |

Browser обращается только по относительным `/bridge/...`. Защищённые методы
требуют введённый ключ. Задания используют настоящие UUID hex (32 символа).
Completed schema проверяется строго: job_id, status, verdict, violations;
violation: type/timecode_sec/description/evidence_frames;
evidence: timestamp_sec/image_base64. Negative не создаёт событий.
HTTP 401/404/408/409/413/415/422/429/503/5xx отображаются безопасными сообщениями,
не полным backend detail/traceback. Автоматических повторов POST нет; конкурентный
одинаковый POST блокируется. При неопределённом исходе annotation POST сначала
обновите серверную разметку перед повторным ручным сохранением.

Polling повторяет только GET статуса при временных сбоях; окно ожидания 6 минут.
Можно остановить ожидание и продолжить GET того же job_id без повторной загрузки.
Смена режима инвалидирует даже поздние ответы. Метаданные/нормы/разметка опциональны:
их ошибка видима, успешно полученный результат не теряется. Превью загружается
отдельной кнопкой; 409/codec/network failure не заменяют completed result ошибкой.

Review сопоставляется с violation только при единственном точном совпадении
`type + timecode_sec + description` с обеих сторон, **не по позиции массива**.
Если соответствие неоднозначно, review отсутствует или localization_status не
`model_proposal`, локализация остаётся неопределённой, интервал не рисуется.
Границы проверяются относительно длительности. Unresolved timestamp 0 — контекст,
не момент действия. Даже localized — модельный кандидат, не human confirmation.
Оценка модели показывается как сырое число, не процент/вероятность.

JPEG декодируются из реального API; проверяются base64 и JPEG envelope, браузер
проверяет декодирование изображения. Error state не подменяется картинкой демо.
Original видео-превью получается аутентифицированным fetch и воспроизводится через
Blob URL. В браузере тело превью ограниченно буферизуется (максимум 128 МиБ),
поэтому для больших превью есть расход RAM; мост при этом передаёт потоково.

## Человеческая проверка и документы

Форма отправляет строго поля: `start_sec`, `end_sec`, `reviewer`, `observed_fact`,
`type`, `visibility`, `rule_id`, `rule_status`, `rule_applicability_confirmed`,
`confirmed`, `origin="human"`. Не добавляются synthetic/score/server metadata.
Нет fake reviewer или предзаполненного наблюдаемого факта. Пустой/неверный интервал
не подставляется из unresolved. Backend остаётся окончательным валидатором типа,
реестра и подтверждений. Каждый POST — только отдельный submit формы.
Черновик, human-confirmed observation и подтверждение применимости нормы
показываются раздельно; это не автоматический официальный вывод о нарушении.
Отсутствие review duration блокирует отправку разметки.

PDF/Passport кнопки активны только после done; backend может вернуть 404/409.
Используются реальные bytes; документы не генерируются фронтендом. При наличии
X-Report-SHA256 / X-Passport-SHA256 соответствующая SHA-256 проверяется через
WebCrypto, несовпадение блокирует скачивание; хеш показывается пользователю.
Без WebCrypto документ с hash header не выдаётся как проверенный. Имя скачивания
генерируется безопасно, Content-Disposition и оба hash header сохранены адаптером.

## Защита моста

- Static allowlist: только index/styles/app/demo/api/real/real-view/scene. Нет листинга,
  symlinks, доступа к README, исходникам сервера, тестам, `.env`, `local/` или root repo.
- API allowlist: только перечисленные endpoint/method пары, HEAD только для video.
  Нет query strings, encoded paths, arbitrary destination host/port, redirects или CORS.
  Браузер сохраняет штатную same-origin аутентификацию приватного Codespaces URL;
  его cookies не пересылаются мостом в FastAPI.
- Проверяются Host (localhost либо hostname текущего Codespace), Origin,
  Sec-Fetch-Site и обязательный `X-Cashframe-Client`. Cross-origin preflight не разрешён.
  CSP допускает connect только same-origin, media только self/blob; внешние ресурсы запрещены.
- Для protected endpoint обязателен единственный непустой ASCII X-API-Key.
- Upload: до 101 МиБ с multipart overhead, JSON annotation до 16 КиБ.
  Content-Length обязателен для POST; chunked upload и двойные lengths запрещены.
- Запросы/ответы копируются блоками до 64 КиБ без proxy-файлов. Не более четырёх
  активных upstream-запросов; JSON/document responses до 128 МиБ, video до 512 МиБ.
- Socket idle limits: client 30 с, upstream 70 с; общий streaming deadline 280 с
  проверяется между блоками. На клиенте upload 275 с, readiness 70 с, документы/видео
  60 с, остальные запросы 20 с. После отправки headers stream failure закрывает соединение.
- Передаются status/content type/content length, Range/If-Range и Content-Range,
  Accept-Ranges, ETag, Last-Modified, Content-Disposition, integrity headers.
- Backend ошибки сохраняют статус, но body заменяется безопасным сообщением;
  нет логирования key/body/path/query/evidence. Ответы no-store.

Мост не является production reverse proxy: доступ ограничивается приватным
Codespaces port и локальным пользователем. Он не делает проверку прав на датасет,
не анонимизирует реальные данные и не меняет backend retention/cleanup.
Реальная обработка подчиняется существующим правилам backend. До интеграции нужен
ручной browser review (desktop/mobile/keyboard), авторизованный end-to-end smoke
после появления моделей и проверка политики хранения. Сейчас реальная обработка
и запись аннотаций **не выполнялись**.

## Синтетические проверки

```bash
node --check research/ui-candidate/app.mjs
node --check research/ui-candidate/api.mjs
node --check research/ui-candidate/real.mjs
node --check research/ui-candidate/real-view.mjs
node research/ui-candidate/test_demo.mjs
node research/ui-candidate/test_api.mjs
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover \
  -s research/ui-candidate -p 'test_*.py' -v
```

HTTP-тесты используют два временных loopback-сервера на случайных портах.
Только test subclass меняет upstream на mock; production порт 8000 не вызывается.
Все upload/annotation/document/evidence payloads и credential fixtures синтетические.
`synthetic_fixture.json` содержит настоящий JPEG 2×2, созданный из однотонного цвета,
и не раздаётся статическим сервером. Нет модели, inference, реального видео,
настоящей разметки или обращения к внешней сети. Браузерная визуальная проверка
не заменяется этими тестами.


## Дополнение независимого ревью

Добавлен отдельный фактический Rule-to-Evidence checklist для трёх записей реестра
команды. Это локальная проверка достаточности человеческих ответов, не backend
подтверждение и не юридический вывод. Unknown/not_visible не считаются выполненными.
Публичный каталог один: `rule-catalog.json`; его используют JS и Python-движок.
Каждая смена режима очищает ответы. Никаких автоматических annotation POST.

Проверка памяти: бинарные ответы собираются прямо в Blob без дополнительной
конкатенации Uint8Array; браузерный preview ограничен 128 МиБ (мост 512 МиБ).
Больший preview отклоняется без потери результата. Проверяются объявленная длина,
усечение ответа и отмена чтения при превышении лимита. Защита не заменяет полный
браузерный аудит и проверку на целевых устройствах.

При выборе другого реального события несохранённая human-review форма очищается;
старые reviewer/fact/confirmation никогда не переносятся автоматически. Сохранённые
серверные аннотации остаются доступными в отдельном списке.
