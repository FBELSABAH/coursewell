import unittest
from review_destination import review_destination

class ReviewDestinationTests(unittest.TestCase):
    def test_unknown_exam_stays_coursework(self):
        self.assertEqual(review_destination({'kind':'deadline','title':'Final exam'}),'coursework')
    def test_club_signup_and_admin_dates_are_calendar(self):
        for title in ('Global Village performer sign-up','Last day to discontinue courses with a 50% refund','Networking workshop registration'):
            self.assertEqual(review_destination({'kind':'deadline','title':title}),'calendar')
    def test_future_course_and_project_remain_academic(self):
        self.assertEqual(review_destination({'kind':'deadline','title':'Seminar presentation','course_id':'future-course'}),'coursework')
        self.assertEqual(review_destination({'kind':'deadline','title':'Workshop assignment'}),'coursework')
    def test_explicit_optional_even_with_course_context(self):
        self.assertEqual(review_destination({'kind':'event','title':'Career talk','course_id':'future-course'}),'calendar')
    def test_change_never_becomes_optional(self):
        self.assertEqual(review_destination({'kind':'change','title':'Workshop moved'}),'coursework')
