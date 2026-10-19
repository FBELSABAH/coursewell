"""Conservative, source-backed lecture discovery; never invent a missing time."""
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
import calendar
import hashlib
import json
import re
import requests
from bs4 import BeautifulSoup
import semester
from assessments import extract
from class_schedule import class_events

CALENDAR_URL='https://calendar.upei.ca/current/chapter/4-the-university-of-prince-edward-island-dates/'
DAY=r'(?:Monday|Mon|Tuesday|Tues|Tue|Wednesday|Wed|Thursday|Thurs|Thu|Th|Friday|Fri|Saturday|Sat|Sunday|Sun|M|T|W|F)s?'
DAYS=re.compile(r'(?<![A-Za-z])('+DAY+r')(?![A-Za-z])',re.I)
TIME=r'(\d{1,2}(?::\d{2})?)\s*(am|pm)?'
INTERVAL=re.compile(TIME+r'\s*(?:[-–—]|to)\s*'+TIME,re.I)

def lecture_slots(text):
    """Only class/lecture lines or an unlabelled weekday + interval. Ignore office/lab lines."""
    lines=[' '.join(x.split()) for x in text.splitlines()]
    slots=[]
    for n,line in enumerate(lines):
        if re.search(r'office|tutorial|lab(?:oratory)?|appointment',line,re.I):continue
        day_matches=list(DAYS.finditer(line))
        match=INTERVAL.search(line)
        if not day_matches or not match:continue
        # The weekdays must precede the time; avoid deadlines and prose containing dates.
        if day_matches[-1].end()>match.start() or re.search(r'due|exam|test|quiz|assignment',line,re.I):continue
        prefix=line[:day_matches[0].start()].strip(' :')
        if prefix and not re.fullmatch(r'(?:class|lecture|lectures|classes)(?:\s+(?:time|times|hours|schedule))?',prefix,re.I):continue
        h1,a1,h2,a2=match.groups()
        if not a1 and not a2:continue
        a1=a1 or a2;a2=a2 or a1
        def clock(h,a):
            parts=h.split(':');hour=int(parts[0]);minute=int(parts[1]) if len(parts)>1 else 0
            if not 1<=hour<=12 or not 0<=minute<60:raise ValueError('Invalid clock')
            return f'{hour%12+(12 if a.lower()=="pm" else 0):02d}:{minute:02d}'
        try:start,end=clock(h1,a1),clock(h2,a2)
        except ValueError:continue
        minutes=lambda t:int(t[:2])*60+int(t[3:])
        if not 0<minutes(end)-minutes(start)<=240:continue
        weekdays=sorted(set({'m':0,'t':1,'w':2,'th':3,'f':4,'sa':5,'su':6}.get(d[0].lower(),
            3 if d[0].lower().startswith('th') else 5 if d[0].lower().startswith('sa') else 6 if d[0].lower().startswith('su') else {'m':0,'t':1,'w':2,'f':4}.get(d[0][0].lower())) for d in day_matches))
        # Room labels can precede/follow the lecture line. Do not read instructor offices.
        location=''
        for nearby in lines[max(0,n-8):n+3]:
            loc=re.match(r'(?:Class Location|Lecture Location|Lecture)\s*:\s*(.+)',nearby,re.I)
            if loc and not INTERVAL.search(loc[1]):location=loc[1]
        tail=line[match.end():].strip(' ;,')
        if tail:location=re.sub(r'^in\s+','',tail,flags=re.I)
        if not location and n+1<len(lines) and re.search(r'Building|Hall|Room',lines[n+1],re.I) and not re.search(r'Office|Instructor',lines[n+1],re.I):location=lines[n+1]
        slots.append(dict(weekdays=weekdays,start_time=start,end_time=end,location=location,evidence=line))
    unique={(tuple(s['weekdays']),s['start_time'],s['end_time']):s for s in slots}
    return list(unique.values())

