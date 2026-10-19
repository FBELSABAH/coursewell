#!/usr/bin/env python3
"""Refresh the selected semester’s courses and publish easy-to-copy folders."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import PROJECT_ROOT, settings
sys.path.insert(0,str(PROJECT_ROOT/'dashboard'))
import semester
from src.course_library import CourseLibrary, LoginRequired, atomic_write, authenticated_html, now, session_from_state
from src.course_export import publish
from src.moodle_browser import _resolve_storage_state_path
from src.moodle_scraper import DiscoveredCourse
from sync_course import browser_login_and_courses, term_match


def enrolled_courses(session):
    response = session.get(settings.moodle_courses_url, timeout=30)
    response.raise_for_status()
    if not authenticated_html(response.text, response.url, settings.moodle_home_url):
        raise LoginRequired('Sign-in expired.')
    match = re.search(r'"sesskey"\s*:\s*"([^"\s]+)"', response.text)
    if not match:
        raise RuntimeError('Could not find the Moodle session key for course discovery.')
    p = urlsplit(settings.moodle_home_url)
    endpoint = f'{p.scheme}://{p.netloc}/lib/ajax/service.php'
    result, offset = {}, 0
    for _ in range(100):
        response = session.post(endpoint, params={'sesskey': match.group(1)}, json=[{
            'index': 0, 'methodname': 'core_course_get_enrolled_courses_by_timeline_classification',
            'args': {'classification': 'all', 'limit': 100, 'offset': offset, 'sort': 'fullname'},
        }], timeout=30)
        response.raise_for_status()
        reply = response.json()[0]
        if reply.get('error'):
            raise RuntimeError('Moodle refused course discovery; refresh sign-in and retry.')
        data = reply['data']
        rows = data['courses']
        for course in rows:
            url = course.get('viewurl') or f'{p.scheme}://{p.netloc}/course/view.php?id={course["id"]}'
            if urlsplit(url).netloc != p.netloc:
                raise RuntimeError('Unexpected course host in course list.')
            result[str(course['id'])] = DiscoveredCourse(name=course['fullname'], url=url,
                                                        moodle_course_id=str(course['id']))
        next_offset = data.get('nextoffset', offset + len(rows))
        if not rows or next_offset <= offset:
            return list(result.values())
        offset = next_offset
    raise RuntimeError('Course discovery exceeded its page limit; not treating this as a complete list.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login', action='store_true')
    parser.add_argument('--destination', type=Path, default=Path.home() / 'Documents' / 'University Courses')
    args = parser.parse_args()
    state_root = PROJECT_ROOT / 'data' / 'course-export-state'
    state_root.mkdir(parents=True, exist_ok=True)
    with (state_root / 'update.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A course update is already running.')
        path = _resolve_storage_state_path()
        state = json.loads(path.read_text()) if path.exists() else None
        if args.login or not state:
            state, _ = browser_login_and_courses()
        session = session_from_state(state, settings.moodle_home_url)
        try:
            try:
                courses = enrolled_courses(session)
            except LoginRequired:
                session.close()
                state, _ = browser_login_and_courses()
                session = session_from_state(state, settings.moodle_home_url)
                courses = enrolled_courses(session)
            courses = sorted([c for c in courses if semester.term_from_name(c.name)==semester.current()['term']], key=lambda c: c.name)
            if not courses:
                raise RuntimeError('No '+semester.current()['label']+' enrolled courses were found; existing folders were left untouched.')
            destination = args.destination.resolve()
            report = [f'University Courses update - {now()}', '', f"{semester.current()['label']} courses found: {len(courses)}", '']
            failed = False
            for course in courses:
                print(f'Updating {course.name} ...', flush=True)
                try:
                    library = CourseLibrary(session, course.url, PROJECT_ROOT / 'data' / 'library')
                    run = library.sync()
                    library.manifest['course_name'] = course.name
                    folder, counts = publish(library, destination, semester.current()['label'], state_root)
                    line = f'{course.name}: {run["files_verified"]} files verified; {counts}'
                    print(line, flush=True)
                    report += [line, f'Folder: {folder}', f'Sync status: {run["status"]}']
                    for issue in run['errors'] + run['not_downloaded']:
                        report.append(f'  {issue["reason"]} {issue["url"]}')
                    unverified = sum(not f.get('verified_in_last_run') for f in library.manifest['files'].values())
                    if unverified:
                        report.append(f'  {unverified} older sources were not verified; their local copies were retained.')
                    report.append('')
                    failed |= bool(run['errors'])
                except Exception as exc:
                    failed = True
                    report += [f'{course.name}: FAILED ({type(exc).__name__}). Existing files retained.', '']
                    print(f'Course failed: {type(exc).__name__}. Existing files retained.', flush=True)
            report += ['Your own files are never overwritten. Replaced downloaded originals are retained under Archive.',
                       'External links and interactive Moodle activities are listed above when they cannot be downloaded.',
                       'These folders do not automatically update files already uploaded to ChatGPT.']
            atomic_write(destination / 'Last Update.txt', ('\n'.join(report) + '\n').encode())
            print(f'\nFinished. Open {destination}\nDetails: {destination / "Last Update.txt"}', flush=True)
            return 1 if failed else 0
        finally:
            session.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nStopped. Completed downloads are retained.')
        sys.exit(130)
    except Exception as exc:
        print(f'Update could not finish: {type(exc).__name__}. Check your connection or rerun with --login.', file=sys.stderr)
        sys.exit(1)
