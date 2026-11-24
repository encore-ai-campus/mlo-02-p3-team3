"""Validate server-owned policy decisions, without recomputing missing lifetime state."""
import math

VERSION = 'prevention-2026-11-23-v1'
RULES = {
    'login_repeated_failure': (None, 5), 'not_open_repeat': (2, 10),
    'reentry_repeat': (2, 10), 'selection_repeat': (5, 10),
    'purchase_limit_attempt': (None, 4),
}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_policy(kind, data, row):
    if not all(key in data for key in ('ruleId','ruleVersion','policyMode','threshold','windowSeconds','restrictionScope')):
        raise ValueError('Incomplete server prevention policy')
    rule = data.get('ruleId')
    if rule not in RULES or data.get('ruleVersion') != VERSION or data.get('policyMode') != 'temporary_restriction':
        raise ValueError('Unknown server prevention policy')
    seconds, threshold = RULES[rule]
    if data.get('threshold') != threshold or data.get('windowSeconds') != seconds:
        raise ValueError('Server prevention threshold mismatch')
    if not row['user'] or not row['request']:
        raise ValueError('Unidentified server decision')
    expected = 'account' if rule == 'login_repeated_failure' else 'account_show_schedule'
    if data.get('restrictionScope') != expected or expected != 'account' and not all(row[k] for k in ('show', 'schedule')):
        raise ValueError('Unidentified server policy scope')
    if rule == 'selection_repeat' and kind in ('prevention_evaluated', 'prevention_detected', 'prevention_applied') and not row['attempt']:
        raise ValueError('Missing selection attempt')
    if kind in ('prevention_detected', 'prevention_applied', 'prevention_rejected', 'prevention_released'):
        if not isinstance(data.get('restrictionId'), str) or not data['restrictionId']:
            raise ValueError('Missing restriction identity')
    if kind in ('prevention_detected', 'prevention_applied'):
        observed = data.get('observed')
        if type(observed) is not int or observed < threshold or rule == 'purchase_limit_attempt' and observed <= threshold:
            raise ValueError('Nonqualifying prevention decision')
        if rule == 'purchase_limit_attempt' and observed != data.get('purchasedQuantity', -100) + data.get('requestedQuantity', -100):
            raise ValueError('Quota evidence mismatch')
    if kind == 'prevention_evaluated' and not (
            type(data.get('observed')) is int and data['observed'] >= 0
            or data.get('observed') is None and data.get('excluded') is True):
        raise ValueError('Invalid policy evaluation')
    if kind in ('prevention_applied', 'prevention_rejected', 'prevention_released'):
        if not number(data.get('appliedAt')) or not number(data.get('expiresAt')) or data['expiresAt']-data['appliedAt'] != 300:
            raise ValueError('Invalid restriction duration')
    if kind == 'prevention_released' and (not number(data.get('releasedAt')) or data.get('releaseReason') not in ('opened', 'expired')):
        raise ValueError('Invalid release evidence')
    if kind == 'prevention_released' and data['releaseReason']=='opened' and rule!='not_open_repeat':
        raise ValueError('Opening cannot release another restriction rule')
    if kind == 'prevention_applied' and abs(data['appliedAt']-row['time'])>0.000001:
        raise ValueError('Restriction application time mismatch')
    if kind == 'prevention_released' and (abs(data['releasedAt']-row['time'])>0.000001 or
            data['releaseReason']=='expired' and data['releasedAt']<data['expiresAt']):
        raise ValueError('Restriction release time mismatch')


def decisions(rows):
    """One identity per rule/restriction/kind (rejects additionally per request)."""
    result = {}
    for row in rows:
        if not row['kind'].startswith('prevention_'):
            continue
        data = row.get('policy')
        if not data:
            raise ValueError('Missing server policy evidence')
        if row['kind'] == 'prevention_evaluated':
            continue
        key = (row['kind'], data['ruleId'], data['restrictionId'],
               row['request'] if row['kind'] == 'prevention_rejected' else None)
        prior = result.get(key)
        if prior and any(prior[k] != row[k] for k in ('time', 'user', 'attempt', 'show', 'schedule', 'simulation', 'policy', 'request')):
            raise ValueError('Conflicting restriction identity')
        result[key] = row
    values = sorted(result.values(), key=lambda row: (row['time'], row['id']))
    detections = {row['policy']['restrictionId']: row for row in values if row['kind']=='prevention_detected'}
    anchors = {row['policy']['restrictionId']: row for row in values if row['kind']=='prevention_applied'}
    anchors.update(detections)
    for row in values:
        data = row['policy']
        anchor = anchors.get(data['restrictionId'])
        if anchor and (any(row[k] != anchor[k] for k in ('user','show','schedule','simulation'))
                       or data['ruleId'] != anchor['policy']['ruleId']):
            raise ValueError('Restriction target scope mismatch')
        detected = detections.get(data['restrictionId'])
        if detected and row['kind'] == 'prevention_applied':
            if any(row[k] != detected[k] for k in ('user','attempt','show','schedule','simulation','request')) or any(
                    data[k] != detected['policy'][k] for k in ('ruleId','ruleVersion','observed','threshold')) or detected['id'] not in data.get('evidence',[]):
                raise ValueError('Restriction application evidence mismatch')
    return values


