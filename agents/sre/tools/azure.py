"""Read bounded ARM evidence and verify designated data-plane fixtures."""
from datetime import datetime, timedelta, timezone
import json
import re

import httpx
from opentelemetry import trace

from tools.diagnostics import EntraAuth, bounded_get


ARM_URL = 'https://management.azure.com'
ACCOUNT_API_VERSION = '2025-06-01'
RESOURCE_ID = (
    r'/subscriptions/([0-9a-f-]{36})/resourceGroups/[a-z0-9_.()-]{1,90}'
    r'/providers/[a-z0-9.]+(?:/[a-z0-9_.()-]+/[a-z0-9_.()@-]+)+'
)
ACCOUNT_ID = (
    r'/subscriptions/[0-9a-f-]{36}/resourceGroups/[a-z0-9_.()-]{1,90}'
    r'/providers/Microsoft\.CognitiveServices/accounts/[a-z0-9][a-z0-9_.-]{1,63}'
)
SECRET_URL = r'https://[a-z0-9-]{3,24}\.vault\.azure\.net/secrets/sre-azure-read-check'
BLOB_URL = r'https://[a-z0-9]{3,24}\.blob\.core\.windows\.net/sre-azure-acceptance/read-check\.txt'


def foundry_account(project_id):
    if not re.fullmatch(ACCOUNT_ID + r'/projects/[a-z0-9][a-z0-9_.-]{0,63}', project_id, re.I):
        raise ValueError('Expected a Foundry project resource ID')
    return project_id.rsplit('/', 2)[0]


def activity_window(event_time):
    now = datetime.now(timezone.utc)
    if event_time is None:
        return now - timedelta(days=2), now
    if not isinstance(event_time, str) or len(event_time) > 40:
        raise ValueError('Azure event time must be an ISO 8601 timestamp with a time zone')
    point = datetime.fromisoformat(event_time)
    if point.tzinfo is None or not now - timedelta(days=90) <= point <= now:
        raise ValueError('Azure event time must identify a time within Activity Log retention')
    point = point.astimezone(timezone.utc)
    radius = timedelta(minutes=15)
    return point - radius, min(point + radius, now)


def failed_activity(row, resource):
    operation = (row.get('operationName') or {}).get('value', '')
    if ((row.get('resourceId') or '').lower() != resource.lower()
            or (row.get('status') or {}).get('value') != 'Failed'
            or not re.fullmatch(r'[a-z0-9.]+(?:/[a-z0-9_.-]+)+', operation, re.I)):
        return None
    timestamp = row.get('eventTimestamp', '')
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?Z', timestamp):
        return None

    details = (row.get('properties') or {}).get('statusMessage') or '{}'
    try:
        error = json.loads(details).get('error') or {}
    except (ValueError, AttributeError, TypeError):
        error = {}
    code = error.get('code', '') if isinstance(error, dict) else ''
    event = {'operation': operation, 'timestamp': timestamp, 'status': 'Failed'}
    if isinstance(code, str) and re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{0,95}', code):
        event['error_code'] = code
    sub_status = (row.get('subStatus') or {}).get('value', '')
    if isinstance(sub_status, str) and re.fullmatch(r'[a-zA-Z0-9_ -]{1,50}', sub_status):
        event['sub_status'] = sub_status
    correlation = row.get('correlationId') or ''
    if re.fullmatch(r'[0-9a-f-]{36}', correlation, re.I):
        event['correlation_id'] = correlation

    if code == 'CannotDeleteResource':
        message = error.get('message') or ''
        if isinstance(message, str) and 'Cannot delete resource while nested resources exist' in message:
            parent = re.escape(re.split('/providers/', resource, maxsplit=1, flags=re.I)[1])
            children = re.findall(r"'" + parent + r"((?:/[a-z0-9_.()-]+/[a-z0-9_.()@-]+)+)'", message, re.I)
            event['blocker'] = 'nested_resources_exist'
            event['nested_resources'] = [resource + child for child in children[:3]]
    return event


class ActivityLogTools:
    source = 'live_arm'
    names = ('read_azure_activity',)
    descriptions = {'read_azure_activity': 'Read failed Azure Activity Log events for this resource and time window.'}

    def __init__(self, client, subscription_scope, resource, window):
        self.client, self.resource, self.window = client, resource, window
        self.path = subscription_scope + '/providers/Microsoft.Insights/eventtypes/management/values'

    def execute(self, name, incident):
        if name not in self.names or incident.get('azure_resource_id') != self.resource:
            raise ValueError('Unexpected Activity Log tool target')
        start, end = (point.isoformat(timespec='seconds').replace('+00:00', 'Z') for point in self.window)
        query = {
            'api-version': '2015-04-01',
            '$filter': f"eventTimestamp ge '{start}' and eventTimestamp le '{end}' and resourceUri eq '{self.resource}'",
            '$select': 'resourceId,eventTimestamp,operationName,status,subStatus,correlationId,properties',
        }
        try:
            payload = json.loads(bounded_get(self.client, self.path, query))
        except httpx.HTTPStatusError as error:
            return {'status': error.response.status_code, 'available': False}
        except httpx.RequestError:
            return {'available': False}
        failed = [event for row in payload['value'] if (event := failed_activity(row, self.resource))]
        failed.sort(key=lambda event: event['timestamp'], reverse=True)
        return {'status': 200, 'available': True, 'window_start': start, 'window_end': end,
                'failed_operations': failed[:5], 'truncated': len(failed) > 5 or bool(payload.get('nextLink'))}


