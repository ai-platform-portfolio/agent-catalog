"""Read bounded ARM evidence and verify designated data-plane fixtures."""
import json
import re

import httpx
from opentelemetry import trace

from tools.diagnostics import EntraAuth, bounded_get


ARM_URL = 'https://management.azure.com'
ACCOUNT_API_VERSION = '2025-06-01'
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


class AzureTools:
    source = 'live_arm'
    names = ('read_foundry_account',)
    descriptions = {'read_foundry_account': 'Read current Foundry account provisioning and private-link states from ARM.'}

    def __init__(self, credential, project_id, secret_url, blob_url):
        self.account_id = foundry_account(project_id)
        if not re.fullmatch(SECRET_URL, secret_url) or not re.fullmatch(BLOB_URL, blob_url):
            raise ValueError('Expected designated Azure read acceptance fixture URLs')
        self.credential, self.secret_url, self.blob_url = credential, secret_url, blob_url
        self.client = httpx.Client(base_url=ARM_URL, auth=EntraAuth(credential, f'{ARM_URL}/.default'),
                                   timeout=8, follow_redirects=False)

    def validate_target(self, incident):
        resource_id = incident.get('azure_resource_id')
        if (set(incident) != {'summary', 'azure_resource_id'} or not isinstance(resource_id, str)
                or resource_id.lower() != self.account_id.lower()):
            raise ValueError('Azure investigation requires the configured Foundry account ID')

    def execute(self, name, incident):
        self.validate_target(incident)
        if name not in self.names:
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
