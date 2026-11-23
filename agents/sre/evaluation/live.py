"""Prepare explicit live acceptance using the existing Azure publisher session."""
import argparse
import json
import os
from pathlib import Path
from shlex import quote
import subprocess

import yaml

from tools.diagnostics import https_url, target
from tools.provenance import validate_record


# Argo checks Accepted and Programmed on the current ListenerSet generation.
TELEMETRY_ROUTES_READY = '''
    .status.health.status == "Healthy" and .status.sync.status == "Synced"
    and .spec.source != null and .status.sync.comparedTo.source == .spec.source
    and any(.status.resources[]?; .namespace == "opentelemetry" and .status == "Synced"
        and .group == "gateway.networking.k8s.io" and .kind == "ListenerSet" and .name == "telemetry")
    and any(.status.resources[]?; .namespace == "opentelemetry" and .status == "Synced"
        and .group == "gateway.envoyproxy.io" and .kind == "SecurityPolicy" and .name == "telemetry-default")
'''


def az_aks(*arguments, timeout=120):
    return subprocess.run(
        ['az', 'aks', *arguments, '--resource-group', os.environ['RESOURCE_GROUP'],
         '--name', os.environ['CLUSTER'], '--only-show-errors'],
        check=True, capture_output=True, text=True, timeout=timeout,
    ).stdout


def read_resource(namespace, resource, name, *, ready=None):
    # Names are validated DNS labels before any shell command is constructed.
    target({'namespace': namespace, 'workload': name}, namespace, name)
    command = f'kubectl -n {namespace} get {resource} {name} -o json'
    if ready is not None:
        # Keep the bounded wait inside one AKS command instead of starting a pod for each read.
        command = f'''
deadline=$(($(date +%s) + 180))
while [ "$(date +%s)" -lt "$deadline" ]; do
  observed=$({command} --ignore-not-found --request-timeout=5s) || exit 2
  if printf '%s' "$observed" | jq -e {quote(ready)} >/dev/null; then
    printf '%s\n' "$observed"
    exit 0
  fi
  sleep 3
done
exit 1
'''
    result = json.loads(az_aks('command', 'invoke', '--command', command, '-o', 'json',
                               timeout=250 if ready is not None else 120))
    if result['exitCode'] != 0:
        if ready is not None:
            reason = 'Kubernetes read failed' if result['exitCode'] == 2 else 'Readiness wait failed'
            raise ValueError(f'{reason} for {namespace}/{resource}/{name}')
        raise ValueError(f'Independent {resource} read failed')
    return json.loads(result['logs'])


def configure(intent, directory):
    if (not isinstance(intent, dict) or not {'application', 'workload'} <= set(intent) <= {'application', 'workload', 'backend'}
            or intent.get('backend', 'kubernetes') not in {'kubernetes', 'telemetry'}):
        raise ValueError('Live diagnostics needs an Argo Application and Deployment name')
    backend = intent.get('backend', 'kubernetes')
    application = read_resource('argocd', 'application.argoproj.io', intent['application'],
                                ready='.kind == "Application"' if backend == 'telemetry' else None)
    destination = application['spec']['destination']
    if destination.get('server') != 'https://kubernetes.default.svc' and destination.get('name') != 'in-cluster':
        raise ValueError('Live diagnostics target must be deployed to the selected AKS cluster')
    namespace, workload = destination['namespace'], intent['workload']
    target({'namespace': namespace, 'workload': workload}, namespace, workload)
    settings = {
        'AGENT_DIAGNOSTICS_BACKEND': backend,
        'KUBE_ALLOWED_NAMESPACE': namespace,
        'KUBE_ALLOWED_WORKLOAD': workload,
    }
    if backend == 'telemetry':
        read_resource(namespace, 'deployment', workload, ready='''
            (.spec.replicas // 0) > 0 and .status.observedGeneration == .metadata.generation
            and .status.updatedReplicas == .spec.replicas and .status.availableReplicas == .spec.replicas
        ''')
        settings['TELEMETRY_CONNECTION'] = configure_telemetry(directory, namespace, workload)
    else:
        profile = yaml.safe_load(az_aks('get-credentials', '--format', 'exec', '--file', '-'))
        cluster, = profile['clusters']
        api = cluster['cluster']
        settings.update(KUBE_API_URL=https_url(api['server']), KUBE_CA_BASE64=api['certificate-authority-data'])
    if any(not value or '\n' in value or '\r' in value for value in settings.values()):
        raise ValueError('Missing or invalid AKS target connection metadata')
    (directory / 'live-target.json').write_text(json.dumps({'namespace': namespace, 'workload': workload}))
    with Path(os.environ['GITHUB_ENV']).open('a') as env:
        env.writelines(f'{key}={value}\n' for key, value in settings.items())


def configure_telemetry(directory, namespace, workload, publication=None):
    target({'namespace': namespace, 'workload': workload}, namespace, workload)
    if publication is not None:
        validate_record(publication, namespace, workload)

    read_resource('argocd', 'application.argoproj.io', 'routes', ready=TELEMETRY_ROUTES_READY)
    defaults = read_resource('opentelemetry', 'configmap', 'telemetry-query-defaults',
                             ready='.data["values.json"] != null')
    metadata = json.loads(defaults['data']['values.json'])
    values_path = directory / ('gateway-' + os.environ['SERVICE']) / 'values.json'
    values = json.loads(values_path.read_text())
    values['platform']['telemetry'] = dict(metadata, namespace=namespace, workload=workload,
                                           tenantId=values['platform']['azure']['tenantId'])
    if publication is not None:
        values['platform']['telemetry']['pod'] = {
            field: publication['pod'][field] for field in ('name', 'uid', 'container')
        }
    values_path.write_text(json.dumps(values))
    rendered = subprocess.run(
        ['helm', 'template', values['name'], os.environ['CHART_DIRECTORY'], '-f', str(values_path),
         '--show-only', 'templates/telemetry-access.yaml'],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout
    return yaml.safe_load(rendered)['data']['telemetry.json']


def observe(directory):
    intent = json.loads((directory / 'live-target.json').read_text())
    namespace, workload = target(intent, intent['namespace'], intent['workload'])
    deployment = read_resource(namespace, 'deployment', workload)
    status = deployment['status']
    if deployment['metadata']['generation'] != status.get('observedGeneration'):
        raise ValueError('Independent read found a rollout in progress')
    field, key = ('available', 'availableReplicas') if os.getenv('AGENT_DIAGNOSTICS_BACKEND') == 'telemetry' else ('ready', 'readyReplicas')
    observation = {'desired': deployment['spec']['replicas'], field: status.get(key, 0),
                   'observed_generation': status['observedGeneration']}
    (directory / 'live-observation.json').write_text(json.dumps(observation))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['configure', 'configure-target', 'observe'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--namespace')
    parser.add_argument('--workload')
    parser.add_argument('--publication', type=Path)
    args = parser.parse_args()
    if args.operation == 'configure':
        configure(json.loads(os.environ['LIVE_DIAGNOSTICS_INTENT']), args.directory)
    elif args.operation == 'configure-target':
        if not args.namespace or not args.workload:
            parser.error('configure-target requires --namespace and --workload')
        publication = json.loads(args.publication.read_text()) if args.publication else None
        connection = configure_telemetry(args.directory, args.namespace, args.workload, publication)
        (args.directory / 'live-target.json').write_text(json.dumps({
            'namespace': args.namespace, 'workload': args.workload,
        }))
        print(connection)
    else:
        observe(args.directory)
