import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from calendar_service import CalendarService, CalendarError, GoogleError, event_body, managed_body, preview, SCOPE, EVENT_COLORS, event_category, identity


def assessment(**overrides):
    return {'course_id':'1', 'course':'STAT 2240', 'title':'Midterm', 'date':'2026-10-20',
            'kind':'Assessment', 'sources':[{'name':'Syllabus.pdf', 'page':2, 'evidence':'Midterm October 20'}], **overrides}


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.service=CalendarService(Path(self.tmp.name), lambda:{'events':[]})
        self.remote={};self.writes=[];self.calendars={}
        self.service.api=self.api
        self.today=patch('calendar_service.datetime', wraps=datetime)
        mocked=self.today.start();mocked.now.return_value=datetime.fromisoformat('2026-10-02T12:00:00-03:00')

    def tearDown(self):
        self.today.stop();self.tmp.cleanup()

    def api(self, method, path, body=None, etag=None):
        if method=='POST' and path=='calendars':
            cid='test-'+str(len(self.calendars)); self.calendars[cid]=dict(body,id=cid)
            self.writes.append(('calendar',body));return 200,self.calendars[cid]
        if '/events' not in path:return 200,self.calendars.get(path.split('/')[-1],{'summary':'Coursewell · Classes'})
        key=path.split('/')[-1]
        if method=='GET':return (200,deepcopy(self.remote[key])) if key in self.remote else (404,{})
        if method=='POST':
            key=body['id']
            if key in self.remote:return 409,{}
            self.remote[key]=dict(deepcopy(body),etag='v1');self.writes.append(('event',key))
        if method=='PATCH':
            self.assertEqual(etag,self.remote[key]['etag'])
            self.remote[key].update(deepcopy(body));self.writes.append(('patch',key))
        if method=='DELETE':
            self.remote.pop(key,None);return 204,{}
        return 200,deepcopy(self.remote[key])

    def test_filters_uncertain_practice_past_and_repeated(self):
        items=[assessment(),assessment(title='Old',date='2026-09-01'),assessment(title='Practice',kind='Practice'),
               assessment(title='Unclear',review=True),assessment(title='Tentative',tentative=True),
               assessment(title='No date',date=None),assessment(title='Repeated'),assessment(title='Repeated',date='2026-10-22')]
        plan=preview(items,date(2026,10,2))
        self.assertEqual([e['title'] for e in plan['ready']],['Midterm'])
        self.assertEqual(len(plan['held']),7)

    def test_google_patch_clears_opposite_time_representation(self):
        from unittest.mock import Mock
        service=CalendarService(Path(self.tmp.name)/'patch',lambda:{'events':[]})
        response=Mock(status_code=200,content=b'{}');response.json.return_value={}
        service.http.request=Mock(return_value=response)
        with patch.object(service,'access_token',return_value='test'):
            body={'start':{'dateTime':'2026-10-20T14:30:00-03:00'},'end':{'dateTime':'2026-10-20T15:45:00-03:00'}}
            service.api('PATCH','calendars/test/events/id',body)
            sent=service.http.request.call_args.kwargs['json']
            self.assertIsNone(sent['start']['date'])
            self.assertNotIn('date',body['start'])
            service.api('PATCH','calendars/test/events/id',{'start':{'date':'2026-10-20'},'end':{'date':'2026-10-21'}})
            sent=service.http.request.call_args.kwargs['json']
            self.assertIsNone(sent['start']['dateTime'])
            self.assertIsNone(sent['end']['timeZone'])

    def test_legacy_generic_identity_migrates_without_duplicate(self):
        first=assessment(title='In-class test')
        self.service.sync([first])
        key=identity(first)
        record=self.service.state['records'].pop(key)
        self.service.state['records']['1|in-class test']=record
        self.service.sync([first,assessment(title='In-class test',date='2026-10-22')])
        self.assertEqual(len(self.remote),2)
        self.assertEqual(self.service.state['records'][key]['event_id'],record['event_id'])
        self.assertNotIn('1|in-class test',self.service.state['records'])

    def test_export_failure_still_syncs_verified_local_class_assessments(self):
        self.service.refresh_source=lambda:{'status':'error'}
        self.service.feed_provider=lambda:{'events':[assessment(source_type='class_time_assessment'),assessment(title='Stale export',source_type='moodle_calendar')]}
        with patch.object(self.service,'access_token',return_value='test'), patch.object(self.service,'sync') as sync:
            self.service._run()
        self.assertEqual([e['title'] for e in sync.call_args.args[0]],['Midterm'])

    def test_repeated_sync_and_restart_do_not_duplicate(self):
        self.service.sync([assessment()]);self.service.sync([assessment()])
        restarted=CalendarService(Path(self.tmp.name), lambda:{'events':[]});restarted.api=self.api
        restarted.sync([assessment()])
        self.assertEqual([w[0] for w in self.writes],['calendar','calendar','calendar','event'])

    def test_date_change_updates_same_event(self):
        self.service.sync([assessment()]);self.service.sync([assessment(date='2026-10-25')])
        self.assertEqual(len(self.remote),1)
        self.assertEqual(next(iter(self.remote.values()))['start'],{'date':'2026-10-25'})
        self.assertEqual(self.writes[-1][0],'patch')

    def test_manual_google_edit_is_preserved(self):
        self.service.sync([assessment()]);next(iter(self.remote.values()))['summary']='My edited exam'
        self.service.sync([assessment(date='2026-10-25')])
        self.assertEqual(next(iter(self.remote.values()))['summary'],'My edited exam')
        self.assertTrue(self.service.state['warnings'])

    def test_deleted_event_is_not_recreated(self):
        self.service.sync([assessment()]);self.remote.clear();self.service.sync([assessment()])
        self.assertFalse(self.remote)
        self.assertIn('not recreated',self.service.state['warnings'][0])

    def test_source_becomes_uncertain_preserves_event_and_warns(self):
        self.service.sync([assessment()]);self.service.sync([assessment(review=True)])
        self.assertEqual(len(self.remote),1)
        self.assertIn('needs checking',self.service.state['warnings'][0])

    def test_insert_with_unknown_outcome_recovers_without_duplicate(self):
        original=self.service.save
        def fail_after_insert():
            if self.remote:raise OSError('simulated disk error')
            original()
        self.service.save=fail_after_insert
        with self.assertRaises(OSError):self.service.sync([assessment()])
        self.service.save=original;self.service.state['records']={}
        self.service.sync([assessment()])
        self.assertEqual(len(self.remote),1)
        self.assertEqual(len([w for w in self.writes if w[0]=='event']),1)

    def test_unknown_calendar_creation_does_not_retry(self):
        self.service.state['creation_pending']=True
        with self.assertRaises(CalendarError):self.service.sync([assessment()])
        self.assertEqual(self.writes,[])

    def test_all_day_end_is_exclusive(self):
        body=event_body(assessment(date='2026-10-31'))
        self.assertEqual(body['end'],{'date':'2026-11-01'})
        self.assertIn('page 2',body['description'])

    def test_oauth_state_pkce_and_private_credentials(self):
        self.service.import_credentials({'installed':{'client_id':'example.apps.googleusercontent.com','client_secret':'example'}})
        params=parse_qs(urlparse(self.service.authorize()).query)
        self.assertEqual(params['code_challenge_method'],['S256'])
        self.assertEqual(params['scope'],[SCOPE])
        self.assertEqual((Path(self.tmp.name)/'credentials.json').stat().st_mode & 0o777,0o600)
        with patch.object(self.service,'token_request') as exchange:
            with self.assertRaises(CalendarError):self.service.callback({'state':['wrong'],'code':['secret']})
            exchange.assert_not_called()

    def test_unconfigured_or_disabled_cannot_start(self):
        self.assertFalse(self.service.start())
        with self.assertRaises(CalendarError):self.service.enable()

    def test_successful_oauth_is_single_use_and_requires_enable(self):
        self.service.import_credentials({'installed':{'client_id':'example.apps.googleusercontent.com','client_secret':'example'}})
        query=parse_qs(urlparse(self.service.authorize()).query)
        callback={'state':query['state'],'code':['private-code']}
        with patch.object(self.service,'token_request',return_value={'access_token':'private-access','refresh_token':'private-refresh','scope':SCOPE}):
            self.service.callback(callback)
            with self.assertRaises(CalendarError):self.service.callback(callback)
        self.assertTrue(self.service.status()['connected'])
        self.assertFalse(self.service.state['enabled'])
        self.assertNotIn('private',json.dumps(self.service.status()))

    def test_callback_denial_does_not_exchange_code(self):
        self.service.credentials={'client_id':'example'}
        query=parse_qs(urlparse(self.service.authorize()).query)
        with patch.object(self.service,'token_request') as exchange:
            with self.assertRaises(CalendarError):self.service.callback({'state':query['state'],'error':['access_denied']})
            exchange.assert_not_called()

    def test_timed_deadline_has_reminders_and_valid_duration(self):
        body=event_body(assessment(start_at='2026-11-06T14:30:00-04:00',source_type='moodle_calendar'))
        self.assertEqual(body['start']['dateTime'],'2026-11-06T14:30:00-04:00')
        self.assertEqual(body['end']['dateTime'],'2026-11-06T14:31:00-04:00')
        self.assertEqual([r['minutes'] for r in body['reminders']['overrides']],[1440,60])

    def test_google_timestamp_and_reminder_normalization(self):
        body=event_body(assessment(start_at='2026-11-06T14:30:00-04:00',source_type='moodle_calendar'))
        remote=deepcopy(body);remote['start']={'dateTime':'2026-11-06T18:30:00Z'}
        remote['reminders']['overrides'].reverse()
        self.assertEqual(managed_body(body),managed_body(remote))

    def test_document_event_upgrades_to_moodle_without_duplicate(self):
        self.service.sync([assessment()])
        self.service.sync([assessment(source_type='moodle_calendar',source_uid='new-uid',start_at='2026-10-20T14:30:00-03:00')])
        self.assertEqual(len(self.remote),1)
        self.assertEqual(len(self.service.state['records']),1)
        self.assertEqual(self.writes[-1][0],'patch')

    def test_explicit_cancellation_removes_only_unedited_managed_event(self):
        e=assessment(source_type='moodle_calendar',source_uid='moodle-1')
        self.service.sync([e]);self.service.sync([dict(e,cancelled=True)])
        self.assertFalse(self.remote)

    def test_google_edit_survives_moodle_cancellation(self):
        e=assessment(source_type='moodle_calendar',source_uid='moodle-1')
        self.service.sync([e]);next(iter(self.remote.values()))['summary']='My note'
        self.service.sync([dict(e,cancelled=True)])
        self.assertEqual(len(self.remote),1)

    def test_definite_create_rejection_allows_retry(self):
        with patch.object(self.service,'api',side_effect=GoogleError('Not authorized',403)):
            with self.assertRaises(GoogleError):self.service.sync([assessment()])
        self.assertFalse(self.service.state['creation_pending'])

    def test_transport_retries_transient_error(self):
        from unittest.mock import Mock
        s=CalendarService(Path(self.tmp.name),lambda:{'events':[]},transport=Mock())
        s.access_token=lambda:'private'
        s.http.request.side_effect=[Mock(status_code=503,json=lambda:{},content=b'{}'),Mock(status_code=200,json=lambda:{'id':'ok'},content=b'{}')]
        with patch('calendar_service.time.sleep'):
            code,result=s.api('GET','calendars/example')
        self.assertEqual((code,result),(200,{'id':'ok'}));self.assertEqual(s.http.request.call_count,2)


class SplitCalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = CalendarService(Path(self.tmp.name), lambda: {'events': []})
        self.remote = {}
        self.calendars = {'legacy': {'id': 'legacy', 'summary': 'University — Fall 2026'}}
        self.service.state.update(calendar_id='legacy', records={})
        self.service.api = self.api
        self.fail_delete = False
        self.today = patch('calendar_service.datetime', wraps=datetime)
        self.today.start().now.return_value = datetime.fromisoformat('2026-10-02T12:00:00-03:00')

    def tearDown(self):
        self.today.stop()
        self.tmp.cleanup()

    def test_suggested_add_uses_google_valid_id_and_is_idempotent(self):
        self.service.access_token=lambda:'test-token'
        e=assessment()
        e.update(course_id='discover',course='University',title='Campus workshop',
                 source_type='discovery',confirmed_by_user=True,location='Library')
        original=self.service.api
        def strict_api(method,path,body=None,etag=None):
            if method=='POST' and '/events' in path:
                self.assertRegex(body['id'],r'^[a-v0-9]{5,1024}$')
            return original(method,path,body,etag)
        self.service.api=strict_api
        first=self.service.add_reviewed(e)
        second=self.service.add_reviewed(e)
        self.assertEqual(first,second)
        self.assertEqual(len(self.remote),1)

    def api(self, method, path, body=None, etag=None):
        parts = path.split('/')
        if path == 'calendars':
            cid = 'new-' + str(len(self.calendars))
            self.calendars[cid] = dict(body, id=cid)
            return 200, deepcopy(self.calendars[cid])
        cid = parts[1]
        if len(parts) == 2:
            if method == 'PATCH':
                self.calendars[cid].update(body)
            return 200, deepcopy(self.calendars[cid])
        eid = body['id'] if method == 'POST' else parts[3]
        key = (cid, eid)
        if method == 'GET':
            return (200, deepcopy(self.remote[key])) if key in self.remote else (404, {})
        if method == 'POST':
            if key in self.remote: return 409, {}
            self.remote[key] = dict(deepcopy(body), etag='version1')
        elif method == 'PATCH':
            self.assertEqual(etag, self.remote[key]['etag'])
            self.remote[key].update(body)
        elif method == 'DELETE':
            if self.fail_delete: return 412, {}
            self.assertEqual(etag, self.remote[key]['etag'])
            self.remote.pop(key)
            return 204, {}
        return 200, deepcopy(self.remote[key])

    def seed_legacy(self, e, eid='old-event'):
        body = event_body(e)
        body.pop('colorId')  # Previous releases did not manage colors.
        self.remote[('legacy', eid)] = dict(deepcopy(body), etag='version1')
        self.service.state['records'][identity(e)] = {'body': body, 'event_id': eid}

    def test_color_categories(self):
        for title, category in [('Midterm 2','exam'), ('In-class test','exam'), ('Final Exam','exam'),
                                ('Quiz 2','quiz'), ('Assignment 4','assignment'), ('Concept Deck','assignment'),
                                ('Registration closes','assessment')]:
            self.assertEqual(event_category(assessment(title=title)), category)
            self.assertEqual(event_body(assessment(title=title))['colorId'], EVENT_COLORS[category])
        self.assertEqual(event_category(assessment(kind='Class')), 'class')

    def test_legacy_assessment_moves_and_repeat_does_not_duplicate(self):
        e = assessment()
        self.seed_legacy(e)
        self.service.sync([e])
        self.service.sync([e])
        self.assertEqual(len(self.remote), 1)
        cid, eid = next(iter(self.remote))
        self.assertNotEqual(cid, 'legacy')
        self.assertEqual(eid, 'old-event')
        self.assertEqual(self.remote[(cid,eid)]['colorId'], '11')
        self.assertEqual(self.calendars['legacy']['summary'], 'Coursewell · Classes')
        self.assertEqual(len(self.service.status()['calendars']), 3)

    def test_legacy_class_stays_and_gets_neutral_color(self):
        e=assessment(kind='Class', source_type='class_schedule', source_uid='class:1:2026-10-20',
                     title='Lecture', start_at='2026-10-20T13:00:00-03:00', end_at='2026-10-20T14:00:00-03:00')
        self.seed_legacy(e)
        self.service.sync([e])
        self.assertEqual(list(self.remote), [('legacy', 'old-event')])
        self.assertEqual(self.remote[('legacy','old-event')]['colorId'], '8')

    def test_past_assessment_is_migrated(self):
        e = assessment(date='2026-09-20')
        self.seed_legacy(e)
        self.service.sync([e])
        self.assertEqual(len(self.remote), 1)
        self.assertNotIn(('legacy','old-event'), self.remote)

    def test_manual_edit_does_not_create_destination_copy(self):
        e = assessment()
        self.seed_legacy(e)
        self.remote[('legacy','old-event')]['summary'] = 'My custom title'
        self.service.sync([e])
        self.assertEqual(list(self.remote), [('legacy','old-event')])
        self.assertIn('Edited in Google', self.service.state['warnings'][0])

    def test_user_added_attendees_are_not_lost_during_migration(self):
        e = assessment()
        self.seed_legacy(e)
        self.remote[('legacy','old-event')]['attendees'] = [{'email':'example@example.com'}]
        self.service.sync([e])
        self.assertEqual(list(self.remote), [('legacy','old-event')])
        self.assertTrue(self.service.state['warnings'])

    def test_partial_migration_resumes_after_restart(self):
        e = assessment()
        self.seed_legacy(e)
        self.fail_delete = True
        self.service.sync([e])
        self.assertEqual(len(self.remote), 2)
        restarted = CalendarService(Path(self.tmp.name), lambda: {'events': []})
        restarted.api = self.api
        self.fail_delete = False
        restarted.sync([e])
        self.assertEqual(len(self.remote), 1)
        self.assertNotIn(('legacy','old-event'), self.remote)

    def test_delete_succeeded_but_local_save_failed_recovers(self):
        e = assessment()
        self.seed_legacy(e)
        save = self.service.save
        def crash_after_delete():
            if ('legacy','old-event') not in self.remote:
                raise OSError('disk failure')
            save()
        self.service.save = crash_after_delete
        with self.assertRaises(OSError): self.service.sync([e])
        restarted = CalendarService(Path(self.tmp.name), lambda: {'events': []})
        restarted.api = self.api
        restarted.sync([e])
        self.assertEqual(len(self.remote), 1)
        self.assertFalse(restarted.state['warnings'])

    def test_deleted_legacy_event_is_not_recreated(self):
        e = assessment()
        self.seed_legacy(e)
        self.remote.clear()
        self.service.sync([e])
        self.assertFalse(self.remote)
        self.assertTrue(self.service.state['warnings'])

    def test_cancellation_uses_assessment_calendar(self):
        e=assessment(source_type='moodle_calendar', source_uid='moodle-1')
        self.service.sync([e])
        self.service.sync([dict(e,cancelled=True)])
        self.assertFalse(self.remote)

    def test_user_color_edit_is_preserved(self):
        e=assessment()
        self.service.sync([e])
        next(iter(self.remote.values()))['colorId']='6'
        self.service.sync([e])
        self.assertEqual(next(iter(self.remote.values()))['colorId'],'6')
        self.assertTrue(self.service.state['warnings'])


if __name__=='__main__':unittest.main()
