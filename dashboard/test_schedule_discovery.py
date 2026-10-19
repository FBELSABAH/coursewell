import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import schedule_discovery as s

HTML='''<h2>UPEI Calendar Dates 2026-2027 (All programs except Doctor of Veterinary Medicine and Bachelor of Education)</h2><table>
<tr><td>FALL SEMESTER (SEPTEMBER–DECEMBER 2026)</td></tr>
<tr><td>SEPTEMBER 2026</td></tr><tr><td>8 Tuesday</td><td>Classes Begin</td></tr>
<tr><td>30 Wednesday</td><td>National Day. No classes</td></tr>
<tr><td>OCTOBER 2026</td></tr><tr><td>13-16 (Tues-Fri)</td><td>Mid-semester break. No classes.</td></tr>
<tr><td>DECEMBER 2026</td></tr><tr><td>9 Wednesday</td><td>*Final Day of Fall Semester Classes</td></tr>
<tr><td>23 Wednesday</td><td>End of Fall Semester. Grades due.</td></tr>
<tr><td>WINTER SEMESTER (JANUARY–MAY 2027)</td></tr><tr><td>JANUARY 2027</td></tr><tr><td>11 Monday</td><td>Classes Begin</td></tr><tr><td>APRIL 2027</td></tr><tr><td>13 Tuesday</td><td>Final Day of Winter Semester Classes</td></tr></table>
<h2>Doctor of Veterinary Medicine</h2><table><tr><td>1 Monday</td><td>Classes Begin</td></tr></table>'''

class ScheduleTests(unittest.TestCase):
    def test_days_shared_meridiem_and_room(self):
        row=s.lecture_slots('Office Hours: Monday 10:00–11:00am\nLecture Hours: Tue/Thu 8:30–9:45 am\nLecture: SDU Main Building 432')[0]
        self.assertEqual((row['weekdays'],row['start_time'],row['end_time'],row['location']),([1,3],'08:30','09:45','SDU Main Building 432'))
    def test_no_guessing_missing_end_or_meridiem(self):
        self.assertEqual(s.lecture_slots('Tue/Thurs 1:00 in Duffy 212\nOffice Hours: Tue/Thurs 12-1pm\nClass Time: Mon 2-3'),[])
    def test_plural_days(self):
        row=s.lecture_slots('Lecture Time: Mondays, Wednesdays, and Fridays, 2:30–3:20 PM')[0]
        self.assertEqual(row['weekdays'],[0,2,4]);self.assertEqual(row['start_time'],'14:30')
    def test_not_exam_or_office(self):
        self.assertEqual(s.lecture_slots('Exam Monday 10am-11am\nOffice Hours: Tuesday 10am-11am'),[])
    def test_general_calendar_range_year_and_class_end(self):
        terms=s.calendar_terms(HTML)
        self.assertEqual(terms['F2026']['end'],'2026-12-09')
        self.assertEqual(terms['F2026']['excluded_dates'],['2026-09-30','2026-10-13','2026-10-14','2026-10-15','2026-10-16'])
        self.assertEqual(terms['W2027']['start'],'2027-01-11')
        with self.assertRaises(ValueError):s.calendar_terms('<table>unrecognized</table>')
    def test_saved_sources_changed_are_held(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);library=root/'library';base=library/'course-1';base.mkdir(parents=True)
            file=base/'outline.txt';file.write_text('Fall 2026\nClass time: T/Th 2:30pm–3:45pm; Hall 211')
            (base/'manifest.json').write_text(json.dumps({'course_name':'2026F Example (TEST-1234-01)','files':{'https://example.edu/file':{'path':'outline.txt'}}}))
            with patch.object(s.semester,'current',return_value={'term':'F2026','label':'Fall 2026'}),patch.object(s,'extract',side_effect=lambda p:[(1,p.read_text())]):
                report=s.refresh(library,root/'calendar',HTML)
                self.assertEqual(report['courses'],['TEST 1234'])
                events=s.events(root/'calendar',library)
                self.assertTrue(events);self.assertFalse(any(e['date']=='2026-10-13' for e in events))
                file.write_text('Changed')
                self.assertTrue(all(e['review'] for e in s.events(root/'calendar',library)))
                report=s.refresh(library,root/'calendar',HTML)
                self.assertFalse(report['courses']);self.assertTrue(report['issues'])
    def test_conflicting_slots_not_silently_selected(self):
        self.assertEqual(len(s.lecture_slots('Class Time: Monday 10am-11am\nLecture Time: Monday 1pm-2pm')),2)

if __name__=='__main__':unittest.main()
