"""Bind this caller's existing live collectors and native event chart to a target."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

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
    (temporary / 'target.json').write_text(json.dumps(target))


def registration_values(intent, target, release, azure, project, endpoint, *, activate):
    if release['agent'] != os.environ['SERVICE'] or release['source_revision'] != os.environ['GITHUB_SHA']:
        raise ValueError('Broker must match the current release')
    name, namespace = intent['name'], intent['namespace']
    if not all(re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', v) for v in (name, namespace, target['namespace'])):
        raise ValueError('Invalid deployment namespace or name')
    events = yaml.safe_load(Path('deploy/events.yaml').read_text())['events']
    events['sources']['readiness']['resource']['readiness']['namespace'] = target['namespace']
    sensor = events['sensors']['readiness']
    for filt in sensor['dependencies'][0]['filters']['data']:
        if filt['path'] == 'body.involvedObject.name':
            filt['value'] = ['^' + re.escape(target['workload']) + '-']
    job = sensor['triggers'][0]['template']['k8s']['source']['resource']
    job['metadata']['namespace'] = namespace
    pod = job['spec']['template']['spec']
    container = pod['containers'][0]
    container['image'] = release['image']
    fields = {'FOUNDRY_PROJECT_ENDPOINT': endpoint, 'SRE_AGENT': release['agent'], 'SRE_VERSION': release['version'],
              'SRE_SOURCE_REVISION': release['source_revision'], 'SRE_IMAGE': release['image'],
              'SRE_TARGET_NAMESPACE': target['namespace'], 'SRE_TARGET_WORKLOAD': target['workload'],
              'AZURE_TENANT_ID': azure['tenantId'], 'AZURE_FEDERATED_TOKEN_FILE': '/var/run/secrets/azure/token'}
    base = f'{namespace}-{name}-sre-broker'
    configmap = f"{base[:37].rstrip('-')}-{hashlib.sha256(base.encode()).hexdigest()[:12]}-client-id"
    container['env'] = [{'name': k, 'value': v} for k, v in fields.items()] + [
        {'name': 'AZURE_CLIENT_ID', 'valueFrom': {'configMapKeyRef': {'name': configmap, 'key': 'clientId'}}}]
    platform = intent['platform']
    credentials = platform['credentials']
    for resource in (credentials['roles']['reader'], credentials['roleBindings']['reader']):
        resource['metadata'] = {'name': name + '-reader', 'namespace': target['namespace']}
    credentials['roleBindings']['reader']['subjects'] = [
        {'kind': 'ServiceAccount', 'name': 'sre-broker', 'namespace': namespace}]
    credentials['serviceAccounts'] = {'sre-events': {'metadata': {'namespace': 'cluster-onboarding'}, 'automountServiceAccountToken': True}}
    credentials['roles']['create-broker-job'] = {'rules': [{'apiGroups': ['batch'], 'resources': ['jobs'], 'verbs': ['create']}]}
    credentials['roleBindings']['create-broker-job'] = {
        'roleRef': {'kind': 'Role', 'apiGroup': 'rbac.authorization.k8s.io', 'name': 'create-broker-job'},
        'subjects': [{'kind': 'ServiceAccount', 'name': 'sre-events', 'namespace': 'cluster-onboarding'}]}
    platform['events'] = {'serviceAccountName': 'sre-events'}
    # The pinned chart requires a scope enum even when armScope takes precedence.
    platform['azure'] = {**azure, 'identities': {'sre-broker': {'azureName': name + '-broker', 'reflectToApplication': True}},
                         'roleAssignments': {'invoke': {'identity': 'sre-broker', 'scope': 'keyVault', 'armScope': project,
                             'roleDefinitionId': '/subscriptions/' + azure['subscriptionId'] +
                                 '/providers/Microsoft.Authorization/roleDefinitions/eed3b665-ab3a-47b6-8f48-c9382fb1dad6'}}}
    platform['syncWaves'] = {'namespace': -30, 'userAssignedIdentity': -20, 'federatedIdentityCredential': -10, 'roleAssignment': -10}
    platform['argocd'] = dict(enabled=True, repository='https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '.git',
                              revision=os.environ['GITHUB_SHA'], chartRevision=os.environ['APPLICATION_CHART_REVISION'],
                              valuesPath=os.environ['DIAGNOSTICS_INTENT'])
    if not activate:
        events['sensors'] = {}
    intent['events'] = events
    return intent, job


def register(intent, temporary):
    release = json.loads(Path(os.environ['RELEASE_RECORD']).read_text())
    target = json.loads((temporary / 'target.json').read_text())
    project = os.environ['FOUNDRY_PROJECT_ID']
    if not re.fullmatch(r'/subscriptions/[0-9a-f-]+/resourceGroups/[^/]+/providers/Microsoft.CognitiveServices/accounts/[^/]+/projects/[^/]+',
                        project, re.IGNORECASE):
        raise ValueError('Require a Foundry project ARM scope')
    cluster = json.loads(command('az', 'aks', 'show', '-g', os.environ['RESOURCE_GROUP'],
                                '-n', os.environ['CLUSTER'], '-o', 'json'))
    azure = {'subscriptionId': project.split('/')[2], 'tenantId': cluster['aadProfile']['tenantId'],
             'oidcIssuerUrl': cluster['oidcIssuerProfile']['issuerUrl'], 'location': cluster['location'],
             'identityResourceGroup': os.environ['RESOURCE_GROUP']}
    activate = sys.argv[1] == 'activate'
    values, job = registration_values(intent, target, release, azure, project, os.environ['FOUNDRY_PROJECT_ENDPOINT'],
                                      activate=activate)
    source = temporary / 'reader-values.json'
    source.write_text(json.dumps(values))
    name = intent['name']
    if not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', name):
        raise ValueError('Invalid registration identity')
    manifest = temporary / 'reader.yaml'
    app = yaml.safe_load(command('helm', 'template', name, '.application-chart', '-f', str(source),
                                '--show-only', 'templates/argocd/application.yaml'))
    app['spec']['sources'][0]['helm']['valuesObject']['platform'].update(
        {k: values['platform'][k] for k in ('azure', 'credentials', 'syncWaves')})
    app['spec']['sources'][0]['helm']['valuesObject']['events'] = values['events']
    manifest.write_text(json.dumps(app))
    revision = os.environ['GITHUB_SHA']
    remote(f'''kubectl apply -f reader.yaml &&
kubectl patch applications.argoproj.io/{name} -n argocd --type=merge -p '{{"operation":{{"sync":{{"prune":true}}}}}}' &&
kubectl wait -n argocd applications.argoproj.io/{name} --for=jsonpath='{{.status.operationState.syncResult.revisions[1]}}'={revision} --timeout=300s &&
kubectl wait -n argocd applications.argoproj.io/{name} --for=jsonpath='{{.status.operationState.phase}}'=Succeeded --timeout=300s''', manifest)

    if activate:
        event_namespace = 'cluster-onboarding'
        base = f"{job['metadata']['namespace']}-{name}-sensor-readiness"
        sensor = f"{base[:37].rstrip('-')}-{hashlib.sha256(base.encode()).hexdigest()[:12]}"
        remote(f'kubectl wait -n {event_namespace} sensor/{sensor} --for=condition=Deployed --timeout=120s')
    else:
        job['metadata']['name'] = name + '-' + uuid4().hex[:12]
        job['spec']['ttlSecondsAfterFinished'] = 300
        spec = temporary / 'broker.json'
        spec.write_text(json.dumps(job))
        job_name, namespace = job['metadata']['name'], job['metadata']['namespace']
        remote(f'kubectl create -f broker.json >/dev/null', spec)
        output = remote(f'kubectl wait -n {namespace} --for=condition=complete job/{job_name} --timeout=250s >/dev/null && '
                        f'kubectl logs -n {namespace} job/{job_name} -c broker')
        (temporary / 'acceptance' / 'live.json').write_text(json.dumps(json.loads(output)))


if __name__ == '__main__':
    try:
        intent = yaml.safe_load(Path(os.environ['DIAGNOSTICS_INTENT']).read_text())
        temporary = Path(os.environ['RUNNER_TEMP'])
        if sys.argv[1] == 'prepare':
            prepare(intent, temporary)
        elif sys.argv[1] in ('verify', 'activate'):
            register(intent, temporary)
        else:
            raise ValueError('Unknown diagnostic operation')
    except Exception:
        sys.exit('Live diagnostics configuration failed; no raw cloud response logged')
