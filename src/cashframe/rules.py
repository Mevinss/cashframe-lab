from urllib.parse import urlsplit


def is_verified_rule(rule):
    """Check source metadata completeness, not applicability to an event."""
    if not isinstance(rule, dict) or rule.get('verified') is not True:
        return False
    for key in ('id', 'title', 'document', 'applicability', 'url'):
        if not isinstance(rule.get(key), str) or not rule[key].strip():
            return False
    cues = rule.get('visual_cues')
    if not isinstance(cues, list) or not cues or any(not isinstance(cue, str) or not cue.strip() for cue in cues):
        return False
    try:
        source = urlsplit(rule['url'])
        return source.scheme == 'https' and bool(source.hostname)
    except ValueError:
        return False


def resolve_rule(rule_id, rules):
    for rule in rules:
        if is_verified_rule(rule) and rule.get('id') == rule_id and rule.get('automatic_matching_enabled') is True:
            return rule
    return None


def assign_priority(event, policy):
    if event.get('confirmed'):
        row = policy.get(event.get('type'))
        if row and row.get('priority') in ('high','medium','low') and row.get('reason'):
            return row['priority'], row['reason']
    return 'unspecified', 'Нет подтверждённого типа и применимого основания в политике сортировки команды.'
