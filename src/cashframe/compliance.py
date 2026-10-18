"""Keep observed facts, candidate references and confirmed applicability separate."""
from urllib.parse import urlsplit

from .annotations import validate_annotation
from .rules import is_verified_rule


_RULE_FIELDS = ('id', 'title', 'document', 'section', 'url', 'applicability', 'limits', 'verified_on')


def _localized(value, *, lines=False):
    """Preserve available catalog translations; missing languages use caller fallback."""
    values = value if isinstance(value, dict) else {'ru': value}
    result = {}
    for language in ('ru', 'kk', 'en'):
        item = values.get(language)
        if lines:
            items = [item] if isinstance(item, str) else item if isinstance(item, list) else []
            valid = [text for text in items if isinstance(text, str) and text.strip()]
            if valid:
                result[language] = valid
        elif isinstance(item, str) and item.strip():
            result[language] = item
    return result


def _candidate_references(type_name, rules, action_catalog, violation_catalog, origin):
    """Exact catalog lookup offers reading context, never asserts visible conditions."""
    if not isinstance(type_name, str) or not type_name.strip():
        return []
    matching = []
    for catalog, kind in ((action_catalog, 'observed_action'), (violation_catalog, 'possible_violation')):
        for category in (catalog or {}).get('classes', []):
            if not isinstance(category, dict) or category.get('id') in (None, '', 'unknown'):
                continue
            labels = category.get('labels', {})
            aliases = [category['id']] + (list(labels.values()) if isinstance(labels, dict) else [])
            if type_name.strip() in aliases:
                matching.append((category, kind))
    # A conflicting or overlapping taxonomy cannot safely choose the intended category.
    if len(matching) != 1:
        return []
    category, kind = matching[0]
    requirements = _localized(category.get('requirements'), lines=True)
    if not requirements:
        return []
    references = []
    for rule in rules:
        if (not is_verified_rule(rule) or rule.get('id') not in category.get('candidate_rule_ids', [])
                or rule.get('obsolete') is True or rule.get('repealed') is True
                or rule.get('active') is False
                or str(rule.get('status', '')).lower() in ('obsolete', 'repealed', 'revoked', 'expired')):
            continue
        # The case limits normative sources to Kazakhstan; a verified foreign URL is not eligible.
        hostname = urlsplit(rule['url']).hostname
        if hostname not in ('adilet.zan.kz', 'nationalbank.kz', 'www.nationalbank.kz'):
            continue
        references.append({'status': 'candidate', 'applicability_status': 'not_confirmed',
                           'type_id': category['id'], 'category_kind': kind, 'origin': origin,
                           'type_labels': _localized(category.get('labels')),
                           'rule': {key: rule.get(key) for key in _RULE_FIELDS},
                           'requirements': requirements,
                           'limitations': _localized(category.get('limits'))})
    return references


def event_compliance(job,rules,annotations,*,action_catalog=None,violation_catalog=None):
    registry={r['id']:r for r in rules if is_verified_rule(r)}
    duration=job.get('duration_sec') or (job.get('review') or {}).get('duration_sec',0)
    events=(job.get('review') or {}).get('events') or [
        dict(e,id=f'event-{i+1}') for i,e in enumerate((job.get('result') or {}).get('violations',[]))]
    observations=[]
    for item in annotations:
        if not job.get('source_sha256') or item.get('source_sha256')!=job['source_sha256']:continue
        try:validate_annotation(dict(item),duration)
        except (ValueError,KeyError,TypeError,OverflowError):continue
        observations.append(item)
    linked={index:[] for index in range(len(events))};ambiguous=set()
    for item in observations:
        matches=[]
        for index,event in enumerate(events):
            start=event.get('start_sec',event.get('timecode_sec',0));end=event.get('end_sec',start)
            if item['start_sec']<=end and item['end_sec']>=start:matches.append(index)
        if len(matches)==1:linked[matches[0]].append(item)
        elif len(matches)>1:ambiguous.update(matches)
    result=[]
    for index,event in enumerate(events):
        matches=linked[index]
        base={'event_id':event.get('id',f'event-{index+1}'),'status':'unresolved','observed_fact':None,
              'type':event.get('type'),'rule':None,'origin':None,'candidate_rules':[],
              'association_status':'ambiguous' if index in ambiguous else 'unmatched',
              'message':'Нормативное основание не установлено.'}
        if matches:
            a=max(matches,key=lambda row:(row.get('rule_status')=='confirmed',row.get('saved_at','')))
            rule=registry.get(a.get('rule_id'))
            confirmed=rule is not None and a.get('rule_status')=='confirmed'
            base.update(status='confirmed' if confirmed else 'proposed' if rule else 'unresolved',observed_fact=a.get('observed_fact'),
                        type=a.get('type'),origin=a.get('origin','human'),reviewer=a.get('reviewer'),
                        observation_status='confirmed' if a.get('confirmed') and a.get('origin')=='human' else 'proposed',
                        association_status='unique',
                        observation_interval={'start_sec':a['start_sec'],'end_sec':a['end_sec']},
                        rule={k:rule.get(k) for k in _RULE_FIELDS} if rule else None,
                        message='Основание подтверждено проверяющим.' if confirmed else 'Предлагаемое основание требует проверки применимости.' if rule else 'Нормативное основание не установлено.')
            if a.get('origin','human') == 'human':
                base['candidate_rules'] = _candidate_references(a.get('type'), rules, action_catalog,
                                                                 violation_catalog, 'human')
        else:
            prediction = event.get('type_prediction')
            if (isinstance(prediction, dict) and prediction.get('origin') == 'automatic'
                    and prediction.get('confirmed') is False and prediction.get('type') == event.get('type')
                    and event.get('localization_status') != 'unresolved'):
                base['candidate_rules'] = _candidate_references(prediction['type'], rules, action_catalog,
                                                                 violation_catalog, 'automatic')
        if base['rule']:
            base['candidate_rules'] = [candidate for candidate in base['candidate_rules']
                                       if candidate['rule']['id'] != base['rule']['id']]
        result.append(base)
    return result