def calendar_terms(html):
    soup=BeautifulSoup(html,'html.parser')
    heading=next((h for h in soup.find_all(['h1','h2','h3','h4']) if 'All programs except Doctor of Veterinary Medicine and Bachelor of Education' in h.get_text(' ',strip=True)),None)
    if not heading:raise ValueError('General-program academic calendar was not found.')
    table=heading.find_next('table');result={};key=None;month=None;year=None
    for row in table.find_all('tr'):
        cells=[c.get_text(' ',strip=True) for c in row.find_all(['td','th'])]
        if not cells:continue
        first=cells[0];body=' '.join(cells[1:]);alltext=' '.join(cells)
        term=re.search(r'(FALL|WINTER) SEMESTER.*?(20\d{2})',first,re.I)
        summer=re.search(r'(FIRST|SECOND) SUMMER SESSION\s+(20\d{2})',first,re.I)
        if term:
            key=term[1][0].upper()+term[2];result[key]={'excluded_dates':[]};month=None;continue
        if summer:
            key='S'+summer[2]+'-'+summer[1].lower();result[key]={'excluded_dates':[]};continue
        mon=re.fullmatch(r'([A-Za-z]+)\s+(20\d{2})',first)
        if mon and mon[1].capitalize() in list(calendar.month_name):month=list(calendar.month_name).index(mon[1].capitalize());year=int(mon[2]);continue
        day=re.match(r'^(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\b',first)
        if not key or not month or not day:continue
        start=date(year,month,int(day[1]));end=date(year,month,int(day[2] or day[1]))
        if 'No classes' in body or 'No Classes' in body:
            result[key]['excluded_dates'] += [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
        if re.search(r'^(?:First Summer Session |Second Summer Session )?Classes Begin\.?$',body,re.I):result[key]['start']=start.isoformat()
        if re.search(r'Final Day of (?:Fall Semester|Winter Semester|First Summer Session|Second Summer Session) [Cc]lasses',body):result[key]['end']=start.isoformat()
    return {k:v for k,v in result.items() if v.get('start') and v.get('end')}

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(path)

def refresh(library,root,html=None):
    library,root=Path(library),Path(root);active=semester.current();term=active['term'];issues=[]
    cache=root/'academic-dates.json';terms={}
    try:
        if html is None:
            response=requests.get(CALENDAR_URL,timeout=20);response.raise_for_status();html=response.text
        terms=calendar_terms(html)
        if not terms:raise ValueError('No class dates found')
        atomic(cache,{'terms':terms,'checked':date.today().isoformat()})
    except (requests.RequestException,ValueError):
        if cache.exists():terms=json.loads(cache.read_text()).get('terms',{})
        issues.append({'course':'University calendar','reason':'Could not refresh academic dates; using saved dates where available.'})
    legacy_path=root/'classes.json';legacy=json.loads(legacy_path.read_text()) if legacy_path.exists() else {}
    old={str(c['course_id']):c for c in legacy.get('courses',[])} if semester.term_from_name(legacy.get('term',''))==term else {}
    configs=[];found=[]
    for cid,code,name,_,_ in semester.catalog(library):
        base=library/('course-'+cid);manifest=json.loads((base/'manifest.json').read_text());candidates=[];incomplete=False
        for url,f in manifest.get('files',{}).items():
            path=(base/f['path']).resolve()
            if not path.is_relative_to(base.resolve()) or not path.is_file():continue
            if not re.search(r'outline|syllabus',path.name+' '+f.get('title',''),re.I):continue
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            try:text='\n'.join(t for _,t in extract(path))
            except Exception:incomplete=True;continue
            document_term=semester.term_from_name(text[:4000])
            if document_term and document_term!=term:continue
            slots=lecture_slots(text)
            if not slots:incomplete=True
            for slot in slots:candidates.append(dict(slot,course_id=cid,code=code,path=f['path'],sha256=sha,source_url=url))
            # Retain explicit user confirmation only while that exact supporting file is unchanged.
            previous=old.get(cid)
            if not slots and previous and previous.get('sha256')==sha and previous.get('path')==f['path'] and previous.get('time_confirmed_by_user'):
                candidates.append(previous);incomplete=False
        unique={(tuple(c['weekdays']),c['start_time'],c['end_time']):c for c in candidates}
        bounds=terms.get(term)
        if term.startswith('S'):bounds=None # Session membership must be explicit; no full-summer guessing.
        if len(unique)!=1 or incomplete or not bounds:
            issues.append({'course':code,'reason':'Lecture times are missing or conflicting.' if len(unique)!=1 or incomplete else 'Confirmed class dates for this semester/session are not available.'});continue
        config=dict(bounds,courses=[next(iter(unique.values()))],term=active['label'],timezone='America/Halifax',calendar_source=CALENDAR_URL,verified_on=date.today().isoformat())
        configs.append(config);found.append(code)
    report={'term':term,'checked_at':datetime.now().astimezone().isoformat(),'courses':found,'issues':issues,'configs':configs}
    atomic(root/('schedule-'+term+'.json'),report)
    return report

def report(root):
    path=Path(root)/('schedule-'+semester.current()['term']+'.json')
    return json.loads(path.read_text()) if path.exists() else {'courses':[],'issues':[],'checked_at':None}

def events(root,library):
    saved=report(root)
    if saved.get('checked_at'):
        return [e for config in saved.get('configs',[]) for e in class_events(None,library,config=config)]
    # Preserve existing verified schedule until the first update discovers the active term.
    return class_events(Path(root)/'classes.json',library)
