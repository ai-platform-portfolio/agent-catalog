"""Candidate-pinned native memory/state acceptance, with synthetic data cleanup."""
import argparse
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from evaluation.run import candidate_request


def validate_retrieval(record, knowledge, version):
    """Require an answer citing the actual retrieved, revision-pinned runbook."""
    deployment = record.get('deployment', {})
    known = {source['file_id']: source for source in knowledge['sources']}
    expected = {key for key, source in known.items() if source['path'] == 'readiness.md'}
    sources = record.get('sources', [])
    cited = record.get('cited_file_ids', [])
    retrieved = {source['file_id'] for source in sources}
    answer = record.get('answer', '').lower()
    if (deployment.get('version') != version or deployment.get('source_revision') != knowledge['revision']
            or not re.fullmatch(r'[a-f0-9]{32}', record.get('trace_id') or '')
            or not sources or any(source != known.get(source['file_id']) for source in sources)
            or not set(cited) <= retrieved or not (set(cited) & expected)
            or 'proposal' not in answer or 'validat' not in answer):
        raise ValueError('Missing, ungrounded or stale hosted runbook citation')


def check(service, environment, version, review, knowledge):
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
        # Remove only recognizable acceptance leftovers from this caller/repository.
        cleaned = 0
        for memory in recalled['memories']:
            if memory['id'] == memory_id:
                continue
            try:
                note = json.loads(memory['content'])
            except (TypeError, ValueError):
                continue
            if (isinstance(note, dict) and isinstance(note.get('content'), str) and isinstance(note.get('review'), str)
                    and re.fullmatch(r'Acceptance marker [0-9a-f]{32}: remember only explicitly reviewed operational context\.', note['content'])
                    and re.fullmatch(re.escape(review.rsplit('/', 1)[0]) + r'/[0-9a-f]{40}', note['review'])):
                invoke({'operation': 'forget', 'memory_id': memory['id']})
                cleaned += 1
        # The question carries no expected answer or fixture evidence. This same
        # invocation exercises retrieval, model citation and a resumable checkpoint.
        incident = {'summary': 'According to the reviewed readiness runbook, what must pass before creating a draft PR? '
                               'Cite the runbook file ID. This is a documentation question, not a request to change a workload.'}
        if os.getenv('KUBE_ALLOWED_NAMESPACE') and os.getenv('KUBE_ALLOWED_WORKLOAD'):
            incident.update(namespace=os.environ['KUBE_ALLOWED_NAMESPACE'], workload=os.environ['KUBE_ALLOWED_WORKLOAD'])
        investigated = invoke({'incident': incident})
        investigation_id = investigated['investigation_id']
        retrieval = {'deployment': investigated['deployment'], 'trace_id': investigated['trace_id'],
                     'answer': investigated['diagnosis']['answer'],
                     'cited_file_ids': investigated['diagnosis']['citations'],
                     'sources': investigated.get('context', {}).get('knowledge', {}).get('citations', [])}
        validate_retrieval(retrieval, knowledge, version)
        checks['grounded_runbook_citation'] = True
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
        return {'version': version, 'checks': checks, 'retrieval': retrieval,
                'model_connection': investigated.get('model_connection'), 'cleaned_stale_test_memories': cleaned,
                'limits': 'New sessions only; container restart, server TTL and a second authenticated caller remain live gates.'}
    finally:
        # Delete only this test's records, never the integration caller's other memory.
        try:
            if memory_id is not None:
                invoke({'operation': 'forget', 'memory_id': memory_id})
        finally:
            if investigation_id is not None:
                invoke({'operation': 'forget_investigation', 'investigation_id': investigation_id})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service')
    parser.add_argument('environment')
    parser.add_argument('version')
    parser.add_argument('--review', required=True)
    parser.add_argument('--knowledge-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(check(args.service, args.environment, args.version, args.review,
                                          json.loads(args.knowledge_manifest.read_text())), indent=2))
