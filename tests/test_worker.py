import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from job_agent.review import DIMENSIONS, content_hash, project, translation_hash
from job_agent.store import Store, Conflict
from job_agent.web import create_app
from job_agent.worker import context, lookup, merge_jobs, write


def assessment(data, profile, decision='eligible'):
    return {'decision': decision, 'reason': 'Fictional fixture evidence',
            'checks': [{'dimension': d, 'kind': 'unknown', 'status': 'unknown',
                        'reason': 'Fixture uncertainty', 'jd_quote': '', 'profile_fact': 'Unknown'} for d in DIMENSIONS],
            'questions': [], 'profile_version': profile['version'], 'job_hash': content_hash(data),
            'assessed_at': '2026-10-13T00:00:00Z'}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = Store(self.home / 'db.sqlite3')
        self.profile = self.store.put('profiles', {'confirmed': {'degree': 'bachelor'}, 'preferences': {}, 'pending': ['unknown'], 'notes': 'note'}, 'candidate')
        self.job = self.store.put('jobs', {'company': 'Fictional Co', 'title': '虚构岗位', 'jd': '虚构完整职责和要求', 'source': 'fixture', 'source_job_id': '1', 'url': 'https://example.invalid/jobs?id=1', 'artifacts': ['fixture-artifact'], 'extra': {'keep': True, 'nested': {'a': 1}}, 'tags': ['old']}, 'one')

    def judged(self, decision='eligible'):
        self.job = self.store.patch('jobs', 'one', {'screening': {'work': assessment(self.job['data'], self.profile, decision)}}, self.job['version'])
        return self.job

    def test_patch_noop_cas_and_preservation(self):
        result = write(self.store, 'jobs', {'extra': {'nested': {'b': 2}}, 'tags': ['new']}, 'one', 1, patch=True)
        data = result['record']['data']
        self.assertEqual(data['extra'], {'keep': True, 'nested': {'a': 1, 'b': 2}})
        self.assertEqual(data['tags'], ['new'])
        self.assertEqual(data['artifacts'], ['fixture-artifact'])
        with self.store.connection() as db:
            db.execute('DELETE FROM outbox')
        before = self.store.export()
        self.assertEqual(write(self.store, 'jobs', {'tags': ['new']}, 'one', 2, patch=True)['action'], 'unchanged')
        self.assertEqual(self.store.export(), before)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
        with self.assertRaises(Conflict):
            write(self.store, 'jobs', {'tags': ['new']}, 'one', 1, patch=True)

    def test_effective_conditions_and_translation_inputs(self):
        self.judged()
        self.assertEqual(context(self.store)['count'], 0)
        self.profile = self.store.patch('profiles', 'candidate', {'notes': 'changed', 'pending': ['different']}, 1)
        self.assertEqual(context(self.store)['count'], 0)
        self.assertNotIn('notes', context(self.store)['profile']['data'])
        self.profile = self.store.patch('profiles', 'candidate', {'confirmed': {'degree': 'master'}}, 2)
        item = context(self.store)['items'][0]
        self.assertEqual(item['tasks'], ['assessment'])
        self.assertEqual(item['record']['data']['jd'], self.job['data']['jd'])
        self.assertEqual(project(self.job, self.profile, self.store)['view']['color'], 'green')
        self.assertTrue(project(self.job, self.profile, self.store)['view']['stale'])
        translated = {'source_hash': translation_hash(self.job['data']), 'title_zh': '虚构', 'summary_zh': '虚构摘要', 'jd_zh': '虚构完整原文'}
        self.job = self.store.patch('jobs', 'one', {'translation': translated}, self.job['version'])
        self.job = self.store.patch('jobs', 'one', {'city': '杭州', 'tags': ['changed'], 'summary': 'Updated generated summary'}, self.job['version'])
        self.assertTrue(project(self.job, self.profile, self.store)['view']['translation_valid'])
        self.job = self.store.patch('jobs', 'one', {'jd': '新虚构原文'}, self.job['version'])
        self.assertFalse(project(self.job, self.profile, self.store)['view']['translation_valid'])

    def test_colors_pending_and_stages(self):
        self.assertTrue(project(self.job, self.profile)['view']['internal_pending'])
        for decision, color in [('eligible', 'green'), ('disadvantage', 'yellow'), ('unknown', 'grey')]:
            self.judged(decision)
            for interest in ('yes', 'no', 'unknown'):
                self.job = self.store.patch('jobs', 'one', {'interest': interest}, self.job['version'])
                self.assertEqual(project(self.job, self.profile)['view']['color'], color)
                self.assertFalse(project(self.job, self.profile)['view']['internal_pending'])
        for stage in ('applied', 'interview', 'ended', 'trash'):
            self.job = self.store.patch('jobs', 'one', {'stage': stage}, self.job['version'], _human=True)
            self.assertEqual(context(self.store)['count'], 0)
        with self.assertRaises(Conflict):
            self.store.patch('jobs', 'one', {'stage': 'pool'}, self.job['version'])

    def test_manual_protection_http_and_cli(self):
        client = create_app(self.store, self.home).test_client()
        headers = {'X-Workspace': '1'}
        manual = client.patch('/api/records/jobs/one', json={'expected_version': 1, 'patch': {'color': 'yellow', 'color_reason': 'My decision', 'human_judgment': 'My reasoning'}}, headers=headers)
        self.assertEqual(manual.status_code, 200, manual.json)
        self.assertEqual(self.store.get('jobs', 'one')['data']['judgment_authorship']['color'], 'human')
        bypass = client.patch('/api/records/jobs/one', json={'expected_version': 2, 'actor': 'workbench', 'patch': {'color': 'green'}}, headers=headers)
        self.assertEqual(bypass.status_code, 409, bypass.json)
        for fields in ({'color': 'green'}, {'color_reason': 'replace'}, {'human_judgment': ''}):
            blocked = client.patch('/api/worker/records/jobs/one', json={'expected_version': 2, 'actor': 'workbench', 'patch': fields}, headers=headers)
            self.assertEqual(blocked.status_code, 409, blocked.json)
        def cli(patch, version=2, *flags):
            return subprocess.run([sys.executable, '-m', 'job_agent.cli', '--db', str(self.store.path), 'patch', 'jobs', '--id', 'one', '--expected-version', str(version), '--actor', 'workbench', *flags], input=json.dumps(patch), text=True, capture_output=True)
        self.assertEqual(cli({'human_judgment': 'overwritten'}).returncode, 1)
        self.assertEqual(cli({'notes': 'editable', 'tags': ['tag'], 'interest': 'yes'}).returncode, 0)
        consent = {'expected_version': 3, 'patch': {'color': 'green'}, 'human_confirm': True, 'confirmation_reason': 'Fixture explicit consent'}
        self.assertEqual(client.patch('/api/worker/records/jobs/one', json=consent, headers=headers).status_code, 409)
        agreed = client.patch('/api/worker/records/jobs/one', json={**consent, 'reason': 'Reviewed fixture evidence'}, headers=headers)
        self.assertEqual(agreed.status_code, 200, agreed.json)
        self.assertIn('Fixture explicit consent', self.store.history('jobs', 'one')[-1]['reason'])
        self.assertIn('Reviewed fixture evidence', self.store.history('jobs', 'one')[-1]['reason'])
        self.assertEqual(cli({'color': 'yellow'}, 3, '--human-confirm', '--confirmation-reason', 'consented').returncode, 1)
        self.assertEqual(self.store.get('jobs', 'one')['data']['notes'], 'editable')
        manual = client.patch('/api/records/jobs/one', json={'expected_version': 4, 'patch': {'human_judgment': 'Updated by me'}, 'human_confirm': True, 'confirmation_reason': '本人在工作台编辑'}, headers=headers)
        self.assertEqual(manual.status_code, 200, manual.json)

    def test_legacy_authorship_and_beige_are_conservative(self):
        data = copy.deepcopy(self.job['data'])
        data.update(color='beige', color_reason='Legacy judgment', screening={'work': assessment(data, self.profile)})
        # Simulate an old database without rewriting it through today's preparation.
        with self.store.connection() as db:
            db.execute('UPDATE records SET data=? WHERE collection=? AND id=?', (json.dumps(data), 'jobs', 'one'))
        old = self.store.get('jobs', 'one')
        before = self.store.export()
        view = project(old, self.profile, self.store)['view']
        self.assertEqual(view['color'], 'beige')
        self.assertTrue(view['stale'])
        self.assertEqual(context(self.store)['items'][0]['tasks'], ['assessment'])
        self.assertEqual(self.store.export(), before)
        with self.assertRaises(Conflict):
            self.store.patch('jobs', 'one', {'color': 'green'}, 1, actor='human')

    def test_lookup_merge_aliases_and_versions(self):
        other = self.store.put('jobs', {'company': 'Fictional Co', 'title': 'Different name', 'source': 'other', 'source_job_id': '2', 'url': 'https://example.invalid/other', 'artifacts': ['other-artifact']}, 'two')
        with self.assertRaises(Conflict):
            merge_jobs(self.store, 'one', 'two', 1, 99, 'Same fictional role', True)
        self.assertEqual(self.store.get('jobs', 'one')['version'], 1)
        result = merge_jobs(self.store, 'one', 'two', 1, 1, 'Same fictional role', True)
        self.assertEqual(result['record']['data']['artifacts'], ['fixture-artifact', 'other-artifact'])
        self.assertEqual(result['record']['data']['merged_evidence'][0]['data'], other['data'])
        found = lookup(self.store, [{'source': 'other', 'source_job_id': '2'}, {'url': 'https://example.invalid/jobs?utm_source=test&id=1'}])
        self.assertEqual({r['id'] for r in found['items'][0]['matches']}, {'one', 'two'})
        self.assertEqual(found['items'][1]['matches'][0]['id'], 'one')
        self.assertEqual(context(self.store)['count'], 1)
        self.store.patch('jobs', 'one', {'stage': 'trash'}, 2)
        self.assertEqual(lookup(self.store, [{'source': 'fixture', 'source_job_id': '1'}])['items'][0]['matches'][0]['data']['stage'], 'trash')
        self.assertFalse(lookup(self.store, [{'url': 'https://example.invalid/jobs?id=99'}])['items'][0]['matches'])

    def test_pagination_and_machine_exclusion_reconsideration(self):
        data = self.job['data']
        work = assessment(data, self.profile, 'ineligible')
        work['checks'][0].update(kind='mandatory', status='fail', jd_quote=data['jd'])
        self.job = self.store.patch('jobs', 'one', {'screening': {'work': work}}, 1)
        self.assertTrue(project(self.job, self.profile)['view']['low_priority'])
        self.assertEqual(context(self.store)['count'], 0)
        self.profile = self.store.patch('profiles', 'candidate', {'preferences': {'changed': True}}, 1)
        self.store.put('jobs', {'company': 'Another fictional Co', 'title': '待办'}, 'two')
        first = context(self.store, 1)
        self.assertEqual(first['count'], 2)
        self.assertEqual(first['next_offset'], 1)
        self.assertNotEqual(first['items'][0]['record']['id'], context(self.store, 1, 1)['items'][0]['record']['id'])
        self.assertFalse(project(self.job, self.profile)['view']['low_priority'])
