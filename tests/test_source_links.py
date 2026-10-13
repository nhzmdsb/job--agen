import json
import tempfile
import unittest
from pathlib import Path

from job_agent.jobs import identity, normalized_url
from job_agent.store import Store, Conflict
from job_agent.web import create_app
from job_agent.worker import lookup, merge_jobs


BASE = 'https://example.invalid/wechat/vuectmjobs/'


def detail(number):
    return BASE + '#/detail?id=fictional-campaign&JobID=' + str(number)


class SourceLinkTests(unittest.TestCase):
    """Fictional hash routes exercise identities without live recruitment data."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = Store(self.home / 'db.sqlite3')
        self.client = create_app(self.store, self.home).test_client()
        self.headers = {'X-Workspace': '1'}

    def test_nine_legacy_routes_lookup_and_noop_without_migration(self):
        for number in range(1, 10):
            field = 'url' if number == 1 else 'detail_url'
            response = self.client.post('/api/worker/records/jobs', json={
                'id': str(number), 'expected_version': 0,
                'data': {'company': 'Fictional Co', 'title': 'Fictional role ' + str(number),
                         'source': 'fixture', 'source_job_id': str(number), field: detail(number)}}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.json)
            # Simulate the trial's legacy keys: the first URL lost its hash;
            # detail_url-only jobs stored only their source identity.
            record = self.store.get('jobs', str(number))
            data = record['data']
            data['dedupe_keys'] = [identity({'source': 'fixture', 'source_job_id': str(number)})]
            if number == 1:
                data['dedupe_keys'].append(identity({'url': BASE}))
            with self.store.connection() as db:
                db.execute('UPDATE records SET data=? WHERE collection=? AND id=?', (json.dumps(data), 'jobs', str(number)))
        before = self.store.export()
        with self.store.connection() as db:
            outbox = db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        queries = [{'url': BASE + '#/detail?utm_source=fixture&JobID=' + str(n) + '&id=fictional-campaign'} for n in range(1, 10)]
        result = self.client.post('/api/worker/lookup', json={'identities': queries}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.json)
        for number, item in enumerate(result.json['items'], 1):
            self.assertEqual([r['id'] for r in item['matches']], [str(number)])
        self.assertEqual(lookup(self.store, [{'url': detail(10)}, {'url': BASE}])['items'][0]['matches'], [])
        self.assertEqual(lookup(self.store, [{'url': BASE}])['items'][0]['matches'], [])
        for number in range(1, 10):
            field = 'url' if number == 1 else 'detail_url'
            response = self.client.patch('/api/worker/records/jobs/' + str(number), json={
                'expected_version': 1, 'patch': {field: detail(number)}}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['action'], 'unchanged')
        self.assertEqual(self.store.export(), before)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], outbox)
        conflict = self.client.patch('/api/worker/records/jobs/1', json={
            'expected_version': 0, 'patch': {'url': detail(1)}}, headers=self.headers)
        self.assertEqual(conflict.status_code, 409)

    def test_distinct_routes_and_same_job_alias_including_trash(self):
        self.store.put('jobs', {'company': 'Fictional Co', 'title': 'One', 'detail_url': detail(1)}, 'one')
        self.store.put('jobs', {'company': 'Fictional Co', 'title': 'Two', 'url': detail(2)}, 'two')
        self.store.patch('jobs', 'one', {'stage': 'trash'}, 1)
        with self.assertRaises(Conflict):
            self.store.put('jobs', {'company': 'Fictional Co', 'title': 'Renamed', 'url': detail(1) + '&utm_campaign=x'}, 'duplicate')
        self.assertEqual(len(self.store.records('jobs')), 2)

    def test_only_tracking_removed_and_merged_detail_alias_survives(self):
        url = BASE + '?JobID=outer&unknown=x&utm_source=a#/detail?JobID=inner&id=campaign&unknown=y&fbclid=b'
        normalized = normalized_url(url)
        self.assertIn('JobID=outer', normalized)
        self.assertIn('#/detail?JobID=inner', normalized)
        self.assertIn('unknown=x', normalized)
        self.assertIn('unknown=y', normalized)
        self.assertNotIn('utm_source', normalized)
        self.assertNotIn('fbclid', normalized)
        self.store.put('jobs', {'company': 'Fictional Co', 'title': 'One', 'url': detail(1)}, 'one')
        self.store.put('jobs', {'company': 'Fictional Co', 'title': 'Alias', 'detail_url': detail(2)}, 'two')
        merged = merge_jobs(self.store, 'one', 'two', 1, 1, 'Fixture confirms same role', True)
        self.assertEqual(merged['record']['data']['identity_aliases'][0]['detail_url'], detail(2))
        self.assertEqual({r['id'] for r in lookup(self.store, [{'url': detail(2)}])['items'][0]['matches']}, {'one', 'two'})
