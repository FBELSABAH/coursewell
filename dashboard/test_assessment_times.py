import unittest
from assessments import candidates, announcement_is_explicit
from assessment_times import resolve_class_times
from calendar_service import identity, event_body

class AssessmentTimingTests(unittest.TestCase):
    def test_tentative_schedule_does_not_leak_into_class_session(self):
        rows=candidates('Tentative Testing Schedule:\nOctober 23 -- Midterm #1\nClass #15 - Midterm #1 Review (October 21)\nClass #16 - Midterm #1 (October 23)',2026,'Course page')
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[0]['tentative'])
        self.assertFalse(rows[1]['tentative'])
        self.assertTrue(rows[1]['class_session_confirmation'])
        self.assertTrue(candidates('Class #16 - tentative Midterm #1 (October 23)',2026,'Page')[0]['tentative'])

    def test_announcement_requires_scheduling_language(self):
        for text in ('The Midterm Exam will be held in class on October 20', 'We will have our Midterm Exam on October 20, during class time'):
            self.assertTrue(announcement_is_explicit({'date':'2026-10-20','evidence':text}))
        self.assertFalse(announcement_is_explicit({'date':'2026-10-20','evidence':'We discussed the exam on October 20'}))

    def test_moodle_instance_does_not_hide_other_dates(self):
        from moodle_dates import merge
        a=dict(course_id='1',title='In-class test',date='2026-10-22',sources=[])
        b=a|{'date':'2026-11-05'}
        result=merge({'events':[a,b]}, {'status':'complete','events':[a|{'source_uid':'one'}]})
        self.assertEqual(len(result['events']),2)
        self.assertEqual({x['date'] for x in result['events']},{'2026-10-22','2026-11-05'})

    def fixtures(self):
        event=dict(course_id='1',course='TEST',title='Midterm exam',date='2026-10-20',sources=[],class_time_reference=True)
        lecture=dict(course_id='1',date='2026-10-20',start_at='2026-10-20T14:30:00-03:00',end_at='2026-10-20T15:45:00-03:00',location='Room 2',sources=[{'evidence':'Tue 2:30–3:45'}])
        return event,lecture

    def test_verified_slot_and_honest_description(self):
        event,lecture=self.fixtures()
        resolved=resolve_class_times([event],[lecture])[0]
        body=event_body(resolved)
        self.assertEqual(body['start']['dateTime'],lecture['start_at'])
        self.assertIn('exact assessment start and duration are not specified',body['description'])
        self.assertNotIn('Exact time from Moodle',body['description'])
        self.assertNotIn('start_at',event)

    def test_no_slot_or_multiple_slots_requires_review(self):
        event,lecture=self.fixtures()
        for slots in ([],[dict(lecture,review=True)],[lecture,dict(lecture,start_at='2026-10-20T16:00:00-03:00')]):
            result=resolve_class_times([event],slots)[0]
            self.assertTrue(result['review'])
            self.assertNotIn('start_at',result)

    def test_does_not_override_explicit_times_or_uncertainty(self):
        event,lecture=self.fixtures()
        for change in ({'review':True},{'tentative':True},{'class_time_reference':False},{'conflict_dates':['2026-10-21']}):
            self.assertNotIn('start_at',resolve_class_times([event|change],[lecture])[0])
        self.assertEqual(resolve_class_times([event|{'start_at':'explicit'}],[lecture])[0]['start_at'],'explicit')

    def test_only_generic_occurrences_have_date_in_identity(self):
        event,_=self.fixtures()
        self.assertEqual(identity(event),identity(event|{'date':'2026-10-21'}))
        self.assertNotEqual(identity(event|{'title':'In-class test'}),identity(event|{'title':'In-class test','date':'2026-10-21'}))

if __name__=='__main__':unittest.main()
