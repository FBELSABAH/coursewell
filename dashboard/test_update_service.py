import unittest
from unittest.mock import Mock,patch
from update_service import UpdateService

class UpdateTests(unittest.TestCase):
    def test_moodle_failure_does_not_prevent_outlook_or_calendar(self):
        moodle=Mock();moodle.snapshot.return_value={'status':'error'}
        outlook=Mock();outlook.status.return_value={'connected':True,'status':'complete'}
        discovery=Mock();discovery.settings.return_value={}
        calendar=Mock();calendar.status.return_value={'busy':False,'result':'complete'}
        service=UpdateService(moodle,outlook,discovery,calendar,'library');service.lock.acquire()
        with patch('update_service.collect_library',return_value={'issues':[]}):service.run(False)
        outlook.start.assert_called_once();calendar.start.assert_called_once()
        self.assertEqual(service.snapshot()['status'],'partial')
        self.assertFalse(service.lock.locked())
    def test_ai_failure_does_not_cancel_calendar(self):
        moodle=Mock();moodle.snapshot.return_value={'status':'complete'}
        outlook=Mock();outlook.status.return_value={'connected':False,'status':'not_connected'}
        discovery=Mock();discovery.settings.return_value={'auto_review':True,'model':'m'};discovery.review.side_effect=ValueError('quota')
        calendar=Mock();calendar.status.return_value={'busy':False,'result':'complete'}
        service=UpdateService(moodle,outlook,discovery,calendar,'library');service.lock.acquire()
        with patch('update_service.collect_library',return_value={'issues':[]}):service.run(False)
        calendar.start.assert_called_once();outlook.start.assert_not_called()
        self.assertIn('AI review',service.snapshot()['message'])
    def test_finished_fetch_does_not_hide_backlog_after_restart(self):
        moodle=Mock();moodle.snapshot.return_value={'status':'complete'}
        outlook=Mock();outlook.status.return_value={'status':'complete'}
        discovery=Mock();discovery.snapshot.return_value={'unreviewed':7,'busy':False}
        service=UpdateService(moodle,outlook,discovery,Mock(),'library')
        result=service.snapshot()
        self.assertEqual(result['status'],'partial')
        self.assertIn('7 AI sources still waiting',result['message'])
        self.assertEqual(moodle.snapshot()['status'],'complete')
