from datetime import datetime, timezone
import math
from .event_labels import is_specific_type


def validate_annotation(item, duration_sec):
    allowed = {'start_sec', 'end_sec', 'visibility', 'rule_status', 'rule_applicability_confirmed',
               'confirmed', 'origin', 'reviewer', 'observed_fact', 'type', 'rule_id'}
    server_fields = {'job_id', 'source_sha256', 'source_partition', 'training_eligible', 'saved_at'}
    if not isinstance(item, dict) or set(item) - allowed - server_fields:
        raise ValueError('Unknown annotation fields')
    item = {key: value for key, value in item.items() if key in allowed}
    if item.get('origin', 'human') not in ('human', 'automatic'):
        raise ValueError('Invalid annotation origin')
    rule_id = item.get('rule_id')
    if rule_id is not None and (not isinstance(rule_id, str) or len(rule_id) > 100):
        raise ValueError('Invalid rule identifier')
    start, end = float(item['start_sec']), float(item['end_sec'])
    visibility=item.get('visibility','unknown')
    rule_status=item.get('rule_status','unresolved')
    applicability=item.get('rule_applicability_confirmed',False)
    if visibility not in ('clear','partial','unusable','unknown') or rule_status not in ('unresolved','proposed','confirmed') or not isinstance(applicability,bool):
        raise ValueError('Invalid visibility, rule status or applicability confirmation')
    if not isinstance(item.get('confirmed',False),bool):
        raise ValueError('Confirmation must be a boolean')
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration_sec):
        raise ValueError('Annotation interval is outside the original video')
    if item.get('confirmed'):
        required = ['reviewer', 'observed_fact', 'type']
        if item.get('origin') != 'human' or any(not isinstance(item.get(k),str) or not item[k].strip() for k in required):
            raise ValueError('Confirmation requires human reviewer, observed fact and type')
        if not is_specific_type(item['type']):
            raise ValueError('Confirmation requires a specific observed action type')
        if not item.get('rule_id') and item.get('rule_status') != 'unresolved':
            raise ValueError('A rule or explicit unresolved status is required')
    if rule_status=='confirmed' and (not item.get('confirmed') or item.get('origin')!='human' or visibility!='clear' or not applicability or not item.get('rule_id')):
        raise ValueError('Rule confirmation requires a human observation, clear visibility and confirmed applicability')
    for key,limit in (('reviewer',100),('type',160),('observed_fact',3000)):
        if key in item and (not isinstance(item[key],str) or len(item[key])>limit):
            raise ValueError('Annotation text is invalid or too long')
    item.update(start_sec=start,end_sec=end,visibility=visibility,rule_status=rule_status,
                rule_applicability_confirmed=applicability,saved_at=datetime.now(timezone.utc).isoformat())
    return item
