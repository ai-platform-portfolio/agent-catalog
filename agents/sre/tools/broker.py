"""Collect live evidence inside AKS and invoke the pinned Foundry release."""

import json
import os
from pathlib import Path
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import VersionRefIndicator
from azure.identity import WorkloadIdentityCredential
import httpx

from tools.diagnostics import KubernetesTools, https_url


def collect(client, namespace, workload):
    incident = {'namespace': namespace, 'workload': workload,
                'summary': 'Check the workload readiness and recent events. Report only what the evidence supports.'}
    tools = KubernetesTools(client, namespace, workload)
    evidence = {name: tools.execute(name, incident) for name in ('workload_state', 'recent_events')}
    return {'incident': incident, 'evidence': evidence}


def invoke(project, credential, endpoint, agent, version, request):
    session = project.agents.create_session(agent_name=agent, version_indicator=VersionRefIndicator(agent_version=version))
    deadline = time.monotonic() + 90
    try:
        while str(getattr(session.status, 'value', session.status)).lower() != 'active':
            if time.monotonic() >= deadline or str(getattr(session.status, 'value', session.status)).lower() in {'failed', 'deleted', 'deleting'}:
                raise RuntimeError('Pinned Foundry session did not become active')
            time.sleep(2)
            session = project.agents.get_session(agent_name=agent, session_id=session.agent_session_id)
        response = httpx.post(endpoint, params={'api-version': 'v1', 'agent_session_id': session.agent_session_id},
                              headers={'Authorization': 'Bearer ' + credential.get_token('https://ai.azure.com/.default').token},
                              json=request, timeout=120, follow_redirects=False)
        response.raise_for_status()
        result = response.json()
        if (result.get('deployment', {}).get('agent') != agent or str(result['deployment'].get('version')) != version
                or result.get('evidence_source') != 'caller_provided'):
            raise ValueError('Response did not match the pinned release and evidence mode')
        return result
    finally:
        project.agents.delete_session(agent_name=agent, session_id=session.agent_session_id)


def main():
    auth = Path('/var/run/secrets/kubernetes.io/serviceaccount')
    project = https_url(os.environ['FOUNDRY_PROJECT_ENDPOINT'])
    agent, version = os.environ['SRE_AGENT'], os.environ['SRE_VERSION']
    if not version.isascii() or not version.isdecimal() or not agent.isascii() or not agent.replace('-', '').isalnum():
        raise ValueError('An explicit agent and numeric version are required')
    with httpx.Client(base_url='https://kubernetes.default.svc', verify=str(auth / 'ca.crt'),
                      headers={'Authorization': 'Bearer ' + (auth / 'token').read_text().strip()},
                      timeout=8, trust_env=False) as kube:
        request = collect(kube, os.environ['SRE_TARGET_NAMESPACE'], os.environ['SRE_TARGET_WORKLOAD'])
    with WorkloadIdentityCredential() as credential, AIProjectClient(project, credential) as client:
        result = invoke(client, credential, f'{project}/agents/{agent}/endpoint/protocols/invocations', agent, version, request)
    expected = {'source_revision': os.environ['SRE_SOURCE_REVISION'], 'image': os.environ['SRE_IMAGE']}
    if (not result.get('trace_id') or 'workload_state' not in result.get('tool_calls', [])
            or any(result['deployment'].get(key) != value for key, value in expected.items())):
        raise ValueError('Live diagnosis did not use the expected release and collected workload state')
    print(json.dumps({'target': {key: request['incident'][key] for key in ('namespace', 'workload')},
                      **{key: result[key] for key in ('deployment', 'diagnosis', 'trace_id', 'tool_calls', 'evidence_source')}}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('SRE broker failed: ' + type(error).__name__) from None
