import semester
"""Conservative, cached extraction of source-backed assessment candidates.

No model, credentials, or network access. Source dates remain local calendar dates;
Moodle display times are retained verbatim until its account timezone is verified.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib
import json
import re
import threading
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from document_reader import read_document, SUPPORTED

VERSION = 9
READER_VERSION = 'tables-v3'
LOCK = threading.Lock()
MONTHS = {m:i+1 for i,m in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'])}
MON = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?'
DATE = re.compile(rf'\b(?:(?P<m1>{MON})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?|(?P<d2>\d{{1,2}})\s+(?P<m2>{MON}))(?:,?\s+(?P<year>20\d{{2}}))?\b',re.I)
MONTH_FIRST = re.compile(rf'\b(?P<m1>{MON})\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d{{2}}))?\b',re.I)
ASSESS = re.compile(r'\b(?:quiz(?:zes)?|tests?|mid[- ]?term|exam|assignment|concept\s+deck|presentation)\b',re.I)


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def date_matches(text):
    # Prefer "October 20" over the overlapping "1 October" in
    # "Quiz 1 October 20". Retain genuine day-first dates elsewhere.
    matches=list(MONTH_FIRST.finditer(text))
    for m in DATE.finditer(text):
        if not any(m.start()<other.end() and other.start()<m.end() for other in matches):matches.append(m)
    return sorted(matches,key=lambda m:m.start())


def dates(text, year):
    found=[]
    for m in date_matches(text):
        try:
            value=date(int(m['year'] or year), MONTHS[(m['m1'] or m['m2'])[:3].lower()], int(m['d1'] or m['d2']))
            found.append(value.isoformat())
        except ValueError:
            continue
    return found


def title_for(text):
    patterns=[(r'concept\s+deck','Concept deck'),(r'(?:technical|techinal)\s+presentation','Technical presentation'),(r'(?:stakeholder|final)\s+presentation','Stakeholder presentation'),(r'assignment\s*#?\s*(\d+)','Assignment'),(r'quiz\s*#?\s*(\d+)','Quiz'),(r'mid[- ]?term\s*#?\s*(\d+)','Midterm'),(r'mid[- ]?term','Midterm exam'),(r'final\s+exam','Final exam'),(r'in[- ]class\s+tests?|\btests?\b','In-class test'),(r'quiz','Quiz')]
    for pattern,label in patterns:
        match=re.search(pattern,text,re.I)
        if match:
            return label+(' '+match[1] if match.lastindex else '')
    return 'Assessment date to check'


def extract(path):
    suffix=path.suffix.lower()
    if suffix=='.pdf':
        from pypdf import PdfReader
        return [(i+1,p.extract_text() or '') for i,p in enumerate(PdfReader(path).pages)]
    if suffix in ('.docx','.pptx'):
        with zipfile.ZipFile(path) as archive:
            names=['word/document.xml'] if suffix=='.docx' else sorted((n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)),key=lambda n:int(re.search(r'slide(\d+)',n)[1]))
            pages=[]
            for i,name in enumerate(names):
                root=ET.fromstring(archive.read(name))
                ns='http://schemas.openxmlformats.org/wordprocessingml/2006/main' if suffix=='.docx' else 'http://schemas.openxmlformats.org/drawingml/2006/main'
                paragraphs=[''.join(t.text or '' for t in p.iter('{'+ns+'}t')) for p in root.iter('{'+ns+'}p')]
                pages.append((None if suffix=='.docx' else i+1,'\n'.join(paragraphs)))
            return pages
    if suffix in ('.md','.txt'):
        return [(None,path.read_text(errors='replace'))]
    return []


def candidates(text, year, source_title, moodle=False):
    lines=[normalized(s) for s in text.splitlines() if normalized(s)]
    rows=[]; heading=''; tentative=False
    for i,line in enumerate(lines):
        if re.search(r'tentative.*(?:schedule|dates)',line,re.I):tentative=True
        if re.search(r'tests? (?:will occur|schedule)|^35%: Tests',line,re.I):heading='In-class test'
        class_session = bool(re.search(r'^Class\s*#?\s*\d+\s*[-–—:]\s*', line, re.I))
        ds=dates(line,year)
        # Moodle's explicit Due label may be on its own line.
        is_due=moodle and (line.lower().startswith('due:') or (i>0 and lines[i-1].lower()=='due:'))
        evidence=line
        context=line
        if is_due:
            local_title=source_title
            if not ASSESS.search(local_title):
                local_title=next((s for s in reversed(lines[max(0,i-8):i]) if ASSESS.search(s) and len(s)<160 and not s.lower().startswith('source:')),source_title)
            context=local_title+' '+line
            evidence='Due: '+line.removeprefix('Due:').strip()
        elif ds and 'tentative schedule' in line.lower() and re.search(r'\btests?\b',' '.join(lines[max(0,i-8):i]),re.I):
            context='In-class test '+line;evidence=context
        elif not ASSESS.search(line) and ds and heading and len(line)<85:
            context=heading+' '+line
            evidence=heading+' schedule: '+line
        elif not ASSESS.search(line) and ds:
            # Dates split onto a separate line after an explicit event sentence.
            previous=lines[i-1] if i else ''
            if re.search(r'(?:midterm|exam|quiz).*?(?:held|scheduled|due|on)$',previous,re.I):
                context=previous+' '+line;evidence=context
        if ds and (ASSESS.search(context) or is_due):
            # "Sample Spaces" is a topic, not a sample assessment.
            if re.search(r'\b(?:review|solutions?|(?:sample|practice|mock|past)\s+(?:exam|test|quiz|midterm)|available from|opens?)\b',context,re.I) and not is_due:
                continue
            title=title_for(context)
            # Unnumbered document assignment header can precede its due-date line.
            if title=='Assessment date to check':title=title_for(source_title)
            if title=='Assessment date to check':continue
            for d in ds:
                date_range=bool(re.search(r'\d\s*(?:–|—|-|to|through)\s*(?:'+MON+r'\s*)?\d',context,re.I))
                rows.append({'title':title,'date':d,'evidence':evidence[:1000], 'class_time_reference':class_session or bool(re.search(r'in[- ]class|during (?:the )?class time|scheduled class', context, re.I)), 'class_session_confirmation':class_session and not re.search(r'tentative|possibly|provisional|maybe',line,re.I), 'tentative':(tentative and not class_session) or bool(re.search(r'tentative|possibly|provisional|maybe',line+' '+source_title,re.I)), 'review':len(ds)>1 or date_range or not d.startswith(str(year)), 'moodle_due':is_due})
        if re.search(r'(?:almost every class|announced later|date.*\b(?:TBA|TBD)\b|date will.*announced|deadline.*announced)',line,re.I):
            context=' '.join(lines[max(0,i-2):min(len(lines),i+2)])
            if ASSESS.search(context):
                rows.append({'title':'Recurring pop quizzes' if 'almost every class' in line.lower() else title_for(line if ASSESS.search(line) else context),'date':None,'evidence':context[:1000],'tentative':False,'review':True,'moodle_due':False})
        if heading and not ds and len(line)>85 and not re.search('following dates|tentative schedule',line,re.I):heading=''
    return rows


def announcement_is_explicit(candidate):
    """A date mention alone does not confirm that an assessment is scheduled."""
    text = candidate['evidence']
    return bool(candidate.get('date') and re.search(
        r"\b(?:will (?:be held|take place|have|write)|is scheduled|is on|due(?: on)?|held in class)\b", text, re.I))


def build(library, runtime):
    """Rebuild from verified sources, reusing content-addressed extraction results."""
    runtime.mkdir(parents=True,exist_ok=True)
    cache_dir=runtime/'assessment-cache';cache_dir.mkdir(exist_ok=True)
    coverage=[];events=[];fresh=0;cached=0
    for manifest_path in semester.manifests(library):
        manifest=json.loads(manifest_path.read_text());cid=manifest_path.parent.name.removeprefix('course-')
        cname=manifest.get('course_name',cid);year_match=re.search(r'20\d{2}',cname)
        if not year_match:
            coverage.append({'course':cname,'name':'Course year','status':'Year unavailable; extraction skipped'});continue
        year=int(year_match[0]);docs=[];practice_evidence=None
        for collection in ('files','pages'):
            for url,item in manifest.get(collection,{}).items():
                if not item.get('verified_in_last_run'):
                    coverage.append({'course':cname,'name':item.get('title',url),'status':'Not verified in latest sync'});continue
                path=(manifest_path.parent/item['path']).resolve()
                if not path.is_relative_to(manifest_path.parent.resolve()):
                    coverage.append({'course':cname,'name':item.get('title',url),'status':'Invalid source path; not analyzed'});continue
                if path.suffix.lower() not in SUPPORTED:
                    coverage.append({'course':cname,'name':path.name,'status':'Data file / unsupported format; not analyzed'});continue
                try:
                    with path.open('rb') as stream:
                        digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    if item.get('sha256') and digest!=item['sha256']:
                        coverage.append({'course':cname,'name':path.name,'status':'Content changed since verified download; update Moodle'});continue
                    cache=cache_dir/f'{READER_VERSION}-{digest}.json'
                    document=json.loads(cache.read_text()) if cache.exists() else None
                    if document and not any('failed' in note or 'unavailable' in note for note in document['notes']):cached+=1
                    else:
                        document=read_document(path,extract);cache.write_text(json.dumps(document));fresh+=1
                    pages=document['pages']
                    notes=list(document['notes'])
                    if not item.get('sha256'):notes.append('Download checksum unavailable; needs verification.')
                    coverage.append({'course':cname,'name':path.name,'status':'; '.join(notes) if notes else 'Text analyzed','file_url':'/file?'+urlencode({'course':cid,'path':item['path']})})
                    source={'name':path.name,'url':url,'file_url':'/file?'+urlencode({'course':cid,'path':item['path']}),'verified_at':item.get('verified_at'),'sha256':digest,'extraction_notes':notes}
                    docs.append((pages,source,item,collection))
                    for page,text in pages:
                        clean=normalized(text)
                        m=re.search(r'.{0,140}(?:Nothing is submitted|Nothing is handed in).{0,180}',clean,re.I)
                        policy_source=re.search(r'syllabus|outline',source['name']+' '+item.get('title',''),re.I)
                        if policy_source and m and re.search(r'practice|assignment',m[0],re.I):practice_evidence=dict(source,page=page,evidence=m[0])
                except Exception as exc:
                    coverage.append({'course':cname,'name':path.name,'status':f'Could not extract text ({type(exc).__name__})'})
        for pages,source,item,collection in docs:
            for page,text in pages:
                extracted = candidates(text,year,item.get('title') or source['name'],collection=='pages')
                announcement = item.get('source_type') == 'announcement'
                for candidate in extracted:
                    if (announcement and not announcement_is_explicit(candidate)) or re.search(r'\b(extend\w*|reschedul\w*|postpon\w*|cancel\w*|moved|instead|tomorrow|next\s+\w+|room change|will not|not held)\b', candidate['evidence'], re.I):
                        candidate['review'] = True
                        candidate['time_note'] = 'Announcement or change wording: verify interpretation before syncing.'
                    # Normalize matching named assessment identities across sources.
                    title=title_for(candidate['title']) if ASSESS.search(candidate['title']) else candidate['title']
                    if candidate['title']=='Recurring pop quizzes':title=candidate['title']
                    kind='Practice' if practice_evidence and 'assignment' in title.lower() else 'Assessment'
                    ev=dict(candidate,course=cname,course_id=cid,title=title,kind=kind,submission_status='Unknown',sources=[dict(source,page=page,evidence=candidate['evidence'])])
                    if source['extraction_notes']:ev['review']=True
                    if kind=='Practice':ev['sources'].append(practice_evidence);ev['submission_status']='Not submitted by course policy'
                    if candidate['moodle_due']:ev['review']=True;ev['time_note']='Moodle display time retained in source; account timezone not verified.'
                    events.append(ev)
    # Deduplicate identical events while retaining all evidence. Recurring tests
    # are separate instances; named assignments/decks can have conflicting dates.
    grouped={}
    for ev in events:
        key=(ev['course_id'],ev['title'].lower(),ev['date'])
        if key not in grouped:grouped[key]=ev
        else:
            existing=grouped[key]
            for source in ev['sources']:
                if source not in existing['sources']:existing['sources'].append(source)
            existing['tentative'] |= ev['tentative'];existing['review'] |= ev['review']
            existing['class_time_reference'] = existing.get('class_time_reference') or ev.get('class_time_reference')
            existing['class_session_confirmation'] = existing.get('class_session_confirmation') or ev.get('class_session_confirmation')
    events=list(grouped.values())
    for ev in events:
        if ev.get('class_session_confirmation'):
            ev['tentative'] = False
        if ev['date'] and re.search(r'concept deck|assignment \d+|^midterm(?: \d+| exam)$|^quiz \d+|^final exam$',ev['title'],re.I):
            other=sorted({e['date'] for e in events if e['course_id']==ev['course_id'] and e['title']==ev['title'] and e['date'] and e['date']!=ev['date']})
            if other:ev['conflict_dates']=other;ev['review']=True
        ev['id']=hashlib.sha256(f"{ev['course_id']}|{ev['title']}|{ev['date']}".encode()).hexdigest()[:16]
    events.sort(key=lambda e:(e['date'] or '9999',e['course'],e['title']))
    result={'generated_at':datetime.now(ZoneInfo('America/Halifax')).isoformat(),'events':events,'coverage':coverage,'newly_extracted':fresh,'reused':cached,'method':'Rules and source evidence; no AI guesses. Dates without years use the course year. No submission status is inferred.'}
    target=runtime/'assessments.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(result,indent=2));temp.replace(target)
    return result


def feed(library, runtime):
    runtime=runtime/semester.current()['term']
    runtime.mkdir(parents=True,exist_ok=True)
    with LOCK:
        paths=semester.manifests(library)
        # Watch local file changes as well as manifests. Hashes are verified on
        # rebuild; inexpensive stats avoid reading every PDF on each UI poll.
        signature=[semester.current()['term']]
        for p in paths:
            raw=p.read_text();signature.append(raw)
            manifest=json.loads(raw)
            for collection in ('files','pages'):
                for item in manifest.get(collection,{}).values():
                    target=(p.parent/item['path']).resolve()
                    if not target.is_relative_to(p.parent.resolve()):continue
                    try:
                        st=target.stat();signature.append(str((st.st_mtime_ns,st.st_ctime_ns,st.st_size)))
                    except OSError:signature.append('missing')
        stamp=hashlib.sha256(('v'+str(VERSION)+''.join(signature)).encode()).hexdigest()
        marker=runtime/'assessment-stamp.txt';output=runtime/'assessments.json'
        if not output.exists() or not marker.exists() or marker.read_text()!=stamp:
            previous=json.loads(output.read_text()) if output.exists() else None
            result=build(library,runtime)
            result['changes']=[]
            if previous:
                def date_groups(rows):
                    groups={}
                    for e in rows:
                        groups.setdefault((e['course_id'],e['title']),set()).add(e['date'] or 'No fixed date')
                    return groups
                old=date_groups(previous['events']);new=date_groups(result['events'])
                for key,values in new.items():
                    if key not in old or old[key]!=values:
                        result['changes'].append({'course_id':key[0],'title':key[1],'before':sorted(old.get(key,[])),'after':sorted(values)})
                for key,values in old.items():
                    if key not in new:result['changes'].append({'course_id':key[0],'title':key[1],'before':sorted(values),'after':['No longer found in current readable sources; not confirmed cancelled']})
            temp=output.with_suffix('.tmp');temp.write_text(json.dumps(result,indent=2));temp.replace(output)
            marker.write_text(stamp)
        else:result=json.loads(output.read_text())
    today=datetime.now(ZoneInfo('America/Halifax')).date();monday=today-timedelta(days=today.weekday());sunday=monday+timedelta(days=6)
    result['period']={'today':today.isoformat(),'start':monday.isoformat(),'end':sunday.isoformat()}
    return result
