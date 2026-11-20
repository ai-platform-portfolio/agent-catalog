"""Bind this caller's existing live collectors and native event chart to a target."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID, uuid4

import yaml


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=900)


def remote(script, file=None):
    args = ['az', 'aks', 'command', 'invoke', '--resource-group', os.environ['RESOURCE_GROUP'],
            '--name', os.environ['CLUSTER'], '--command', script, '-o', 'json']
    if file:
        args += ['--file', str(file)]
    result = json.loads(command(*args))
    if result['exitCode'] != 0:
        raise RuntimeError('Native reader reconciliation failed; inspect its Argo Application')
    return result.get('logs') or ''


def resolve_target(applications, intent):
    matches = []
    for app in applications:
        spec = app['spec']
        for source in spec.get('sources', [spec.get('source', {})]):
            check = source.get('helm', {}).get('valuesObject', {}).get('platform', {}).get('release', {}).get('check', {})
            if check.get('repository') == intent['repository'] and str(check.get('pullRequest')) == str(intent['pullRequest']):
                matches.append(spec['destination']['namespace'])
    if len(matches) != 1 or not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', intent['workload']):
        raise ValueError('Require exactly one registered preview and an explicit workload')
    return {'namespace': matches[0], 'workload': intent['workload']}


def prepare(intent, temporary):
    previews = remote('kubectl get applications.argoproj.io -n argocd '
                      '-l platform.ai-platform-portfolio/preview=true -o json')
    apps = json.loads(previews)['items']
    target = resolve_target(apps, intent['target'])
    kubeconfig = temporary / 'diagnostics-kubeconfig'
    command('az', 'aks', 'get-credentials', '--resource-group', os.environ['RESOURCE_GROUP'],
            '--name', os.environ['CLUSTER'], '--file', str(kubeconfig), '--overwrite-existing')
    kubeconfig.chmod(0o600)
    cluster = yaml.safe_load(kubeconfig.read_text())['clusters'][0]['cluster']
    values = {'AGENT_DIAGNOSTICS_BACKEND': 'kubernetes', 'KUBE_API_URL': cluster['server'],
              'KUBE_CA_PEM': base64.b64decode(cluster['certificate-authority-data']).decode(),
              'KUBE_ALLOWED_NAMESPACE': target['namespace'], 'KUBE_ALLOWED_WORKLOAD': target['workload']}
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        for key, value in values.items():
            delimiter = uuid4().hex
            output.write(f'{key}<<{delimiter}\n{value}\n{delimiter}\n')
    (temporary / 'target.json').write_text(json.dumps(target))


def registration_values(intent, target, release):
    if release['agent'] != os.environ['SERVICE'] or release['source_revision'] != os.environ['GITHUB_SHA']:
        raise ValueError('Reader must match the current release')
    principal = str(UUID(release['instance_principal_id']))
    values = intent
    platform = values['platform']
    credentials = platform['credentials']
    for resource in (credentials['roles']['reader'], credentials['roleBindings']['reader']):
        resource['metadata'] = {'name': intent['name'] + '-reader', 'namespace': target['namespace']}
    credentials['roleBindings']['reader']['subjects'] = [
        {'kind': 'User', 'apiGroup': 'rbac.authorization.k8s.io', 'name': principal}]
    platform['argocd'] = dict(enabled=True, repository='https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '.git',
                              revision=os.environ['GITHUB_SHA'], chartRevision=os.environ['APPLICATION_CHART_REVISION'],
                              valuesPath=os.environ['DIAGNOSTICS_INTENT'])
    return values


def register(intent, temporary):
    release = json.loads(Path(os.environ['RELEASE_RECORD']).read_text())
    target = json.loads((temporary / 'target.json').read_text())
    values = registration_values(intent, target, release)
    source = temporary / 'reader-values.json'
    source.write_text(json.dumps(values))
    name = intent['name']
    if not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', name):
        raise ValueError('Invalid registration identity')
    manifest = temporary / 'reader.yaml'
    app = yaml.safe_load(command('helm', 'template', name, '.application-chart', '-f', str(source),
                                '--show-only', 'templates/argocd/application.yaml'))
    # The existing chart only forwards credential overrides for event subscriptions.
    app['spec']['sources'][0]['helm']['valuesObject']['platform']['credentials'] = values['platform']['credentials']
    manifest.write_text(json.dumps(app))
    revision = os.environ['GITHUB_SHA']
    remote(f'''kubectl apply -f reader.yaml &&
kubectl patch applications.argoproj.io/{name} -n argocd --type=merge -p '{{"operation":{{"sync":{{"prune":true}}}}}}' &&
kubectl wait -n argocd applications.argoproj.io/{name} --for=jsonpath='{{.status.operationState.syncResult.revisions[1]}}'={revision} --timeout=300s &&
kubectl wait -n argocd applications.argoproj.io/{name} --for=jsonpath='{{.status.operationState.phase}}'=Succeeded --timeout=300s''', manifest)


if __name__ == '__main__':
    try:
        intent = yaml.safe_load(Path(os.environ['DIAGNOSTICS_INTENT']).read_text())
        temporary = Path(os.environ['RUNNER_TEMP'])
        if sys.argv[1] == 'prepare':
            prepare(intent, temporary)
        else:
            register(intent, temporary)
    except Exception:
        sys.exit('Live diagnostics configuration failed; no raw cloud response logged')
