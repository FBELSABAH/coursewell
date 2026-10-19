import semester
"""App-owned Google OAuth and conservative, click-triggered Calendar sync."""
import base64
import fcntl
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlencode, quote
from zoneinfo import ZoneInfo
import requests

SCOPE = 'https://www.googleapis.com/auth/calendar.app.created'
CALENDAR_NAME = 'Coursewell · Classes'
CALENDAR_NAMES = {'classes': CALENDAR_NAME, 'assessments': 'Coursewell · Assessments', 'events': 'Coursewell · Events'}
EVENT_COLORS = {'class': '8', 'exam': '11', 'quiz': '5', 'assignment': '9', 'assessment': '3', 'event': '10'}


def event_category(event):
    if event.get('kind') == 'Event':
        return 'event'
    if event.get('source_type') == 'class_schedule' or event.get('kind') == 'Class':
        return 'class'
    title = event.get('title', event.get('summary', '')).casefold()
    if re.search(r'\bquiz(?:zes)?\b', title):
        return 'quiz'
    if re.search(r'\b(?:exam(?:ination)?s?|mid[ -]?terms?|finals?|tests?)\b', title):
        return 'exam'
    if re.search(r'\b(?:assignments?|homework|problem sets?|projects?|concept deck|reports?|essays?|lab submissions?)\b', title):
        return 'assignment'
    return 'assessment'


def calendar_group(event):
    return {'class':'classes','event':'events'}.get(event_category(event), 'assessments')


def calendar_url(calendar_id):
    return 'https://calendar.google.com/calendar/u/0/r?' + urlencode({'cid': calendar_id})



def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + secrets.token_hex(6))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
    temp.replace(path)


def identity(event):
    if event.get('source_uid'):
        return str(event['course_id'])+'|moodle:'+event['source_uid']
    key = str(event['course_id']) + '|' + event['title'].strip().casefold()
    if event['title'].strip().casefold() in ('in-class test', 'quiz') and event.get('date'):
        key += '|' + event['date']
    return key


def preview(events, today=None):
    today = today or datetime.now(ZoneInfo('America/Halifax')).date()
    counts = Counter(identity(e) for e in events)
    ready, held = [], []
    for event in events:
        reason = None
        try:
            day = date.fromisoformat(event.get('date') or '')
        except ValueError:
            day = None
        if event.get('kind') == 'Class' and day and day < today:
            continue
        if event.get('cancelled'):
            reason = 'Cancelled in Moodle'
        elif event.get('kind') == 'Practice':
            reason = 'Practice material, not a submitted assessment'
        elif day is None:
            reason = 'No definite date'
        elif day < today:
            reason = 'Past date'
        elif not semester.contains(day.isoformat()):
            reason = 'Outside '+semester.current()['label']
        elif event.get('review') or event.get('tentative') or event.get('source_changed') or event.get('conflict_dates'):
            reason = 'Date or source needs checking'
        elif counts[identity(event)] > 1:
            reason = 'Repeated assessment name; occurrence needs checking'
        elif not event.get('sources') and not event.get('confirmed_by_user'):
            reason = 'No source evidence'
        if reason:
            held.append(dict(event, hold_reason=reason, calendar_category=event_category(event)))
        else:
            ready.append(dict(event, calendar_category=event_category(event)))
    return {'ready': ready, 'held': held}


