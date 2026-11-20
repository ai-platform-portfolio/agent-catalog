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

    def test_broker_is_read_only_and_sensor_requires_activation(self):
        path = ROOT / 'agents/sre/config/diagnostics.yaml'
        revision = 'a' * 40
        release = {'agent': 'catalog-sre', 'version': '3', 'source_revision': revision, 'image': 'registry/image@sha256:' + 'c' * 64}
        env = {'SERVICE': 'catalog-sre', 'GITHUB_REPOSITORY': 'example/catalog', 'GITHUB_SHA': revision,
               'APPLICATION_CHART_REVISION': 'b'*40, 'DIAGNOSTICS_INTENT': 'agents/sre/config/diagnostics.yaml'}
        target = {'namespace': 'selected-target', 'workload': 'api'}
        azure = {'subscriptionId': 'fixture', 'tenantId': 'fixture', 'oidcIssuerUrl': 'https://oidc.example',
                 'location': 'uksouth', 'identityResourceGroup': 'example'}
        for activate in (False, True):
            with patch.dict(os.environ, env):
                values, job = delivery.registration_values(yaml.safe_load(path.read_text()), target, release,
                    azure, '/example/project', 'https://foundry.example/api/projects/test', activate=activate)
            self.assertEqual(bool(values['events']['sensors']), activate)
            self.assertNotIn('githubTokens', values['events'])
            credentials = values['platform']['credentials']
            binding = credentials['roleBindings']['reader']
            self.assertEqual(binding['metadata']['namespace'], 'selected-target')
            self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'sre-broker',
                                                   'namespace': values['namespace']}])
            self.assertTrue(all(set(rule['verbs']) <= {'get', 'list'} for rule in credentials['roles']['reader']['rules']))
            self.assertEqual(values['platform']['azure']['roleAssignments']['invoke']['armScope'], '/example/project')
            self.assertEqual(job['spec']['template']['spec']['containers'][0]['image'], release['image'])
