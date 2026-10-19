import tempfile
import unittest
from unittest.mock import Mock
from discovery_service import DiscoveryService
from review_filter import needs_review

class TrackingTests(unittest.TestCase):
    def test_usage_and_missing_measurements_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            connection=Mock()
            def answer(*args,usage_callback=None):
                usage_callback({'input_tokens':100,'output_tokens':30,'input_tokens_details':{'cached_tokens':20},'output_tokens_details':{'reasoning_tokens':10}})
                return '{"items":[]}'
            connection.complete.side_effect=answer
            service=DiscoveryService(folder,connection)
            service.measured_complete('model','instructions','source')
            connection.complete.side_effect=RuntimeError('interrupted')
            with self.assertRaises(RuntimeError):service.measured_complete('model','instructions','source')
            usage=DiscoveryService(folder,connection).snapshot()['usage']
            self.assertEqual((usage['requests'],usage['measured_requests'],usage['failed_requests']),(2,1,1))
            self.assertEqual((usage['input_tokens'],usage['output_tokens'],usage['reasoning_tokens']),(100,30,10))
    def test_no_requests_for_filtered_or_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            connection=Mock();connection.complete.return_value='{"items":[]}'
            service=DiscoveryService(folder,connection)
            service.ingest('lecture','moodle','Lecture 2','We test hypotheses using examples.')
            service.ingest('notice','moodle','Assignment 2','Due tomorrow.')
            service.review('model');service.review('model')
            self.assertEqual(connection.complete.call_count,1)
            self.assertEqual(service.snapshot()['filtered'],1)
            self.assertEqual(service.snapshot()['unreviewed'],0)
    def test_filter_preserves_real_signals(self):
        for title,text in [('Lecture','Quiz on Friday.'),('Lecture','Deadline extended.'),('Syllabus','Dates TBD'),('Assignment 2','See Moodle'),('Lecture','Submit tomorrow.')]:
            self.assertTrue(needs_review(title,text),(title,text))
        self.assertTrue(needs_review('Notice','No date yet',url='/mod/forum/view.php'))
        self.assertTrue(needs_review('Lecture','Unclear',context={'needs_review':True}))
        self.assertFalse(needs_review('Lecture','Test statistics and assignment operators.'))
