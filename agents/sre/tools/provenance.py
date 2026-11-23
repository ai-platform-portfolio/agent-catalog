"""Validate the publisher's bounded, version-specific evidence record."""
import re

from tools.diagnostics import target


REPOSITORY = r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'
REVISION = r'[a-f0-9]{40}'
IMAGE = r'[a-z0-9.-]+/[a-z0-9._/-]+@sha256:[a-f0-9]{64}'
PROBE = r'/[A-Za-z0-9._/-]{0,199}'


def validate_record(record, namespace, workload):
    if not isinstance(record, dict) or set(record) != {
        'repository', 'revision', 'image', 'run_id', 'pod', 'files',
    }:
        raise ValueError('Invalid workload publication record')
    for field, pattern in {'repository': REPOSITORY, 'revision': REVISION, 'image': IMAGE,
                           'run_id': r'[1-9][0-9]{0,19}'}.items():
        if not isinstance(record[field], str) or not re.fullmatch(pattern, record[field]):
            raise ValueError(f'Invalid workload publication {field}')

    pod = record['pod']
    if not isinstance(pod, dict) or set(pod) != {'namespace', 'workload', 'name', 'uid', 'container', 'probe'}:
        raise ValueError('Invalid publication Pod identity')
    target(pod, namespace, workload)
    pod_patterns = {
        'name': re.escape(workload) + r'-[a-z0-9]+-[a-z0-9]+',
        'uid': r'[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}',
        'container': r'[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?',
        'probe': PROBE,
    }
    for field, pattern in pod_patterns.items():
        if not isinstance(pod[field], str) or not re.fullmatch(pattern, pod[field]):
            raise ValueError(f'Invalid publication Pod {field}')

    files = record['files']
    if not isinstance(files, dict) or set(files) != {'deploy/values.yaml', 'backend/app.py'}:
        raise ValueError('Expected the reviewed source paths')
    probe = files['deploy/values.yaml']
    if not isinstance(probe, str) or not re.fullmatch(PROBE, probe):
        raise ValueError('Invalid declared readiness path')
    routes = files['backend/app.py']
    if (not isinstance(routes, list) or not 1 <= len(routes) <= 32
            or any(not isinstance(route, str) or not re.fullmatch(r'/[A-Za-z0-9._/{}-]{0,199}', route)
                   for route in routes)):
        raise ValueError('Invalid source GET routes')
