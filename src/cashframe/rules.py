def resolve_rule(rule_id, rules):
    for rule in rules:
        if rule.get('id') == rule_id and rule.get('verified') and rule.get('automatic_matching_enabled') and rule.get('applicability') and rule.get('visual_cues'):
            return rule
    return None


def assign_priority(event, policy):
    if event.get('confirmed'):
        row = policy.get(event.get('type'))
        if row and row.get('priority') in ('high','medium','low') and row.get('reason'):
            return row['priority'], row['reason']
    return 'unspecified', 'Нет подтверждённого типа и применимого основания в политике сортировки команды.'
