import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from ai_review import assess, chunks, signature, explicit_date
from discovery_service import DiscoveryService
from outlook_service import search_window, received_time
from datetime import datetime, timezone

class SecondReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.event={'id':'quiz2','course_id':'27641','course':'MATH 2420','title':'Quiz 2','date':None,'review':True,'kind':'Assessment','sources':[]}
        self.events=[self.event]
        self.service=DiscoveryService(Path(self.tmp.name),Mock(),lambda:self.events)
        self.text='Quiz 2 is on November 20, 2026. Please revise chapters 3 and 4.'
        self.service.ingest('s','moodle','Announcement',self.text,posted_at='2026-10-18T10:00:00-03:00',context={'course_id':'27641'})
        with self.service.db() as db:self.source=dict(db.execute('SELECT * FROM sources').fetchone())
        self.item={'title':'Quiz 2','kind':'deadline','target_id':'quiz2','course_id':'27641','date':'2026-11-20','quote':self.text,'uncertainties':[],'verified':True}
    def accept(self,item=None):
        self.service.accept_result(self.source,json.dumps({'items':[item or self.item]}))
    def test_supported_resolution_preserves_identity(self):
        self.accept();self.assertEqual(self.service.apply_academic(),1)
        result=self.service.overlay(self.events)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['id'],'quiz2');self.assertTrue(result[0]['ai_reviewed'])
        self.assertEqual(result[0]['date'],'2026-11-20');self.assertFalse(result[0]['review'])
    def test_verbatim_but_wrong_date_is_not_auto_applied(self):
        self.accept(dict(self.item,date='2026-11-21'))
        self.assertEqual(self.service.apply_academic(),0)
    def test_verifier_rejection_blocks_automatic_write(self):
        self.accept(dict(self.item,verified=False));self.assertEqual(self.service.apply_academic(),0)
    def test_newer_user_correction_invalidates_saved_overlay(self):
        self.accept();self.service.apply_academic()
        self.event.update(date='2026-11-25',confirmed_by_user=True)
        self.assertEqual(self.service.overlay(self.events)[0]['date'],'2026-11-25')
    def test_source_change_invalidates_saved_overlay(self):
        self.accept();self.service.apply_academic()
        self.service.ingest('s','moodle','Announcement',self.text+' Revised.',context={'course_id':'27641'})
        self.assertIsNone(self.service.overlay(self.events)[0]['date'])
    def test_structured_moodle_cannot_be_silently_replaced(self):
        self.event['source_type']='moodle_calendar';self.accept();self.assertEqual(self.service.apply_academic(),0)
    def test_cross_source_conflict_blocks_both(self):
        self.accept()
        other='Quiz 2 is on November 21, 2026.'
        self.service.ingest('s2','moodle','Other',other,context={'course_id':'27641'})
        with self.service.db() as db:source=dict(db.execute("SELECT * FROM sources WHERE id='s2'").fetchone())
        self.service.accept_result(source,json.dumps({'items':[dict(self.item,date='2026-11-21',quote=other)]}))
        self.assertEqual(self.service.apply_academic(),0)
    def test_email_course_guess_not_trusted(self):
        self.source['context']='{}';self.source['kind']='outlook'
        self.assertTrue(assess(self.item,self.source,self.events)['reasons'])
    def test_optional_events_never_auto_applied(self):
        self.accept(dict(self.item,kind='event',title='Campus workshop'));self.assertEqual(self.service.apply_academic(),0)
    def test_long_documents_fully_covered(self):
        text='a'*19000+'Quiz 2 is on November 20, 2026.'+'b'*31000
        parts=chunks(text);self.assertGreater(len(parts),2)
        self.assertTrue(any('Quiz 2 is on November 20, 2026.' in p for p in parts))
        self.assertEqual(parts[0]+''.join(p[1200:] for p in parts[1:]),text)
    def test_failed_section_not_marked_reviewed(self):
        self.service.connection.complete.side_effect=RuntimeError('quota')
        report=self.service.review('m')
        self.assertTrue(report['errors']);self.assertEqual(self.service.snapshot()['unreviewed'],1)
    def test_confirm_targets_existing_event_without_duplicate(self):
        self.accept(dict(self.item,verified=False));pid=self.service.snapshot()['items'][0]['id']
        self.service.confirm_academic(pid,'27641','quiz2','2026-11-20')
        result=self.service.overlay(self.events)
        self.assertEqual(len(result),1);self.assertTrue(result[0]['confirmed_by_user'])
    def test_checkpoint_overlap_and_first_run(self):
        now=datetime(2026,10,18,12,tzinfo=timezone.utc)
        self.assertEqual(search_window({},now),('2026-10-11','2026-10-19'))
        self.assertEqual(search_window({'last_success':'2026-10-17T12:00:00+00:00'},now),('2026-10-16','2026-10-19'))
        self.assertEqual(received_time('Fri 10/16/2026 7:51 PM'),'2026-10-16T19:51:00-03:00')
        self.assertIsNone(received_time('Yesterday'))

if __name__=='__main__':unittest.main()

class PrivacyTests(unittest.TestCase):
    def test_personal_health_mail_is_not_queued_for_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            service=DiscoveryService(folder,Mock())
            service.ingest('private','outlook','Follow-up','From: Health Centre\nPrivate appointment details.')
            self.assertEqual(service.snapshot()['unreviewed'],0)
            service.review('test')
            service.connection.complete.assert_not_called()
    def test_health_topic_course_material_remains_eligible(self):
        with tempfile.TemporaryDirectory() as folder:
            service=DiscoveryService(folder,Mock())
            service.ingest('course','moodle','Community health assignment','Assignment due November 20.')
            self.assertEqual(service.snapshot()['unreviewed'],1)

class HistoricalSourceTests(unittest.TestCase):
    def test_month_day_cannot_override_explicit_old_year(self):
        self.assertFalse(explicit_date('Exam October 28, 2024','2026-10-28'))
        self.assertTrue(explicit_date('Exam October 28, 2026','2026-10-28'))

class ReviewIsolationTests(unittest.TestCase):
    def test_invalid_source_does_not_block_remaining_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            connection=Mock()
            connection.complete.side_effect=[json.dumps({'items':[{'kind':'event','title':''}]}),json.dumps({'items':[]})]
            service=DiscoveryService(folder,connection)
            service.ingest('good','moodle','Good source','No upcoming assessments in this document.',context={'needs_review':True})
            service.ingest('bad','moodle','Ambiguous source','A date that could not be interpreted.',context={'needs_review':True})
            result=service.review('test')
            self.assertEqual(result['reviewed'],1)
            self.assertEqual(len(result['errors']),1)
            self.assertEqual(service.snapshot()['unreviewed'],1)
