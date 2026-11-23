from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock

import yaml

from evaluation.alerts import INTENT, request, scoped_events
from hosting.server import validate_request
from tools.telemetry import TelemetryTools


class AlertsTests(unittest.TestCase):
    now = datetime(2026, 11, 23, 23, tzinfo=timezone.utc)
    alert = {
        'cluster': 'management', 'namespace': 'preview-example', 'workload': 'api',
        'fingerprint': 'c6eadffa33fcdf37', 'starts_at': '2026-11-23T22:55:00Z',
    }

    def test_repeats_share_an_incident_and_a_new_firing_has_a_new_id(self):
        original = request(self.alert, '100', self.now)
        repeat = request(dict(self.alert, starts_at='2026-11-23T22:55:00+00:00'), '101', self.now)
        new = request(dict(self.alert, starts_at='2026-11-23T22:56:00Z'), '102', self.now)
        self.assertEqual(original['event_uid'], repeat['event_uid'])
        self.assertNotEqual(original['delivery_id'], repeat['delivery_id'])
        self.assertNotEqual(original['event_uid'], new['event_uid'])

    def test_rejects_untrusted_metadata_and_ignores_expired_alerts(self):
        with self.assertRaises(ValueError):
            request(dict(self.alert, summary='untrusted alert content'), '100', self.now)
        self.assertIsNone(request(self.alert, '100', self.now + timedelta(days=1)))
        self.assertIsNone(request(self.alert, '100', self.now - timedelta(hours=1)))

    def test_registration_maps_alert_to_the_resolved_target(self):
        intent = Path(__file__).resolve().parents[3] / INTENT
        events = yaml.safe_load(intent.read_text())['events']
        scoped_events(events, 'management', 'preview-example', 'api')
        sensor = events['sensors']['readiness']
        filters = {f['path']: f['value'] for f in sensor['dependencies'][0]['filters']['data']}
        self.assertEqual(filters['body.alerts.0.labels.cluster'], ['^management$'])
        self.assertEqual(filters['body.alerts.0.labels.namespace'], [r'^preview\-example$'])
        self.assertEqual(sensor['triggers'][0]['template']['http']['payload'][-1], {
            'src': {'dependencyName': 'firing', 'value': 'api'}, 'dest': 'client_payload.workload',
        })

    def test_alert_intake_requires_the_scoped_telemetry_backend(self):
        payload = request(self.alert, '100', self.now)
        live = (Mock(spec=TelemetryTools), None, 'preview-example', 'api')
        self.assertEqual(validate_request(payload, live, True, {}), 'alert')
        for backend in (None, (Mock(), None, 'preview-example', 'api'),
                        (live[0], None, 'another-preview', 'api')):
            with self.assertRaises(ValueError):
                validate_request(payload, backend, True, {})
        with self.assertRaises(ValueError):
            validate_request(dict(payload, operation='event'), live, True, {})
