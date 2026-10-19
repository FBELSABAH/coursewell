import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from discovery_service import DiscoveryService, StudyError

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=DiscoveryService(Path(self.tmp.name),Mock())
        self.text='Workshop takes place on November 20, 2026 at the Library.'
        self.service.ingest('mail:1','outlook','Workshop',self.text)
        self.item={'title':'Workshop','kind':'event','date':'2026-11-20','quote':self.text,'uncertainties':[]}
    def source(self):
        with self.service.db() as db:return dict(db.execute('SELECT * FROM sources').fetchone())
    def accept(self,item=None):self.service.accept_result(self.source(),json.dumps({'items':[item or self.item]}))
    def test_repeat_ingestion_preserves_skip(self):
        self.accept();pid=self.service.snapshot()['items'][0]['id'];self.service.decide(pid,'skip')
        self.assertFalse(self.service.ingest('mail:1','outlook','Workshop',self.text))
        self.accept();self.assertEqual(len(self.service.snapshot()['items']),1)
        self.assertEqual(self.service.snapshot()['items'][0]['decision'],'skipped')
    def test_bad_quote_rejected_atomically(self):
        with self.assertRaises(StudyError):self.accept(dict(self.item,quote='Invented evidence for an event.'))
        self.assertEqual(self.service.snapshot()['items'],[])
    def test_invalid_time_rejected(self):
        with self.assertRaises(StudyError):self.accept(dict(self.item,start_at='2026-11-20T10:00:00'))
    def test_changed_source_during_review_rejected(self):
        old=self.source();self.service.ingest('mail:1','outlook','Workshop',self.text+' Changed.')
        with self.assertRaises(StudyError):self.service.accept_result(old,json.dumps({'items':[self.item]}))
    def test_added_only_after_google_confirmation(self):
        self.accept();pid=self.service.snapshot()['items'][0]['id'];calendar=Mock()
        calendar.add_reviewed.side_effect=RuntimeError('offline')
        with self.assertRaises(RuntimeError):self.service.decide(pid,'add',calendar)
        self.assertEqual(self.service.snapshot()['items'][0]['decision'],'pending')
        calendar.add_reviewed.side_effect=None;calendar.add_reviewed.return_value={'id':'g','calendar_id':'c'}
        self.service.decide(pid,'add',calendar);self.service.decide(pid,'add',calendar)
        self.assertEqual(calendar.add_reviewed.call_count,2)
        self.service.ingest('mail:1','outlook','Workshop',self.text+' Changed.')
        self.assertEqual(self.service.snapshot()['items'][0]['decision'],'changed')
    def test_uncertain_and_change_proposals_not_written(self):
        for item in (dict(self.item,uncertainties=['Year unclear']),dict(self.item,kind='change')):
            self.accept(item)
        for row in self.service.snapshot()['items']:
            with self.assertRaises(ValueError):self.service.decide(row['id'],'add',Mock())
    def test_review_bounded_and_cached(self):
        self.service.connection.complete.side_effect=[json.dumps({'items':[self.item]}),json.dumps({'checks':[{'index':0,'supported':True,'reason':'The date is explicit.'}]})]
        self.assertEqual(self.service.review('model')['reviewed'],1)
        self.assertEqual(self.service.review('model')['reviewed'],0)
        self.assertEqual(self.service.connection.complete.call_count,2)

    def test_old_optional_deadline_is_calendar_only(self):
        self.service.events_provider=lambda:[]
        self.accept(dict(self.item, title='Workshop registration deadline',kind='deadline'))
        row=self.service.snapshot()['items'][0]
        self.assertEqual(row['review_destination'],'calendar')
        self.assertEqual(self.service.pending_events(),[])
        calendar=Mock();calendar.add_reviewed.return_value={'id':'g','calendar_id':'c'}
        self.service.decide(row['id'],'add',calendar)
        self.assertEqual(calendar.add_reviewed.call_args.args[0]['kind'],'Event')
        self.assertEqual(len(self.service.calendar_events()),1)

    def test_routing_does_not_bypass_uncertainty(self):
        self.service.events_provider=lambda:[]
        self.accept(dict(self.item,title='Workshop registration deadline',kind='deadline',uncertainties=['Date boundary unclear']))
        calendar=Mock()
        with self.assertRaises(ValueError):self.service.decide(self.service.snapshot()['items'][0]['id'],'add',calendar)
        calendar.add_reviewed.assert_not_called()

    def test_duplicate_optional_source_adds_only_one_calendar_event(self):
        self.accept()
        self.service.ingest('mail:2','outlook','Another newsletter',self.text)
        with self.service.db() as db:source=dict(db.execute("SELECT * FROM sources WHERE id='mail:2'").fetchone())
        self.service.accept_result(source,json.dumps({'items':[self.item]}))
        calendar=Mock();calendar.add_reviewed.return_value={'id':'g','calendar_id':'c'}
        for row in self.service.snapshot()['items']:self.service.decide(row['id'],'add',calendar)
        calendar.add_reviewed.assert_called_once()
        self.assertEqual(len(self.service.calendar_events()),1)

    def test_ignore_resolves_duplicate_suggestions(self):
        self.accept()
        self.service.ingest('mail:2','outlook','Another newsletter',self.text)
        with self.service.db() as db:source=dict(db.execute("SELECT * FROM sources WHERE id='mail:2'").fetchone())
        self.service.accept_result(source,json.dumps({'items':[self.item]}))
        self.service.decide(self.service.snapshot()['items'][0]['id'],'skip')
        self.assertTrue(all(i['decision']=='skipped' for i in self.service.snapshot()['items']))

    def test_add_resolves_duplicate_suggestions_immediately(self):
        self.accept()
        self.service.ingest('mail:2','outlook','Another newsletter',self.text)
        with self.service.db() as db:source=dict(db.execute("SELECT * FROM sources WHERE id='mail:2'").fetchone())
        self.service.accept_result(source,json.dumps({'items':[self.item]}))
        calendar=Mock();calendar.add_reviewed.return_value={'id':'g','calendar_id':'c'}
        self.service.decide(self.service.snapshot()['items'][0]['id'],'add',calendar)
        self.assertNotIn('pending',[i['decision'] for i in self.service.snapshot()['items']])
        calendar.add_reviewed.assert_called_once()
