"""Build a trusted fixture record from a Pod and an existing preview publication."""
import argparse
import ast
from base64 import b64decode
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

import yaml

from tools.provenance import REPOSITORY, REVISION, validate_record


def gh(*arguments):
    return subprocess.run(['gh', *arguments], check=True, capture_output=True, text=True, timeout=60).stdout


def source_file(repository, revision, path):
    response = json.loads(gh('api', f'repos/{repository}/contents/{path}?ref={revision}'))
    if response['type'] != 'file' or response['encoding'] != 'base64' or not 0 < response['size'] <= 16384:
        raise ValueError(f'Expected a bounded source file at {path}')
    return b64decode(response['content']).decode()


def record_pod(pod, image, workload, container):
    spec, = (item for item in pod['spec']['containers'] if item['name'] == container)
    status, = (item for item in pod['status']['containerStatuses'] if item['name'] == container)
    if spec['image'] != image or status['imageID'] != image or 'running' not in status['state']:
        raise ValueError('Running Pod image must match the immutable publication')
    metadata = pod['metadata']
    return {'namespace': metadata['namespace'], 'workload': workload, 'name': metadata['name'],
            'uid': metadata['uid'], 'container': container, 'probe': spec['readinessProbe']['httpGet']['path']}


def prepare(pod, repository, run_id, workload, container):
    if not re.fullmatch(REPOSITORY, repository) or not re.fullmatch(r'[1-9][0-9]{0,19}', run_id):
        raise ValueError('An explicit repository and workflow run are required')
    run = json.loads(gh('api', f'repos/{repository}/actions/runs/{run_id}'))
    revision = run['head_sha']
    if (run['head_repository']['full_name'] != repository or run['conclusion'] != 'success'
            or run['event'] != 'push' or run['head_branch'] != 'main'
            or run['path'] != '.github/workflows/publish.yml' or not re.fullmatch(REVISION, revision)):
        raise ValueError('Expected a successful main preview publication')

    attempt = str(run['run_attempt'])
    with TemporaryDirectory() as directory:
        gh('run', 'download', run_id, '--repo', repository,
           '--name', f'artifacts-{revision}-{run_id}-{attempt}', '--dir', directory)
        publication = json.loads((Path(directory) / 'artifacts.json').read_text())
    if (publication['source'] != {'repository': repository, 'revision': revision}
            or publication['workflow'] != {'runId': run_id, 'attempt': attempt}):
        raise ValueError('Artifact does not match the successful source publication')

    backend = publication['images']['backend']
    image = backend['reference']
    if image.rpartition('@')[2] != backend['digest']:
        raise ValueError('Published image reference and digest disagree')
    observed = record_pod(pod, image, workload, container)
    values = yaml.safe_load(source_file(repository, revision, 'deploy/values.yaml'))
    app = ast.parse(source_file(repository, revision, 'backend/app.py'))
    routes = sorted({decorator.args[0].value for node in app.body if isinstance(node, ast.FunctionDef)
                     for decorator in node.decorator_list if isinstance(decorator, ast.Call)
                     and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == 'get'
                     and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == 'app'
                     and decorator.args and isinstance(decorator.args[0], ast.Constant)
                     and isinstance(decorator.args[0].value, str)})
    record = {'repository': repository, 'revision': revision, 'run_id': run_id, 'image': image,
              'pod': observed, 'files': {'deploy/values.yaml': values['components'][workload]['readinessPath'],
                                         'backend/app.py': routes}}
    validate_record(record, observed['namespace'], workload)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository')
    parser.add_argument('run_id')
    parser.add_argument('pod', type=Path)
    parser.add_argument('--workload', required=True)
    parser.add_argument('--container', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(json.loads(args.pod.read_text()), args.repository, args.run_id,
                             args.workload, args.container), separators=(',', ':')))
