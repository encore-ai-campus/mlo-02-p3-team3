from datetime import datetime, timedelta, timezone
from math import ceil

from ticket_guard_admin.user_flow.presentation import KST
from ticket_guard_admin.user_flow.contracts import moment

from .reader import COUNTS, FEATURES


def chart(buckets, names):
    top, bottom, spacing = 12, 116, 76
    peak = max((bucket[key] or 0 for bucket in buckets for key in names), default=0)
    step = max(1, ceil(peak / 4))
    ticks = [{'y': bottom - index * 26, 'value': index * step} for index in range(5)]
    slots = []
    for index, bucket in enumerate(buckets):
        x = 47 + index * spacing
        marks = []
        for series, key in enumerate(names):
            value = bucket[key]
            center = x + spacing / 2 + (0 if len(names) == 1 else (series * 2 - 1) * 10)
            if value is not None:
                height = (bottom - top) * value / (step * 4)
                marks.append(dict(key=key, x=center, value=value, height=height, y=bottom - height))
        slots.append(dict(left=x, center=x + spacing / 2, label=moment(bucket['at']).astimezone(KST).strftime('%H:%M'),
                          missing=not marks, marks=marks))
    return dict(ticks=ticks, slots=slots)


def for_display(result, *, now=None):
    now = now or datetime.now(timezone.utc)
    if result is None:
        end = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        start = end - timedelta(hours=1)
        buckets = [dict(at=(start + timedelta(minutes=5 * index)).isoformat(), requests=None, http5xx=None, gateway=None)
                   for index in range(12)]
        result = dict(start=start, end=end, buckets=buckets, version=None, latest=None,
                      published=None, threshold=None, summary=dict.fromkeys(COUNTS), features=[])
    start, end = result['start'].astimezone(KST), result['end'].astimezone(KST)
    values = result['summary']
    features = {row['id']: row for row in result['features']}
    # Missing features are not zero unless the whole window is confirmed empty.
    empty_feature = dict.fromkeys(COUNTS) if values['requests'] != 0 or not all(b['observed'] for b in result['buckets']) else {
        'requests': 0, 'http4xx': 0, 'http5xx': 0, 'measured': 0,
        'slow': 0 if result['threshold'] is not None else None,
    }
    return dict(
        version=result['version'], window=f'{start:%m.%d %H:%M} ~ {end:%m.%d %H:%M} KST',
        last_ingest=moment(result['latest']).astimezone(KST).strftime('%m.%d %H:%M:%S KST') if result['latest'] else None,
        published=result['published'].astimezone(KST).strftime('%m.%d %H:%M:%S KST') if result['published'] else None,
        stale=result['published'] is not None and now - result['published'] > timedelta(minutes=10),
        summary=values, ratio=values['http5xx'] / values['requests'] * 100 if values['requests'] else None,
        threshold=result['threshold'], features=[dict(label=label, counts=features.get(key, empty_feature))
                                                for key, label in FEATURES.items()
                                                if key != 'other' or key in features],
        requests=chart(result['buckets'], ('requests',)), errors=chart(result['buckets'], ('gateway', 'http5xx')),
    )
