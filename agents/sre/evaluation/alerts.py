"""Build a bounded SRE request from a Grafana notification."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


TARGET_LABEL = re.compile(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?')


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


if __name__ == '__main__':
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    payload = event['client_payload']
    if payload.pop('agent', None) != 'sre':
        raise ValueError('Readiness alert must target SRE')
    invocation = request(payload, os.environ['GITHUB_RUN_ID'], datetime.now(timezone.utc))
    if invocation is not None:
        Path(sys.argv[1]).write_text(json.dumps(invocation))
        print(f'incident={invocation["event_uid"]}')
