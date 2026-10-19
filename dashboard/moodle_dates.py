import semester
"""Read Moodle's own calendar using the existing session, without AI or feed tokens."""
from datetime import date, datetime
import json
import hashlib
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from icalendar import Calendar
from assessments import title_for
from src.config import settings
from src.course_library import authenticated_html, session_from_state, atomic_write
from src.moodle_browser import _resolve_storage_state_path

ZONE = ZoneInfo('America/Halifax')


def parse_calendar(content, courses):
    calendar = Calendar.from_ical(content)
    results, skipped = [], 0
    for entry in calendar.walk('VEVENT'):
        title = str(entry.get('summary', '')).strip()
        if not re.search(r'\b(due|deadline|exam|midterm|quiz|test|assignment|presentation|concept deck)\b', title, re.I):
            skipped += 1
            continue
        categories = entry.get('categories')
        category_text = ' '.join(str(x) for x in getattr(categories, 'cats', []))
        matches = [c for c in courses if semester.term_from_name(category_text)==semester.current()['term'] and re.search(r'\b'+re.escape(c['code']).replace(r'\ ',r'[ -]')+r'(?:-|\b)',category_text,re.I)]
        if len(matches) != 1:
            skipped += 1
            continue
        start = getattr(entry.get('dtstart'), 'dt', None)
        uid = str(entry.get('uid', '')).strip()
        if not isinstance(start, date) or not uid:
            skipped += 1
            continue
        uncertain = bool(entry.get('rrule') or entry.get('recurrence-id'))
        if isinstance(start, datetime):
            uncertain |= start.tzinfo is None
            local = start.astimezone(ZONE) if start.tzinfo else start
        else:
            local = start
        course = matches[0]
        normalized = title_for(title)
        result = {'id': 'moodle-'+hashlib.sha256(uid.encode()).hexdigest()[:20], 'source_uid': uid, 'course_id': course['id'], 'course': course['name'],
                  'title': normalized if normalized != 'Assessment date to check' else title,
                  'display_title': title, 'date': local.date().isoformat() if isinstance(local, datetime) else local.isoformat(),
                  'kind': 'Assessment', 'review': uncertain, 'tentative': False,
                  'cancelled': str(entry.get('status', '')).upper() == 'CANCELLED',
                  'source_type': 'moodle_calendar', 'submission_status': 'Unknown',
                  'time_note': 'Exact time from Moodle calendar: '+local.isoformat() if isinstance(local, datetime) and local.tzinfo else 'Date from Moodle calendar; exact time unverified.',
                  'sources': [{'name': 'Moodle calendar', 'file_url': course.get('url', ''),
                  'url': course.get('url', ''), 'evidence': title+' — '+start.isoformat()}]}
        if isinstance(start, datetime) and start.tzinfo:
            result['start_at'] = local.isoformat()
            end = getattr(entry.get('dtend'), 'dt', None)
            if isinstance(end, datetime) and end.tzinfo and end > start:
                result['end_at'] = end.astimezone(ZONE).isoformat()
        results.append(result)
    return {'events': results, 'ignored_non_assessment_or_other_course': skipped}


def refresh(path, courses):
    """Export through the normal authenticated form. Never persist its secret URL."""
    session = session_from_state(json.loads(_resolve_storage_state_path().read_text()), settings.moodle_home_url)
    origin = urlsplit(settings.moodle_home_url)
    def allowed(url):
        p = urlsplit(url)
        return p.scheme == 'https' and p.netloc == origin.netloc and p.path in ('/calendar/export.php', '/calendar/export_execute.php')
    try:
        url = settings.moodle_calendar_export_url
        if not allowed(url):
            raise ValueError('Unexpected Moodle calendar address')
        response = session.get(url, timeout=25, allow_redirects=False)
        response.raise_for_status()
        if not authenticated_html(response.text, response.url, settings.moodle_home_url):
            raise ValueError('Moodle sign-in required')
        soup = BeautifulSoup(response.text, 'html.parser')
        form = next((f for f in soup.select('form') if f.select_one('input[name="_qf__core_calendar_export_form"]')), None)
        if form is None:
            raise ValueError('Calendar export form unavailable')
        values = {i['name']: i.get('value', '') for i in form.select('input[type=hidden][name]')}
        values.update({'events[exportevents]': 'courses', 'period[timeperiod]': 'custom', 'export': 'Export'})
        action = urljoin(url, form.get('action') or url)
        if not allowed(action):
            raise ValueError('Unexpected calendar form target')
        response = session.post(action, data=values, timeout=25, allow_redirects=False)
        for _ in range(3):
            if response.status_code not in (301, 302, 303):
                break
            target = urljoin(response.url, response.headers.get('Location', ''))
            if not allowed(target):
                raise ValueError('Calendar sign-in required')
            response = session.get(target, timeout=25, allow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 5*1024*1024 or not response.content.lstrip().startswith(b'BEGIN:VCALENDAR'):
            raise ValueError('Calendar export unavailable')
        data = parse_calendar(response.content, courses)
        data.update(status='complete', checked_at=datetime.now(ZONE).isoformat())
        atomic_write(Path(path), json.dumps(data).encode())
        return data
    except Exception:
        # An old export is not a fresh observation. Retain prior events for history only.
        old = json.loads(Path(path).read_text()) if Path(path).exists() else {'events': []}
        old.update(status='error', message='Moodle calendar could not refresh. Click Update Moodle to reconnect and retry.')
        atomic_write(Path(path), json.dumps(old).encode())
        return old
    finally:
        session.close()


def merge(document_feed, snapshot):
    result = dict(document_feed)
    if snapshot.get('status') != 'complete':
        return result
    direct = [dict(e) for e in snapshot.get('events', [])]
    docs = document_feed['events']
    def key(e):
        title=e['title'].casefold()
        return (str(e['course_id']), title, e.get('date') if title in ('in-class test','quiz') else None)
    direct_keys = {key(e) for e in direct}
    confirmed = {key(e) for e in docs if e.get('confirmed_by_user')}
    confirmed_uids = {e['source_uid'] for e in docs if e.get('confirmed_by_user') and e.get('source_uid')}
    practice = {key(e) for e in docs if e.get('kind') == 'Practice'}
    practice_courses = {str(e['course_id']) for e in docs if e.get('kind') == 'Practice' and e.get('submission_status') == 'Not submitted by course policy'}
    for event in direct:
        if key(event) in practice or (str(event['course_id']) in practice_courses and re.match(r'^Assignment\b', event['title'], re.I)):
            event['kind'] = 'Practice'
            event['submission_status'] = 'Not submitted by course policy'
    # Explicit Moodle event identities supersede text matching; user confirmations
    # remain authoritative. Keep both sources attached for auditability.
    for event in direct:
        for doc in docs:
            if key(doc) == key(event):
                event['sources'] += doc.get('sources', [])
    result['events'] = [e for e in docs if key(e) not in direct_keys or key(e) in confirmed]
    result['events'] += [e for e in direct if key(e) not in confirmed and e.get('source_uid') not in confirmed_uids]
    result['events'].sort(key=lambda e:(e.get('date') or '9999', e.get('course', ''), e['title']))
    result['calendar_checked_at'] = snapshot.get('checked_at')
    return result
