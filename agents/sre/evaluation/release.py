"""Caller policy: route only to a version with matching successful evaluation evidence."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from azure.identity import AzureDeveloperCliCredential
import httpx
import yaml

from evaluation.run import hosted_request, load_case, score
from evaluation.context import validate_retrieval
from runtime.agent import load_config


def azd(*arguments):
    return subprocess.run(['azd', *arguments, '-e', os.environ['AZURE_ENV_NAME'], '--no-prompt'],
                          check=True, capture_output=True, text=True).stdout


def contract(endpoint):
    return (sorted(endpoint.get('protocols') or list(endpoint.get('protocol_configuration', {}))),
            endpoint.get('authorization_schemes', []))


def validate_evidence(directory, agent, version):
    release = json.loads((directory / 'release.json').read_text())
    if (release['agent'] != agent or release['version'] != version
            or release['source_revision'] != os.environ['EXPECTED_REVISION']):
        raise ValueError('Release evidence belongs to another agent/version')
    for path in sorted(Path('evaluation/cases').glob('*.yaml')):
        case, _ = load_case(path)
        report = json.loads((directory / (path.stem + '.json')).read_text())
        if (report.get('case') != case['id'] or report.get('case_version') != case['version']
                or report.get('mode') != 'hosted_recorded' or not report.get('checks')
                or set(report['checks']) != {'required_tools', 'forbidden_tools', 'known_tools', 'tool_budget', 'request_budget', 'no_forbidden_claims'}
                or not all(value is True for value in report['checks'].values())
                or report.get('status') != 'human_review_required'):
            raise ValueError('Missing, stale or failed hosted evaluation evidence')
        result = report['result']
        if (not result.get('trace_id') or any(result.get('deployment', {}).get(key) != release[key]
                                             for key in ('agent', 'version', 'image', 'source_revision'))):
            raise ValueError('Evaluation did not exercise this immutable release')
    if yaml.safe_load(Path('config/agent.yaml').read_text()).get('memory', {}).get('enabled'):
        context = json.loads((directory / 'context.json').read_text())
        required = {'recall_in_new_session', 'checkpoint_in_new_session', 'forget_memory', 'forget_checkpoint', 'grounded_runbook_citation'}
        if (context.get('version') != version or set(context.get('checks', {})) != required
                or not all(value is True for value in context['checks'].values())):
            raise ValueError('Missing or failed native context acceptance')
        expected = json.loads((directory / 'gateway.json').read_text())
        connection = {'mode': expected['MODEL_CONNECTION_MODE'],
                      'models': {capability: expected[prefix + 'MODEL_DEPLOYMENT_NAME']
                                 for capability, prefix in [('default', ''), ('coding', 'CODING_'), ('embedding', 'EMBEDDING_')]},
                      'embedding_version': expected['EMBEDDING_MODEL_VERSION']}
        if connection['mode'] != 'gateway' or context.get('model_connection') != connection:
            raise ValueError('Hosted connection differs from the reviewed gateway capability mapping')
        knowledge = json.loads((directory / 'knowledge.json').read_text())
        if knowledge.get('revision') != release['source_revision'] or not knowledge.get('sources'):
            raise ValueError('Knowledge publication does not match the immutable release')
        validate_retrieval(context.get('retrieval', {}), knowledge, version)
        if context['retrieval']['deployment'] != {key: release[key] for key in ('agent', 'version', 'image', 'source_revision')}:
            raise ValueError('Retrieval did not exercise this immutable release')
    return release


def endpoint_state(service):
    return json.loads(azd('ai', 'agent', 'endpoint', 'show', service, '--output', 'json'))['agent_endpoint']


def selector(endpoint):
    return (endpoint.get('version_selector') or {}).get('version_selection_rules') or []


def fixed_rule(version):
    return {'type': 'FixedRatio', 'agent_version': version, 'traffic_percentage': 100}


def release_version(directory):
    version = json.loads((directory / 'release.json').read_text())['version']
    if not isinstance(version, str) or not re.fullmatch(r'[1-9][0-9]*', version):
        raise ValueError('Release evidence needs a numeric Foundry version')
    return version


def smoke(agent, version, release=None):
    project = os.environ['FOUNDRY_PROJECT_ENDPOINT'].rstrip('/')
    endpoint = f'{project}/agents/{agent}/endpoint/protocols/invocations'
    case, payload = load_case(Path('evaluation/cases/healthy.yaml'))
    # A request with no version or session override exercises the stable selector.
    with AzureDeveloperCliCredential() as credential, httpx.Client() as client:
        result = hosted_request(endpoint, payload, credential, client)
    deployment = result.get('deployment', {})
    expected = {'agent': agent, 'version': version}
    if release:
        expected.update({key: release[key] for key in ('image', 'source_revision')})
    if not result.get('trace_id') or any(deployment.get(key) != value for key, value in expected.items()):
        raise ValueError('Stable endpoint response does not match the expected release')
    if release and score(case, result, load_config())['status'] == 'failed':
        raise ValueError('Stable endpoint failed its automated smoke checks')
    return {'deployment': deployment, 'trace_id': result['trace_id']}


def update_selector(service, config, endpoint, version):
    endpoint['versionSelector'] = {'versionSelectionRules': [
        {'type': 'FixedRatio', 'agentVersion': version, 'trafficPercentage': 100}]}
    Path('azure.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    azd('ai', 'agent', 'endpoint', 'update', service)
    return endpoint_state(service)


def route(service, version, directory, *, expected_version, review, operation='promote', reason=''):
    url = urlsplit(review.get('url', ''))
    if (review.get('approved') is not True or url.scheme != 'https' or not url.hostname
            or url.username is not None or not re.fullmatch(r'[1-9][0-9]*', expected_version)
            or operation not in {'promote', 'rollback'}
            or operation == 'rollback' and not 10 <= len(reason) <= 2048):
        raise ValueError('Routing requires explicit reviewed approval, expected serving version and rollback reason')
    if not re.fullmatch(r'[1-9][0-9]*', version):
        raise ValueError('Route target must be an explicit numeric version')
    path = Path('azure.yaml')
    config = yaml.safe_load(path.read_text())
    declaration = config['services'][service]
    declaration['name'] = os.environ['AZURE_ENV_NAME']
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    release = validate_evidence(directory, declaration['name'], version)
    key = re.sub('[^A-Z0-9]', '_', service.upper())
    azd('env', 'set', f'AGENT_{key}_NAME={release["agent"]}', f'AGENT_{key}_VERSION={version}',
        f'HOSTED_AGENT_IMAGE={release["image"]}', f'RELEASE_IMAGE={release["image"]}',
        f'SOURCE_REVISION={release["source_revision"]}', 'AGENT_TRACES_ENDPOINT=')
    current = json.loads(azd('ai', 'agent', 'show', service, '--output', 'json'))
    definition = current['definition']
    image = definition.get('container_configuration', {}).get('image') or definition.get('image')
    if (current['version'] != version or current.get('status', '').lower() not in ('active', 'idle')
            or image != release['image']
            or definition.get('environment_variables', {}).get('SOURCE_REVISION') != release['source_revision']):
        raise ValueError('Foundry version no longer matches the validated release')
    before = endpoint_state(service)
    observed = selector(before)
    # Only an initial version can pin its own previously unpinned route.
    bootstrap = not observed and version == expected_version == '1' and operation == 'promote'
    if observed != [fixed_rule(expected_version)] and not bootstrap:
        raise ValueError('Serving version changed since review; refusing to overwrite the route')
    endpoint = declaration['agentEndpoint']
    if contract(before) != (sorted(endpoint['protocols']), endpoint['authorizationSchemes']):
        raise ValueError('Endpoint protocols/authentication differ; review before routing')

    record = {'release': release, 'before': before, 'review': review, 'operation': operation,
              'reason': reason, 'expected_version': expected_version}
    try:
        if bootstrap:
            smoke(release['agent'], version, release)
        after = update_selector(service, config, endpoint, version)
        record['after'] = after
        if selector(after) != [fixed_rule(version)] or contract(after) != contract(before):
            raise ValueError('Route change could not be verified')
        record['smoke'] = smoke(release['agent'], version, release)
    except Exception as failure:
        record['failure'] = type(failure).__name__
        if version != expected_version:
            try:
                live = endpoint_state(service)
                if selector(live) == [fixed_rule(version)] and contract(live) == contract(before):
                    restored = update_selector(service, config, endpoint, expected_version)
                    if selector(restored) != observed or contract(restored) != contract(before):
                        raise ValueError('Previous route could not be verified')
                    record['restored'] = restored
                    record['restore_smoke'] = smoke(release['agent'], expected_version)
                elif selector(live) != observed or contract(live) != contract(before):
                    raise ValueError('Route changed independently; refusing automatic restore')
            except Exception as restore_failure:
                record['restore_failure'] = type(restore_failure).__name__
                raise RuntimeError('Route change failed and automatic recovery could not be verified') from None
        raise RuntimeError('Route change failed; see routing evidence for the serving state') from None
    finally:
        report = 'rollback.json' if operation == 'rollback' else 'routing.json'
        (directory / report).write_text(json.dumps(record, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['promote', 'rollback'])
    parser.add_argument('service')
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    repository_url = f'{os.environ["GITHUB_SERVER_URL"]}/{os.environ["GITHUB_REPOSITORY"]}'
    route(args.service, release_version(args.evidence), args.evidence,
          expected_version=os.environ['EXPECTED_SERVING_VERSION'],
          review={'approved': True, 'url': f'{repository_url}/actions/runs/{os.environ["GITHUB_RUN_ID"]}',
                  'actor': os.environ['GITHUB_ACTOR'], 'triggering_actor': os.environ['GITHUB_TRIGGERING_ACTOR'],
                  'source_run': os.environ['RELEASE_RUN'], 'human_rubric_reviewed': False},
          operation=args.operation, reason=os.getenv('ROLLBACK_REASON', ''))
