import semester
"""Collect verified course text for bounded interpretation without rereading unchanged files."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from assessments import extract
from review_filter import needs_review
from document_reader import read_document


def collect_library(library, discovery):
    added=0;issues=[]
    events=discovery.events_provider()
    for manifest_path in semester.manifests(library):
        manifest=json.loads(manifest_path.read_text());base=manifest_path.parent.resolve()
        for group in ('pages','files'):
            for url,item in manifest.get(group,{}).items():
                if not item.get('verified_in_last_run'):continue
                matching=[e for e in events if any(s.get('url')==url for s in e.get('sources',[]))]
                announcement='announcement' in item.get('title','').lower() or '/mod/forum/' in url
                if matching and not announcement and all(not (e.get('review') or e.get('tentative') or e.get('conflict_dates') or not e.get('date')) for e in matching):continue
                path=(base/item.get('path','')).resolve()
                if not path.is_relative_to(base) or not path.is_file():continue
                # Version/hash coverage is tracked independently of AI success.
                if path.stat().st_size>100*1024*1024:
                    issues.append(path.name+': exceeds reader size limit');continue
                with path.open('rb') as stream:fingerprint=hashlib.file_digest(stream,'sha256').hexdigest()
                if fingerprint!=item.get('sha256'):continue
                sid='moodle:'+hashlib.sha256((base.name+url).encode()).hexdigest()
                cache=discovery.root/'reader-cache'/(fingerprint+'-v3.json')
                try:
                    document=json.loads(cache.read_text()) if cache.exists() else None
                    if not document or any('failed' in n or 'unavailable' in n for n in document['notes']):
                        document=read_document(path,extract)
                        from chatgpt_connection import private_write
                        private_write(cache,document)
                    text='\n\n'.join(f'Page {n}:\n{t}' for n,t in document['pages'])
                    # Only date-bearing material needs the interpretation pass.
                    import re
                    if not announcement and not matching and not needs_review(item.get('title',''),text,url=url):continue
                    notes=document['notes'][:]
                    added+=discovery.ingest(sid,'moodle',item.get('title') or path.name,text,url,
                           item.get('posted_at') or item.get('published_at'),notes,
                           context={'semester':semester.current()['term'],'course_id':base.name.removeprefix('course-'),'course':manifest.get('course_name',''),
                           'needs_review':bool(matching),'file_url':'/file?'+urlencode({'course':base.name.removeprefix('course-'),'path':item['path']})})
                except Exception:issues.append(path.name+': reading failed; original retained.')
    return {'added':added,'issues':issues}
