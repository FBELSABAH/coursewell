"""Resolve explicit in-class assessment references against verified lecture slots.

The slot is a window, not a claim about an exam's exact duration. Never infer a
slot for a date-only assessment, conflicted source, holiday or multiple sections.
"""
from copy import deepcopy


def resolve_class_times(events, lectures):
    result = deepcopy(events)
    for event in result:
        if (not event.get('class_time_reference') or event.get('start_at')
                or any(event.get(k) for k in ('review','tentative','conflict_dates','source_changed','cancelled'))):
            continue
        matches = [x for x in lectures if str(x.get('course_id')) == str(event.get('course_id'))
                   and x.get('date') == event.get('date') and not x.get('review')
                   and not x.get('source_changed') and x.get('start_at') and x.get('end_at')]
        slots = {(x['start_at'], x['end_at'], x.get('location','')) for x in matches}
        if len(slots) != 1:
            event['review'] = True
            event['time_note'] = 'In-class assessment: no unique verified lecture period for this date.'
            continue
        start, end, location = next(iter(slots))
        event.update(start_at=start, end_at=end, location=location,
                     source_type='class_time_assessment', time_precision='class_window',
                     time_note='During the scheduled class period (America/Halifax). This calendar block shows the class window; the exact assessment start and duration are not specified.')
        for source in matches[0].get('sources', []):
            if source not in event['sources']: event['sources'].append(source)
    return result
