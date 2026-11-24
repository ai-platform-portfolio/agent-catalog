"""Check the pinned hosted runtime against an independent ARM observation."""
import json
import os
from pathlib import Path
import re
import subprocess

from evaluation.run import candidate_request
from tools.azure import ACCOUNT_API_VERSION, ARM_URL, foundry_account


def trace_id(result):
    value = result.get('trace_id')
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Azure acceptance needs a hosted trace')
    return value


def main():
    release = json.loads(Path(os.environ['RELEASE_RECORD']).read_text())
    version = os.environ['CANDIDATE_VERSION']
    account = foundry_account(os.environ['SRE_AZURE_PROJECT_ID'])
    reference = subprocess.run([
        'az', 'rest', '--method', 'get', '--url', f'{ARM_URL}{account}?api-version={ACCOUNT_API_VERSION}',
        '--query', '{provisioning_state:properties.provisioningState,'
        'connection_states:properties.privateEndpointConnections[].properties.privateLinkServiceConnectionState.status}',
        '--output', 'json', '--only-show-errors',
    ], capture_output=True, text=True, timeout=30)
    if reference.returncode:
        raise RuntimeError('Independent Azure Foundry account read failed')
    expected = json.loads(reference.stdout)
    expected['connection_states'] = sorted(set(expected['connection_states'] or []))
    if expected != {'provisioning_state': 'Succeeded', 'connection_states': ['Approved']}:
        raise ValueError('Expected an independently verified approved Foundry private-link connection')

    service, environment = os.environ['SERVICE'], os.environ['AZURE_ENV_NAME']
    result = candidate_request(service, environment, version, {'incident': {
        'summary': 'Check if the reported Foundry private-link disconnection is visible in current Azure evidence.',
        'azure_resource_id': account,
    }})
    deployment, diagnosis = result.get('deployment', {}), result.get('diagnosis', {})
    expected_observation = {'status': 200, 'available': True, **expected,
                            'unverified': ['DNS', 'end-to-end connectivity']}
    answer = diagnosis.get('answer', '')
    if (version != release.get('version')
            or any(deployment.get(key) != release.get(key) for key in ('agent', 'version', 'image', 'source_revision'))
            or result.get('evidence_source') != 'live_arm'
            or result.get('azure_observation') != expected_observation
            or result.get('tool_calls') != ['read_foundry_account']
            or diagnosis.get('evidence') != ['read_foundry_account']
            or diagnosis.get('decision') != 'abstain'
            or not all(re.search(pattern, answer, re.I) for pattern in
                       (r'\bapproved\b', r'\bDNS\b', r'\bconnectivity\b',
                        r'\b(unverified|not verified|cannot verify|could not verify)\b'))):
        raise ValueError('Hosted diagnosis disagrees with the independently observed ARM evidence')

    data_reads = candidate_request(service, environment, version, {'operation': 'verify_azure_data_reads'})
    checks = {key: data_reads.get(key) for key in ('key_vault', 'blob')}
    if any(type(status) is not int or not 100 <= status <= 599 for status in checks.values()):
        raise ValueError('Hosted fixture check did not return HTTP statuses')
    if data_reads.get('version') != version or checks != {'key_vault': 200, 'blob': 206}:
        raise ValueError(f'Hosted data-plane read failed: Key Vault HTTP {checks["key_vault"]}, Blob HTTP {checks["blob"]}')
    print(json.dumps({'version': version, 'arm': expected_observation, 'incident_trace_id': trace_id(result),
                      'data_reads': checks, 'data_reads_trace_id': trace_id(data_reads)}, indent=2))


if __name__ == '__main__':
    main()
