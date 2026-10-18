"""Attach explicit human observations to events, never infer law from a model score."""
from urllib.parse import urlsplit

from .annotations import validate_annotation


def event_compliance(job,rules,annotations):
    registry={r['id']:r for r in rules if r.get('id') and r.get('verified') is True
              and urlsplit(r.get('url','')).scheme=='https' and urlsplit(r.get('url','')).hostname}
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
              'type':event.get('type'),'rule':None,'origin':None,
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
                        rule={k:rule.get(k) for k in ('id','title','document','section','url','applicability','limits','verified_on')} if rule else None,
                        message='Основание подтверждено проверяющим.' if confirmed else 'Предлагаемое основание требует проверки применимости.' if rule else 'Нормативное основание не установлено.')
        result.append(base)
    return result
