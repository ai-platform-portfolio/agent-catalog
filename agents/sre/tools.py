"""SRE case study: archived read-only collectors with external Entra authentication.

The hosting runtime is independent of these APIs. No service-account token,
Kubernetes client executable or AKS Job is needed in the Foundry container.
"""
import json
import os
import re
import ssl
from urllib.parse import quote, urlsplit

import httpx

TOOL_DESCRIPTIONS = {
    'workload_state': 'Read the approved workload and its selected Pods, including readiness and restart state.',
    'recent_events': 'Read bounded recent Kubernetes events for the approved workload.',
    'bounded_logs': 'Read at most 8 KiB of recent logs from one selected target Pod.',
    'service_metrics': 'Read fixed CPU and restart metrics for the approved workload.',
}


_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+")
_FIELD = re.compile(r"(?i)(\"?(?:authorization|password|api[_-]?key|token)\"?\s*[:=]\s*\"?)[^\"\s,;]+")


def redact(value: str) -> str:
    """Mask common credential forms; callers must still treat logs as sensitive."""
    return _FIELD.sub(r"\1[REDACTED]", _BEARER.sub(r"\1[REDACTED]", value))


def target(incident: dict, allowed_namespace: str) -> tuple[str, str]:
    namespace, workload = incident.get("namespace"), incident.get("workload")
    if namespace != allowed_namespace or not isinstance(workload, str):
        raise ValueError("Incident target must match the approved namespace")
    if len(namespace) > 63 or len(workload) > 63 or not _DNS_LABEL.fullmatch(namespace) or not _DNS_LABEL.fullmatch(workload):
        raise ValueError("Incident target must use Kubernetes DNS labels")
    return namespace, workload


class EntraAuth(httpx.Auth):
    def __init__(self, credential, scope):
        self.credential, self.scope = credential, scope

    def auth_flow(self, request):
        request.headers['Authorization'] = 'Bearer ' + self.credential.get_token(self.scope).token
        yield request


def https_url(value):
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username is not None or url.query or url.fragment:
        raise ValueError('Tool endpoint must be HTTPS without embedded credentials or query')
    return value.rstrip('/')


def bounded_get(client, path, params=None, limit=262144):
    with client.stream('GET', path, params=params) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes(chunk_size=8192):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('Tool HTTP response exceeded its byte budget')
        return data.decode('utf-8', errors='replace')


class ProvidedTools:
    source = 'caller_provided'
    descriptions = TOOL_DESCRIPTIONS

    def __init__(self, evidence):
        if not isinstance(evidence, dict) or not set(evidence) <= set(TOOL_DESCRIPTIONS):
            raise ValueError('Evidence must contain known read-only tools only')
        for value in evidence.values():
            if not isinstance(value, dict) or len(json.dumps(value, allow_nan=False).encode()) > 24576:
                raise ValueError('Evidence must be an object of at most 24 KiB per tool')
        self.evidence = evidence
        self.names = list(evidence)

    def execute(self, name, incident):
        return json.loads(redact(json.dumps(self.evidence[name])))


