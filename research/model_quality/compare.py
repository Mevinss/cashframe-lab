"""Paired binary comparison and uncertainty; reuses team binary_metrics read-only."""
import math
import numpy as np
from cashframe.research import binary_metrics


def metrics(labels, predictions):
    result = binary_metrics(labels, predictions)
    tn, fp = result['confusion_matrix'][0]
    fn, tp = result['confusion_matrix'][1]
    return {**result, 'tn': tn, 'fp': fp, 'fn': fn, 'tp': tp}


def wilson(successes, total, z=1.959963984540054):
    if not total:
        return None
    p = successes / total
    denominator = 1 + z*z/total
    centre = (p + z*z/(2*total)) / denominator
    radius = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return [max(0., centre-radius), min(1., centre+radius)]


def compare(labels, reference, candidate, *, independent=False):
    """No IDs or individual predictions returned. No automated model promotion."""
    base = metrics(labels, reference)
    trial = metrics(labels, candidate)
    y, old, new = map(np.asarray, (labels, reference, candidate))
    fixed = int(((old != y) & (new == y)).sum())
    regressed = int(((old == y) & (new != y)).sum())
    # Exact two-sided paired sign/McNemar binomial test, not an unpaired accuracy test.
    discordant = fixed + regressed
    pvalue = min(1., 2 * sum(math.comb(discordant, i) for i in range(min(fixed, regressed)+1)) / 2**discordant) if discordant else 1.
    return {'reference': base, 'candidate': trial,
            'delta': {key: trial[key]-base[key] for key in ('accuracy','precision','recall','f1','specificity','balanced_accuracy','fp','fn')},
            'paired': {'fixed':fixed,'regressed':regressed,'exact_two_sided_p':pvalue},
            'accuracy_wilson_95': {'reference':wilson(int((old==y).sum()),len(y)), 'candidate':wilson(int((new==y).sum()),len(y))},
            'evaluation_status':'independent_declared_by_operator' if independent else 'exploratory_previously_used_validation',
            'independence_verified':False, 'promotion':False,
            'notice':'Fewer FP can cost additional FN; intervals and paired p-values do not establish deployment fitness.'}
