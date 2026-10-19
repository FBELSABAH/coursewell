"""Incremental Outlook web collection using an isolated, user-authorized profile.

Search date windows, stable message IDs and a durable ledger make retries idempotent.
A failed/limited scan NEVER advances the last-success checkpoint.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from urllib.parse import quote
from zoneinfo import ZoneInfo
from chatgpt_connection import private_write
from document_reader import read_document
from assessments import extract

ZONE=ZoneInfo('America/Halifax')
ACCOUNT='fbelsabah@upei.ca'
COLLECTOR_VERSION=4


def search_window(state, now):
    previous=state.get('last_success')
    # One-day overlap handles Outlook indexing delays; IDs/cache avoid repeated AI usage.
    start=datetime.fromisoformat(previous).astimezone(ZONE)-timedelta(days=1) if previous else now.astimezone(ZONE)-timedelta(days=7)
    return start.date().isoformat(),(now.astimezone(ZONE)+timedelta(days=1)).date().isoformat()


def received_time(value):
    for pattern in ('%a %m/%d/%Y %I:%M %p','%m/%d/%Y %I:%M %p','%a %Y-%m-%d %I:%M %p'):
        try:return datetime.strptime(value.strip(),pattern).replace(tzinfo=ZONE).isoformat()
        except ValueError:pass
    try:
        value=datetime.fromisoformat(value)
        return value.isoformat() if value.tzinfo else None
    except ValueError:return None


class OutlookService:
    def __init__(self, root, discovery):
        self.root,self.discovery=Path(root),discovery
        self.lock=threading.Lock()
        self.state={'status':'not_connected','message':'Connect your university Outlook to check new mail.'}
        if (self.root/'status.json').exists():self.state.update(json.loads((self.root/'status.json').read_text()))
        if self.state['status'] in ('running','connecting'):self.state.update(status='interrupted',message='Outlook check was interrupted. Retry to resume.')

    def status(self):return dict(self.state)
    def update(self, **fields):
        self.state.update(fields);private_write(self.root/'status.json',self.state)

    def start(self, connect=False):
        if not self.lock.acquire(False):return False
        self.update(status='connecting' if connect else 'running',message='Opening university Outlook…',diagnostic='',error_type='',issues=[])
        threading.Thread(target=self.run,args=(connect,),daemon=True).start();return True

    def run(self, connect=False):
        try:self.collect(connect)
        except Exception as exc:
            self.update(status='error',message='Outlook could not finish. Reconnect or retry; the last successful checkpoint was kept.',
                        error_type=type(exc).__name__, diagnostic=str(exc)[:500])
        finally:self.lock.release()

    def read_attachments(self, page, part, mid):
        texts=[];notes=[]
        attachments=part.get_by_role('listbox',name='file attachments').get_by_role('option')
        total=attachments.count()
        for index in range(min(total,30)):
            card=attachments.nth(index);label=card.get_attribute('aria-label') or ''
            name=label.split(' Open ')[0].strip()
            # Never trust sender-controlled filenames as filesystem paths.
            filename=re.sub(r'[^\w. ()-]','_',Path(name).name)[:160] or 'attachment'
            folder=self.root/'attachments'/hashlib.sha256(mid.encode()).hexdigest()
            target=folder/(hashlib.sha256(label.encode()).hexdigest()[:12]+'-'+filename)
            try:
                if not target.exists():
                    card.get_by_role('button',name='More actions',exact=True).click()
                    with page.expect_download(timeout=45000) as pending:
                        page.get_by_role('menuitem',name='Download',exact=True).click()
                    download=pending.value
                    if download.failure():raise ValueError('Attachment download failed')
                    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                    temporary=target.with_name(target.name+'.partial')
                    download.save_as(str(temporary));temporary.chmod(0o600);temporary.replace(target)
                document=read_document(target,extract)
                notes.extend(name+': '+n for n in document['notes'])
                text='\n\n'.join(f'Page {n}: {t}' for n,t in document['pages'])
                if not text.strip():notes.append(name+': no readable text.')
                texts.append('Attachment: '+name+'\n'+text)
            except Exception:notes.append(name+': attachment could not be downloaded/read; retry.')
        if total>30:notes.append('More than 30 attachments; remaining attachments need review.')
        return texts,notes

    def collect(self, connect=False):
        from playwright.sync_api import sync_playwright
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        began=datetime.now(timezone.utc)
        scan_state=self.state if self.state.get('collector_version')==COLLECTOR_VERSION else {}
        start,end=search_window(scan_state,began)
        ledger_path=self.root/'ledger.json'
        ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() and scan_state else {}
        with sync_playwright() as pw:
            context=pw.chromium.launch_persistent_context(str(self.root/'browser'),headless=not connect,accept_downloads=True)
            try:
                page=context.pages[0] if context.pages else context.new_page()
                page.goto('https://outlook.office.com/mail/',wait_until='domcontentloaded',timeout=60000)
                nav=page.get_by_role('region',name='Navigation pane')
                try:nav.wait_for(state='visible',timeout=300000 if connect else 30000)
                except Exception:
                    self.update(status='login_required',message='Use Connect Outlook to renew Microsoft sign-in.');return
                if not nav.get_by_role('treeitem',name=ACCOUNT,exact=True).count():
                    self.update(status='wrong_account',connected=False,message='Use your '+ACCOUNT+' university account.');return
                self.update(connected=True,message='Checking mail received since '+start+'…')
                search=page.get_by_role('combobox',name='Search for email, meetings, files and more.')
                self.update(stage='date_search')
                # Wait for the hydrated search UI; early fills can be reset by Outlook.
                page.get_by_role('listbox',name=re.compile('^Message list')).wait_for(timeout=30000)
                for attempt in range(3):
                    try:
                        search.click()
                        search.fill('Coursewell')
                        search.press('Enter')
                        page.get_by_role('heading',name='Results',exact=True).wait_for(timeout=15000)
                        page.get_by_role('button',name=re.compile('^Search filters,')).click(timeout=5000)
                        page.get_by_role('combobox',name='Search in',exact=True).wait_for(timeout=5000)
                        break
                    except Exception:
                        if attempt==2:raise
                page.get_by_role('combobox',name='Search in',exact=True).select_option(label='All folders')
                page.get_by_role('textbox',name='Keywords',exact=True).fill('')
                for field,day in [('Start date of search',datetime.fromisoformat(start)),
                                  ('End date of search',datetime.fromisoformat(end)-timedelta(days=1))]:
                    page.get_by_role('combobox',name=field,exact=True).click()
                    picker=page.get_by_role('dialog',name='Calendar',exact=True)
                    wanted=f'{day.day}, {day.strftime("%B")}, {day.year}'
                    for _ in range(60):
                        choice=picker.get_by_role('button',name=wanted,exact=True)
                        if choice.count():choice.click();break
                        header=picker.get_by_role('grid').get_attribute('aria-label').split(',')[0]
                        current=datetime.strptime(header,'%B %Y')
                        direction='previous' if day<current else 'next'
                        picker.get_by_role('button',name=re.compile('^Go to '+direction+' month')).click()
                    else:raise ValueError('Date picker range unavailable')
                page.get_by_role('button',name='Search',exact=True).click()
                page.get_by_role('heading',name='Results',exact=True).wait_for(timeout=30000)
                self.update(stage='search_results')
                page.get_by_text('Searching...',exact=True).wait_for(state='hidden',timeout=30000)
                listing=page.get_by_role('listbox',name=re.compile('^Message list'))
                try:listing.wait_for(state='visible',timeout=15000)
                except Exception:
                    # An empty-result message is proof; absence of a list alone is not.
                    if page.get_by_text(re.compile('No results|We didn.t find anything')).count():
                        self.update(status='complete',last_success=began.isoformat(),checked_at=began.isoformat(),message='No new mail in the checked date window.',issues=[],collector_version=COLLECTOR_VERSION,diagnostic='',error_type='',coverage=f'All-folder date-filtered search from {start} through {end} (exclusive).');return
                    raise
                # Search scope changes replace the virtual list asynchronously.
                previous=None;stable=0
                for _ in range(20):
                    ids=listing.get_by_role('option').evaluate_all('(rows)=>rows.map(r=>r.getAttribute("data-convid"))')
                    stable=stable+1 if ids==previous else 0
                    if stable>=3:break
                    previous=ids;page.wait_for_timeout(500)
                self.update(stage='reading_messages')
                seen=set();saved=0;issues=[];finished=False
                # PageDown traverses the virtual list; End would skip intermediate rows.
                for batch in range(100):
                    rows=listing.get_by_role('option')
                    labels=rows.evaluate_all('(rows)=>rows.map(r=>({label:r.getAttribute("aria-label"),id:r.getAttribute("data-convid"),text:r.innerText}))')
                    fresh=[x for x in labels if x['label'] and x['id'] and x['id'] not in seen]
                    for item in fresh:
                        cid=item['id'];seen.add(cid)
                        label=re.sub(r' No conversations selected$','',item['label'])
                        stamp=hashlib.sha256(label.encode()).hexdigest()
                        if ledger.get(cid)==stamp:continue
                        row=listing.locator('[role=option][data-convid='+json.dumps(cid)+']')
                        # These folders are never university announcements.
                        if row.get_by_role('button',name=re.compile('^(Sent Items|Drafts|Deleted Items|Junk Email)$')).count():
                            ledger[cid]=stamp;continue
                        try:
                            row.click(timeout=10000)
                            page.wait_for_function('''label => {const h=document.querySelector('[aria-label="Reading Pane"] [role="heading"]');return h && h.textContent.trim() && label.includes(h.textContent.trim())}''',arg=label,timeout=15000)
                            pane=page.get_by_role('main',name='Reading Pane')
                            pane.get_by_role('document',name='Message body').first.wait_for(timeout=15000)
                            collapsed=pane.locator('[data-itempart-id] [aria-expanded="false"]')
                            for _ in range(min(collapsed.count(),30)):collapsed.first.click(timeout=3000)
                            title=pane.get_by_role('heading',level=3).first.inner_text()
                            parts=pane.locator('[data-itempart-id]')
                            parts.first.wait_for(state='attached',timeout=15000)
                            complete=True
                            if re.search(r'has attachments?',label,re.I):
                                try:pane.get_by_role('listbox',name='file attachments').first.wait_for(timeout=15000)
                                except Exception:raise ValueError('Expected attachments did not load')
                            for part in parts.all():
                                mid=part.get_attribute('data-itempart-id');bodies=part.get_by_role('document',name='Message body')
                                if not mid or bodies.count()!=1:raise ValueError('Incomplete conversation')
                                sender=part.locator('[aria-label^="From:"]').first.inner_text()
                                received=part.get_by_test_id('SentReceivedSavedTime').inner_text();published=received_time(received)
                                # Conversation search can include older replies; enforce the window locally too.
                                if published and not start<=datetime.fromisoformat(published).astimezone(ZONE).date().isoformat()<end:continue
                                text=bodies.inner_text();attachments,notes=self.read_attachments(page,part,mid)
                                if not published:notes.append('Message publication timezone/date could not be verified.')
                                if notes:issues.extend(notes);complete=False
                                source='https://outlook.office.com/mail/deeplink/read/'+quote(mid,safe='')
                                sid='outlook:'+hashlib.sha256(mid.encode()).hexdigest()
                                saved+=self.discovery.ingest(sid,'outlook',title,'From: '+sender+'\nReceived: '+received+'\n\n'+text+'\n\n'+'\n\n'.join(attachments),source,published,notes)
                            if complete:ledger[cid]=stamp
                            private_write(ledger_path,ledger)
                        except Exception as exc:
                            if not row.count():
                                seen.discard(cid)
                                continue
                            issues.append('A conversation could not be fully read; its checkpoint was retained for retry.')
                            self.update(diagnostic=type(exc).__name__+': '+str(exc)[:400])
                            page.screenshot(path=str(self.root/'last-error.png'))
                    # Revisit rows replaced during a search refresh before declaring completion.
                    current=listing.get_by_role('option').evaluate_all('(rows)=>rows.map(r=>r.getAttribute("data-convid"))')
                    if any(cid and cid not in seen for cid in current):continue
                    # Detect list scroll end, never equate one recycled viewport to completion.
                    dimensions=listing.evaluate('''el=>{const p=[el,...el.querySelectorAll('*')].find(p=>p.scrollHeight>p.clientHeight+2 && /auto|scroll/.test(getComputedStyle(p).overflowY));return p?{top:p.scrollTop,height:p.scrollHeight,client:p.clientHeight}:null}''')
                    if dimensions and dimensions['top']+dimensions['client']>=dimensions['height']-2:
                        finished=True;break
                    if not dimensions and all(cid in seen for cid in current):finished=True;break
                    listing.evaluate('''el=>{const p=[el,...el.querySelectorAll('*')].find(p=>p.scrollHeight>p.clientHeight+2 && /auto|scroll/.test(getComputedStyle(p).overflowY));if(p)p.scrollBy(0,Math.max(100,p.clientHeight*0.8))}''');page.wait_for_timeout(500)
                    if len(seen)>=250:break
                if not finished:issues.append('Search limit reached or list did not finish; next check retries the same date window.')
                state=dict(status='partial' if issues else 'complete',connected=True,
                    message=f'{saved} new or changed messages saved; {len(seen)} conversations checked.',
                    checked_at=began.isoformat(),issues=list(dict.fromkeys(issues)),
                    coverage=f'All-folder search from {start} through {end} (exclusive); sent, draft, deleted and junk results excluded. Opening mail may mark it read.')
                if not issues:state.update(last_success=began.isoformat(),collector_version=COLLECTOR_VERSION,diagnostic='',error_type='')
                self.update(**state)
            except Exception:
                page.screenshot(path=str(self.root/'last-error.png'))
                raise
            finally:context.close()
