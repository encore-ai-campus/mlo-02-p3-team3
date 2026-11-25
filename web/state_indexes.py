"""Readiness validates deployed indexes without DDL in the request path."""
REQUIRED = {
    'purchases': [([('booking', 1)], {'unique': True}), ([('scope', 1)], {})],
    'outbox': [([('created', 1)], {})],
    'browser_guards': [([('expires_at', 1)], {'expireAfterSeconds': 0})],
    'web_sessions': [([('expires_at', 1)], {'expireAfterSeconds': 0})],
    'booking_attempts': [([('value.id', 1)], {'unique': True}),
                         ([('value.stage', 1), ('value.rank', 1)], {}),
                         ([('value.stage', 1), ('value.rank', 1), ('_id', 1)], {})],
    'bookings': [([('value.user', 1), ('value.created_at', -1)], {})],
}
REQUIRED['booking_attempts'].append(([('value.stage',1),('value.heartbeat',1)],{}))

def check_ready(db):
    missing=[]
    names=set(db.list_collection_names())
    for name in ('accounts','account_events','booking_inventory','booking_runtime','booking_opens',
                 'booking_scenarios','state','adjustments','transaction_guard'):
        if name not in names:missing.append(name+': collection missing')
    if 'transaction_guard' in names and not db.transaction_guard.find_one({'_id':'outbox_dispatch'}):
        missing.append('outbox dispatcher lease not initialized')
    for name, specs in REQUIRED.items():
        indexes=list(db[name].list_indexes()) if name in names else []
        for keys,options in specs:
            expected={'unique':False, 'expireAfterSeconds':None, **options}
            if not any(list(i['key'].items())==keys and all(i.get(k,False if k=='unique' else None)==v for k,v in expected.items()) for i in indexes):
                missing.append(f'{name}: {keys} {options}')
    return missing
