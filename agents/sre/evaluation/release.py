"""Caller policy: route only to a version with matching successful evaluation evidence."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

import yaml

from evaluation.run import load_case


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
        required = {'recall_in_new_session', 'checkpoint_in_new_session', 'forget_memory', 'forget_checkpoint'}
        if (context.get('version') != version or set(context.get('checks', {})) != required
                or not all(value is True for value in context['checks'].values())):
            raise ValueError('Missing or failed native context acceptance')
        knowledge = json.loads((directory / 'knowledge.json').read_text())
        if knowledge.get('revision') != release['source_revision'] or not knowledge.get('sources'):
            raise ValueError('Knowledge publication does not match the immutable release')
    return release


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
    before = json.loads(azd('ai', 'agent', 'endpoint', 'show', service, '--output', 'json'))['agent_endpoint']
    expected = [{'type': 'FixedRatio', 'agent_version': expected_version, 'traffic_percentage': 100}]
    observed = before.get('version_selector', {}).get('version_selection_rules', [])
    # Native first deployment has no fixed selector yet. Only its initial version
    # may bootstrap that route; later unpinned/split routes still require review.
    bootstrap = not observed and version == expected_version == '1' and operation == 'promote'
    if observed != expected and not bootstrap:
        raise ValueError('Serving version changed since review; refusing to overwrite the route')
    endpoint = declaration['agentEndpoint']
    if contract(before) != (sorted(endpoint['protocols']), endpoint['authorizationSchemes']):
        raise ValueError('Endpoint protocols/authentication differ; review before routing')
    endpoint['versionSelector'] = {'versionSelectionRules': [
        {'type': 'FixedRatio', 'agentVersion': version, 'trafficPercentage': 100}]}
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    azd('ai', 'agent', 'endpoint', 'update', service)
    after = json.loads(azd('ai', 'agent', 'endpoint', 'show', service, '--output', 'json'))['agent_endpoint']
    rules = [{'type': 'FixedRatio', 'agent_version': version, 'traffic_percentage': 100}]
    if (after.get('version_selector', {}).get('version_selection_rules') != rules
            or contract(after) != contract(before)):
        raise ValueError('Route change could not be verified')
    (directory / 'routing.json').write_text(json.dumps({
        'release': release, 'before': before, 'after': after, 'review': review,
        'operation': operation, 'reason': reason, 'expected_version': expected_version,
    }, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service')
    parser.add_argument('version')
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    route(args.service, args.version, args.evidence,
          expected_version=os.environ['EXPECTED_SERVING_VERSION'],
          review={'approved': os.getenv('REVIEW_APPROVED') == 'true', 'url': os.environ['REVIEW_URL']},
          operation=os.environ['RELEASE_OPERATION'], reason=os.getenv('ROLLBACK_REASON', ''))
