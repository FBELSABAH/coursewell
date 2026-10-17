from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
import mimetypes
import secrets
import os
import html
from course_folders import open_course_folder
from chatgpt_connection import ChatGPTConnection, StudyError
from study_ai import StudyAI
from calendar_service import CalendarService, CalendarError, preview
from assessments import feed
from chat_service import ChatService
from sync_service import SyncService
import moodle_dates
from class_schedule import class_events

SYNC = SyncService()
TOKEN = secrets.token_urlsafe(32)
PORT = int(os.environ.get('ACADEMIC_DASHBOARD_PORT','8767'))

ROOT = Path(__file__).parent
CHAT = ChatService(ROOT/'.runtime'/'chat.sqlite')
LIBRARY = Path('/Users/fbelsabah/Documents/academic-ai-assistant/data/library')
COURSES = [('27355','CS 2520','Computer Organization & Architecture','Systems, circuits & computation','coral'),('27974','STAT 2910','Probability & Mathematical Statistics I','Probability, distributions & inference','blue'),('27973','STAT 2240','Applied Regression Analysis','Models, relationships & prediction','purple'),('27641','MATH 2420','Combinatorics I','Counting, proofs & graphs','gold'),('27151','AMS 1910','Introduction to Data Science','Data, exploration & discovery','green')]
CALENDAR_ROOT = Path('/Users/fbelsabah/Documents/academic-ai-assistant/data/calendar')
MOODLE_DATES = CALENDAR_ROOT/'moodle.json'
STUDY_CONNECTION = ChatGPTConnection(ROOT/'.runtime'/'chatgpt', PORT)
STUDY_AI = StudyAI(LIBRARY, ROOT/'.runtime'/'study-ai', STUDY_CONNECTION)

def calendar_feed(include_classes=False):
    documents = CHAT.overlay(feed(LIBRARY, ROOT/'.runtime'))
    snapshot = json.loads(MOODLE_DATES.read_text()) if MOODLE_DATES.exists() else {}
    result = moodle_dates.merge(documents, snapshot)
    if include_classes:
        result["events"] += class_events(CALENDAR_ROOT/"classes.json", LIBRARY)
    return result

CALENDAR = CalendarService(CALENDAR_ROOT, lambda: calendar_feed(True), PORT,
    refresh_source=lambda: moodle_dates.refresh(MOODLE_DATES, library()))
SYNC.on_complete = CALENDAR.start

