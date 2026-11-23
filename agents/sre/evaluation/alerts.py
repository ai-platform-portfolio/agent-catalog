"""Register and invoke the read-only SRE subscription to shared Grafana events."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


TARGET_LABEL = re.compile(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?')
INTENT = 'deploy/alert-investigation.values.yaml'


def request(payload, delivery_id, now):
    if set(payload) != {'cluster', 'namespace', 'workload', 'fingerprint', 'starts_at'}:
        raise ValueError('Unexpected Grafana incident fields')
    cluster, namespace, workload = (payload[key] for key in ('cluster', 'namespace', 'workload'))
    if not all(isinstance(item, str) and TARGET_LABEL.fullmatch(item) for item in (cluster, namespace, workload)):
        raise ValueError('Invalid Grafana incident target')
    fingerprint = payload['fingerprint']
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{16}', fingerprint):
        raise ValueError('Invalid Grafana alert fingerprint')
    started = datetime.fromisoformat(payload['starts_at'])
    if started.tzinfo is None:
        raise ValueError('Grafana alert start must include its timezone')
    started = started.astimezone(timezone.utc)
    if not now - timedelta(hours=24) <= started <= now + timedelta(minutes=2):
        return None

    identity = f'{cluster}/{namespace}/{workload}/{fingerprint}/{started.isoformat()}'
    return {'operation': 'alert', 'event_uid': hashlib.sha256(identity.encode()).hexdigest(),
            'namespace': namespace, 'workload': workload, 'delivery_id': delivery_id}


def scoped_events(events, cluster, namespace, workload):
    if not all(TARGET_LABEL.fullmatch(item) for item in (cluster, namespace, workload)):
        raise ValueError('Invalid alert subscription target')
    sensor = events['sensors']['readiness']
    filters = sensor['dependencies'][0]['filters']['data']
    for key, item in [('cluster', cluster), ('namespace', namespace)]:
        filters.append({'path': f'body.alerts.0.labels.{key}', 'type': 'string', 'value': [f'^{re.escape(item)}$']})
    sensor['triggers'][0]['template']['http']['payload'].append({
        'src': {'dependencyName': 'firing', 'value': workload}, 'dest': 'client_payload.workload',
    })
    return events


def register(directory):
    import yaml

    from .live import az_aks, read_resource

    root = Path(__file__).resolve().parents[3]
    subscription = yaml.safe_load((root / INTENT).read_text())
    target = json.loads((directory / 'live-target.json').read_text())
    receiver = read_resource('cluster-onboarding', 'eventsource.argoproj.io', 'grafana-alerts',
                             ready='any(.status.conditions[]?; .type == "Deployed" and .status == "True")')
    cluster = receiver['spec']['webhook']['alert']['metadata']['cluster']
    subscription['events'] = scoped_events(subscription['events'], cluster, target['namespace'], target['workload'])
    defaults = read_resource('cluster-onboarding', 'configmap', 'event-consumer-defaults',
                             ready='.data["values.json"] != null')
    events = json.loads(defaults['data']['values.json'])
    name = subscription['name']
    revision = os.environ['GITHUB_SHA']
    if not TARGET_LABEL.fullmatch(name) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Invalid subscription release identity')
    subscription['platform'] = {
        'events': events,
        'argocd': {
            'enabled': True,
            'repository': f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}.git',
            'revision': revision,
            'chartRevision': os.environ['APPLICATION_CHART_REVISION'],
            'valuesPath': INTENT,
        },
        'lifecycle': {'cleanup': {'enabled': True}},
    }
    inputs = directory / 'alert-subscription.json'
    inputs.write_text(json.dumps(subscription))
    rendered = subprocess.run(
        ['helm', 'template', name, os.environ['CHART_DIRECTORY'], '-f', str(inputs),
         '--show-only', 'templates/argocd/application.yaml',
         '--show-only', 'templates/events/subscriptions.yaml',
         '--show-only', 'templates/credentials/events.yaml'],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout
    documents = [doc for doc in yaml.safe_load_all(rendered) if doc]
    application, = [doc for doc in documents if doc['kind'] == 'Application']
    sensor, = [doc['metadata']['name'] for doc in documents if doc['kind'] == 'Sensor']
    token, = [doc['metadata']['name'] for doc in documents if doc['kind'] == 'GithubAccessToken']
    if not all(TARGET_LABEL.fullmatch(name) for name in (sensor, token)):
        raise ValueError('Invalid rendered Sensor or dispatch credential name')
    manifest = directory / 'alert-application.yaml'
    # The shared registration template owns the Application; sync this pinned intent explicitly.
    manifest.write_text(yaml.safe_dump(application))
    operation = json.dumps({'operation': {'sync': {'prune': True}}})
    result = json.loads(az_aks('command', 'invoke', '--command', f"""
set -e
kubectl apply -f alert-application.yaml
kubectl patch applications.argoproj.io/{name} -n argocd --type=merge -p '{operation}'
kubectl wait -n argocd applications.argoproj.io/{name} --for=jsonpath='{{.status.operationState.syncResult.revisions[1]}}'={revision} --timeout=180s
kubectl wait -n argocd applications.argoproj.io/{name} --for=jsonpath='{{.status.operationState.phase}}'=Succeeded --timeout=180s
kubectl wait -n cluster-onboarding externalsecrets.external-secrets.io/{token} --for=condition=Ready --timeout=60s
kubectl wait -n cluster-onboarding sensors.argoproj.io/{sensor} --for=condition=Deployed --timeout=90s
""", '--file', str(manifest), '-o', 'json', timeout=550))
    if result['exitCode']:
        raise RuntimeError('Alert subscription reconciliation failed; inspect its Argo Application')


if __name__ == '__main__':
    if sys.argv[1] == 'register':
        register(Path(sys.argv[2]))
    elif sys.argv[1] == 'request':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        invocation = request(event['client_payload'], os.environ['GITHUB_RUN_ID'], datetime.now(timezone.utc))
        if invocation is not None:
            Path(sys.argv[2]).write_text(json.dumps(invocation))
            print(f'incident={invocation["event_uid"]}')
    else:
        raise SystemExit('Expected register or request')
