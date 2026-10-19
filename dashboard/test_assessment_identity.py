import unittest
from copy import deepcopy
from assessment_identity import reconcile


def event(title, day, **kw):
    return dict(id=title+str(day), course_id='1', title=title, date=day, kind='Assessment', sources=[], **kw)

class IdentityTests(unittest.TestCase):
    def test_pending_ai_does_not_create_duplicates_or_override_date(self):
        rows=[event('Concept deck','2026-10-08'),event('Concept Deck submission','2026-10-09',proposal_id='p',review=True)]
        before=deepcopy(rows)
        result=reconcile(rows)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['date'],'2026-10-08')
        self.assertEqual(result[0]['related_proposal_ids'],['p'])
        self.assertEqual(rows,before)

    def test_real_conflict_becomes_one_undated_review_item(self):
        rows=[event('Assignment 2','2026-10-08'),event('Assignment 2','2026-10-09')]
        result=reconcile(rows)
        self.assertEqual(len(result),1)
        self.assertIsNone(result[0]['date'])
        self.assertEqual(result[0]['conflict_dates'],['2026-10-08','2026-10-09'])
        self.assertTrue(result[0]['review'])

    def test_submission_is_not_presentation_window(self):
        rows=[event('Technical presentation',d,review=True) for d in ('2026-10-28','2026-10-29','2026-11-05')]
        rows[0]['sources']=[{'evidence':'Midterm Technical Presentation — submitted Oct 28 with presentations Oct 29–Nov 5'}]
        result=reconcile(rows)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['date'],'2026-10-28')
        self.assertEqual(result[0]['presentation_dates'],['2026-10-29','2026-11-05'])
        self.assertTrue(result[0]['review']) # identity resolution does not bypass safety

    def test_distinct_courses_numbered_tests_and_classes_survive(self):
        rows=[event('Quiz 1','2026-10-08'),event('Quiz 2','2026-10-08'),event('In-class test','2026-10-08'),event('In-class test','2026-10-15')]
        other=event('Quiz 1','2026-10-08');other['course_id']='2';rows.append(other)
        self.assertEqual(len(reconcile(rows)),5)

    def test_confirmed_date_beats_unconfirmed_mentions(self):
        rows=[event('Final exam','2026-12-01'),event('Final exam','2026-12-02',confirmed_by_user=True)]
        self.assertEqual(reconcile(rows)[0]['date'],'2026-12-02')

    def test_unknown_date_does_not_conflict_with_known_date(self):
        rows=[event('Midterm exam',None,review=True),event('Midterm exam','2026-10-20')]
        result=reconcile(rows)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['date'],'2026-10-20')

    def test_range_without_submission_never_invents_group_slot(self):
        result=reconcile([event('Final presentation','2026-11-26'),event('Stakeholder presentation','2026-12-03')])
        self.assertEqual(len(result),1)
        self.assertIsNone(result[0]['date'])

    def test_explicit_submission_sentence_resolves_only_multi_date_flag(self):
        text='Midterm Technical Presentation — submitted Oct 28 with presentations Oct 29–Nov 5'
        rows=[event('Technical presentation',d,review=True,evidence=text) for d in ('2026-10-28','2026-10-29','2026-11-05')]
        for e in rows:e['sources']=[{'evidence':text}]
        result=reconcile(rows)[0]
        self.assertEqual(result['date'],'2026-10-28')
        self.assertFalse(result['review'])
        rows[0]['sources'][0]['extraction_notes']=['OCR incomplete']
        self.assertTrue(reconcile(rows)[0]['review'])
