import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import site_data as site


class DashboardEvidenceTests(unittest.TestCase):
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
