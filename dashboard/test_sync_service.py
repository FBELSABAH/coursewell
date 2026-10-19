import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from sync_service import SyncService, changed_files, LoginRequired


class SyncTests(unittest.TestCase):
    def test_history_survives_empty_sync_restart_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json';service=SyncService(path)
            change={'kind':'new','name':'Lecture.pdf','source':'https://moodle/file','course_id':'1','sha256':'one'}
            service.update(started_at='2026-10-01T12:00:00Z',changes=[change])
            service.update(changes=[change])
            self.assertEqual(len(service.snapshot()['change_history']),1)
            with patch('sync_service.threading.Thread'):
                service.start()
            service.update(changes=[],status='complete')
            reopened=SyncService(path)
            self.assertEqual(reopened.snapshot()['changes'],[])
            self.assertEqual(len(reopened.snapshot()['change_history']),1)
            reopened.update(status='error',changes=[])
            self.assertEqual(len(SyncService(path).snapshot()['change_history']),1)

    def test_later_version_adds_history_and_old_summary_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json'
            change={'kind':'new','name':'Lecture.pdf','source':'https://moodle/file','course_id':'1','sha256':'one'}
            path.write_text(json.dumps({'status':'complete','started_at':'first','changes':[change]}))
            service=SyncService(path)
            self.assertEqual(len(service.snapshot()['change_history']),1)
            service.update(started_at='second',changes=[dict(change,kind='changed',sha256='two')])
            self.assertEqual(len(SyncService(path).snapshot()['change_history']),2)

    def test_changes_ignore_unverified_and_detect_content_changes(self):
        old={'files':{'a':{'sha256':'1'},'b':{'sha256':'2'}}}
        new={'files':{'a':{'sha256':'1','path':'a','verified_in_last_run':True},'b':{'sha256':'3','path':'b','verified_in_last_run':True},'c':{'sha256':'4','path':'c','verified_in_last_run':False},'d':{'sha256':'5','path':'d','verified_in_last_run':True}}}
        self.assertEqual([(c['name'],c['kind']) for c in changed_files(old,new)],[('b','changed'),('d','new')])

    def test_restart_marks_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'status.json';p.write_text(json.dumps({'status':'running'}))
            self.assertEqual(SyncService(p).snapshot()['status'],'interrupted')

    def test_reject_overlapping_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=SyncService(Path(tmp)/'status.json')
            with patch('sync_service.threading.Thread') as thread:
                self.assertTrue(service.start())
                self.assertFalse(service.start())
                thread.assert_called_once()

    def test_login_required_is_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=SyncService(Path(tmp)/'status.json')
            with patch('sync_service.PROJECT',Path(tmp)),patch.object(service,'_sync',side_effect=LoginRequired('secret must not leak')):
                service._run(False)
            state=service.snapshot()
            self.assertEqual(state['status'],'login_required')
            self.assertNotIn('secret',json.dumps(state))

    def test_failure_message_does_not_expose_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=SyncService(Path(tmp)/'status.json')
            with patch('sync_service.PROJECT',Path(tmp)),patch.object(service,'_sync',side_effect=RuntimeError('token=SECRET')):
                service._run(False)
            self.assertEqual(service.snapshot()['status'],'error')
            self.assertNotIn('SECRET',json.dumps(service.snapshot()))

    def test_partial_failure_keeps_other_course_results(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);session_path=root/'session.json';session_path.write_text('{}')
            service=SyncService(root/'status.json')
            courses=[SimpleNamespace(name='2026F A',url='https://moodle/course/1',moodle_course_id='1'),SimpleNamespace(name='2026F B',url='https://moodle/course/2',moodle_course_id='2')]
            good=MagicMock();good.manifest={'files':{}};good.sync.return_value={'status':'complete','files_verified':2,'new':0,'updated':0,'unchanged':2,'errors':[],'not_downloaded':[]}
            with patch('sync_service._resolve_storage_state_path',return_value=session_path),patch('sync_service.session_from_state'),patch('sync_service.enrolled_courses',return_value=courses),patch('sync_service.CourseLibrary',side_effect=[RuntimeError('failed'),good]),patch('sync_service.publish',return_value=(root,{})),patch('sync_service.atomic_write'):
                service._sync(False,root)
            self.assertEqual(service.snapshot()['status'],'partial')
            self.assertEqual([c['status'] for c in service.snapshot()['courses']],['error','complete'])

    def test_last_fetch_survives_running_and_failed_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json';service=SyncService(path)
            service.update(status='complete',finished_at='2026-10-17T12:00:00Z',courses=[{'verified':4}],changes=[])
            with patch('sync_service.threading.Thread'):service.start()
            self.assertEqual(service.snapshot()['last_fetch']['courses'][0]['verified'],4)
            service.update(status='error',finished_at='2026-10-18T12:00:00Z')
            self.assertEqual(SyncService(path).snapshot()['last_fetch']['finished_at'],'2026-10-17T12:00:00Z')

if __name__=='__main__':
    unittest.main()
