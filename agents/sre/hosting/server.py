"""Foundry protocol adapter and the composition point for this case study."""
import asyncio
import json
import logging
import os
from pathlib import Path
import re

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.identity import DefaultAzureCredential
from opentelemetry import trace
from starlette.responses import JSONResponse
import yaml

from portfolio_agent_runtime.coding import propose_request, safe_path
from portfolio_agent_runtime.context import bounded, identifier
from portfolio_agent_runtime.hosting import invoke as persistent_invoke
from portfolio_agent_runtime.intake import investigate_event
from runtime.agent import load_config, model_client, run
from telemetry.tracing import configure, shutdown as shutdown_tracing
from tools.diagnostics import KubernetesTools, ProvidedTools, https_url, live_clients, target, validate_diagnosis


class InvalidRequest(ValueError):
    """Only validation at the HTTP input boundary produces a client error."""


def validate_request(payload, live, persistent, policy):
    """Validate caller fields before crossing the model/SDK boundary."""
    shapes = {
        'investigate': ({'incident'}, {'evidence', 'investigation_id'}),
        'event': ({'event_uid', 'namespace', 'workload', 'delivery_id'}, set()),
        'propose': ({'incident_id', 'base_sha', 'diagnosis', 'files'}, set()),
        'remember': ({'content', 'review'}, set()),
        'recall': ({'query'}, set()),
        'forget': (set(), {'memory_id'}),
        'resume': ({'investigation_id'}, set()),
        'forget_investigation': ({'investigation_id'}, set()),
    }
    try:
        operation = payload.get('operation', 'investigate')
        required, optional = shapes[operation]
        if not required <= payload.keys() <= required | optional | {'operation'}:
            raise ValueError('Unexpected fields')
        if operation == 'investigate':
            incident = bounded(payload['incident'])
            if not isinstance(incident, dict) or not isinstance(incident.get('summary'), str) or not incident['summary']:
                raise ValueError('Incident summary is required')
            if 'evidence' in payload:
                ProvidedTools(payload['evidence'])
                if 'investigation_id' in payload:
                    raise ValueError('Replays cannot select a persistent checkpoint')
            elif live:
                target(incident, live[2], live[3])
        elif operation == 'event':
            if not live:
                raise ValueError('Event intake requires live collectors')
            target(payload, live[2], live[3])
            identifier(payload['event_uid'])
            identifier(payload['delivery_id'])
        elif operation == 'propose':
            if not re.fullmatch(r'[a-f0-9]{40}', payload['base_sha']) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', payload['incident_id']):
                raise ValueError('Invalid proposal identity')
            files, diagnosis = payload['files'], payload['diagnosis']
            if not isinstance(files, dict) or not 1 <= len(files) <= 32 or len(json.dumps(files).encode()) > 98304:
                raise ValueError('Invalid source snapshot')
            for path, content in files.items():
                safe_path(path)
                if (not isinstance(content, str) or len(content.encode()) > 16384
                        or not any(path.startswith(prefix) for prefix in policy['context_paths'])):
                    raise ValueError('Invalid source path or content')
            if (not isinstance(diagnosis, dict) or set(diagnosis) != {'answer', 'trace_id'}
                    or not isinstance(diagnosis['answer'], str) or len(diagnosis['answer']) > 12000
                    or not re.fullmatch(r'[a-f0-9]{32}', diagnosis['trace_id'])):
                raise ValueError('Invalid proposal diagnosis')
        elif not persistent:
            raise ValueError('Persistent operations are disabled')
        elif operation == 'remember':
            content, review = payload['content'], payload['review']
            if (not isinstance(content, str) or not 20 <= len(content) <= 2048
                    or not isinstance(review, dict) or set(review) != {'approved', 'url'} or review['approved'] is not True):
                raise ValueError('Remember requires approved content')
            https_url(review['url'])
        elif operation == 'recall' and (not isinstance(payload['query'], str) or not 1 <= len(payload['query']) <= 1024):
            raise ValueError('Invalid recall query')
        for field in ('investigation_id', 'memory_id'):
            if field in payload:
                if field == 'memory_id' and payload[field] is None:
                    continue
                if not persistent:
                    raise ValueError('Persistent operations are disabled')
                identifier(payload[field])
        return operation
    except (AttributeError, KeyError, TypeError, ValueError):
        raise InvalidRequest('Invalid request fields') from None


