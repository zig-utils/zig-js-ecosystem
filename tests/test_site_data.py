import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import site_data as site


class DashboardEvidenceTests(unittest.TestCase):
    def test_finished_consumer_artifacts_overlay_a_running_matrix(self):
        inventory = {'selected': [{'full_name': 'stacksjs/stacks', 'sha': 'a' * 40},
                                  {'full_name': 'home-lang/home', 'sha': 'c' * 40}]}
        check = {'label': 'real build', 'lane': 'candidate', 'status': 'failed', 'engine_invocations': 1}
        result = {'repository': 'stacksjs/stacks', 'sha': 'a' * 40, 'engine_sha': 'b' * 40,
                  'checks': [check], 'runtime_provenance': {'engine_sha': 'b' * 40}}
        bootstrap = {'inventory': inventory, 'records': [
            site.compact_record({**result, 'engine_sha': 'd' * 40, 'runtime_provenance': {}},
                                {'repository': 'zig-utils/zig-js', 'run_id': 3, 'measured_at': '2026-11-11'}, {'stacksjs/stacks': 'a' * 40}),
            site.compact_record({'repository': 'home-lang/home', 'sha': 'c' * 40,
                                 'engine_sha': 'd' * 40, 'checks': [check]},
                                {'measured_at': '2026-11-08'}, {'home-lang/home': 'c' * 40})]}
        archives, artifacts = {}, []
        for artifact_id, name, members in [(10, 'ecosystem-inventory', {'inventory.json': inventory}),
                                          (11, 'ecosystem-results-0', {'stacksjs--stacks.json': result})]:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                for member, value in members.items():
                    z.writestr(member, json.dumps(value))
                z.writestr('../../never-execute.py', 'raise Exception("not data")')
            archives[artifact_id] = buf.getvalue()
            artifacts.append({'id': artifact_id, 'name': name, 'expired': False,
                              'size_in_bytes': len(buf.getvalue()),
                              'digest': 'sha256:' + hashlib.sha256(buf.getvalue()).hexdigest(),
                              'created_at': '2026-11-10T12:00:00Z'})
        run = {'id': 1, 'html_url': 'https://github.com/zig-utils/zig-js/actions/runs/1',
               'status': 'in_progress', 'created_at': '2026-11-10T10:00:00Z',
               'updated_at': '2026-11-10T10:00:00Z'}

        def api(path, binary=False):
            if path.endswith('/actions/runs/3'):
                return {'created_at': '2026-11-08T10:00:00Z'}
            if '/workflows/' in path:
                return {'workflow_runs': [] if 'status=completed' in path else [run]}
            if '/jobs?' in path:
                return {'jobs': [{'id': 7, 'name': 'Consumers stacksjs/stacks',
                                  'status': 'completed', 'conclusion': 'failure'}]}
            if '/artifacts?' in path:
                return {'artifacts': artifacts}
            if binary:
                return archives[int(path.split('/')[-2])]
            self.fail('Unexpected API path: ' + path)

        with patch.object(site, 'REPOS', ('zig-utils/zig-js',)), patch.object(site, 'api', side_effect=api):
            data = site.collect(bootstrap)
        records = {r['name']: r for r in data['records']}
        self.assertEqual(records['stacksjs/stacks']['engine_sha'], 'b' * 40)
        self.assertEqual(records['stacksjs/stacks']['status'], 'failed')
        self.assertEqual(records['stacksjs/stacks']['source']['artifact_id'], 11)
        self.assertEqual(records['home-lang/home']['engine_sha'], 'd' * 40)
        self.assertEqual(data['live']['zig-utils/zig-js']['status'], 'in_progress')
        self.assertEqual(data['warnings'], [])

        def unavailable_run(path, binary=False):
            if '/runs/2/artifacts?' in path:
                raise RuntimeError('temporarily unavailable')
            if '/workflows/' in path and 'status=completed' not in path:
                return {'workflow_runs': [{**run, 'id': 2}, run]}
            return api(path, binary)

        with patch.object(site, 'REPOS', ('zig-utils/zig-js',)), patch.object(site, 'api', side_effect=unavailable_run):
            recovered = site.collect(bootstrap)
        self.assertEqual(next(r for r in recovered['records'] if r['name'] == 'stacksjs/stacks')['engine_sha'], 'b' * 40)
        self.assertIn('run 2: refresh unavailable', recovered['warnings'][0])

        archives[11] += b'tampered'
        with patch.object(site, 'REPOS', ('zig-utils/zig-js',)), patch.object(site, 'api', side_effect=api):
            rejected = site.collect(bootstrap)
        self.assertEqual(next(r for r in rejected['records'] if r['name'] == 'stacksjs/stacks')['engine_sha'], 'd' * 40)
        self.assertIn('artifact 11: unavailable (ValueError)', rejected['warnings'][0])

    def test_late_older_aggregate_cannot_replace_newer_consumer_measurement(self):
        inventory = {'selected': [{'full_name': 'stacksjs/stacks', 'sha': 'a' * 40}]}
        record = {'repository': 'stacksjs/stacks', 'sha': 'a' * 40, 'engine_sha': 'b' * 40,
                  'checks': [{'lane': 'candidate', 'status': 'failed', 'engine_invocations': 1}]}
        report = {'engine_sha': 'b' * 40, 'results': [record]}
        older = {'run_id': 1, 'run_created_at': '2026-11-10T10:00:00Z',
                 'measured_at': '2026-11-10T18:06:06Z'}
        newer = {'run_id': 2, 'run_created_at': '2026-11-10T16:58:29Z',
                 'measured_at': '2026-11-10T17:40:00Z', 'partial': True}
        for sources in [(older, newer), (newer, older)]:
            records = {}
            for source in sources:
                site.merge_report(records, report, inventory, source)
            self.assertEqual(records['stacksjs/stacks']['source']['run_id'], 2)
            self.assertEqual(records['stacksjs/stacks']['status'], 'failed')
            # A later aggregate from the same run can still replace its partial.
            later = {**newer, 'measured_at': '2026-11-10T19:00:00Z', 'partial': False}
            site.merge_report(records, report, inventory, later)
            self.assertEqual(records['stacksjs/stacks']['source'], later)

    def test_consumer_archive_requires_canonical_nonempty_json(self):
        def archive(members):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                for name, value in members.items():
                    z.writestr(name, json.dumps(value))
            raw = buf.getvalue()
            return raw, {'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()}

        for members in ({'other.json': {}},
                        {'stacksjs--stacks.json': {'repository': 'home-lang/home'}},
                        {'stacksjs--stacks.json': []}):
            with self.assertRaises(ValueError):
                site.read_consumer_artifact(*archive(members))
        raw, meta = archive({'stacksjs--stacks.json': {'repository': 'stacksjs/stacks'}})
        with patch.object(site, 'MAX_JSON', 1), self.assertRaisesRegex(ValueError, 'read limit'):
            site.read_consumer_artifact(raw, meta)

    def test_only_real_candidate_execution_can_pass(self):
        passed = {'lane': 'candidate', 'status': 'passed', 'engine_invocations': 1}
        self.assertEqual(site.outcome([passed]), 'passed')
        self.assertEqual(site.outcome([{'lane': 'native', 'status': 'passed'}]), 'native-only')
        self.assertEqual(site.outcome([{'lane': 'native-zig', 'status': 'failed'}]), 'native-only')
        self.assertEqual(site.outcome([{'lane': 'coverage', 'status': 'not-applicable'}]), 'not-applicable')
        self.assertEqual(site.outcome([{'lane': 'coverage', 'status': 'unsupported'}]), 'unsupported')
        self.assertEqual(site.outcome([{'lane': 'reference', 'status': 'passed'}]), 'unknown')
        self.assertEqual(site.outcome([{**passed, 'engine_invocations': 0}]), 'unknown')
        for status, expected in [('unsupported', 'unsupported'), ('not-run', 'not-run'),
                                 ('timed-out', 'failed'), ('memory-exceeded', 'failed'), ('failed', 'failed')]:
            self.assertEqual(site.outcome([passed, {'lane': 'candidate', 'status': status}]), expected)
        self.assertEqual(site.outcome([{'lane': 'setup', 'status': 'failed'}]), 'blocked')
        self.assertEqual(site.outcome([]), 'unknown')

    def test_report_archive_digest_and_expected_members(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('report/report.json', '{"results":[]}')
            z.writestr('inventory.json', '{"selected":[]}')
            z.writestr('../../do-not-extract.py', 'raise Exception("never executed")')
        raw = buf.getvalue()
        artifact = {'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()}
        self.assertEqual(site.read_artifact(raw, artifact), ({'results': []}, {'selected': []}))
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            site.read_artifact(raw + b'tampered', artifact)

    def test_focused_report_preserves_other_consumers_and_rejects_mismatches(self):
        inventory = {'selected': [{'full_name': 'stacksjs/stacks', 'sha': 'a' * 40}]}
        record = {'repository': 'stacksjs/stacks', 'sha': 'a' * 40, 'engine_sha': 'b' * 40,
                  'checks': [{'lane': 'candidate', 'status': 'unsupported'}]}
        report = {'engine_sha': 'b' * 40, 'results': [record]}
        records = {'home-lang/home': {'source': {'measured_at': '2026-11-08'}}}
        site.merge_report(records, report, inventory, {'measured_at': '2026-11-09'})
        self.assertIn('home-lang/home', records)
        self.assertEqual(records['stacksjs/stacks']['status'], 'unsupported')
        site.merge_report(records, report, inventory, {'measured_at': '2026-11-07'})
        self.assertEqual(records['stacksjs/stacks']['source']['measured_at'], '2026-11-09')
        for invalid in ({**record, 'sha': 'c' * 40}, {**record, 'engine_sha': 'c' * 40},
                        {**record, 'checks': []}, {**record, 'runtime_provenance': {'engine_sha': 'c' * 40}}):
            with self.assertRaises(ValueError):
                site.merge_report(records, {**report, 'results': [invalid]}, inventory, {'measured_at': '2026-11-10'})


if __name__ == '__main__':
    unittest.main()
