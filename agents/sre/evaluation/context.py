"""Candidate-pinned native memory/state acceptance, with synthetic data cleanup."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from evaluation.run import candidate_request


def check(service, environment, version, review):
    marker = 'Acceptance marker ' + uuid4().hex + ': remember only explicitly reviewed operational context.'
    memory_id = investigation_id = None
    checks = {}
    def invoke(body):
        return candidate_request(service, environment, version, body)
    try:
        remembered = invoke({'operation': 'remember', 'content': marker,
                             'review': {'approved': True, 'url': review}})
        memory_id = remembered['id']
        recalled = invoke({'operation': 'recall', 'query': marker})
        checks['recall_in_new_session'] = any(m['id'] == memory_id and marker in m['content'] for m in recalled['memories'])
        incident = {'summary': 'Investigate readiness and distinguish current observations from uncertainty.'}
        if os.getenv('KUBE_ALLOWED_NAMESPACE') and os.getenv('KUBE_ALLOWED_WORKLOAD'):
            incident.update(namespace=os.environ['KUBE_ALLOWED_NAMESPACE'], workload=os.environ['KUBE_ALLOWED_WORKLOAD'])
        investigated = invoke({'incident': incident})
        investigation_id = investigated['investigation_id']
        resumed = invoke({'operation': 'resume', 'investigation_id': investigation_id})
        checks['checkpoint_in_new_session'] = resumed['checkpoint']['trace_id'] == investigated['trace_id']
        invoke({'operation': 'forget', 'memory_id': memory_id})
        checks['forget_memory'] = not any(m['id'] == memory_id for m in invoke({'operation': 'recall', 'query': marker})['memories'])
        memory_id = None
        invoke({'operation': 'forget_investigation', 'investigation_id': investigation_id})
        checks['forget_checkpoint'] = invoke({'operation': 'resume', 'investigation_id': investigation_id})['checkpoint'] is None
        investigation_id = None
        if not all(checks.values()):
            raise ValueError('Native durable context acceptance failed')
        return {'version': version, 'checks': checks,
                'limits': 'New sessions only; container restart, server TTL and a second authenticated caller remain live gates.'}
    finally:
        # Delete only this test's records, never the integration caller's other memory.
        if memory_id is not None:
            invoke({'operation': 'forget', 'memory_id': memory_id})
        if investigation_id is not None:
            invoke({'operation': 'forget_investigation', 'investigation_id': investigation_id})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service')
    parser.add_argument('environment')
    parser.add_argument('version')
    parser.add_argument('--review', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(check(args.service, args.environment, args.version, args.review), indent=2))