class KubernetesTools:
    source = 'live_kubernetes'
    descriptions = TOOL_DESCRIPTIONS

    def __init__(self, api_client, allowed_namespace, metrics_client=None):
        self.api, self.metrics = api_client, metrics_client
        self.allowed_namespace = allowed_namespace
        self.names = [name for name in TOOL_DESCRIPTIONS if name != 'service_metrics' or metrics_client]

    def _get(self, path, params=None):
        return json.loads(bounded_get(self.api, path, params))

    def execute(self, name: str, incident: dict) -> dict:
        # Mask common credentials in every collector, including workload status messages.
        return json.loads(redact(json.dumps(self._collect(name, incident), allow_nan=False)))

    def _collect(self, name: str, incident: dict) -> dict:
        if name not in self.names:
            raise ValueError("Tool is not in the read-only registry")
        namespace, workload = target(incident, self.allowed_namespace)
        prefix = f"/api/v1/namespaces/{quote(namespace)}"

        if name == "workload_state":
            kind = "Deployment"
            try:
                deployment = self._get(f"/apis/apps/v1/namespaces/{quote(namespace)}/deployments/{quote(workload)}")
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
                deployment = self._get(f"/apis/argoproj.io/v1alpha1/namespaces/{quote(namespace)}/rollouts/{quote(workload)}")
                kind = "Rollout"
            selector = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
            if not selector or deployment.get("spec", {}).get("selector", {}).get("matchExpressions"):
                raise ValueError(f"{kind} has no exact pod selector")
            label_selector = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
            pods = self._get(f"{prefix}/pods", {"labelSelector": label_selector, "limit": 20})
            containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            return {
                "deployment": {
                    "kind": kind,
                    "name": workload,
                    "generation": deployment.get("metadata", {}).get("generation"),
                    "observed_generation": deployment.get("status", {}).get("observedGeneration"),
                    "desired": deployment.get("spec", {}).get("replicas"),
                    "ready": deployment.get("status", {}).get("readyReplicas", 0),
                    "conditions": deployment.get("status", {}).get("conditions", [])[:8],
                    "selector": selector,
                    "containers": [
                        {
                            "name": container.get("name"),
                            "image": container.get("image"),
                            "readiness_http_get": {
                                key: container.get("readinessProbe", {}).get("httpGet", {}).get(key)
                                for key in ("path", "port", "scheme")
                                if key in container.get("readinessProbe", {}).get("httpGet", {})
                            },
                        }
                        for container in containers[:8]
                    ],
                },
                "pods": [
                    {
                        "name": pod["metadata"]["name"],
                        "phase": pod.get("status", {}).get("phase"),
                        "restarts": sum(c.get("restartCount", 0) for c in pod.get("status", {}).get("containerStatuses", [])),
                        "waiting": [c.get("state", {}).get("waiting") for c in pod.get("status", {}).get("containerStatuses", []) if c.get("state", {}).get("waiting")],
                    }
                    for pod in sorted(
                        pods.get("items", []),
                        key=lambda item: (
                            sum(c.get("restartCount", 0) for c in item.get("status", {}).get("containerStatuses", [])),
                            any(c.get("state", {}).get("waiting") for c in item.get("status", {}).get("containerStatuses", [])),
                        ),
                        reverse=True,
                    )[:20]
                ],
                "truncated": len(pods.get("items", [])) > 20 or bool(pods.get("metadata", {}).get("continue")),
            }

        if name == "recent_events":
            events = self._get(f"{prefix}/events", {"limit": 100})
            items = sorted(
                (event for event in events.get("items", [])
                 if str(event.get("involvedObject", {}).get("name", "")).startswith(workload)),
                key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True,
            )
            return {"events": [
                {
                    "reason": event.get("reason"),
                    "message": redact(str(event.get("message", "")))[:500],
                    "kind": event.get("involvedObject", {}).get("kind"),
                    "object": event.get("involvedObject", {}).get("name"),
                    "time": event.get("lastTimestamp") or event.get("eventTime"),
                }
                for event in items[:20]
            ], "truncated": len(items) > 20 or bool(events.get("metadata", {}).get("continue"))}

        if name == "bounded_logs":
            state = self.execute("workload_state", incident)
            pods = state["pods"]
            if not pods:
                return {"pod": None, "logs": "", "reason": "No matching pods"}
            pod = pods[0]["name"]
            logs = bounded_get(self.api, f"{prefix}/pods/{quote(pod)}/log",
                               {"tailLines": 50, "limitBytes": 8192, "timestamps": "true"}, limit=8192)
            return {"pod": pod, "logs": redact(logs), "truncated": len(logs.encode()) >= 8192}

        queries = {
            "cpu_5m": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{workload}-.*"}}[5m]))',
            "restarts": f'sum(kube_pod_container_status_restarts_total{{namespace="{namespace}",pod=~"{workload}-.*"}})',
        }
        results = {}
        for key, query in queries.items():
            result = json.loads(bounded_get(self.metrics, "/api/v1/query", {"query": query}, limit=32768))
            series = result.get("data", {}).get("result", [])
            results[key] = [entry.get("value") for entry in series[:5]]
        return {"metrics": results, "scope": namespace}


def live_clients(credential):
    api = httpx.Client(
        base_url=https_url(os.environ['KUBE_API_URL']),
        auth=EntraAuth(credential, '6dae42f8-4368-4678-94ff-3960e28e3630/.default'),
        verify=ssl.create_default_context(cafile=os.getenv('KUBE_CA_FILE')),
        timeout=8, follow_redirects=False,
    )
    metrics = None
    if os.getenv('METRICS_URL'):
        metrics = httpx.Client(base_url=https_url(os.environ['METRICS_URL']),
                               auth=EntraAuth(credential, os.environ['METRICS_TOKEN_SCOPE']),
                               timeout=8, follow_redirects=False)
    return api, metrics


def validate_diagnosis(result, evidence):
    """Case-study guard: model text alone cannot establish actionable evidence."""
    diagnosis = result['diagnosis']
    if (not isinstance(diagnosis, dict) or set(diagnosis) != {'decision', 'answer', 'evidence'}
            or diagnosis['decision'] not in {'actionable', 'abstain'}
            or not isinstance(diagnosis['answer'], str) or not 20 <= len(diagnosis['answer']) <= 2500
            or not isinstance(diagnosis['evidence'], list)
            or any(not isinstance(name, str) for name in diagnosis['evidence'])
            or len(set(diagnosis['evidence'])) != len(diagnosis['evidence'])
            or not set(diagnosis['evidence']) <= set(evidence)):
        raise ValueError('Diagnosis must have the declared structure and cite only executed tools')
    if diagnosis['decision'] == 'actionable':
        if not {'workload_state', 'recent_events'} <= set(diagnosis['evidence']):
            raise ValueError('Actionable diagnosis requires workload and event evidence')
        state = evidence['workload_state'].get('deployment', {})
        desired, ready = state.get('desired'), state.get('ready')
        events = evidence['recent_events'].get('events', [])
        if (type(desired) is not int or desired < 1 or type(ready) is not int or ready >= desired
                or not any(event.get('reason') == 'Unhealthy' and
                           str(event.get('message', '')).startswith('Readiness probe failed:')
                           for event in events if isinstance(event, dict))):
            raise ValueError('Actionable SRE result requires degraded readiness and a matching event')
    return result