def metrics(history, now):
    from .events import scope
    values = decisions(history)
    visible = [row for row in history if now-3600 <= row['time'] <= now]
    output = {}
    for rule, (seconds, threshold) in RULES.items():
        evaluated = any(row.get('policy', {}).get('ruleId') == rule for row in visible)
        hits = [row for row in values if row['kind'] == 'prevention_detected' and row['policy']['ruleId'] == rule and now-3600 <= row['time'] <= now]
        applied = {row['policy']['restrictionId']: row for row in values if row['kind'] == 'prevention_applied' and row['policy']['ruleId'] == rule}
        releases = {row['policy']['restrictionId']: row for row in values if row['kind'] == 'prevention_released' and row['policy']['ruleId'] == rule}
        rejected = [row for row in values if row['kind'] == 'prevention_rejected' and row['policy']['ruleId'] == rule and now-3600 <= row['time'] <= now]
        hit_requests = {row['request'] for row in hits}
        hit_ids = {row['policy']['restrictionId'] for row in hits}
        pending = any(row['kind']=='prevention_evaluated' and row.get('policy',{}).get('ruleId')==rule
            and type(row['policy'].get('observed')) is int
            and (row['policy']['observed']>threshold if rule=='purchase_limit_attempt' else row['policy']['observed']>=threshold)
            and row['request'] not in hit_requests for row in visible)
        pending = pending or any(now-3600 <= row['time'] <= now and identity not in hit_ids for identity,row in applied.items())
        cases = []
        for row in hits[-200:]:
            data = row['policy']
            identity = data['restrictionId']
            action = applied.get(identity)
            release = releases.get(identity)
            # A server event missing its independently emitted applied evidence stays unknown.
            status = ('released' if release else 'expired' if action and action['policy']['expiresAt'] <= now else 'active') if action else 'unconfirmed'
            cases.append(dict(eventId=row['id'], at=row['time'], user=row['user'], attempt=row['attempt'],
                reason=rule, evidence=[row['id']] + ([action['id']] if action else []) + ([release['id']] if release else []),
                scope=scope(row),
                ruleVersion=VERSION, observed=data['observed'], threshold=threshold, windowSeconds=seconds,
                restrictionId=identity, actionStatus=status, appliedAt=action['policy']['appliedAt'] if action else None,
                expiresAt=action['policy']['expiresAt'] if action else None,
                releasedAt=release['policy']['releasedAt'] if release else None,
                releaseReason=release['policy']['releaseReason'] if release else None))
        linked = all(row['policy']['restrictionId'] in applied for row in hits)
        actions_known = evaluated and linked and not pending
        output[rule] = dict(count=len(hits) if evaluated and not pending else None, eventCount=len(hits) if evaluated and not pending else None,
            actionCount=sum(now-3600 <= row['time'] <= now for row in applied.values()) if actions_known else None,
            rejectedCount=len(rejected) if evaluated and not pending else None,
            actionSubjects=len({row['user'] for row in applied.values() if now-3600 <= row['time'] <= now}) if actions_known else None,
            unknownTargets=0, truncated=len(hits)>200, cases=cases,
            pendingEvidence=pending,
            ruleVersion=VERSION, threshold=threshold, windowSeconds=seconds, mode='temporary_restriction')
    # Login limit reached represents applications, not every request refused while locked.
    login = dict(output['login_repeated_failure'])
    count = login['actionCount']
    login.update(count=count, eventCount=count, cases=[case for case in login['cases'] if case['actionStatus'] != 'unconfirmed'])
    output['login_limit'] = login
    return output
