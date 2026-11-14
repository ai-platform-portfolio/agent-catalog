"""Foundry protocol adapter and the small composition point for this case study."""
import asyncio
import json
import logging
import os

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.identity import DefaultAzureCredential
from starlette.responses import JSONResponse

from runtime.agent import load_config, model_client, run
from telemetry.tracing import configure, shutdown as shutdown_tracing
from tools.diagnostics import KubernetesTools, ProvidedTools, live_clients, validate_diagnosis


def investigate(payload, config, client, live=None, historical_context=None):
    if not isinstance(payload, dict) or not {'incident'} <= payload.keys() <= {'incident', 'evidence'}:
        raise ValueError('Request needs incident and optional evidence only')
    if live is not None:
        if 'evidence' in payload:
            raise ValueError('Caller evidence cannot override the configured live collectors')
        api, metrics, namespace = live
        tools = KubernetesTools(api, namespace, metrics)
    else:
        tools = ProvidedTools(payload.get('evidence', {}))
    result, evidence = run(payload['incident'], config, client, tools, historical_context)
    return validate_diagnosis(result, evidence)


def build_app(client=None, config=None, **host_options):
    config = config or load_config()
    credential = None
    if client is None:
        credential = DefaultAzureCredential()
        client = model_client(credential)
    backend = os.getenv('DIAGNOSTICS_BACKEND', 'provided')
    live = None
    project = None
    if backend == 'kubernetes':
        credential = credential or DefaultAzureCredential()
        api, metrics = live_clients(credential)
        namespace = os.environ['KUBE_ALLOWED_NAMESPACE']
        live = (api, metrics, namespace)
    elif backend != 'provided':
        raise ValueError('DIAGNOSTICS_BACKEND must be provided or kubernetes')

    os.environ.setdefault('OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', 'false')
    if config.get('memory', {}).get('enabled'):
        from azure.ai.projects import AIProjectClient
        credential = credential or DefaultAzureCredential()
        project = AIProjectClient(endpoint=os.environ['FOUNDRY_PROJECT_ENDPOINT'], credential=credential)

    host_options.setdefault('configure_observability', configure)
    app = InvocationAgentServerHost(**host_options)
    # Bound concurrent model/tool work per container. Request histories stay local.
    slots = asyncio.Semaphore(2)

    @app.invoke_handler
    async def invoke(request):
        async with slots:
            try:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 131072:
                        return JSONResponse({'error': 'Request exceeds 128 KiB'}, status_code=413)
                payload = json.loads(body)
                if project is not None and 'evidence' not in payload:
                    from portfolio_agent_runtime.hosting import invoke as invoke_with_context
                    manifest = json.loads(os.environ['KNOWLEDGE_MANIFEST_JSON'])
                    result = await invoke_with_context(
                        payload, lambda body, history: investigate(body, config, client, live, history),
                        agent=os.environ['FOUNDRY_AGENT_NAME'], project=project,
                        memory_store=os.environ['MEMORY_STORE_NAME'],
                        retention_days=config['memory']['retention_days'], knowledge=manifest)
                else:
                    result = await asyncio.to_thread(investigate, payload, config, client, live)
                return JSONResponse(result)
            except (ValueError, TypeError, KeyError):
                return JSONResponse({'error': 'Invalid incident, evidence or diagnosis'}, status_code=400)
            except Exception:
                # SDK telemetry carries spans, never log incident text or credentials here.
                logging.getLogger(__name__).error('Agent dependency or investigation failed')
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
