from datetime import datetime, timezone
import math


def validate_annotation(item, duration_sec):
    item = dict(item)
    start, end = float(item['start_sec']), float(item['end_sec'])
    if not isinstance(item.get('confirmed',False),bool):
        raise ValueError('Confirmation must be a boolean')
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration_sec):
        raise ValueError('Annotation interval is outside the original video')
    if item.get('confirmed'):
        required = ['reviewer', 'observed_fact', 'type']
        if item.get('origin') != 'human' or any(not isinstance(item.get(k),str) or not item[k].strip() for k in required):
            raise ValueError('Confirmation requires human reviewer, observed fact and type')
        if not item.get('rule_id') and item.get('rule_status') != 'unresolved':
            raise ValueError('A rule or explicit unresolved status is required')
    item.update(start_sec=start, end_sec=end, saved_at=datetime.now(timezone.utc).isoformat())
    return item
