"""Trace export and version-specific acceptance without paid model calls."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
import yaml

from evaluation.release import route, validate_evidence
from evaluation.run import candidate_request, load_case, score
from hosting.server import investigate
from runtime.agent import load_config
from test_agent import Model

ROOT = Path(__file__).resolve().parents[1]


class TelemetryTests(unittest.TestCase):
    def test_real_optional_export_marks_all_spans_without_capturing_content(self):
        packets = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                packets.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        program = '''
import json, time
from types import SimpleNamespace
from telemetry.tracing import shutdown
from hosting.server import build_app, investigate
from runtime.agent import load_config
from test_agent import Model
model = Model()
original = model.create
def create(**request):
    response = original(**request)
    response.model = 'test-model'
    response.usage = SimpleNamespace(input_tokens=11, output_tokens=5)
    return response
model.create = create
started = time.monotonic()
app = build_app(client=model)
assert time.monotonic() - started < 1, 'export blocked startup'
started = time.monotonic()
result = investigate({'incident': {'summary': 'private-prompt-never-export'}}, load_config(), model)
assert time.monotonic() - started < 1, 'export blocked investigation'
assert result['trace_id'] and len(result['trace_id']) == 32
shutdown()
print(json.dumps(result['trace_id']))
'''
        try:
            for endpoint in ('', f'http://127.0.0.1:{server.server_port}/v1/traces', 'http://127.0.0.1:1/v1/traces'):
                env = {**os.environ, 'PYTHONPATH': str(ROOT) + os.pathsep + str(ROOT / 'tests'),
                       'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT': endpoint}
                result = subprocess.run([sys.executable, '-c', program], env=env,
                                        capture_output=True, text=True, timeout=12)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packets)
            names = set()
            for packet in packets:
                request = ExportTraceServiceRequest.FromString(packet)
                for group in request.resource_spans:
                    attributes = {item.key: item.value.string_value for item in group.resource.attributes}
                    self.assertEqual(attributes['langfuse.export'], 'true')
                    for scope in group.scope_spans:
                        names.update(span.name for span in scope.spans)
                        for span in scope.spans:
                            if span.name == 'agent.model':
                                attributes = {item.key: item.value for item in span.attributes}
                                self.assertEqual(attributes['langfuse.observation.type'].string_value, 'generation')
                                self.assertEqual(attributes['gen_ai.usage.input_tokens'].int_value, 11)
                                self.assertEqual(attributes['gen_ai.usage.output_tokens'].int_value, 5)
                self.assertNotIn(b'private-prompt-never-export', packet)
            self.assertTrue({'agent.investigate', 'agent.model'} <= names)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.previous = Path.cwd()
        os.chdir(ROOT)
        self.addCleanup(os.chdir, self.previous)
        self.release = {'agent': 'agent-catalog-sre', 'version': '7',
                        'image': 'example.azurecr.io/sre@sha256:' + 'a' * 64, 'source_revision': 'b' * 40}
        self.env = patch.dict(os.environ, {'EXPECTED_REVISION': 'b' * 40, 'AZURE_ENV_NAME': 'agent-catalog-sre'})
        self.env.start()
        self.addCleanup(self.env.stop)
        (self.root / 'release.json').write_text(json.dumps(self.release))
        for path in Path('evaluation/cases').glob('*.yaml'):
            case, payload = load_case(path)
            calls = case['rubric'].get('automated', {}).get('required_tools', [])
            if path.stem == 'readiness-fault':
                calls = ['workload_state', 'recent_events', 'bounded_logs']
            result = investigate(payload, load_config(), Model(calls, 'actionable' if path.stem == 'readiness-fault' else 'abstain'))
            result.update(trace_id='c' * 32, deployment=self.release)
            report = score(case, result, load_config())
            report['mode'] = 'hosted_recorded'
            (self.root / (path.stem + '.json')).write_text(json.dumps(report))

    def test_evidence_rejects_wrong_version_revision_and_stale_case(self):
        self.assertEqual(validate_evidence(self.root, self.release['agent'], '7'), self.release)
        with self.assertRaises(ValueError):
            validate_evidence(self.root, self.release['agent'], '8')
        with patch.dict(os.environ, {'EXPECTED_REVISION': 'd' * 40}), self.assertRaises(ValueError):
            validate_evidence(self.root, self.release['agent'], '7')
        report = json.loads((self.root / 'healthy.json').read_text())
        report['case_version'] += 1
        (self.root / 'healthy.json').write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            validate_evidence(self.root, self.release['agent'], '7')

    def test_promotion_and_rollback_preserve_auth_and_verify_route_readback(self):
        (self.root / 'azure.yaml').write_text((ROOT / 'azure.yaml').read_text())
        os.chdir(self.root)
        endpoint = {'protocol_configuration': {'invocations': {}}, 'authorization_schemes': [{'type': 'Entra'}],
                    'version_selector': {'version_selection_rules': [
                        {'type': 'FixedRatio', 'agent_version': '6', 'traffic_percentage': 100}]}}
        after = copy.deepcopy(endpoint)
        after['version_selector'] = {'version_selection_rules': [
            {'type': 'FixedRatio', 'agent_version': '7', 'traffic_percentage': 100}]}
        current = {'version': '7', 'status': 'Active', 'definition': {
            'container_configuration': {'image': self.release['image']},
            'environment_variables': {'SOURCE_REVISION': self.release['source_revision']}}}
        for observed in (after, endpoint):
            with patch('evaluation.release.validate_evidence', return_value=self.release), patch(
                    'evaluation.release.azd', side_effect=['', json.dumps(current), json.dumps({'agent_endpoint': endpoint}),
                                                         '', json.dumps({'agent_endpoint': observed})]):
                if observed == after:
                    route('sre', '7', self.root, expected_version='6',
                          review={'approved': True, 'url': 'https://example.test/review'})
                else:
                    with self.assertRaisesRegex(ValueError, 'verified'):
                        route('sre', '7', self.root, expected_version='6',
                          review={'approved': True, 'url': 'https://example.test/review'})
        declaration = yaml.safe_load((self.root / 'azure.yaml').read_text())['services']['sre']
        self.assertEqual(declaration['agentEndpoint']['authorizationSchemes'], [{'type': 'Entra'}])

    def test_native_smoke_selects_numeric_version_and_hides_failed_raw_response(self):
        response = subprocess.CompletedProcess([], 0, 'HTTP/2.0 200 OK\nHeader: value\n\n{"ok":true}', '')
        with patch('evaluation.run.subprocess.run', return_value=response) as cli:
            self.assertEqual(candidate_request('sre', 'agent-catalog-sre', '7', {'incident': {}}), {'ok': True})
            command = cli.call_args.args[0]
            self.assertEqual(command[command.index('--version') + 1], '7')
            self.assertIn('--new-session', command)
        response.returncode, response.stdout = 1, 'HTTP/2.0 403 Forbidden\n\nprivate-diagnostics'
        with patch('evaluation.run.subprocess.run', return_value=response), self.assertRaisesRegex(RuntimeError, '^Version-pinned hosted invocation failed$'):
            candidate_request('sre', 'agent-catalog-sre', '7', {'incident': {}})