def event_body(event):
    day = date.fromisoformat(event['date'])
    is_class = event.get('source_type') == 'class_schedule'
    timed = event.get('start_at') and event.get('source_type') in ('moodle_calendar', 'class_schedule', 'discovery', 'ai_review', 'class_time_assessment')
    description = ['Coursewell', 'Exact time from Moodle calendar (America/Halifax).' if timed else 'Date only — exact deadline time has not been verified.',
                   'Confirmed by you.' if event.get('confirmed_by_user') else 'Extracted from saved course materials. Check the source for instructions.']
    if event.get('ai_reviewed'):
        description.append('✦ AI second review — supporting excerpt below.')
    if is_class:
        if not event.get('end_at'):
            raise CalendarError('Class end time has not been confirmed.')
        description = ['Coursewell', 'Regular class time from your course outline (America/Halifax).', event.get('schedule_note', '')]
    for source in event.get('sources', [])[:5]:
        description.append('\n' + source.get('name', '') + (f" · page {source['page']}" if source.get('page') else ''))
        description.append(source.get('evidence', '')[:1000])
        if source.get('url', '').startswith('https://'):
            description.append(source['url'])
    body = {'summary': event['course'] + ' — ' + event.get('display_title', event['title']),
            'description': '\n'.join(description), 'colorId': EVENT_COLORS[event_category(event)],
            'start': {'date': day.isoformat()}, 'end': {'date': (day + timedelta(days=1)).isoformat()},
            'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 900}]}}
    if timed:
        start = datetime.fromisoformat(event['start_at'])
        if start.tzinfo is None:
            raise CalendarError('A deadline has no verified timezone.')
        end = datetime.fromisoformat(event['end_at']) if event.get('end_at') else start+timedelta(minutes=1)
        if end.tzinfo is None or end <= start:
            raise CalendarError('A deadline has an invalid end time.')
        body.update(start={'dateTime': start.isoformat(), 'timeZone': 'America/Halifax'},
                    end={'dateTime': end.isoformat(), 'timeZone': 'America/Halifax'},
                    reminders={'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 1440}, {'method': 'popup', 'minutes': 60}]})
    if event.get('source_type') == 'ai_review':
        body['description']=body['description'].replace('Exact time from Moodle calendar (America/Halifax).','Time interpreted from quoted source evidence (America/Halifax).')
    if event.get('source_type') == 'discovery':
        body['description'] = body['description'].replace('Exact time from Moodle calendar (America/Halifax).', 'Time reviewed by you from a source proposal (America/Halifax).')
        body['location'] = event.get('location', '')
    if event.get('source_type') == 'class_time_assessment':
        body['description'] = body['description'].replace('Exact time from Moodle calendar (America/Halifax).', event['time_note'])
        body['location'] = event.get('location', '')
    if is_class:
        body["location"] = event.get("location", "")
        body["reminders"] = {"useDefault": False, "overrides": [{"method":"popup", "minutes":15}]}
    return body


def managed_body(event):
    """Google may reorder reminders or serialize a timestamp in UTC."""
    result = {k: deepcopy(event.get(k)) for k in ('summary', 'description', 'start', 'end', 'reminders', 'colorId')}
    for key in ('start', 'end'):
        value = result[key] or {}
        if value.get('dateTime'):
            instant = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
            result[key] = {'dateTime': instant.astimezone(ZoneInfo('UTC')).isoformat()}
    if result.get('reminders', {}).get('overrides'):
        result['reminders']['overrides'].sort(key=lambda x:(x['method'], x['minutes']))
    if event.get("location"):
        result["location"] = event["location"]
    return result


class CalendarError(Exception):
    pass


class GoogleError(CalendarError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class CalendarService:
    def __init__(self, root, feed_provider, port=8767, transport=None, refresh_source=None):
        self.root = Path(root)
        self.feed_provider = feed_provider
        self.port = port
        self.http = transport or requests.Session()
        self.lock = threading.RLock()
        self.busy = False
        self.pending = None
        self.queued = False
        self.refresh_source = refresh_source
        self.state = self.read('state.json', {'enabled': False, 'records': {}, 'message': 'Connect Google Calendar to get started.'})
        self.credentials = self.read('credentials.json', {})
        self.tokens = self.read('tokens.json', {})

    def read(self, name, default):
        path = self.root / name
        return json.loads(path.read_text()) if path.exists() else default

    def save(self):
        private_write(self.root / 'state.json', self.state)

    def import_credentials(self, value):
        config = value.get('installed', {})
        if not isinstance(config, dict) or not all(isinstance(config.get(k), str) and config[k] for k in ('client_id', 'client_secret')):
            raise CalendarError('Choose the JSON downloaded for a Desktop app OAuth client.')
        if not config['client_id'].endswith('.apps.googleusercontent.com'):
            raise CalendarError('Invalid Google OAuth client ID.')
        with self.lock:
            if self.busy or self.tokens:
                raise CalendarError('Disconnect Google before changing credentials.')
            self.credentials = {k: config[k] for k in ('client_id', 'client_secret')}
            private_write(self.root / 'credentials.json', self.credentials)

    def status(self):
        with self.lock:
            return {k: deepcopy(self.state.get(k)) for k in ('enabled', 'message', 'last_sync', 'counts', 'warnings', 'result', 'needs_reconnect', 'source_status')} | {
                'configured': bool(self.credentials), 'connected': bool(self.tokens.get('refresh_token')),
                'busy': self.busy, 'calendar_name': CALENDAR_NAME,
                'calendar_url': calendar_url(self.state['calendar_id']) if self.state.get('calendar_id') else None,
                'calendars': [{'key': key, 'name': name, 'url': calendar_url(cid)}
                              for key, name in CALENDAR_NAMES.items()
                              if (cid := self.state.get('calendar_ids', {}).get(key))]}


    def authorize(self):
        with self.lock:
            if not self.credentials:
                raise CalendarError('Import your Google credentials first.')
            if self.busy:
                raise CalendarError('Wait for calendar syncing to finish.')
            verifier = secrets.token_urlsafe(48)
            self.pending = {'state': secrets.token_urlsafe(32), 'verifier': verifier, 'expires': time.time()+600}
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
                'client_id': self.credentials['client_id'], 'redirect_uri': self.redirect_uri,
                'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent',
                'state': self.pending['state'], 'code_challenge': challenge, 'code_challenge_method': 'S256'})

    @property
    def redirect_uri(self):
        return f'http://127.0.0.1:{self.port}/oauth/google/callback'

    def token_request(self, values):
        try:
            response = self.http.post('https://oauth2.googleapis.com/token', data=values | self.credentials, timeout=20)
            if response.status_code != 200:
                if response.status_code in (400, 401):
                    self.state['needs_reconnect'] = True
                raise CalendarError('Google authorization expired or failed. Connect Google again.')
            result = response.json()
            if not result.get('access_token'):
                raise CalendarError('Google did not return an access token. Connect again.')
            result['expires_at'] = time.time() + int(result.get('expires_in', 3600))
            return result
        except requests.RequestException:
            raise CalendarError('Could not reach Google. Check your internet connection and retry.') from None

    def callback(self, query):
        with self.lock:
            pending, self.pending = self.pending, None
            supplied = query.get('state', [''])[0]
            if not pending or time.time() > pending['expires'] or not secrets.compare_digest(supplied, pending['state']):
                raise CalendarError('Sign-in expired or was not started by this app. Connect again from the dashboard.')
            if query.get('error') or not query.get('code'):
                raise CalendarError('Google access was not granted. You can reconnect from the dashboard.')
            tokens = self.token_request({'code': query['code'][0], 'grant_type': 'authorization_code',
                                         'redirect_uri': self.redirect_uri, 'code_verifier': pending['verifier']})
            if not tokens.get('refresh_token') or SCOPE not in tokens.get('scope', '').split():
                raise CalendarError('Calendar access was not granted. Connect again and allow calendar access.')
            self.tokens = tokens
            private_write(self.root / 'tokens.json', tokens)
            self.state.update(needs_reconnect=False, message='Google connected. Review the preview, then enable syncing.')
            self.save()

    def access_token(self):
        if not self.tokens.get('refresh_token'):
            raise CalendarError('Connect Google Calendar first.')
        if self.tokens.get('expires_at', 0) < time.time()+60:
            self.tokens.update(self.token_request({'grant_type': 'refresh_token', 'refresh_token': self.tokens['refresh_token']}))
            private_write(self.root / 'tokens.json', self.tokens)
        return self.tokens['access_token']

    def api(self, method, path, body=None, etag=None):
        # Calendar creation cannot be safely retried after an unknown outcome.
        # Event inserts have deterministic IDs; GET/PATCH/DELETE are retry-safe.
        # Google PATCH merges nested fields: clear the opposite representation
        # when converting an all-day event to a timed event (or back).
        if method == 'PATCH' and body and '/events/' in path:
            body = deepcopy(body)
            for field in ('start','end'):
                if body.get(field, {}).get('dateTime'):
                    body[field]['date'] = None
                elif body.get(field, {}).get('date'):
                    body[field].update(dateTime=None, timeZone=None)
        attempts = 1 if method == 'POST' and path == 'calendars' else 3
        for attempt in range(attempts):
            headers = {'Authorization': 'Bearer '+self.access_token()}
            if etag:
                headers['If-Match'] = etag
            try:
                response = self.http.request(method, 'https://www.googleapis.com/calendar/v3/'+path,
                                             json=body, headers=headers, timeout=20)
            except requests.RequestException:
                if attempt+1 < attempts:
                    time.sleep(2**attempt)
                    continue
                raise CalendarError('Google could not be reached. Retry calendar sync; Moodle files are safe.') from None
            if response.status_code == 401 and attempt+1 < attempts:
                self.tokens['expires_at'] = 0
                continue
            try:
                reasons = {e.get('reason') for e in response.json().get('error', {}).get('errors', [])}
            except (ValueError, AttributeError):
                reasons = set()
            transient = response.status_code in (429, 500, 502, 503, 504) or bool(reasons & {'rateLimitExceeded', 'userRateLimitExceeded'})
            if transient and attempt+1 < attempts:
                time.sleep(2**attempt)
                continue
            break
        if response.status_code in (404, 409, 410, 412):
            return response.status_code, {}
        if response.status_code >= 400:
            if response.status_code == 401:
                self.state['needs_reconnect'] = True
            message = {401: 'Google sign-in expired. Connect Google again.',
                       403: 'Google denied calendar access. Check the Calendar API and your Google authorization.',
                       429: 'Google temporarily limited requests. Retry later.'}.get(response.status_code, 'Google Calendar could not finish this request. Retry later.')
            raise GoogleError(message, response.status_code)
        return response.status_code, response.json() if response.content else {}

    def enable(self):
        with self.lock:
            if not self.tokens:
                raise CalendarError('Connect Google first.')
            self.state['enabled'] = True
            self.save()
        self.start()

    def disconnect(self):
        with self.lock:
            if self.busy:
                raise CalendarError('Wait for calendar syncing to finish.')
            self.tokens = {}
            self.pending = None
            (self.root/'tokens.json').unlink(missing_ok=True)
            self.state.update(enabled=False, message='Disconnected locally. Existing Google events are preserved. You can also revoke access in your Google account.')
            self.save()

    def add_reviewed(self, event):
        """Idempotent explicit Add, serialized with normal sync across processes."""
        with self.lock:
            if self.busy:
                raise CalendarError('Calendar is syncing. Try Add again when it finishes.')
            with (self.root/'sync.lock').open('a') as lockfile:
                try:
                    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise CalendarError('Another calendar sync is running.')
                self.state = self.read('state.json', self.state)
                self.tokens = self.read('tokens.json', self.tokens)
                self.access_token()
                ids = self.ensure_calendars()
                cid = ids[calendar_group(event)]
                body = event_body(event)
                # Same title/time from two sources must not create two events.
                stable = json.dumps([event['course_id'], event['title'].casefold().strip(), body['start'], body['end']], sort_keys=True)
                # Google permits base32hex characters only (a-v and 0-9).
                eid = 'ce' + hashlib.sha256(stable.encode()).hexdigest()
                path = self.events_base(cid)
                code, remote = self.api('GET', path+eid)
                if code in (404,410):
                    code, remote = self.api('POST', path.rstrip('/'), dict(body,id=eid))
                    if code == 409:
                        code, remote = self.api('GET', path+eid)
                if code not in (200,201) or remote.get('status') == 'cancelled':
                    raise CalendarError('Google did not confirm this event. Retry after checking Calendar.')
                if managed_body(remote) != managed_body(body):
                    raise CalendarError('An existing Google event differs. Check it before retrying.')
                return {'id':eid,'calendar_id':cid}

    def start(self):
        with self.lock:
            if not self.state.get('enabled'):
                return False
            if self.busy:
                self.queued = True
                return False
            self.busy = True
            self.state['result'] = 'running'
            self.state['message'] = 'Checking assessment dates and syncing Google Calendar…'
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        acquired = False
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (self.root/'sync.lock').open('a') as lockfile:
                try:
                    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    raise CalendarError('Another calendar sync is running. Retry when it finishes.')
                self.state = self.read('state.json', self.state)
                self.state.update(result='running', message='Checking sources and syncing classes and deadlines…')
                self.tokens = self.read('tokens.json', self.tokens)
                self.access_token()
                source_failed = False
                if self.refresh_source:
                    source = self.refresh_source()
                    self.state['source_status'] = {k:source.get(k) for k in ('status', 'checked_at', 'message')}
                    if source.get('status') != 'complete':
                        source_failed = True
                events = self.feed_provider()['events']
                if source_failed:
                    # Verified local timetable and source-backed in-class assessments
                    # are independent of the live Moodle calendar export.
                    # Never reuse structured deadlines from a failed refresh.
                    events = [e for e in events if e.get('source_type') in ('class_schedule','class_time_assessment','ai_review')]
                    if not events:
                        raise CalendarError('Moodle calendar could not refresh. No Google dates were changed. Click Update Moodle to reconnect and retry.')
                self.sync(events)
                self.state['result'] = 'attention' if self.state.get('warnings') else 'complete'
        except CalendarError as exc:
            with self.lock:
                self.state['message'] = str(exc)
                self.state['result'] = 'error'
        except Exception:
            with self.lock:
                self.state['message'] = 'Calendar sync could not finish. Existing events and Moodle files are safe.'
                self.state['result'] = 'error'
        finally:
            with self.lock:
                self.busy = False
                if acquired:
                    self.save()
                again, self.queued = self.queued, False
            if again:
                self.start()

    @staticmethod
    def events_base(calendar_id):
        return 'calendars/' + quote(calendar_id, safe='') + '/events/'

    def ensure_calendars(self):
        ids = self.state.setdefault('calendar_ids', {})
        if self.state.get('calendar_id') and not ids.get('classes'):
            ids['classes'] = self.state['calendar_id']
        pending = self.state.setdefault('calendar_creation_pending', {})
        for group, name in CALENDAR_NAMES.items():
            if not ids.get(group):
                if pending.get(group) or (group == 'classes' and self.state.get('creation_pending')):
                    raise CalendarError('Calendar creation had an uncertain result. Check Google Calendar before retrying; no duplicate will be created.')
                pending[group] = True
                self.save()
                try:
                    code, calendar = self.api('POST', 'calendars', {
                        'summary': name, 'timeZone': 'America/Halifax',
                        'description': 'Coursewell ' + group + ' managed by Coursewell.'})
                except GoogleError as exc:
                    if exc.status < 500:
                        pending[group] = False
                        self.state['creation_pending'] = False
                        self.save()
                    raise
                if code not in (200, 201) or not calendar.get('id'):
                    raise CalendarError('Calendar creation could not be verified. Check Google Calendar before retrying.')
                ids[group] = calendar['id']
                pending[group] = False
                if group == 'classes':
                    self.state.update(calendar_id=calendar['id'], creation_pending=False)
                self.save()
            path = 'calendars/' + quote(ids[group], safe='')
            code, calendar = self.api('GET', path)
            if code != 200:
                raise CalendarError('A saved Coursewell calendar is unavailable. Reconnect the original Google account or restore it.')
            if calendar.get('summary') != name:
                code, _ = self.api('PATCH', path, {'summary': name})
                if code != 200:
                    raise CalendarError('Could not update the Coursewell calendar name. Retry sync.')
        return ids

    def migrate_record(self, record, origin, destination, warnings):
        """Copy, verify, then remove the original using its ETag.

        events.move requires broader permissions than calendar.app.created. This
        resumable migration keeps our existing narrow scope and deterministic IDs.
        """
        event_id = record['event_id']
        source_path = self.events_base(origin) + event_id
        target_path = self.events_base(destination) + event_id
        desired = deepcopy(record['body'])
        desired['colorId'] = EVENT_COLORS[event_category({'summary': desired['summary']})]
        source_code, source = self.api('GET', source_path)
        target_code, target = self.api('GET', target_path)
        title = desired['summary']
        def matches(remote):
            return remote.get('status') != 'cancelled' and managed_body(remote) == managed_body(desired)
        if source_code in (404, 410) or source.get('status') == 'cancelled':
            if record.get('migration_target') == destination and target_code == 200 and matches(target):
                record.update(calendar_id=destination, body=desired)
                record.pop('migration_target', None)
                self.save()
            else:
                warnings.append('Missing or deleted Google event was not migrated: ' + title)
            return
        if (managed_body(source) != managed_body(record['body']) or not source.get('etag')
                or any(source.get(field) for field in ('attendees', 'attachments', 'recurrence', 'conferenceData'))
                or source.get('visibility', 'default') != 'default'
                or source.get('eventType', 'default') != 'default'):
            warnings.append('Edited in Google; kept in its current calendar: ' + title)
            return
        if target_code not in (200, 404, 410):
            raise CalendarError('Could not inspect the destination calendar. Retry sync.')
        record['migration_target'] = destination
        self.save()
        if target_code in (404, 410):
            target_code, target = self.api('POST', self.events_base(destination).rstrip('/'), desired | {
                'id': event_id, 'extendedProperties': {'private': {'academicAssistant': 'v1'}}})
            if target_code == 409:
                target_code, target = self.api('GET', target_path)
        if target_code not in (200, 201) or not matches(target):
            warnings.append('Destination event could not be verified; original preserved: ' + title)
            return
        code, _ = self.api('DELETE', source_path, etag=source['etag'])
        if code not in (200, 204, 404, 410):
            warnings.append('Calendar transfer needs retry; original preserved: ' + title)
            return
        record.update(calendar_id=destination, body=desired)
        record.pop('migration_target', None)
        self.save()

    def sync(self, events):
        plan = preview(events)
        warnings = []
        if self.state.get('source_status', {}).get('status') == 'error':
            warnings.append('Moodle calendar could not refresh. Previous Google events were preserved; click Update Moodle to retry.')
        counts = {'added': 0, 'updated': 0, 'unchanged': 0, 'held': len(plan['held'])}
        calendar_ids = self.ensure_calendars()
        records = self.state.setdefault('records', {})
        # Include past and held records: splitting calendars must not strand older
        # managed events in the Classes calendar. Never invent missing events.
        by_key = {identity(e): e for e in events}
        for key, record in records.items():
            if record.get('cancelled'):
                continue
            event = by_key.get(key)
            group = calendar_group(event) if event else ('classes' if '|moodle:class:' in key else 'assessments')
            destination = calendar_ids[group]
            origin = record.get('calendar_id', self.state['calendar_id'])
            if origin != destination:
                self.migrate_record(record, origin, destination, warnings)
        cancelled = [e for e in events if e.get('cancelled') and e.get('source_type') in ('moodle_calendar','ai_review') and not e.get('review')]
        for event in cancelled:
            key = identity(event)
            record = records.get(key)
            if not record or record.get('cancelled'):
                continue
            base = self.events_base(record.get('calendar_id', self.state['calendar_id']))
            code, remote = self.api('GET', base+record['event_id'])
            if code in (404, 410) or remote.get('status') == 'cancelled':
                record['cancelled'] = True
            elif managed_body(remote) == managed_body(record['body']) and remote.get('etag'):
                code, _ = self.api('DELETE', base+record['event_id'], etag=remote['etag'])
                if code in (200, 204, 404, 410):
                    record['cancelled'] = True
                else:
                    warnings.append('Cancelled assessment changed in Google; check it there: '+event['title'])
            else:
                warnings.append('Cancelled in Moodle but edited in Google; left untouched: '+event['title'])
            self.save()
        eligible = {identity(e) for e in plan['ready']}
        past = {identity(e) for e in events if e.get('date') and e['date'] < datetime.now(ZoneInfo('America/Halifax')).date().isoformat()}
        for event in plan['ready']:
            key = identity(event)
            legacy_key = str(event['course_id'])+'|'+event['title'].strip().casefold()
            # Upgrade a uniquely named document event to its Moodle UID without
            # creating a second Google event when a structured deadline appears.
            same_name = [e for e in plan['ready'] if str(e['course_id']) == str(event['course_id']) and e['title'].casefold() == event['title'].casefold()]
            legacy = records.get(legacy_key, {}).get('body', {}).get('start', {})
            legacy_day = legacy.get('date') or legacy.get('dateTime', '')[:10]
            matching_day = [e for e in same_name if e.get('date') == event.get('date')]
            migrate_generic = event['title'].casefold() in ('in-class test','quiz') and legacy_day == event.get('date') and len(matching_day) == 1
            if key != legacy_key and key not in records and legacy_key in records and ((event.get('source_uid') and len(same_name) == 1) or migrate_generic):
                records[key] = records.pop(legacy_key)
                self.save()
            record = records.get(key)
            destination = calendar_ids[calendar_group(event)]
            if record and record.get('calendar_id', self.state['calendar_id']) != destination:
                # Migration could not be verified; do not create a second event.
                continue
            base = self.events_base(destination)
            event_id = record['event_id'] if record else hashlib.sha256(('academic-assistant-v1|'+key).encode()).hexdigest()
            body = event_body(event)
            digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            code, remote = self.api('GET', base+event_id)
            if code in (404, 410):
                if record:
                    warnings.append('Deleted Google event was not recreated: '+event['title'])
                    continue
                code, remote = self.api('POST', base.rstrip('/'), body | {'id': event_id,
                     'extendedProperties': {'private': {'academicAssistant': 'v1'}}})
                if code == 409:
                    code, remote = self.api('GET', base+event_id)
                if code not in (200, 201):
                    raise CalendarError('Could not save a calendar event. Retry to safely resume.')
                # Verify a recovered event before claiming a timed-out insert succeeded.
                if managed_body(remote) != managed_body(body):
                    warnings.append('Existing Google event differs; left untouched: '+event['title'])
                    continue
                counts['added'] += 1
            else:
                if remote.get('status') == 'cancelled':
                    warnings.append('Cancelled Google event was kept cancelled: '+event['title'])
                    continue
                managed = managed_body(remote)
                previous = record.get('body') if record else body
                if managed != managed_body(previous) and managed != managed_body(body):
                    warnings.append('Edited in Google; left untouched: '+event['title'])
                    continue
                if managed != managed_body(body):
                    if not remote.get('etag'):
                        warnings.append('Could not verify Google event version: '+event['title'])
                        continue
                    code, remote = self.api('PATCH', base+event_id, body, remote['etag'])
                    if code != 200:
                        warnings.append('Google event changed during sync; retry: '+event['title'])
                        continue
                    counts['updated'] += 1
                else:
                    counts['unchanged'] += 1
            records[key] = {'body': body, 'digest': digest, 'event_id': event_id, 'calendar_id': destination}
            self.save()
        for key in records.keys()-eligible-past:
            if records[key].get('cancelled'):
                continue
            warnings.append('Previously synced item now missing or needs checking; its Google event was kept: '+records[key]['body']['summary'])
        self.state.update(counts=counts, warnings=warnings, last_sync=datetime.now(ZoneInfo('America/Halifax')).isoformat(),
                          message=f"{counts['added']} added · {counts['updated']} updated · {counts['unchanged']} unchanged · {counts['held']} held back.")
        self.save()
