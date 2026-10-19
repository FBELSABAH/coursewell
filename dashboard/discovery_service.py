import semester
"""Evidence-backed review inbox. Email text is data, never executable instructions."""
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from zoneinfo import ZoneInfo
from chatgpt_connection import StudyError
from review_destination import review_destination

from ai_review import PROMPT, VERIFY, VERSION, chunks, assess, overlay_event, signature


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def optional_identity(item):
    return (re.sub(r'[^a-z0-9]+',' ',item.get('title','').casefold()).strip(),item.get('date'),item.get('start_at'))


def norm(value):
    return ' '.join(value.split())


def private_mail(title, text=''):
    """Keep clearly personal sensitive messages out of automatic cloud review."""
    return bool(re.search(r'\b(prescription|medication|medical appointment|patient|verification code|password reset|bank statement|credit card statement)\b',title,re.I)) or bool(re.search(r'^From:.*\b(health centre|counselling|student health)\b',text,re.I))


class DiscoveryService:
    def __init__(self, root, connection, events_provider=None):
        self.root, self.connection = Path(root), connection
        self.events_provider = events_provider or (lambda: [])
        self.on_review_complete = None
        self.review_state = {"status":"idle"}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root/'discovery.sqlite'
        self.lock = threading.RLock()
        self.review_lock = threading.Lock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sources (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                  text TEXT NOT NULL, url TEXT NOT NULL, posted_at TEXT,
                  hash TEXT NOT NULL, notes TEXT NOT NULL, reviewed_hash TEXT);
                CREATE TABLE IF NOT EXISTS proposals (
                  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                  source_hash TEXT NOT NULL, payload TEXT NOT NULL,
                  decision TEXT NOT NULL DEFAULT 'pending'
                    CHECK(decision IN ('pending','skipped','added','changed')),
                  google_id TEXT, calendar_id TEXT);
                CREATE INDEX IF NOT EXISTS proposals_source ON proposals(source_id);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(sources)')}
            if 'context' not in columns:db.execute("ALTER TABLE sources ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
            db.execute('CREATE TABLE IF NOT EXISTS review_chunks (key TEXT PRIMARY KEY, result TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS ai_usage (at TEXT, model TEXT, status TEXT, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER, reasoning_tokens INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS review_runs (started TEXT, finished TEXT, status TEXT, reviewed INTEGER, errors TEXT)')
        with self.db() as db:
            for row in db.execute("SELECT id,title,text,context FROM sources WHERE kind='outlook'").fetchall():
                if private_mail(row['title'],row['text']):
                    context=json.loads(row['context']);context['review_excluded']='Personal sensitive mail; not sent for automatic AI review.'
                    db.execute('UPDATE sources SET context=? WHERE id=?',(json.dumps(context),row['id']))
        self.path.chmod(0o600)
        self.filter_waiting()

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=20)
            db.row_factory = sqlite3.Row
            db.create_function('active_source',2,semester.source_active)
            db.execute('PRAGMA foreign_keys=ON')
            try:
                with db:
                    yield db
            finally:
                db.close()

    def settings(self, values=None):
        with self.db() as db:
            if values is not None:
                for key, value in values.items():
                    if key not in ('model', 'auto_review', 'auto_apply'):
                        raise ValueError('Unknown setting.')
                    if key in ('auto_review','auto_apply') and not isinstance(value,bool):raise ValueError('Auto review must be on or off.')
                    if key=='model' and (not isinstance(value,str) or len(value)>150):raise ValueError('Invalid model.')
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))
            return {r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}

    def ingest(self, sid, kind, title, text, url='', posted_at=None, notes=None, context=None):
        if not text.strip() and not notes:
            return False
        context=dict(context or {})
        context.setdefault('semester',semester.current()['term'])
        if kind=='outlook' and private_mail(title,text):
            context['review_excluded']='Personal sensitive mail; not sent for automatic AI review.'
        fingerprint = digest(json.dumps([text, title, posted_at, notes or [], context]))
        with self.db() as db:
            old = db.execute('SELECT hash FROM sources WHERE id=?',(sid,)).fetchone()
            if old and old['hash'] == fingerprint:
                return False
            db.execute('''INSERT INTO sources(id,kind,title,text,url,posted_at,hash,notes)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,text=excluded.text,url=excluded.url,
                posted_at=excluded.posted_at,hash=excluded.hash,notes=excluded.notes''',
                (sid,kind,title,text,url,posted_at,fingerprint,json.dumps(notes or [])))
            db.execute('UPDATE sources SET context=? WHERE id=?',(json.dumps(context or {}),sid))
            db.execute("UPDATE proposals SET decision='changed' WHERE source_id=? AND decision='added'",(sid,))
            db.execute("DELETE FROM proposals WHERE source_id=? AND decision='pending'",(sid,))
        return True

    def measured_complete(self, model, instructions, prompt):
        usage={};status='failed'
        def capture(value):
            if isinstance(value,dict):usage.update(value)
        try:
            result=self.connection.complete(model,instructions,prompt,usage_callback=capture)
            status='complete'
            return result
        finally:
            def number(value):
                return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
            with self.db() as db:
                db.execute('INSERT INTO ai_usage VALUES (?,?,?,?,?,?,?)',
                    (datetime.now().astimezone().isoformat(),model,status,
                     number(usage.get('input_tokens')),number(usage.get('output_tokens')),
                     number((usage.get('input_tokens_details') or {}).get('cached_tokens')),
                     number((usage.get('output_tokens_details') or {}).get('reasoning_tokens'))))

    def filter_waiting(self):
        from review_filter import needs_review
        with self.db() as db:
            for row in db.execute('SELECT * FROM sources WHERE reviewed_hash IS NULL OR reviewed_hash!=hash').fetchall():
                context=json.loads(row['context'])
                if context.get('review_excluded'):continue
                if not needs_review(row['title'],row['text'],row['kind'],row['url'],context):
                    context['review_excluded']='No actionable scheduling signal (filter v1)'
                    db.execute('UPDATE sources SET context=? WHERE id=?',(json.dumps(context),row['id']))

    def snapshot(self):
        with self.db() as db:
            rows=db.execute('''SELECT p.*,s.title source_title,s.url,s.hash current_hash,s.notes,s.kind source_kind
                 FROM proposals p JOIN sources s ON s.id=p.source_id WHERE active_source(s.context,s.posted_at) ORDER BY p.rowid DESC''').fetchall()
            usage=dict(db.execute('SELECT COUNT(*) requests, COUNT(input_tokens) measured_requests, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens, SUM(cached_tokens) cached_tokens, SUM(reasoning_tokens) reasoning_tokens, SUM(status="failed") failed_requests, MIN(at) since FROM ai_usage').fetchone())
            pending=db.execute('SELECT COUNT(*) FROM sources WHERE active_source(context,posted_at) AND (reviewed_hash IS NULL OR reviewed_hash!=hash) AND json_extract(context,"$.review_excluded") IS NULL').fetchone()[0]
            filtered=db.execute("SELECT COUNT(*) FROM sources WHERE json_extract(context,'$.review_excluded') LIKE 'No actionable%'").fetchone()[0]
            total=db.execute('SELECT COUNT(*) FROM sources WHERE active_source(context,posted_at)').fetchone()[0]
            coverage=[dict(r,notes=json.loads(r['notes'])) for r in db.execute("SELECT title,notes,url FROM sources WHERE active_source(context,posted_at) AND notes!='[]'")]
        return {'items':[dict(r, payload=json.loads(r['payload']),review_destination=review_destination(json.loads(r['payload'])),notes=json.loads(r['notes'])) for r in rows],
                'filtered':filtered,'usage':usage,'unreviewed':pending,'sources':total,'coverage':coverage,'busy':self.review_lock.locked(),
                'settings':self.settings(),'connection':self.connection.status(),'review_state':dict(self.review_state)}

    def start_review(self, model, limit=25):
        if not self.review_lock.acquire(False):raise StudyError('A review is already running.')
        self.review_state={'status':'running','reviewed':0,'errors':[]}
        threading.Thread(target=self.review,args=(model,limit,True),daemon=True).start()
        return {'message':'AI review started. You can continue using Coursewell.'}

    def review(self, model, limit=25, acquired=False):
        if not acquired and not self.review_lock.acquire(False):
            raise StudyError('A review is already running.')
        started=datetime.now().astimezone().isoformat();count=0;errors=[];requests=0
        self.review_state={'status':'running','reviewed':0,'errors':[]}
        try:
            self.filter_waiting()
            if not isinstance(model,str) or not model:raise StudyError('Choose an available ChatGPT model first.')
            with self.db() as db:
                sources=db.execute('SELECT * FROM sources WHERE active_source(context,posted_at) AND (reviewed_hash IS NULL OR reviewed_hash!=hash) AND json_extract(context,"$.review_excluded") IS NULL ORDER BY CASE WHEN kind="outlook" OR lower(title) LIKE "%announcement%" THEN 0 WHEN lower(title) LIKE "%outline%" OR lower(title) LIKE "%syllabus%" THEN 1 ELSE 2 END, rowid DESC LIMIT ?', (max(1,min(limit,100)),)).fetchall()
            events=self.events_provider()
            for raw_source in sources:
                source=dict(raw_source);source['context']=json.loads(source['context'])
                sections=chunks(source['text'])
                if not sections:
                    errors.append(source['title']+': no readable text; open the original.');continue
                # Persist section successes so interruptions resume without paying twice.
                items=[];complete=True;stop=False
                for index,section in enumerate(sections):
                    context=source['context']
                    related=[e for e in events if not context.get('course_id') or str(e.get('course_id'))==context['course_id']]
                    related=[{k:e.get(k) for k in ('id','course_id','title','date','kind','review','source_type')} for e in related if e.get('kind')!='Class']
                    packet={'source_title':source['title'],'text':section,'posted_at':source['posted_at'],
                            'courses':dict(semester.COURSES),'context':context,'semester':semester.current()['label'],'existing_events':related,'section':index+1,'sections':len(sections)}
                    key=digest(json.dumps([VERSION,PROMPT,VERIFY,model,source['hash'],packet],sort_keys=True))
                    with self.db() as db:cached=db.execute('SELECT result FROM review_chunks WHERE key=?',(key,)).fetchone()
                    try:
                        if cached:part=json.loads(cached['result'])
                        else:
                            if requests>=100:
                                complete=False;stop=True;errors.append('Review request budget reached; remaining sections will resume next time.');break
                            self.review_state.update(source=source['title'],section=index+1,sections=len(sections))
                            requests+=1
                            part=json.loads(self.measured_complete(model,PROMPT,json.dumps(packet)))['items']
                            self.validate_items(source,part,section)
                            if part:
                                requests+=1
                                checks=json.loads(self.measured_complete(model,VERIFY,json.dumps(dict(packet,proposals=part))))['checks']
                                if not isinstance(checks,list) or len(checks)!=len(part) or {c.get('index') for c in checks}!=set(range(len(part))):raise ValueError('Incomplete verification')
                                for c in checks:
                                    if not isinstance(c.get('supported'),bool) or not isinstance(c.get('reason'),str):raise ValueError('Invalid verification')
                                    part[c['index']]['verified']=c['supported']
                                    part[c['index']]['verification_reason']=c['reason'][:1000]
                            with self.db() as db:db.execute('INSERT OR REPLACE INTO review_chunks VALUES (?,?)',(key,json.dumps(part)))
                        items.extend(part)
                    except Exception as exc:
                        complete=False;stop=isinstance(exc,StudyError)
                        errors.append(source['title']+': '+(str(exc) if isinstance(exc,StudyError) else 'Interpretation failed validation; retained for retry.'))
                        break
                if complete:
                    # Overlap repeats must not create duplicate proposals.
                    unique={json.dumps({k:i.get(k) for k in ('title','kind','course_id','date','start_at','target_id')},sort_keys=True):i for i in items}
                    self.accept_result(source,json.dumps({'items':list(unique.values())}))
                    count+=1;self.review_state['reviewed']=count
                if stop:break  # Pause on service/allowance failures; one malformed source must not block other sources.
            if self.settings().get('auto_apply'):self.apply_academic()
            if self.on_review_complete:self.on_review_complete()
            self.review_state.update(status='attention' if errors else 'complete',reviewed=count,errors=errors,
                message=f'Reviewed {count} sources. Optional events await your choice.')
            return dict(self.review_state)
        except Exception as exc:
            errors.append(str(exc) if isinstance(exc,StudyError) else 'Review interrupted. Saved sections will resume on retry.')
            self.review_state.update(status='attention',errors=errors)
            if not acquired:raise
        finally:
            with self.db() as db:db.execute('INSERT INTO review_runs VALUES (?,?,?,?,?)',
                (started,datetime.now().astimezone().isoformat(),self.review_state['status'],count,json.dumps(errors)))
            self.review_lock.release()

    def validate_items(self,source,items,evidence=None):
        if not isinstance(items,list) or len(items)>200:raise ValueError('Invalid AI item count.')
        for item in items:
            if not isinstance(item,dict) or item.get('kind') not in ('event','deadline','change','cancellation'):raise ValueError('Invalid AI event type.')
            if not isinstance(item.get('title'),str) or not 1<=len(item['title'])<=300:raise ValueError('Invalid AI title.')
            if item['kind']=='event' and re.fullmatch(r'(?:midterm|final)(?: exam)?|(?:quiz|test|assignment|homework)\s+\d+',item['title'].strip(),re.I):
                item['kind']='deadline'
            quote=item.get('quote')
            if not isinstance(quote,str) or len(quote.strip())<12 or norm(quote) not in norm(evidence if evidence is not None else source['text']):raise ValueError('AI quotation did not match the source.')
            for key in ('summary','location','course_id','target_id'):
                if key in item and (not isinstance(item[key],str) or len(item[key])>3000):raise ValueError('Invalid AI field.')
            if item.get('date'):date.fromisoformat(item['date'])
            for key in ('start_at','end_at'):
                if item.get(key):
                    stamp=datetime.fromisoformat(item[key])
                    if stamp.tzinfo is None or stamp.astimezone(ZoneInfo('America/Halifax')).date().isoformat()!=item.get('date'):raise ValueError('Invalid AI time.')
            if item.get('end_at') and (not item.get('start_at') or datetime.fromisoformat(item['end_at'])<=datetime.fromisoformat(item['start_at'])):raise ValueError('Invalid AI interval.')
            if not isinstance(item.get('uncertainties',[]),list) or any(not isinstance(n,str) for n in item.get('uncertainties',[])):raise ValueError('Invalid uncertainty list.')

    def accept_result(self, source, raw):
        try:
            items=json.loads(raw)['items']
            self.validate_items(source,items)
        except (ValueError,KeyError,TypeError):
            raise StudyError('AI output failed evidence or date checks; nothing from this source was accepted.') from None
        validated=items
        with self.db() as db:
            current=db.execute('SELECT hash FROM sources WHERE id=?',(source['id'],)).fetchone()
            if not current or current['hash']!=source['hash']:
                raise StudyError('Source changed during review; retry with its latest version.')
            for item in validated:
                item={k:v for k,v in item.items() if k in ('title','kind','course_id','target_id','date','start_at','end_at','location','summary','quote','uncertainties','verified','verification_reason')}
                if review_destination(item)=='coursework':
                    plan=assess(item,dict(source),self.events_provider())
                    item['application']={k:v for k,v in plan.items() if k!='target'}
                item['ai_reviewed']=True
                pid=digest(source['id']+source['hash']+json.dumps(item,sort_keys=True))
                db.execute('INSERT OR IGNORE INTO proposals(id,source_id,source_hash,payload) VALUES (?,?,?,?)',
                           (pid,source['id'],source['hash'],json.dumps(item)))
            db.execute('UPDATE sources SET reviewed_hash=? WHERE id=?',(source['hash'],source['id']))

    def calendar_events(self):
        result=[];seen=set()
        for row in self.snapshot()['items']:
            if row['decision'] not in ('added','changed'):continue
            p=row['payload']
            if review_destination(p)!='calendar':continue
            key=optional_identity(p)
            if key in seen:continue
            seen.add(key)
            result.append({'course_id':'discover','course':'University','title':p['title'],
                'date':p.get('date'),'start_at':p.get('start_at'),'end_at':p.get('end_at'),
                'kind':'Event', 'source_type':'discovery',
                'id':'discover:'+row['id'],'source_uid':'discover:'+row['id'],'confirmed_by_user':True,'ai_reviewed':True,
                'review':row['decision']=='changed','sources':[{'name':row['source_title'],
                'url':row['url'],'evidence':p['quote']}]})
        return result

    def resolve_optional_duplicates(self, db, pid, payload):
        """Resolve equivalent pending suggestions, retaining every source for history."""
        if review_destination(payload)!='calendar':return
        for row in db.execute("SELECT id,payload FROM proposals WHERE decision='pending' AND id!=?",(pid,)).fetchall():
            other=json.loads(row['payload'])
            if review_destination(other)=='calendar' and optional_identity(other)==optional_identity(payload):
                db.execute("UPDATE proposals SET decision='skipped' WHERE id=?",(row['id'],))

    def decide(self, pid, action, calendar=None):
        with self.db() as db:
            r=db.execute('''SELECT p.*,s.hash current_hash,s.title source_title,s.url,s.notes
                 FROM proposals p JOIN sources s ON p.source_id=s.id WHERE p.id=?''',(pid,)).fetchone()
            if not r:raise ValueError('Unknown proposal.')
            if action=='skip':
                if r['decision'] in ('added','changed'):raise ValueError('This event already exists in Google; skipping will not delete it.')
                db.execute("UPDATE proposals SET decision='skipped' WHERE id=?",(pid,))
                self.resolve_optional_duplicates(db,pid,json.loads(r['payload']))
                return {'message':'Ignored. The original email is unchanged.'}
            if action!='add':raise ValueError('Unknown action.')
            if r['decision']=='added':return {'message':'Already added.'}
            if r['decision']=='changed' or r['source_hash']!=r['current_hash']:raise ValueError('Source changed. Review the updated proposal first.')
            if db.execute("SELECT 1 FROM proposals WHERE source_id=? AND decision='changed'",(r['source_id'],)).fetchone():
                raise ValueError('An event from this source already exists and changed. Check it in Google Calendar before adding another.')
            if json.loads(r['notes']):
                raise ValueError('Source coverage needs checking. Open the original before adding this proposal.')
            item=json.loads(r['payload'])
            if review_destination(item)!='calendar':raise ValueError('Academic proposals are managed by the second-review pipeline.')
            if not item.get('date') or item['date']<datetime.now(ZoneInfo('America/Halifax')).date().isoformat():raise ValueError('Choose an upcoming, definite date.')
            if item['kind'] in ('change','cancellation'):raise ValueError('Check the existing assessment in Calendar before applying this change.')
            if item.get('uncertainties'):raise ValueError('This proposal has unresolved details. Check the source before adding it.')
            event={'course_id':'discover','course':'University','title':item['title'],'date':item['date'],
                   'start_at':item.get('start_at'),'end_at':item.get('end_at'),'source_type':'discovery',
                   'source_uid':'discover:'+pid,'confirmed_by_user':True,'ai_reviewed':True,'location':item.get('location',''),
                   'sources':[{'name':r['source_title'],'url':r['url'],'evidence':item['quote']}],
                   'kind':'Event'}
            # Repeated newsletters or interpretations must not create the same event twice.
            for other in db.execute("SELECT payload FROM proposals WHERE decision='added'"):
                previous=json.loads(other['payload'])
                if review_destination(previous)=='calendar' and optional_identity(previous)==optional_identity(item):
                    db.execute("UPDATE proposals SET decision='skipped' WHERE id=?",(pid,))
                    self.resolve_optional_duplicates(db,pid,item)
                    return {'message':'This event was already added from another source.'}
            result=calendar.add_reviewed(event)
            db.execute("UPDATE proposals SET decision='added',google_id=?,calendar_id=? WHERE id=?",(result['id'],result['calendar_id'],pid))
            self.resolve_optional_duplicates(db,pid,item)
            return {'message':'Added to Google Calendar.'}

    def apply_academic(self):
        events=self.events_provider();applied=0
        with self.db() as db:
            rows=db.execute("SELECT p.*,s.title,s.text,s.url,s.posted_at,s.context,s.notes,s.hash current_hash FROM proposals p JOIN sources s ON s.id=p.source_id WHERE active_source(s.context,s.posted_at) AND decision='pending'").fetchall()
            for row in rows:
                p=json.loads(row['payload'])
                if review_destination(p)=='calendar' or row['source_hash']!=row['current_hash']:continue
                plan=assess(p,dict(row),events)
                # Another active proposal for this identity is a conflict, not a new event.
                competing=[]
                for other in rows:
                    q=json.loads(other['payload'])
                    if other['id']!=row['id'] and q.get('kind')!='event' and q.get('application',{}).get('course_id')==plan['course_id'] and q.get('title','').casefold()==p['title'].casefold() and q.get('date')!=p.get('date'):
                        competing.append(other)
                if competing:plan['reasons'].append('Other source proposals disagree about this assessment.')
                p['application']={k:v for k,v in plan.items() if k!='target'}
                if not plan['reasons']:
                    p['applied_event']=overlay_event(p,dict(row),plan,row['id'])
                    p['automatic']=True
                    db.execute("UPDATE proposals SET decision='added' WHERE id=?",(row['id'],));applied+=1
                db.execute('UPDATE proposals SET payload=? WHERE id=?',(json.dumps(p),row['id']))
        return applied

    def overlay(self, events):
        result=[dict(e) for e in events]
        with self.db() as db:
            rows=db.execute("SELECT p.*,s.hash current_hash FROM proposals p JOIN sources s ON s.id=p.source_id WHERE active_source(s.context,s.posted_at) AND decision IN ('added','changed')").fetchall()
        for row in rows:
            p=json.loads(row['payload']);event=p.get('applied_event')
            if not event:continue
            plan=p['application'];target=next((e for e in result if e.get('id')==plan.get('target_id')),None)
            stale=row['decision']=='changed' or row['source_hash']!=row['current_hash']
            if plan.get('target_id') and (not target or signature(target)!=plan['target_signature']):stale=True
            if not target and any(str(e.get('course_id'))==str(event['course_id']) and e['title'].casefold()==event['title'].casefold() for e in result):stale=True
            superseded=plan.get('superseded',[])
            if any(not any(e.get('id')==x['id'] and signature(e)==x['signature'] for e in result) for x in superseded):stale=True
            if stale:continue  # Never overwrite newer source facts or user corrections.
            result=[e for e in result if e.get('id') not in {x['id'] for x in superseded}]
            if target:result.remove(target)
            if any(e.get('id')==event['id'] for e in result):continue
            result.append(event)
        return result

    def confirm_academic(self, pid, course_id, target_id, day):
        from ai_review import COURSES
        if course_id not in COURSES:raise ValueError('Choose a course.')
        if not isinstance(day,str):raise ValueError('Choose a date.')
        date.fromisoformat(day)
        if not semester.contains(day):raise ValueError('Choose a date in '+semester.current()['label']+'.')
        events=self.events_provider()
        target=next((e for e in events if e.get('id')==target_id and str(e.get('course_id'))==course_id),None)
        if target_id and not target:raise ValueError('The assessment changed; refresh and choose it again.')
        with self.db() as db:
            row=db.execute('SELECT p.*,s.title,s.text,s.url,s.posted_at,s.context,s.notes,s.hash current_hash FROM proposals p JOIN sources s ON s.id=p.source_id WHERE p.id=?',(pid,)).fetchone()
            if not row or row['decision']!='pending' or row['source_hash']!=row['current_hash']:raise ValueError('Proposal is stale; refresh first.')
            item=json.loads(row['payload'])
            if item['kind']=='event':raise ValueError('Use Add for optional events.')
            if item['kind'] in ('change','cancellation') and not target:raise ValueError('Select the existing assessment to change.')
            if not target and any(str(e.get('course_id'))==course_id and e['title'].casefold()==item['title'].casefold() for e in events):raise ValueError('This assessment exists; select it instead of creating another.')
            item['date']=day
            for field in ('start_at','end_at'):
                if item.get(field) and datetime.fromisoformat(item[field]).date().isoformat()!=day:item[field]=None
            plan={'course_id':course_id,'target_id':target_id or None,'target_signature':signature(target) if target else None,'target':target,'reasons':[]}
            item['application']={k:v for k,v in plan.items() if k!='target'}
            item['applied_event']=overlay_event(item,dict(row),plan,pid)
            item['applied_event']['confirmed_by_user']=True
            item['automatic']=False
            db.execute("UPDATE proposals SET decision='added',payload=? WHERE id=?",(json.dumps(item),pid))
        if self.on_review_complete:self.on_review_complete()
        return {'message':'Confirmed in Coursewell. Google sync will use this correction when connected.'}

    def pending_events(self):
        result=[]
        for row in self.snapshot()['items']:
            item=row['payload']
            if row['decision'] not in ('pending','changed') or review_destination(item)=='calendar':continue
            result.append({'id':'proposal:'+row['id'],'proposal_id':row['id'],'course_id':item.get('application',{}).get('course_id') or '',
                'course':'AI review','title':item['title'],'date':item.get('date'),'kind':'Assessment','review':True,
                'ai_reviewed':True,'submission_status':'Unknown','sources':[{'name':row['source_title'],
                'url':row['url'],'file_url':row['url'],'evidence':item['quote']}],
                'time_note':'; '.join(item.get('application',{}).get('reasons',[]))})
        return result