def investigate(payload, config, client, live=None, historical_context=None):
    if not isinstance(payload, dict) or not {'incident'} <= payload.keys() <= {'incident', 'evidence'}:
        raise ValueError('Request needs incident and optional evidence only')
    if 'evidence' in payload:
        tools = ProvidedTools(payload['evidence'])
    elif live is not None:
        api, metrics, namespace, workload = live
        tools = KubernetesTools(api, namespace, workload, metrics)
    else:
        tools = ProvidedTools({})
    result, evidence = run(payload['incident'], config, client, tools, historical_context)
    return validate_diagnosis(result, evidence, historical_context)


def build_app(client=None, config=None, **host_options):
    config = config or load_config()
    policy = yaml.safe_load((Path(__file__).resolve().parents[1] / 'config/coding.yaml').read_text())
    credential = None
    if client is None:
        credential = DefaultAzureCredential()
        client = model_client(credential)
    backend = os.getenv('DIAGNOSTICS_BACKEND', 'provided')
    live = project = None
    context_settings = {}
    if backend == 'kubernetes':
        namespace, workload = os.environ['KUBE_ALLOWED_NAMESPACE'], os.environ['KUBE_ALLOWED_WORKLOAD']
        target({'namespace': namespace, 'workload': workload}, namespace, workload)
        credential = credential or DefaultAzureCredential()
        api, metrics = live_clients(credential)
        live = (api, metrics, namespace, workload)
    elif backend != 'provided':
        raise ValueError('DIAGNOSTICS_BACKEND must be provided or kubernetes')
    if config.get('memory', {}).get('enabled'):
        from azure.ai.projects import AIProjectClient
        credential = credential or DefaultAzureCredential()
        context_settings = dict(agent=os.environ['FOUNDRY_AGENT_NAME'],
                                memory_store=os.environ['MEMORY_STORE_NAME'],
                                retention_days=config['memory']['retention_days'],
                                knowledge=json.loads(os.environ['KNOWLEDGE_MANIFEST_JSON']))
        project = AIProjectClient(endpoint=os.environ['FOUNDRY_PROJECT_ENDPOINT'], credential=credential,
                                  connection_timeout=5, read_timeout=15, retry_total=0)

    os.environ.setdefault('OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', 'false')
    host_options.setdefault('configure_observability', configure)
    app = InvocationAgentServerHost(**host_options)
    slots = asyncio.Semaphore(2)

    async def investigation(body):
        if project is not None and 'evidence' not in body:
            return await persistent_invoke(body, lambda request_body, history: investigate(request_body, config, client, live, history),
                                           project=project, **context_settings)
        return await asyncio.to_thread(investigate, {key: body[key] for key in ('incident', 'evidence') if key in body},
                                       config, client, live)

    @app.invoke_handler
    async def invoke(request):
        async with slots:
            operation = 'unknown'
            try:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 131072:
                        return JSONResponse({'error': 'Request exceeds 128 KiB'}, status_code=413)
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    raise InvalidRequest('Invalid JSON') from None
                operation = validate_request(payload, live, project is not None, policy)
                if operation == 'event':
                    result = await investigate_event(payload, investigation, agent=os.environ['FOUNDRY_AGENT_NAME'],
                                                     namespace=live[2], workload=live[3])
                elif operation == 'propose':
                    proposal = await asyncio.to_thread(propose_request, payload, policy, client,
                                                       os.getenv('MODEL_DEPLOYMENT_NAME', config['model']))
                    result = {'proposal': proposal, 'deployment': {
                        'agent': os.getenv('FOUNDRY_AGENT_NAME'), 'version': os.getenv('FOUNDRY_AGENT_VERSION'),
                        'source_revision': os.getenv('SOURCE_REVISION'), 'image': os.getenv('RELEASE_IMAGE')}}
                else:
                    result = await investigation(payload)
                return JSONResponse(result)
            except InvalidRequest:
                return JSONResponse({'error': 'Invalid request'}, status_code=400)
            except Exception as error:
                span = trace.get_current_span().get_span_context()
                trace_id = f'{span.trace_id:032x}' if span.is_valid else '-'
                # Deliberately omit exception messages, traceback locals and payloads.
                status = getattr(error, 'status_code', None)
                status = status if type(status) is int and 100 <= status <= 599 else None
                logging.getLogger(__name__).error(
                    'Agent failure operation=%s error_type=%s upstream_status=%s trace_id=%s',
                    operation, type(error).__name__, status, trace_id)
                return JSONResponse({'error': 'Investigation failed'}, status_code=502)

    @app.shutdown_handler
    async def shutdown():
        client.close()
        if project is not None:
            project.close()
        if live:
            live[0].close()
            if live[1] is not None:
                live[1].close()
        if credential is not None:
            credential.close()
        await asyncio.to_thread(shutdown_tracing)

    return app


if __name__ == '__main__':
    build_app().run()
