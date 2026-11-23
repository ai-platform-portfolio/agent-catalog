"""Read-only LGTM tools for one approved Kubernetes Deployment."""
from datetime import datetime, timezone
import json
from urllib.parse import quote

import httpx

from tools.diagnostics import EntraAuth, bounded_get, https_url, redact, target


class TelemetryTools:
    source = 'live_lgtm'
    names = ['workload_state', 'recent_events']
    descriptions = {
        'workload_state': 'Read desired and available Deployment replicas and observed generation from Mimir.',
        'recent_events': 'Read matching Kubernetes readiness Events from Loki from the last five minutes.',
    }

    def __init__(self, client, queries, namespace, workload):
        self.client = client
        self.queries = queries
        self.namespace = namespace
        self.workload = workload

    def _query(self, name):
        result = json.loads(bounded_get(self.client, f'{name}?{self.queries[name]}', limit=32768))
        if result.get('status') != 'success':
            raise ValueError('Telemetry backend did not return success')
        return result['data']['result']

    def execute(self, name, incident):
        namespace, workload = target(incident, self.namespace, self.workload)
        if name == 'workload_state':
            metrics = {}
            for series in self._query('replicas'):
                metric = series['metric']
                if metric.get('namespace') != namespace or metric.get('deployment') != workload:
                    raise ValueError('Telemetry returned an unexpected target')
                key = metric['__name__']
                number = float(series['value'][1])
                if key in metrics or not number.is_integer() or number < 0:
                    raise ValueError('Unexpected or ambiguous replica metric')
                metrics[key] = int(number)
            return {'deployment': {
                'name': workload,
                'desired': metrics['kube_deployment_spec_replicas'],
                'available': metrics['kube_deployment_status_replicas_available'],
                'observed_generation': metrics['kube_deployment_status_observed_generation'],
            }}
        if name != 'recent_events':
            raise ValueError('Tool is not in the telemetry registry')
        events = []
        for stream in self._query('events'):
            labels = stream['stream']
            if labels.get('namespace') != namespace:
                raise ValueError('Telemetry returned an unexpected event namespace')
            for timestamp, line in stream['values']:
                _, marker, message = line.partition('Readiness probe failed:')
                if marker:
                    events.append({'object': labels['name'], 'reason': labels['reason'],
                                   'message': redact(marker + message)[:500],
                                   'time': datetime.fromtimestamp(int(timestamp) / 10**9, timezone.utc).isoformat()})
        events.sort(key=lambda event: event['time'], reverse=True)
        return {'events': events[:20], 'truncated': len(events) >= 20}

    def verify_boundary(self):
        # Only this fixed scope change is sent. The invoker cannot supply a path or query.
        namespace = quote(f'namespace="{self.namespace}"', safe='')
        foreign = quote('namespace="telemetry-boundary-probe"', safe='')
        results = {}
        for name, query in self.queries.items():
            changed = query.replace(namespace, foreign)
            if changed == query:
                raise ValueError('Telemetry query has no configured scope')
            with self.client.stream('GET', f'{name}?{changed}') as response:
                results[name] = response.status_code
        return results


def telemetry_tools(credential, connection, namespace, workload):
    client = httpx.Client(base_url=https_url(connection['endpoint']), follow_redirects=False,
                          auth=EntraAuth(credential, connection['scope']), timeout=8)
    return TelemetryTools(client, connection['queries'], namespace, workload)
