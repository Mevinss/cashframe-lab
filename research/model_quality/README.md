# Сопоставление качества моделей

`compare.py` переиспользует `cashframe.research.binary_metrics`, а не заменяет
метрики команды. Добавляет явные FP/FN, парные исправления/регрессии, exact paired
binomial/McNemar p-value и Wilson 95% intervals точности. Выход только aggregate,
без source IDs, индивидуальных predictions, кадров или описаний.

Вход: одинаково упорядоченные бинарные labels, reference predictions и candidate
predictions для одних источников. Не передавать разные samples или дубли одной
записи; source identity аудит выполняется до вызова, например существующим
`audit_partitions`. Пустые/небинарные/несогласованные массивы отклоняются.
Поля accuracy/precision/recall/F1/specificity/balanced_accuracy берутся из
существующего контракта, включая его zero-denominator convention.

Данные validation, которые команда уже смотрела, остаются exploratory. Аргумент
independent — декларация оператора, не проверка происхождения; поле
independence_verified всегда false. Автопродвижения моделей нет.
Один источник не равен независимому camera/scenario. Малый paired p-value не
доказывает эксплуатационную пригодность, а отсутствие значимости — эквивалентность.
Читать снижение FP вместе с возможным ростом FN. Метрики не оценивают типы,
локализацию, visual evidence или юридическую применимость.
