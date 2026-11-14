"""Offline datasets and online hosted evaluations; grader data never enters runtime input."""
import argparse
import json
from pathlib import Path
import re

from azure.identity import DefaultAzureCredential
import httpx
import yaml

from runtime import load_config, model_client
from server import investigate
from tools import https_url


def load_case(path):
    case = yaml.safe_load(Path(path).read_text())
    if not isinstance(case, dict) or case.get('schema_version') != 1:
        raise ValueError('Unsupported evaluation case')
    if not isinstance(case.get('reference'), dict) or not isinstance(case.get('rubric'), dict):
        raise ValueError('Case requires an independent reference and rubric')
    payload = {'incident': case['incident'], 'evidence': case['tool_fixtures']}
    return case, payload


def score(case, result, config):
    # These are contract checks. A correct root cause still needs human assessment.
    automated = case['rubric'].get('automated', {})
    checks = {
        'required_tools': set(automated.get('required_tools', [])) <= set(result['tool_calls']),
        'known_tools': set(result['tool_calls']) <= set(config['tools']),
        'tool_budget': len(result['tool_calls']) <= config['limits']['tool_calls'],
        'request_budget': result['model_requests'] <= config['limits']['model_requests'],
        'no_forbidden_claims': not any(re.search(pattern, result['diagnosis']['answer'])
                                       for pattern in automated.get('forbidden_answer_patterns', [])),
    }
    return {'case': case['id'], 'checks': checks,
            'status': 'human_review_required' if all(checks.values()) else 'failed',
            'human_rubric': case['rubric'].get('checks', []), 'result': result}


def hosted_request(endpoint, payload, credential, session):
    """Use the exact Invocations URL returned by azd; do not construct a latest route."""
    https_url(endpoint)
    token = credential.get_token('https://ai.azure.com/.default').token
    response = session.post(endpoint, json=payload, headers={'Authorization': 'Bearer ' + token},
                            params={'api-version': 'v1'}, timeout=180)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('--mode', choices=['model', 'hosted'], default='model')
    parser.add_argument('--endpoint', help='Exact HTTPS Invocations endpoint for hosted mode')
    parser.add_argument('--live', action='store_true', help='Hosted live collectors; send no recorded evidence')
    args = parser.parse_args()
    if args.live and args.mode != 'hosted' or args.mode == 'hosted' and not args.endpoint:
        parser.error('--live needs hosted mode; hosted mode needs --endpoint')
    case, payload = load_case(args.case)
    config = load_config()
    with DefaultAzureCredential() as credential:
        if args.mode == 'model':
            with model_client(credential) as client:
                result = investigate(payload, config, client)
        else:
            if args.live:
                payload.pop('evidence')
            with httpx.Client(follow_redirects=False) as session:
                result = hosted_request(args.endpoint, payload, credential, session)
            expected_source = 'live_kubernetes' if args.live else 'caller_provided'
            if result.get('evidence_source') != expected_source:
                raise ValueError('Hosted backend does not match the evaluation evidence mode')
    report = score(case, result, config)
    report['mode'] = args.mode + ('_live' if args.live else '_recorded')
    print(json.dumps(report, indent=2))
    return 1 if report['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
