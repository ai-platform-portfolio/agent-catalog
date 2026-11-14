import copy
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from starlette.testclient import TestClient

from evaluate import load_case, score
from runtime import load_config, model_client, run
from server import build_app, investigate
from tools import EntraAuth, KubernetesTools, ProvidedTools, bounded_get, validate_diagnosis

ROOT = Path(__file__).resolve().parents[1]


class Call:
    type = 'function_call'
    arguments = '{}'

    def __init__(self, name):
        self.name = self.call_id = name

    def model_dump(self, **kwargs):
        return {'type': self.type, 'name': self.name, 'call_id': self.call_id, 'arguments': self.arguments}


class Model:
    def __init__(self, calls=(), decision='abstain'):
        self.calls, self.decision, self.requests = list(calls), decision, []
        self.responses = self

    def create(self, **request):
        self.requests.append(copy.deepcopy(request))
        number = len(self.requests)
        calls = [Call(self.calls[number - 1])] if number <= len(self.calls) else []
        answer = {'decision': self.decision, 'answer': 'Observed evidence requires verification; no change was made.',
                  'evidence': self.calls}
        return SimpleNamespace(status='completed', output=calls, output_text=json.dumps(answer))

    def close(self):
        pass


class AgentTests(unittest.TestCase):
    def test_complete_fault_investigation_uses_one_loop_and_withholds_grader(self):
        case, payload = load_case(ROOT / 'evals/readiness-fault.yaml')
        model = Model(['workload_state', 'recent_events', 'bounded_logs'], 'actionable')
        result = investigate(payload, load_config(), model)
        self.assertEqual(result['evidence_source'], 'caller_provided')
        self.assertEqual(result['model_requests'], 4)
        self.assertEqual(result['diagnosis']['decision'], 'actionable')
        self.assertEqual(score(case, result, load_config())['status'], 'human_review_required')
        sent = json.dumps(model.requests)
        for grader in ['tool_fixtures', 'rubric', 'provenance', 'reference']:
            self.assertNotIn(grader, sent)
        self.assertNotIn('/api/missing-readyz', model.requests[0]['input'][0]['content'])
        self.assertIn('/api/missing-readyz', json.dumps(model.requests[-1]['input']))
        self.assertTrue(all(not request['store'] for request in model.requests))

    def test_healthy_and_no_evidence_cases_abstain(self):
        _, payload = load_case(ROOT / 'evals/healthy.yaml')
        healthy = investigate(payload, load_config(), Model(['workload_state']))
        self.assertEqual(healthy['diagnosis']['decision'], 'abstain')
        model = Model()
        result = investigate({'incident': {'summary': 'Elevated errors without evidence'}}, load_config(), model)
        self.assertNotIn('tools', model.requests[0])
        self.assertEqual(result['tool_calls'], [])

    def test_actionable_claim_requires_the_observed_fault(self):
        _, payload = load_case(ROOT / 'evals/healthy.yaml')
        with self.assertRaisesRegex(ValueError, 'degraded readiness'):
            investigate(payload, load_config(), Model(['workload_state', 'recent_events'], 'actionable'))
        result = {'diagnosis': {'decision': 'abstain', 'answer': 'Need more verified evidence for a diagnosis.',
                                'evidence': ['not_executed']}}
        with self.assertRaises(ValueError):
            validate_diagnosis(result, {})

    def test_unknown_repeated_and_argument_bearing_tools_are_rejected(self):
        for calls in [['delete_pod'], ['workload_state', 'workload_state']]:
            with self.subTest(calls=calls), self.assertRaises(ValueError):
                run({'summary': 'fault'}, load_config(), Model(calls), ProvidedTools({'workload_state': {}}))
        model = Model(['workload_state'])
        with patch.object(Call, 'arguments', '{"namespace":"other"}'), self.assertRaises(ValueError):
            run({'summary': 'fault'}, load_config(), model, ProvidedTools({'workload_state': {}}))

    def test_request_tool_and_evidence_budgets_fail_closed(self):
        config = load_config()
        config['limits']['model_requests'] = 1
        with self.assertRaisesRegex(RuntimeError, 'Model-request budget'):
            run({'summary': 'fault'}, config, Model(['workload_state']), ProvidedTools({'workload_state': {}}))
        config = load_config()
        config['limits']['tool_calls'] = 1
        with self.assertRaisesRegex(RuntimeError, 'Tool-call budget'):
            run({'summary': 'fault'}, config, Model(['workload_state', 'recent_events']),
                ProvidedTools({'workload_state': {}, 'recent_events': {}}))
        with self.assertRaises(ValueError):
            ProvidedTools({'bounded_logs': {'logs': 'x' * 24576}})
        with self.assertRaises(ValueError):
            run({'summary': 'x' * 8192}, config, Model(), ProvidedTools({}))
        with self.assertRaises(ValueError):
            run({'summary': 'fault', 'rubric': {}}, config, Model(), ProvidedTools({}))

    def test_model_endpoint_and_identity_are_injected_without_a_secret(self):
        credential = Mock(spec=['get_token'])
        credential.get_token.return_value = SimpleNamespace(token='short-lived-test-token', expires_on=time.time() + 3600)
        with patch('runtime.OpenAI') as constructor:
            model_client(credential, 'https://example.services.ai.azure.com/api/projects/demo')
            self.assertEqual(constructor.call_args.kwargs['base_url'],
                             'https://example.services.ai.azure.com/api/projects/demo/openai/v1/')
            self.assertEqual(constructor.call_args.kwargs['api_key'](), 'short-lived-test-token')
            credential.get_token.assert_called_with('https://ai.azure.com/.default')
        for endpoint in ['http://example.test', 'https://user:secret@example.test',
                         'https://:secret@example.test', 'https://example.test/?token=secret']:
            with self.assertRaises(ValueError):
                model_client(credential, endpoint)

    def test_live_tools_reject_cross_namespace_and_only_issue_reads(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'items': [{'reason': 'Unhealthy',
                'message': 'Readiness probe failed: token=private-value',
                'involvedObject': {'name': 'api-pod', 'kind': 'Pod'}}]})

        with httpx.Client(base_url='https://kube.test', transport=httpx.MockTransport(respond)) as client:
            tools = KubernetesTools(client, 'approved')
            with self.assertRaises(ValueError):
                tools.execute('recent_events', {'namespace': 'other', 'workload': 'api'})
            self.assertEqual(requests, [])
            evidence = tools.execute('recent_events', {'namespace': 'approved', 'workload': 'api'})
            self.assertNotIn('private-value', json.dumps(evidence))
            self.assertEqual([request.method for request in requests], ['GET'])
            self.assertNotIn('service_metrics', tools.names)

    def test_live_rollout_fallback_selects_pods_and_bounds_log_reads(self):
        requests = []

        def respond(request):
            requests.append(request)
            path = request.url.path
            if '/deployments/' in path:
                return httpx.Response(404)
            if '/rollouts/' in path:
                return httpx.Response(200, json={'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'api'}}}})
            if path.endswith('/pods'):
                self.assertEqual(request.url.params['labelSelector'], 'app=api')
                return httpx.Response(200, json={'items': [{'metadata': {'name': 'api-selected'},
                                                           'status': {'phase': 'Running'}}]})
            self.assertTrue(path.endswith('/pods/api-selected/log'))
            self.assertEqual(request.url.params['limitBytes'], '8192')
            return httpx.Response(200, text='Authorization: Bearer sensitive-value')

        with httpx.Client(base_url='https://kube.test', transport=httpx.MockTransport(respond)) as client:
            tools = KubernetesTools(client, 'approved')
            incident = {'namespace': 'approved', 'workload': 'api'}
            self.assertEqual(tools.execute('workload_state', incident)['deployment']['kind'], 'Rollout')
            logs = tools.execute('bounded_logs', incident)
            self.assertNotIn('sensitive-value', logs['logs'])
            self.assertTrue(all(request.method == 'GET' for request in requests))

    def test_transport_failures_are_sanitized_at_the_protocol_boundary(self):
        model = Model()
        with patch.object(model, 'create', side_effect=RuntimeError('private credential detail')):
            with TestClient(build_app(client=model, configure_observability=None)) as client:
                response = client.post('/invocations', json={'incident': {'summary': 'fault'}})
                self.assertEqual(response.status_code, 502)
                self.assertNotIn('private credential detail', response.text)

    def test_http_evidence_budget_and_short_lived_tool_auth(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'x' * 20))) as client:
            with self.assertRaises(ValueError):
                bounded_get(client, 'https://kube.test/events', limit=10)
        credential = Mock()
        credential.get_token.return_value = SimpleNamespace(token='temporary-token')
        request = httpx.Request('GET', 'https://kube.test')
        next(EntraAuth(credential, 'test-scope/.default').auth_flow(request))
        self.assertEqual(request.headers['Authorization'], 'Bearer temporary-token')
        credential.get_token.assert_called_once_with('test-scope/.default')

    def test_live_mode_cannot_accept_recorded_evidence(self):
        with self.assertRaises(ValueError):
            investigate({'incident': {'summary': 'fault'}, 'evidence': {}}, load_config(), Model(),
                        live=(Mock(), None, 'approved'))

    def test_official_protocol_adapter_serves_health_and_invocation(self):
        with patch.dict('os.environ', {'DIAGNOSTICS_BACKEND': 'provided'}):
            with TestClient(build_app(client=Model(), configure_observability=None)) as client:
                self.assertEqual(client.get('/readiness').status_code, 200)
                response = client.post('/invocations', json={'incident': {'summary': 'No evidence attached'}})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['diagnosis']['decision'], 'abstain')
                self.assertEqual(client.post('/invocations', json={'rubric': {}}).status_code, 400)
                self.assertEqual(client.post('/invocations', content=b'x' * 131073).status_code, 413)


if __name__ == '__main__':
    unittest.main()
