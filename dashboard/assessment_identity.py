"""Reconcile source mentions without turning pending AI proposals into facts.

Only named, single assessments are grouped across dates. Numbered occurrences,
other courses, recurring classes and generic tests retain separate identities.
"""
from copy import deepcopy
import re
from assessments import title_for, dates


def assessment_key(event):
    if event.get('kind') in ('Class', 'Event'):
        return ('instance', event.get('source_uid') or event.get('id'))
    raw = event.get('title', '')
    title = title_for(raw)
    if title == 'Assessment date to check' or title in ('Quiz', 'In-class test'):
        return (str(event.get('course_id')), raw.casefold(), event.get('source_uid') or event.get('date'))
    return (str(event.get('course_id')), title.casefold())


def reconcile(events):
    groups = {}
    for original in events:
        groups.setdefault(assessment_key(original), []).append(deepcopy(original))
    result = []
    for rows in groups.values():
        facts = [e for e in rows if not e.get('proposal_id')]
        # Proposals supplement evidence but never override accepted facts.
        pool = facts or rows
        rank = lambda e: (bool(e.get('confirmed_by_user')), e.get('source_type') == 'moodle_calendar',
                          not e.get('review') and not e.get('tentative'), bool(e.get('date')))
        winner = max(pool, key=rank)
        item = deepcopy(winner)
        all_sources = []
        for e in rows:
            for source in e.get('sources', []):
                if source not in all_sources: all_sources.append(source)
        item['sources'] = all_sources
        item['related_proposal_ids'] = list(dict.fromkeys(e['proposal_id'] for e in rows if e.get('proposal_id')))
        item['merged_ids'] = list(dict.fromkeys(e['id'] for e in rows if e.get('id') and e['id'] != item.get('id')))
        title = title_for(item.get('title', ''))
        presentation = title in ('Technical presentation', 'Stakeholder presentation')
        authoritative = [e for e in pool if e.get('confirmed_by_user')]
        if not authoritative:
            authoritative = [e for e in pool if e.get('source_type') == 'moodle_calendar']
        candidate_dates = sorted({e['date'] for e in pool if e.get('date')})
        submission_dates = set()
        if presentation:
            for e in facts:
                for s in e.get('sources', []):
                    evidence = re.sub(r'\s+', ' ', s.get('evidence', ''))
                    match = re.search(r'\b(?:submitted|submission due|due)\s+(.+?)(?=\s+with\s+presentations|$)', evidence, re.I)
                    if match:
                        submission_dates.update(dates(match[1], int((e.get('date') or '2026')[:4])))
            if len(submission_dates) == 1:
                day = next(iter(submission_dates))
                # The document explicitly distinguishes submission from delivery.
                matches = [e for e in facts if e.get('date') == day]
                if matches and not authoritative:
                    item = deepcopy(max(matches, key=rank)) | {'sources': all_sources,
                        'related_proposal_ids': item['related_proposal_ids'], 'merged_ids': item['merged_ids']}
                    candidate_dates = [day]
                    item['date_role'] = 'Submission due'
                    # Resolve only the extractor's multi-date flag when the
                    # original sentence explicitly distinguishes both roles.
                    evidence = re.sub(r'\s+', ' ', item.get('evidence', ''))
                    if (re.search(r'\bsubmitted\s+.+?\s+with\s+presentations\b', evidence, re.I)
                            and not item.get('tentative') and not item.get('time_note')
                            and not item.get('moodle_due') and not item.get('source_type')
                            and not any(s.get('extraction_notes') for s in item.get('sources', []))):
                        item['review'] = False
                delivery = sorted({e['date'] for e in facts if e.get('date') and e['date'] not in submission_dates})
                if delivery:
                    item['presentation_dates'] = delivery
                    item['schedule_note'] = 'Submission deadline shown. Presentation sessions: ' + ', '.join(delivery) + '. Your group’s exact presentation slot is not confirmed.'
        chosen = sorted({e['date'] for e in authoritative if e.get('date')}) if authoritative else candidate_dates
        if len(chosen) > 1:
            item['date'] = None
            item['conflict_dates'] = chosen
            item['review'] = True
            item.pop('start_at', None); item.pop('end_at', None)
            item['time_note'] = 'Sources disagree. One assessment is shown; confirm its date from the evidence.'
        elif chosen:
            item['date'] = chosen[0]
            item.pop('conflict_dates', None)
        if presentation and title != 'Assessment date to check':
            item['title'] = title
            item.pop('display_title', None)
        result.append(item)
    return sorted(result, key=lambda e: (e.get('date') or '9999', str(e.get('course_id')), e['title']))
