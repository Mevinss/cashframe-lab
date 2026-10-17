# Rule-to-Evidence Verification Engine

Фактический checklist, не юридическая квалификация. Использует только три ссылки
существующего реестра команды: RK231-20, RK231-22, RK231-19-6, акт НБРК №231.
[Официальная страница](https://adilet.zan.kz/rus/docs/V1900019680) при этой проверке
отдала JS-оболочку: новой независимой проверки актуальности текста не заявлено.
Основания и ограничения перенесены из read-only `config/rules.json` и `docs/rules.md`.
Новых обязательств, правовых категорий и автоматических связей не добавлено.

Один публичный каталог в `research/ui-candidate/rule-catalog.json` используется
Python `engine.py` и браузерным `rule-checklist.mjs`. Вход только rule_id,
origin, human_confirmed, visibility и answers по идентификаторам вопросов.
Нет reviewer, исходных имён, кадров, свободных описаний или ключей.
Ответы satisfied/not_satisfied/unknown/not_visible. Пропущенные ответы — unknown.
Человек должен подтвердить наблюдения и ясную видимость; automatic или false
никогда не дают поддержанного checklist. Отрицательный контекст не подменяет
неизвестность; выход context_not_established требует отдельного рассмотрения.

Выход различает needs_human_confirmation, insufficient_visibility,
context_not_established, insufficient_evidence, factual_discrepancy_requires_review
и factual_checklist_supported. Даже последний означает только заполнение
фактических условий, не подтверждение нарушения/применимости нормы. Поле
legal_conclusion всегда null, automatic_rule_confirmation всегда false.
Отсутствие наблюдаемой передачи документа само по себе не доказывает его отсутствия;
выход из кадра не равен оставлению рабочего места. Эти ограничения видимы в UI.

UI: кнопка «Открыть фактический чек-лист» под рабочей областью. Ответы только в RAM,
очищаются при смене режима; данные не отправляются в API. Human confirmation
в checklist не включает checkbox применимости в реальной annotation форме.
Демо должно заполняться только синтетическими значениями. Движок не читает видео
или real annotations автоматически и не меняет backend.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. .venv/bin/python -m unittest \
  discover -s research/rule_evidence -p 'test_*.py' -v
```