def library():
    result=[]
    for cid,code,name,description,color in COURSES:
        path=LIBRARY / ('course-'+cid) / 'manifest.json'
        manifest=json.loads(path.read_text()) if path.exists() else {}
        files=[]
        for url, item in manifest.get('files',{}).items():
            files.append(dict(name=Path(item['path']).name,title=item.get('title',''),path=item['path'],source=url,size=item.get('bytes',0),verified=item.get('verified_at'),current=item.get('verified_in_last_run',False)))
        result.append(dict(id=cid,code=code,name=name,description=description,color=color,files=files,url=manifest.get('course_url',''),last_run=manifest.get('last_run')))
    return result

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # OAuth authorization codes must never be written to the server log.
        if not urlparse(self.path).path.startswith('/oauth/'):
            super().log_message(format, *args)

    def trusted(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def json_response(self, value, status=200):
        payload=json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(payload)

    def do_POST(self):
        if not self.trusted() or self.headers.get('Origin') not in (f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}') or not secrets.compare_digest(self.headers.get('X-Dashboard-Token',''), TOKEN):
            self.send_error(403); return
        if self.path.startswith('/api/course-folder/'):
            try:
                cid = self.path.rsplit('/', 1)[-1]
                if cid not in {course[0] for course in COURSES}:
                    raise ValueError('Unknown course.')
                open_course_folder(cid, LIBRARY.parent/'course-export-state', Path.home()/'Documents'/'University Courses'/'Fall 2026')
                self.json_response({'opened': True})
            except (ValueError, OSError) as exc:
                self.json_response({'error': str(exc)}, 400)
            return
        if self.path.startswith('/api/study/'):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > 20000:
                    self.send_error(413); return
                body = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(body, dict):
                    raise StudyError('Invalid request.')
                action = self.path.rsplit('/', 1)[-1]
                if action == 'connect':
                    self.json_response({'url': STUDY_CONNECTION.authorize(body.get('account'))})
                elif action == 'disconnect':
                    self.json_response(STUDY_CONNECTION.disconnect())
                elif action == 'ask':
                    self.json_response(STUDY_AI.ask(body.get('question'), body.get('sources'), body.get('model'), body.get('mode', 'study')))
                else:
                    self.send_error(404)
            except (StudyError, ValueError) as exc:
                self.json_response({'error': str(exc) if isinstance(exc, StudyError) else 'Invalid study request.'}, 400)
            except Exception:
                self.json_response({'error': 'Study request failed. Your saved files and calendar are unchanged.'}, 500)
            return
        if self.path.startswith('/api/calendar/'):
            try:
                action = self.path.rsplit('/',1)[-1]
                if action == 'connect':
                    self.json_response({'url': CALENDAR.authorize()}); return
                elif action == 'enable': CALENDAR.enable()
                elif action == 'sync': CALENDAR.start()
                elif action == 'disconnect': CALENDAR.disconnect()
                else:
                    self.send_error(404); return
                self.json_response(CALENDAR.status())
            except CalendarError as exc:
                self.json_response({'error': str(exc)},400)
            except Exception:
                self.json_response({'error': 'Calendar request failed. Retry from the dashboard.'},500)
            return
        if self.path not in ('/api/sync', '/api/reconnect'):
            self.send_error(404); return
        started=SYNC.start(reconnect=self.path=='/api/reconnect')
        self.json_response(SYNC.snapshot(), 202 if started else 409)

    def do_GET(self):
        if not self.trusted():
            self.send_error(403); return
        u=urlparse(self.path)
        if u.path == '/oauth/chatgpt/callback':
            try:
                STUDY_CONNECTION.callback(parse_qs(u.query))
                message = 'ChatGPT connected. Return to Study and choose a model to test your course materials.'
                code = 200
            except StudyError as exc:
                message, code = str(exc), 400
            except Exception:
                message, code = 'ChatGPT sign-in could not finish. Try again from Study.', 500
            payload = ('<!doctype html><meta charset="utf-8"><title>Coursewell</title><h1>ChatGPT connection</h1><p>'+html.escape(message)+'</p><a href="/#study">Return to Study</a>').encode()
            self.send_response(code); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Referrer-Policy','no-referrer'); self.end_headers(); self.wfile.write(payload); return
        if u.path.startswith('/api/study/'):
            try:
                action = u.path.rsplit('/',1)[-1]
                if action == 'status': self.json_response(STUDY_CONNECTION.status())
                elif action == 'sources': self.json_response(STUDY_AI.catalog())
                elif action == 'models': self.json_response(STUDY_CONNECTION.models())
                elif action == 'history': self.json_response(STUDY_AI.history())
                else: self.send_error(404)
            except StudyError as exc:
                self.json_response({'error': str(exc)},400)
            except Exception:
                self.json_response({'error': 'Study data is unavailable. Check the saved course library.'},500)
            return
        if u.path=='/oauth/google/callback':
            try:
                CALENDAR.callback(parse_qs(u.query))
                message='Google connected. Return to the dashboard to review dates and enable calendar syncing.'
                code=200
            except CalendarError as exc:
                message=str(exc);code=400
            except Exception:
                message='Sign-in could not finish. Return to the dashboard and try again.';code=500
            payload=('<!doctype html><meta charset="utf-8"><title>Coursewell</title><h1>Google Calendar</h1><p>'+html.escape(message)+'</p><a href="/">Return to dashboard</a>').encode()
            self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(payload);return
        if u.path=='/api/calendar':
            self.json_response(CALENDAR.status()); return
        if u.path=='/api/calendar/preview':
            try:
                self.json_response(preview(calendar_feed(True)['events']))
            except Exception:
                self.json_response({'error':'Could not prepare assessment preview. Try again after updating Moodle.'},500)
            return
        if u.path=='/api/status':
            self.json_response(dict(SYNC.snapshot(), token=TOKEN, app='academic-assistant', version=21)); return
        if u.path=='/api/assessments':
            try:
                self.json_response(calendar_feed())
            except Exception:
                self.json_response({'error':'Assessment extraction could not complete. Saved files are safe.'},500)
            return
        if u.path=='/api/library':
            try:
                payload=json.dumps(library()).encode()
            except Exception:
                self.send_error(500,'Could not read saved library'); return
            self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(payload); return
        if u.path=='/file':
            args=parse_qs(u.query); cid=args.get('course',[''])[0]; relative=args.get('path',[''])[0]
            course=next((c for c in library() if c['id']==cid),None)
            manifest_path=LIBRARY/('course-'+cid)/'manifest.json'
            page_paths=[]
            if course and manifest_path.exists():
                page_paths=[p['path'] for p in json.loads(manifest_path.read_text()).get('pages',{}).values()]
            if not course or relative not in [f['path'] for f in course['files']]+page_paths:
                self.send_error(404); return
            base=(LIBRARY/('course-'+cid)).resolve(); path=(base/relative).resolve()
            if not path.is_relative_to(base):
                self.send_error(403); return
        elif u.path in ('/','/index.html'):
            path=ROOT/'index.html'
        elif u.path in ('/calendar.js', '/study_ai.js', '/study_ai.css', '/ui.css', '/ui.js', '/brand/coursewell-mark.png'):
            path=ROOT/u.path.lstrip('/')
        else:
            self.send_error(404); return
        if not path.is_file():
            self.send_error(404); return
        self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream'); self.send_header('Content-Length',str(path.stat().st_size)); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers()
        with path.open('rb') as f:
            while chunk:=f.read(1024*1024):
                self.wfile.write(chunk)

if __name__=='__main__':
    print(f'Coursewell: http://127.0.0.1:{PORT}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
