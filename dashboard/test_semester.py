import json
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch, Mock
import semester
from calendar_service import preview
from study_ai import StudyAI
from update_service import UpdateService

class SemesterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name)
        p=patch.object(semester,'DATA',self.data);p.start();self.addCleanup(p.stop)
    def manifest(self,cid,name):
        path=self.data/'library'/('course-'+cid)/'manifest.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'course_name':name,'files':{}}));return path
    def test_auto_year_and_boundaries(self):
        for day,term in [('2026-12-31','F2026'),('2027-01-01','W2027'),('2027-05-01','S2027'),('2027-09-01','F2027')]:
            self.assertEqual(semester.current(date.fromisoformat(day))['term'],term)
    def test_manual_override_and_validation(self):
        semester.save('W2027');self.assertEqual(semester.current()['label'],'Winter 2027')
        self.assertTrue(semester.contains('2027-04-30'));self.assertFalse(semester.contains('2026-10-08'))
        for invalid in ('../x','F27',None,'X2027'):
            with self.assertRaises(ValueError):semester.save(invalid)
        self.assertEqual(semester.current()['term'],'W2027')
    def test_discovery_formats_and_archives(self):
        old=self.manifest('10','2026F Old Course (AMS-1910-01)')
        new=self.manifest('20','2027W New Course (CS-3000-01)')
        self.manifest('30','Winter 2027 Other (MATH-2000-01)')
        self.manifest('40','Unknown semester')
        semester.save('W2027')
        self.assertEqual({c[0] for c in semester.catalog()},{'20','30'})
        self.assertEqual(semester.COURSES['20'],'CS 3000')
        self.assertTrue(old.exists());self.assertTrue(new.exists())
        semester.save('F2026');self.assertEqual(list(semester.COURSES),['10'])
    def test_review_does_not_reuse_old_course_or_mail(self):
        self.manifest('10','2026F Course (AMS-1910-01)');self.manifest('20','2027W Course (CS-3000-01)')
        semester.save('W2027')
        self.assertFalse(semester.source_active({'course_id':'10'}))
        self.assertTrue(semester.source_active({'course_id':'20'}))
        self.assertFalse(semester.source_active({},'2026-10-01T10:00:00Z'))
        self.assertTrue(semester.source_active({},'2027-01-20T10:00:00Z'))
    def test_calendar_accepts_new_term_and_holds_old(self):
        semester.save('W2027')
        def event(day):return {'id':day,'course_id':'20','title':'Quiz '+day,'date':day,'kind':'Assessment','review':False,'sources':[{'evidence':'Quiz on '+day}]}
        plan=preview([event('2027-02-02'),event('2027-05-02')],today=date(2027,1,1))
        self.assertEqual(len(plan['ready']),1);self.assertEqual(len(plan['held']),1)
    def test_study_empty_term_does_not_open_old_course(self):
        self.manifest('10','2026F Course (AMS-1910-01)');semester.save('W2027')
        self.assertEqual(StudyAI(self.data/'library',self.data/'study',Mock()).catalog()['sources'],[])

    def test_review_queue_is_scoped_without_deleting_archives(self):
        from discovery_service import DiscoveryService
        self.manifest('10','2026F Course (AMS-1910-01)');self.manifest('20','2027W Course (CS-3000-01)')
        connection=Mock();connection.status.return_value={}
        service=DiscoveryService(self.data/'review',connection,lambda:[])
        semester.save('F2026')
        service.ingest('old','moodle','Exam announcement','Exam on October 20, 2026.',context={'course_id':'10'})
        semester.save('W2027')
        service.ingest('new','moodle','Exam announcement','Exam on February 20, 2027.',context={'course_id':'20'})
        self.assertEqual(service.snapshot()['unreviewed'],1)
        with service.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],2)
        semester.save('F2026');self.assertEqual(service.snapshot()['unreviewed'],1)

    def test_assessment_caches_are_separate(self):
        from assessments import feed
        self.manifest('10','2026F Course (AMS-1910-01)');self.manifest('20','2027W Course (CS-3000-01)')
        for term in ('F2026','W2027'):
            semester.save(term);feed(self.data/'library',self.data/'runtime')
            self.assertTrue((self.data/'runtime'/term/'assessments.json').exists())
        self.assertTrue((self.data/'runtime'/'F2026'/'assessments.json').exists())

class ProgressTests(unittest.TestCase):
    def test_progress_advances_and_partial_never_claims_complete(self):
        moodle=Mock();outlook=Mock();review=Mock()
        moodle.snapshot.return_value={'status':'running','total':5,'completed':0}
        outlook.status.return_value={'status':'complete'}
        review.snapshot.return_value={'unreviewed':10,'busy':False}
        service=UpdateService(moodle,outlook,review,Mock(),Path('/tmp'))
        service.state={'status':'running','phase':'fetching'}
        values=[service.snapshot()['progress']]
        moodle.snapshot.return_value['completed']=5;values.append(service.snapshot()['progress'])
        service.state['phase']='reading';values.append(service.snapshot()['progress'])
        service.state.update(phase='reviewing',review_total=10);review.snapshot.return_value['unreviewed']=5;values.append(service.snapshot()['progress'])
        service.state['phase']='calendar';values.append(service.snapshot()['progress'])
        service.state.update(status='partial',phase='finished');self.assertLess(service.snapshot()['progress'],100)
        service.state['status']='complete';review.snapshot.return_value['unreviewed']=0;values.append(service.snapshot()['progress'])
        self.assertEqual(values,sorted(values));self.assertEqual(values[-1],100)
