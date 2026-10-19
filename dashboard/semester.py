"""Shared semester policy. Archives and account credentials are never removed."""
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import os
import re

PROJECT = Path(__file__).resolve().parents[1]
# Installed mirror shares the canonical course library and preferences.
DATA = Path(os.environ.get('COURSEWELL_DATA', '/Users/fbelsabah/Documents/academic-ai-assistant/data'))
NAMES = {'W':'Winter', 'S':'Summer', 'F':'Fall'}

def term_from_name(name):
    match = re.search(r'(?<![A-Za-z0-9])(?:(20\d{2})([WSF])|([WSF])(20\d{2}))(?![A-Za-z0-9])', name, re.I)
    if match:return (match[2] or match[3]).upper()+(match[1] or match[4])
    match = re.search(r'\b(Winter|Summer|Fall)\s+(20\d{2})\b',name,re.I)
    return match[1][0].upper()+match[2] if match else None

def current(today=None):
    today=today or datetime.now(ZoneInfo('America/Halifax')).date()
    automatic=('W' if today.month<5 else 'S' if today.month<9 else 'F')+str(today.year)
    path=DATA/'semester.json'
    saved=json.loads(path.read_text()) if path.exists() else {}
    term=saved.get('term','auto')
    if term=='auto':term=automatic
    if not re.fullmatch(r'[WSF]20\d{2}',term):raise ValueError('Invalid semester setting.')
    year=int(term[1:]);letter=term[0]
    bounds={'W':('01-01','04-30'),'S':('05-01','08-31'),'F':('09-01','12-31')}
    return {'term':term,'mode':saved.get('term','auto'),'label':NAMES[letter]+' '+str(year),
            'year':year,'start':str(year)+'-'+bounds[letter][0],'end':str(year)+'-'+bounds[letter][1]}

def save(term):
    if term!='auto' and (not isinstance(term,str) or not re.fullmatch(r'[WSF]20\d{2}',term)):
        raise ValueError('Choose Auto or a semester such as W2027.')
    DATA.mkdir(parents=True,exist_ok=True)
    path=DATA/'semester.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps({'term':term}));temp.chmod(0o600);temp.replace(path)
    return current()

def contains(day):
    s=current();return bool(day and s['start']<=day[:10]<=s['end'])

def manifests(library):
    term=current()['term']
    return sorted(p for p in Path(library).glob('course-*/manifest.json')
                  if term_from_name(json.loads(p.read_text()).get('course_name',''))==term)

def catalog(library=None):
    result=[]
    for p in manifests(library or DATA/'library'):
        m=json.loads(p.read_text());full=m['course_name'];cid=p.parent.name.removeprefix('course-')
        code=re.search(r'\b([A-Z]{2,8})[ -](\d{3,5})(?:-\d+)?\b',full)
        label=code[1]+' '+code[2] if code else full
        name=re.sub(r'^\s*(?:20\d{2}[WSF]|[WSF]20\d{2}|(?:Winter|Summer|Fall)\s+20\d{2})\s*','',full,flags=re.I)
        name=re.sub(r'\s*\([^)]*\)\s*$','',name)
        color=('coral','blue','purple','gold','green')[int(cid)%5] if cid.isdigit() else 'green'
        result.append((cid,label,name,'',color))
    return sorted(result,key=lambda row:row[1])

class CourseCodes(Mapping):
    def _values(self):return {c[0]:c[1] for c in catalog()}
    def __getitem__(self,key):return self._values()[key]
    def __iter__(self):return iter(self._values())
    def __len__(self):return len(self._values())

COURSES=CourseCodes()

def source_active(context,posted_at=None):
    context=json.loads(context) if isinstance(context,str) else (context or {})
    if context.get('semester'):return context['semester']==current()['term']
    if context.get('course_id'):return str(context['course_id']) in COURSES
    # Legacy undated mail stays reviewable; dated older mail stays archived.
    return contains(posted_at) if posted_at else True
