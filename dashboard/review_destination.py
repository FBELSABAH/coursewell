"""Route review decisions without coupling the UI to a semester or course list.

Routing never authorizes a write. Existing evidence/date/confirmation gates remain.
Unknown coursework stays academic rather than becoming an optional calendar event.
"""
import re


def review_destination(item):
    if item.get('kind') == 'event':
        return 'calendar'
    plan = item.get('application') or {}
    if item.get('course_id') or plan.get('course_id') or item.get('target_id') or plan.get('target_id'):
        return 'coursework'
    # Older reviews used "deadline" for university-wide dates and club sign-ups.
    # Only clearly non-course titles are routed; ambiguous deadlines remain coursework.
    title = item.get('title', '')
    academic = r'\b(exam|midterm|quiz|test|assignment|homework|coursework|assessment|project|presentation)\b'
    optional = r'\b(workshop|seminar|networking|club|performer|volunteer|social|refund|tuition|scholarship)\b|\b(?:discontinue|withdraw from) courses\b'
    if item.get('kind') == 'deadline' and not re.search(academic, title, re.I) and re.search(optional, title, re.I):
        return 'calendar'
    return 'coursework'
