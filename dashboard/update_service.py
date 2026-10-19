"""One click, independent source outcomes. No source failure erases another's results."""
import threading
import time
from source_review import collect_library

class UpdateService:
    def __init__(self,moodle,outlook,discovery,calendar,library,schedule_refresh=None):
        self.moodle,self.outlook,self.discovery,self.calendar,self.library=moodle,outlook,discovery,calendar,library
        self.schedule_refresh=schedule_refresh
        self.lock=threading.Lock();self.state={'status':'idle','sources':{},'message':''}

    def snapshot(self):
        result=dict(self.moodle.snapshot())
        if self.state['status']!='idle':result.update(self.state)
        result['sources']={'moodle':self.moodle.snapshot(),'outlook':self.outlook.status()}
        review=self.discovery.snapshot()
        if isinstance(review,dict):
            result['ai_remaining']=review.get('unreviewed',0)
            result['ai_busy']=review.get('busy',False)
            result['fetch_status']=result['sources']['moodle'].get('status','idle')
            if self.state['status']=='idle' and result.get('status')=='complete':
                result['phase']='finished'
                result['message']='Fetching finished · '+('AI review in progress' if result['ai_busy'] else str(result['ai_remaining'])+' AI sources still waiting' if result['ai_remaining'] else 'AI review queue cleared')
                if result['ai_remaining'] or result['ai_busy']:result['status']='partial'
        phase=result.get('phase','fetching')
        moodle=result['sources']['moodle']
        if result.get('status')=='complete' and not result.get('ai_remaining') and not result.get('ai_busy'):
            progress=100
        elif phase=='fetching':
            total=moodle.get('total',0)
            progress=5+50*min(1,(moodle.get('completed',0)/total if total else 0))
            if result['sources']['outlook'].get('status') not in ('running','connecting'):progress+=5
        elif phase=='reading':progress=65
        elif phase=='reviewing':
            initial=self.state.get('review_total',0)
            remaining=review.get('unreviewed',initial)
            progress=70+20*max(0,min(1,(initial-remaining)/initial if initial else 0))
        elif phase=='calendar':progress=95
        else:progress=95 if result.get('status')=='partial' else 0
        result['progress']=round(progress)
        return result

    def start(self,reconnect=False):
        if not self.lock.acquire(False):return False
        self.state={'status':'running','message':'Fetching Moodle and Outlook…','phase':'fetching'}
        threading.Thread(target=self.run,args=(reconnect,),daemon=True).start();return True

    def run(self,reconnect):
        problems=[]
        try:
            self.moodle.start(reconnect)
            connected=self.outlook.status().get('connected',False)
            if connected:self.outlook.start()
            while self.moodle.snapshot()['status'] in ('running','connecting') or self.outlook.status()['status'] in ('running','connecting'):
                self.state['message']='Moodle: '+self.moodle.snapshot()['status']+' · Outlook: '+self.outlook.status()['status']
                time.sleep(.5)
            if self.moodle.snapshot()['status'] not in ('complete',):problems.append('Moodle needs attention')
            if connected and self.outlook.status()['status']!='complete':problems.append('Outlook needs attention')
            self.state.update(phase='reading',message='Fetching finished. Reading saved sources…')
            try:
                coverage=collect_library(self.library,self.discovery)
                if coverage['issues']:problems.append('Some documents could not be read')
            except Exception:problems.append('Source review collection needs retry')
            if self.schedule_refresh:
                try:self.schedule_refresh()
                except Exception:problems.append('Lecture schedule discovery needs retry')
            settings=self.discovery.settings()
            if settings.get('auto_review') and settings.get('model'):
                self.state.update(review_total=self.discovery.snapshot().get('unreviewed',0),phase='reviewing',message='Fetching finished. AI review in progress; saved files are ready to use.')
                try:
                    review=self.discovery.review(settings['model'])
                    if review.get('errors'):problems.append('Some sources still need AI review')
                except Exception:problems.append('AI review needs attention; fetching completed independently')
            self.state.update(phase='calendar',message='Fetching finished. Syncing calendar…')
            self.calendar.start()
            while self.calendar.status().get('busy'):time.sleep(.5)
            if self.calendar.status().get('result') in ('error','attention'):problems.append('Calendar needs attention')
            remaining=self.discovery.snapshot().get('unreviewed',0)
            if isinstance(remaining,int) and remaining:problems.append(f'{remaining} AI sources still waiting; review is not finished')
            self.state.update(phase='finished',status='partial' if problems else 'complete',message=' · '.join(problems) if problems else 'Fetching finished · AI review queue cleared · Calendar sync finished.')
        except Exception:self.state.update(status='error',message='Update interrupted. Saved source records are safe; retry.')
        finally:self.lock.release()
