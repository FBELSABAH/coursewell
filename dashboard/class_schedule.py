"""Source-checked, explicit semester timetable, expanded into stable dated meetings.

No guesses about office hours, lab sections, or missing end times. Configuration
is private data. Changes to the saved supporting file hold the schedule for review.
"""
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import hashlib
import json


def class_events(config_path, library_root, config=None):
    if config is None:
        path = Path(config_path)
        if not path.exists():
            return []
        config = json.loads(path.read_text())
    start, end = date.fromisoformat(config['start']), date.fromisoformat(config['end'])
    if end < start or (end-start).days > 180:
        raise ValueError('Invalid class term bounds')
    tz = ZoneInfo(config['timezone'])
    excluded = set(config.get('excluded_dates', []))
    events = []
    for row in config['courses']:
        base = (Path(library_root)/('course-'+row['course_id'])).resolve()
        source = (base/row['path']).resolve()
        changed = not source.is_relative_to(base) or not source.is_file()
        if not changed:
            changed = hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']
        manifest_path = base/'manifest.json'
        if manifest_path.exists() and row.get('source_url'):
            current = json.loads(manifest_path.read_text()).get('files', {}).get(row['source_url'])
            changed = changed or not current or current.get('path') != row['path']
        review = changed or not row.get('end_time')
        sources = [{'name': source.name, 'url': row.get('source_url',''),
                    'file_url': '/file?'+urlencode({'course':row['course_id'],'path':row['path']}),
                    'evidence': row['evidence'], 'page': 1},
                   {'name':'University academic calendar','url':config['calendar_source'],
                    'evidence':'Classes '+config['start']+'–'+config['end']+'. Excluded dates: '+', '.join(sorted(excluded))+'. Verified '+config['verified_on']}]
        if row.get('time_confirmed_by_user'):
            sources.append({'name':'Your timetable confirmation','evidence':row['time_confirmed_by_user']})
        day = start
        while day <= end:
            if day.weekday() in row['weekdays'] and day.isoformat() not in excluded:
                begin = datetime.combine(day, time.fromisoformat(row['start_time']), tz)
                finish = datetime.combine(day, time.fromisoformat(row['end_time']), tz) if row.get('end_time') else None
                if finish and finish <= begin:
                    raise ValueError('Class end must follow start')
                uid=f"class:{row['course_id']}:{day.isoformat()}"
                events.append(dict(id=uid,source_uid=uid,course_id=row['course_id'],course=row['code'],
                    title='Lecture',display_title='Lecture',kind='Class',date=day.isoformat(),
                    start_at=begin.isoformat(),end_at=finish.isoformat() if finish else None,
                    location=row['location'],source_type='class_schedule',sources=sources,
                    review=review,source_changed=changed,
                    schedule_note='Source file changed; review timetable.' if changed else 'Class end time needs confirmation.' if not finish else 'Regular lecture slot; check course announcements for individual cancellations or in-class assessments.'))
            day += timedelta(days=1)
    return events
