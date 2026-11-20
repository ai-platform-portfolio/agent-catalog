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

    def test_reader_binds_only_the_current_instance_in_the_target_namespace(self):
        intent = yaml.safe_load((ROOT / 'agents/sre/config/diagnostics.yaml').read_text())
        revision = 'a' * 40
        principal = '11111111-1111-1111-1111-111111111111'
        release = {'agent': 'catalog-sre', 'instance_principal_id': principal, 'source_revision': revision}
        env = {'SERVICE': 'catalog-sre', 'GITHUB_REPOSITORY': 'example/catalog', 'GITHUB_SHA': revision,
               'APPLICATION_CHART_REVISION': 'b'*40, 'DIAGNOSTICS_INTENT': 'agents/sre/config/diagnostics.yaml'}
        target = {'namespace': 'selected-target', 'workload': 'api'}
        with patch.dict(os.environ, env):
            values = delivery.registration_values(intent, target, release)
            with self.assertRaises(ValueError):
                delivery.registration_values(intent, target, {**release, 'source_revision': 'c'*40})
        credentials = values['platform']['credentials']
        binding = credentials['roleBindings']['reader']
        self.assertEqual(binding['metadata']['namespace'], 'selected-target')
        self.assertEqual([subject['name'] for subject in binding['subjects']], [principal])
        self.assertTrue(all(set(rule['verbs']) <= {'get', 'list'} for rule in credentials['roles']['reader']['rules']))
