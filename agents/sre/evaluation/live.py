"""Prepare explicit live acceptance using the existing Azure publisher session."""
import argparse
import json
import os
from pathlib import Path
import subprocess

import yaml

from tools.diagnostics import https_url, target


def az_aks(*arguments):
    return subprocess.run(
        ['az', 'aks', *arguments, '--resource-group', os.environ['RESOURCE_GROUP'],
         '--name', os.environ['CLUSTER'], '--only-show-errors'],
        check=True, capture_output=True, text=True, timeout=120,
    ).stdout


def read_resource(namespace, resource, name):
    # Names are validated DNS labels before any shell command is constructed.
    target({'namespace': namespace, 'workload': name}, namespace, name)
    result = json.loads(az_aks('command', 'invoke', '--command',
                               f'kubectl -n {namespace} get {resource} {name} -o json', '-o', 'json'))
    if result['exitCode'] != 0:
        raise ValueError(f'Independent {resource} read failed')
    return json.loads(result['logs'])


def configure(intent, directory):
    if not isinstance(intent, dict) or set(intent) != {'application', 'workload'}:
        raise ValueError('Live diagnostics needs an Argo Application and Deployment name')
    application = read_resource('argocd', 'application.argoproj.io', intent['application'])
    destination = application['spec']['destination']
    if destination.get('server') != 'https://kubernetes.default.svc' and destination.get('name') != 'in-cluster':
        raise ValueError('Live diagnostics target must be deployed to the selected AKS cluster')
    namespace, workload = destination['namespace'], intent['workload']
    target({'namespace': namespace, 'workload': workload}, namespace, workload)
    profile = yaml.safe_load(az_aks('get-credentials', '--format', 'exec', '--file', '-'))
    cluster, = profile['clusters']
    api = cluster['cluster']
    settings = {
        'AGENT_DIAGNOSTICS_BACKEND': 'kubernetes',
        'KUBE_API_URL': https_url(api['server']),
        'KUBE_CA_BASE64': api['certificate-authority-data'],
        'KUBE_ALLOWED_NAMESPACE': namespace,
        'KUBE_ALLOWED_WORKLOAD': workload,
    }
    if any(not value or '\n' in value or '\r' in value for value in settings.values()):
        raise ValueError('Missing or invalid AKS target connection metadata')
    (directory / 'live-target.json').write_text(json.dumps({'namespace': namespace, 'workload': workload}))
    with Path(os.environ['GITHUB_ENV']).open('a') as env:
        env.writelines(f'{key}={value}\n' for key, value in settings.items())


def observe(directory):
    intent = json.loads((directory / 'live-target.json').read_text())
    namespace, workload = target(intent, intent['namespace'], intent['workload'])
    deployment = read_resource(namespace, 'deployment', workload)
    status = deployment['status']
    if deployment['metadata']['generation'] != status.get('observedGeneration'):
        raise ValueError('Independent read found a rollout in progress')
    observation = {'desired': deployment['spec']['replicas'], 'ready': status.get('readyReplicas', 0),
                   'observed_generation': status['observedGeneration']}
    (directory / 'live-observation.json').write_text(json.dumps(observation))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['configure', 'observe'])
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.operation == 'configure':
        configure(json.loads(os.environ['LIVE_DIAGNOSTICS_INTENT']), args.directory)
    else:
        observe(args.directory)
