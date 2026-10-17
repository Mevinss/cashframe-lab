import numpy as np
from .numerics import finite_array, finite_scalar


def merge_events(times, scores, threshold, duration_sec, gap_sec=1.5):
    times, scores = finite_array(times, 'event times', 1), finite_array(scores, 'event scores', 1)
    threshold = finite_scalar(threshold, 'event threshold')
    if times.shape != scores.shape:raise ValueError('Event timestamps and scores do not match')
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


def propose_events(times, scores, threshold, duration_sec, positive):
    if not positive:return []
    events = merge_events(times, scores, threshold, duration_sec)
    coverage = sum(event['end_sec'] - event['start_sec'] for event in events) / duration_sec
    if not events or np.ptp(scores) <= 1e-6 or coverage >= .8 or len(events)>12:
        # A whole-video decision cannot supply an action time. The contract still
        # requires evidence, so timestamp zero explicitly identifies context only.
        return [{'id':'event-1', 'timecode_sec':0., 'start_sec':0., 'end_sec':duration_sec,
                 'score':float(np.max(scores)), 'priority':'unspecified',
                 'priority_reason':'Локализация и тип действия не подтверждены.',
                 'type':'Сигнал уровня видео — локализация не подтверждена',
                 'description':'Классификатор пометил видео для проверки. Временной ряд не позволяет выделить надёжный интервал действия. Отметка 0 секунд и кадры показывают контекст видео, а не установленное нарушение.',
                 'localization_status':'unresolved', 'proposed_interval_count':len(events),
                 'localization_reason':'fragmented_signal' if len(events)>12 else 'uninformative_signal',
                 'rule_id':None, 'confirmed':False}]
    for event in events:event['localization_status'] = 'model_proposal'
    return events
