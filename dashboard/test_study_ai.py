import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from study_ai import StudyAI
import semester
from chatgpt_connection import StudyError


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.course = self.root/'library/course-27641'
        self.course.mkdir(parents=True)
        self.text = 'A tree is a connected graph containing no cycles. A forest contains no cycles. The assignment was originally due Friday but now due Monday.'
        (self.course/'notes.txt').write_text(self.text)
        self.manifest = {'course_name':semester.current()['term']+' Demo (MATH-2420-01)', 'files':{'https://example.test/notes':{
            'path':'notes.txt','title':'Trees','sha256':hashlib.sha256(self.text.encode()).hexdigest(),
            'verified_in_last_run':True}}}
        self.save()
        self.connection = Mock()
        self.connection.complete.return_value = json.dumps({'answer':'A tree is connected and has no cycles. [S1]',
            'evidence':[{'source_id':'S1','quote':'A tree is a connected graph containing no cycles.'}], 'uncertainties':[]})
        self.service = StudyAI(self.root/'library', self.root/'study', self.connection)
        self.ids = [s['id'] for s in self.service.catalog()['sources']]

    def save(self):
        (self.course/'manifest.json').write_text(json.dumps(self.manifest))

    def test_answer_has_exact_quote_and_source_link(self):
        answer = self.service.ask('What is a tree?',self.ids,'demo')
        self.assertEqual(answer['evidence'][0]['quote'],self.text.split(' A forest')[0])
        self.assertTrue(answer['evidence'][0]['file_url'].startswith('/file?'))
        self.assertEqual(len(self.service.history()),1)

    def test_fabricated_quote_not_saved(self):
        self.connection.complete.return_value=json.dumps({'answer':'Invented [S1]', 'evidence':[{'source_id':'S1','quote':'This is not in the document at all.'}]})
        with self.assertRaises(StudyError): self.service.ask('Question',self.ids,'demo')
        self.assertEqual(self.service.history(),[])

    def test_unknown_citation_not_saved(self):
        self.connection.complete.return_value=json.dumps({'answer':'Claim [S2]', 'evidence':[{'source_id':'S1','quote':self.text[:48]}]})
        with self.assertRaises(StudyError): self.service.ask('Question',self.ids,'demo')

    def test_modified_file_never_sent(self):
        (self.course/'notes.txt').write_text('changed')
        with self.assertRaises(StudyError): self.service.ask('Question',self.ids,'demo')
        self.connection.complete.assert_not_called()

    def test_unverified_or_traversal_never_sent(self):
        item=next(iter(self.manifest['files'].values()))
        item['verified_in_last_run']=False; self.save()
        with self.assertRaises(StudyError): self.service.ask('Question',self.ids,'demo')
        item['verified_in_last_run']=True; item['path']='../../outside.txt'; self.save()
        with self.assertRaises(StudyError): self.service.ask('Question',self.ids,'demo')
        self.connection.complete.assert_not_called()

    def test_date_mode_is_preview_only(self):
        answer=self.service.ask('When is the assignment due?',self.ids,'demo','dates')
        self.assertTrue(answer['preview_only'])
        self.assertIn('preview only',self.connection.complete.call_args.args[1])
        self.assertIn('posted_at',self.connection.complete.call_args.args[2])

    def test_missing_and_excessive_selection(self):
        for ids in ([],['unknown'],self.ids*13):
            with self.assertRaises(StudyError): self.service.ask('Question',ids,'demo')
        self.connection.complete.assert_not_called()
