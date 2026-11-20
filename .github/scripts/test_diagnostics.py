import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('diagnostics_delivery', Path(__file__).with_name('diagnostics.py'))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class DiagnosticsDeliveryTests(unittest.TestCase):
    def test_target_uses_registered_repository_and_pr_not_a_copied_namespace(self):
        intent = {'repository': 'example/one', 'pullRequest': 12, 'workload': 'api'}
        def app(repo, namespace):
            return {'spec': {'destination': {'namespace': namespace}, 'source': {'helm': {
                'valuesObject': {'platform': {'release': {'check': {'repository': repo, 'pullRequest': '12'}}}}}}}}
        apps = [app('example/two', 'unrelated'), app('example/one', 'generated-by-delivery')]
        self.assertEqual(delivery.resolve_target(apps, intent), {'namespace': 'generated-by-delivery', 'workload': 'api'})
        for inventory in ([], apps + [app('example/one', 'ambiguous')]):
            with self.assertRaises(ValueError):
                delivery.resolve_target(inventory, intent)

    def test_readers_precede_dispatch_and_bind_only_the_real_instance(self):
        intent = yaml.safe_load((ROOT / 'agents/sre/config/diagnostics.yaml').read_text())
        intent['subscription'] = str(ROOT / intent['subscription'])
        release = {'agent': 'catalog-sre', 'instance_principal_id': '11111111-1111-1111-1111-111111111111'}
        target = {'namespace': 'selected-target', 'workload': 'worker'}
        defaults = {'namespace': 'events', 'eventBus': 'platform', 'github': {}}
        env = {'SERVICE': 'catalog-sre', 'GITHUB_REPOSITORY': 'example/catalog', 'GITHUB_SHA': 'a'*40,
               'APPLICATION_CHART_REVISION': 'b'*40, 'DIAGNOSTICS_INTENT': 'agents/sre/config/diagnostics.yaml'}
        with patch.dict(os.environ, env):
            for activate in (False, True):
                values = delivery.registration_values(intent, target, release, defaults, activate)
                self.assertEqual(bool(values['events']['sensors']), activate)
                self.assertEqual(values['events']['sources']['readiness']['resource']['readiness']['namespace'], target['namespace'])
                credentials = values['platform']['credentials']
                role = credentials['roles']['reader']
                binding = credentials['roleBindings']['reader']
                self.assertEqual(role['metadata']['namespace'], target['namespace'])
                self.assertEqual(binding['subjects'][0]['name'], release['instance_principal_id'])
                self.assertEqual(binding['roleRef']['name'], role['metadata']['name'])
                self.assertTrue(all(set(rule['verbs']) <= {'get', 'list'} for rule in role['rules']))
                self.assertFalse(any('secrets' in rule['resources'] or '*' in rule['resources'] for rule in role['rules']))
            bad = {**release, 'agent': 'another-agent'}
            with self.assertRaises(ValueError):
                delivery.registration_values(intent, target, bad, defaults, True)


if __name__ == '__main__':
    unittest.main()