class AzureTools:
    source = 'live_arm'
    names = ('read_foundry_account',)
    descriptions = {'read_foundry_account': 'Read current Foundry account provisioning and private-link states from ARM.'}

    def __init__(self, credential, project_id, secret_url, blob_url):
        self.account_id = foundry_account(project_id)
        self.subscription_scope = '/'.join(self.account_id.split('/', 3)[:3])
        if not re.fullmatch(SECRET_URL, secret_url) or not re.fullmatch(BLOB_URL, blob_url):
            raise ValueError('Expected designated Azure read acceptance fixture URLs')
        self.credential, self.secret_url, self.blob_url = credential, secret_url, blob_url
        self.client = httpx.Client(base_url=ARM_URL, auth=EntraAuth(credential, f'{ARM_URL}/.default'),
                                   timeout=8, follow_redirects=False)

    def validate_target(self, incident):
        resource_id = incident.get('azure_resource_id')
        if (set(incident) - {'summary', 'azure_resource_id', 'azure_event_time'} or not isinstance(resource_id, str)
                or len(resource_id) > 512 or not re.fullmatch(RESOURCE_ID, resource_id, re.I)
                or not resource_id.lower().startswith(self.subscription_scope.lower() + '/')):
            raise ValueError('Azure investigation requires a resource ID in the configured subscription')
        if 'azure_event_time' in incident:
            activity_window(incident['azure_event_time'])

    def for_incident(self, incident):
        self.validate_target(incident)
        resource = incident['azure_resource_id']
        if resource.lower() == self.account_id.lower() and 'azure_event_time' not in incident:
            return self
        return ActivityLogTools(self.client, self.subscription_scope, resource,
                                activity_window(incident.get('azure_event_time')))

    def execute(self, name, incident):
        self.validate_target(incident)
        if name not in self.names or incident['azure_resource_id'].lower() != self.account_id.lower():
            raise ValueError('Unknown ARM evidence tool')
        try:
            payload = json.loads(bounded_get(self.client, self.account_id, {'api-version': ACCOUNT_API_VERSION}))
        except httpx.HTTPStatusError as error:
            return {'status': error.response.status_code, 'available': False}
        except httpx.RequestError:
            return {'available': False}
        if payload.get('id', '').lower() != self.account_id.lower():
            raise ValueError('ARM returned an unexpected resource')
        properties = payload.get('properties') or {}
        provisioning = properties.get('provisioningState')
        connection_states = set()
        for connection in properties.get('privateEndpointConnections') or []:
            state = (connection.get('properties') or {}).get('privateLinkServiceConnectionState') or {}
            status = state.get('status')
            connection_states.add(status if status in {'Approved', 'Disconnected', 'Pending', 'Rejected'} else 'Unknown')
        return {
            'status': 200,
            'available': True,
            'provisioning_state': provisioning if provisioning in {'Succeeded', 'Failed', 'Creating', 'Deleting'} else 'Unknown',
            'connection_states': sorted(connection_states),
            'unverified': ['DNS', 'end-to-end connectivity'],
        }

    def verify_data_reads(self):
        # Read only the designated fixtures. Bodies never enter the response, model or trace.
        with trace.get_tracer(__name__).start_as_current_span('agent.azure.verify_data_reads') as span:
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                with client.stream('GET', self.secret_url, params={'api-version': '7.4'},
                                   auth=EntraAuth(self.credential, 'https://vault.azure.net/.default')) as secret:
                    secret_status = secret.status_code
                with client.stream('GET', self.blob_url, headers={'x-ms-version': '2023-11-03', 'Range': 'bytes=0-0'},
                                   auth=EntraAuth(self.credential, 'https://storage.azure.com/.default')) as blob:
                    blob_status = blob.status_code
            span.set_attribute('azure.key_vault.status', secret_status)
            span.set_attribute('azure.blob.status', blob_status)
            context = span.get_span_context()
            return {'key_vault': secret_status, 'blob': blob_status,
                    'trace_id': f'{context.trace_id:032x}' if context.is_valid else None}
