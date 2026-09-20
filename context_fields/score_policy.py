"""Session-fixed Dominance display policy. Never used by Evidence or reducers."""
import copy
import hashlib
import json
from pathlib import Path
from .clock import finite

C0 = 'c0-provider-score-v1'
CD = 'cd-distribution-display-v1'


def policy_record(policy_id=C0):
    if policy_id not in (C0, CD):
        raise ValueError('unknown_adoption_policy')
    config = {'adoption_policy_id': policy_id, 'validator_version': 'dominance-score-contract-v1',
              'schema_version': 'dominance-policy-binding-v1', 'relation_tolerance': 1e-6,
              'probability_sum_tolerance': 1e-6,
              'transform_version': 'distribution-score-0-4-to-pct-v1' if policy_id == CD else 'score-0-4-to-pct-v1',
              'usage_scope': 'display_only', 'calculation': 'python-float-ordered-sum-v1'}
    config['config_sha256'] = hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    config['implementation_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return config


def validate_dominance_answer(answer, question, policy_id):
    from .jev import validate_answer, distribution, probability
    if policy_id not in (C0, CD):
        raise ValueError('unknown_adoption_policy')
    if policy_id == C0 or question['type'] != 'score':
        return validate_answer(answer, question)
    if not isinstance(answer,dict) or answer.get('type') != 'score':
        raise ValueError('primitive_type')
    probability(answer['confidence'])
    legend = {str(i): v for i,v in enumerate(question['criteria'])}
    if len(legend) != 5 or answer['legend'] != legend:
        raise ValueError('score_legend')
    probabilities, normalized = distribution(answer['probabilities'], legend)
    score = finite(answer['score'])
    if not 0 <= score <= 4:
        raise ValueError('score_range')
    # Preserve the existing ordered float sum and >1e-6 comparison, including
    # its floating-point boundary. Only the consequence of this relation changes.
    mean = sum(int(k)*v for k,v in probabilities.items())
    total = sum(answer['probabilities'].values())
    delta = score-mean
    mismatch = abs(mean-score) > 1e-6
    diagnostic = {'provider_score':score,'returned_probabilities':copy.deepcopy(answer['probabilities']),
        'legend':copy.deepcopy(answer['legend']),'probability_sum':total,'normalized':normalized,
        'normalization_factor':1/total,'probabilities_used':probabilities,
        'mean_before':sum(int(k)*v for k,v in answer['probabilities'].items()),'mean_used':mean,
        'numeric_delta':delta,'relation_tolerance':1e-6,'relation_comparison':'abs(mean-score)>1e-6 (float)',
        'numeric_discrepancy':mismatch,'selected_source':'returned_distribution','selected_score':mean,
        'numeric_candidate_pct':mean*25,'usage_scope':'display_only',
        'warning_codes':['NUMERIC_DISCREPANCY'] if mismatch else []}
    return {'score':score,'probabilities':probabilities,'normalized':normalized,'score_derivation':diagnostic}
