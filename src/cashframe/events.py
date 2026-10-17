import numpy as np


def merge_events(times, scores, threshold, duration_sec, gap_sec=1.5):
    active = np.flatnonzero(scores >= threshold)
    if not len(active):
        return []
    groups = [[int(active[0])]]
    for index in active[1:]:
        if times[index] - times[groups[-1][-1]] <= gap_sec:
            groups[-1].append(int(index))
        else:
            groups.append([int(index)])
    events = []
    for indices in groups:
        peak = max(indices, key=lambda i: scores[i])
        events.append({'id': f'event-{len(events)+1}', 'timecode_sec': round(float(times[peak]), 3),
                       'start_sec': max(0., float(times[indices[0]])-1),
                       'end_sec': min(duration_sec, float(times[indices[-1]])+1),
                       'score': float(scores[peak]), 'priority': 'unspecified',
                       'priority_reason': 'Тип события и основание приоритета ещё не подтверждены.',
                       'type': 'Визуальный сигнал — тип не подтверждён',
                       'description': 'Модель обнаружила сходство с размеченными положительными фрагментами. Конкретное действие и возможное несоответствие правилу требуют проверки человеком.',
                       'rule_id': None, 'confirmed': False})
    return events
