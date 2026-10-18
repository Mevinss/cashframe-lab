# Исследование уменьшения ложных сигналов

**Реальный эксперимент не выполнен:** пользователь запретил обработку реальных
данных в Codespaces. `local/open.zip`, подготовленный manifest и features также
отсутствовали при проверке наличия. Архив, labels, видео и embeddings не открывались.
V2 остаётся неизменённым референсом; улучшение не доказано.

После read-only анализа `learning.py`, `research.py`, `experiment_v2.py`,
`experiment_v3.py` выбран другой небольшой класс кандидатов: negative-reference
persistence multiple-instance learning. V2/V3 уже пробовали mean/std/quantiles,
linear/RBF ridge, temporal pyramid и dynamics. Здесь negative prototype,
поканальная шкала и cutoff оцениваются **заново внутри каждого training fold**.
Из последовательности расстояний формируются три квантили, максимум скользящего
среднего, доля и максимальная серия превышений. Обучается регуляризованная
логистическая голова уровня видео, DINO остаётся замороженным.

Ровно четыре кандидата: persistence 3/7 выборок × L2 1/10. Это экспериментальные
настройки, не нормативная длительность. Предварительный протокол встроен в
`PROTOCOL` и записывается до чтения подготовленных inputs. Четыре group folds
с seed 42; выбор архитектуры и порога только по train OOF, экспериментальный
recall floor .75. Validation используется один раз после заморозки выбора,
без подбора порога или последующих попыток. Test features/labels не используются;
source IDs берутся только из trusted fixed split. OOF является selection estimate,
не unbiased nested-CV оценкой. Повторно использованная командой validation не
является независимым доказательством. Production promotion всегда выключен.

`run_prepared` отказывается читать данные без явного разрешения. CLI-флаг не
заменяет разрешение владельца данных; **в текущей сессии запуск запрещён**.
Выход только в новый `local/...`: candidate NPZ, protocol, aggregate report и
private provenance (source IDs, feature hashes и OOF scores). Никакой замены V2.
Лимиты: 120 секунд и 1 ГиБ RSS, проверяемые между операциями (не OS hard limits).
Основные риски: маленькая выборка, чувствительность отрицательного эталона к
камере и фону, persistence по числу samples, возможный рост FN при снижении FP.

Будущий разрешённый запуск из root (сейчас не выполнять):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. .venv/bin/python \
  -m research.model_improvement.experiment --authorized-codespaces-data \
  --output local/experiments/negative-reference-new-run
```

Сначала отдельно авторизовать обработку именно в Codespaces, проверить CRC/paths/
symlinks/collision/размеры архива, затем использовать `scripts/prepare_local_data.py`
в новом `local/source/...`, `cashframe.data.audit_dataset` в новом local directory.
Сопоставить sha256 group IDs и split **в точности** с сохранённым исходным split
в model config; при несовпадении остановиться, не строить удобное новое разбиение.
Проверить counts, дубликаты и point bounds локально без вывода имён или описаний.
Вызвать существующие `Encoder`/`extract_dataset` только для train/validation;
готовые признаки повторно использовать только при совпадении encoder metadata,
исходных hashes и extraction configuration. Все детали и кэши хранить в `local/`.
Этот модуль не открывает архивы/видео автоматически и требует уже подготовленный
проверенный cache. Полная автоматизация подготовки пока не реализована.

Синтетические тесты:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. .venv/bin/python -m unittest \
  discover -s research/model_improvement -p 'test_*.py' -v
```

Прохождение toy-тестов подтверждает механику, а не улучшение банковской модели.
