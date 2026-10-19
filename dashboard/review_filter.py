"""Conservative scheduling filter. Never discard announcements or explicit ambiguity."""
import re

def needs_review(title,text,kind='moodle',url='',context=None):
    context=context or {}
    if kind=='outlook' or context.get('needs_review') or '/mod/forum/' in url or 'announcement' in title.lower():return True
    if re.search(r'\b(outline|syllabus|schedule|assignment|quiz|midterm|exam|submission|deadline|project)\b',title,re.I):return True
    # Ordinary lectures mentioning tests/examples do not qualify. Keep actual due/change language,
    # including relative and unresolved dates, rather than requiring a concrete numeric date.
    return bool(re.search(r'\b(due|deadline|cancel(?:led|ed|lation)?|postpon\w*|reschedul\w*|extension|extended|tomorrow|next week)\b|\b(?:quiz|exam|test|assignment|workshop|event)\b.{0,100}\b(?:on|at|scheduled|held|submit|monday|tuesday|wednesday|thursday|friday)\b',text,re.I))
