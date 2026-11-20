"""Offline datasets and online hosted evaluations; grader data never enters runtime input."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from azure.identity import DefaultAzureCredential
import httpx
import yaml
from opentelemetry import trace
from opentelemetry.propagate import inject

from runtime.agent import load_config, model_client
from hosting.server import investigate
from tools.diagnostics import https_url
from telemetry.tracing import configure, shutdown
from portfolio_agent_runtime.evaluation import load_case, score


def hosted_request(endpoint, payload, credential, session):
    """Use the exact Invocations URL returned by azd; do not construct a latest route."""
    https_url(endpoint)
    token = credential.get_token('https://ai.azure.com/.default').token
    headers = {'Authorization': 'Bearer ' + token}
    inject(headers)
    response = session.post(endpoint, json=payload, headers=headers,
                            params={'api-version': 'v1'}, timeout=180)
    response.raise_for_status()
    return response.json()


def candidate_request(service, environment, version, payload):
    """Native azd creates a session pinned to the candidate, bypassing serving traffic."""
    with tempfile.TemporaryDirectory() as directory:
        request = Path(directory) / 'request.json'
        request.write_text(json.dumps(payload))
        response = subprocess.run([
            'azd', 'ai', 'agent', 'invoke', service, '--environment', environment,
            '--version', version, '--protocol', 'invocations', '--new-session',
            '--input-file', str(request), '--output', 'raw', '--timeout', '180', '--no-prompt',
        ], capture_output=True, text=True, timeout=210)
        # Native raw output is HTTP status + headers + JSON body. Do not log raw
        # responses or headers on failure; retain only the sanitized report.
        headers, separator, body = response.stdout.partition('\n\n')
        if response.returncode or not separator or not re.match(r'^HTTP/\S+ 2[0-9]{2}\b', headers):
            raise RuntimeError('Version-pinned hosted invocation failed')
        # azd 1.37.3 appends its extension-update notice even in raw mode.
        # Accept that exact CLI suffix, never arbitrary text or another JSON value.
        body = body.lstrip()
        result, end = json.JSONDecoder().raw_decode(body)
        notice = body[end:].strip()
        if notice and not re.fullmatch(
                r"WARNING: A new version of extension 'Foundry agents' is available: \S+ -> \S+\n"
                r"  • To update: azd extension update azure\.ai\.agents\n"
                r"  • To update all: azd extension update --all", notice):
            raise RuntimeError('Unexpected output after hosted response')
        if not isinstance(result, dict):
            raise ValueError('Hosted response must be a JSON object')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('--mode', choices=['model', 'hosted'], default='model')
    parser.add_argument('--endpoint', help='Exact HTTPS Invocations endpoint for hosted mode')
    parser.add_argument('--live', action='store_true', help='Hosted live collectors; send no recorded evidence')
    parser.add_argument('--expected-version', help='Reject a response from a different hosted agent version')
    parser.add_argument('--service', help='Use native azd version-pinned invocation instead of an endpoint URL')
    parser.add_argument('--environment', help='Existing azd environment for --service')
    parser.add_argument('--release-record', type=Path, help='Require the returned deployment to match this image/source record')
    parser.add_argument('--target-record', type=Path, help='Resolved namespace/workload for live acceptance')
    args = parser.parse_args()
    if (args.live and args.mode != 'hosted'
            or args.mode == 'hosted' and bool(args.endpoint) == bool(args.service)
            or args.service and (not args.environment or not args.expected_version)):
        parser.error('Hosted mode needs exactly one of --endpoint or --service; --service needs environment/version')
    case, payload = load_case(args.case)
    target = None
    if args.target_record:
        target = json.loads(args.target_record.read_text())
        if (not args.live or set(target) != {'namespace', 'workload'}
                or any(not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', v) for v in target.values())):
            raise ValueError('A live target needs an explicit namespace and workload')
        payload['incident'].update(target)
        payload['incident']['summary'] = (
            'Check the configured target\'s current Kubernetes readiness using workload_state. '
            'Separate observed workload health from application availability; do not claim a fix or recovery.')
    config = load_config()
    configure()
    try:
        with trace.get_tracer(__name__).start_as_current_span('agent.evaluate') as span:
            span.set_attribute('langfuse.observation.type', 'evaluator')
            span.set_attribute('evaluation.case.id', case['id'])
            span.set_attribute('evaluation.case.version', case['version'])
            result = evaluate(args, payload, config)
            if args.expected_version and str(result.get('deployment', {}).get('version')) != args.expected_version:
                raise ValueError('Hosted response version does not match the candidate')
            if args.release_record:
                release = json.loads(args.release_record.read_text())
                deployment = result.get('deployment', {})
                if any(deployment.get(key) != release.get(key) for key in ('agent', 'version', 'image', 'source_revision')):
                    raise ValueError('Hosted response does not match the immutable release record')
            if not result.get('trace_id'):
                raise ValueError('Investigation did not produce a trace identifier')
            report = score(case, result, config)
            report['evaluation_trace_id'] = f'{span.get_span_context().trace_id:032x}'
            report['mode'] = args.mode + ('_live' if args.live else '_recorded')
            if target:
                report['target'] = target
            print(json.dumps(report, indent=2))
            return 1 if report['status'] == 'failed' else 0
    finally:
        shutdown()


def evaluate(args, payload, config):
    with DefaultAzureCredential() as credential:
        if args.mode == 'model':
            with model_client(credential) as client:
                result = investigate(payload, config, client)
        else:
            if args.live:
                payload.pop('evidence')
            if args.service:
                result = candidate_request(args.service, args.environment, args.expected_version, payload)
            else:
                with httpx.Client(follow_redirects=False) as session:
                    result = hosted_request(args.endpoint, payload, credential, session)
            expected_source = 'live_kubernetes' if args.live else 'caller_provided'
            if result.get('evidence_source') != expected_source:
                raise ValueError('Hosted backend does not match the evaluation evidence mode')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
