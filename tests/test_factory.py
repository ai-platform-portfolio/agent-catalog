import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_factory.manifest import load_agent
from agent_factory.publish import create_draft, version_request


AGENT = Path(__file__).resolve().parents[1] / "agents/sre.yaml"
IMAGE = "localacrk8s.azurecr.io/portfolio/agent-catalog@sha256:" + "a" * 64
REVISION = "b" * 40


class FactoryTests(unittest.TestCase):
    def test_manifest_and_request_are_reusable_and_digest_pinned(self):
        agent = load_agent(AGENT)
        request = version_request(agent, IMAGE, REVISION)
        self.assertEqual(agent.handler, "agent_runtime.runner:run_incident")
        self.assertEqual(request["definition"]["protocol_versions"], [{"protocol": "invocations", "version": "2.0.0"}])
        self.assertEqual(request["definition"]["environment_variables"]["MODEL_DEPLOYMENT_NAME"], "chat")
        self.assertTrue(request["draft"])
        self.assertEqual(json.loads(json.dumps(request))["metadata"]["source_revision"], REVISION)
        with self.assertRaisesRegex(ValueError, "sha256 digest"):
            version_request(agent, "localacrk8s.azurecr.io/portfolio/agent-catalog:latest", REVISION)

    def test_second_agent_uses_the_same_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capacity.yaml"
            path.write_text(AGENT.read_text().replace("id: sre", "id: capacity"))
            request = version_request(load_agent(path), IMAGE, REVISION)
        self.assertEqual(request["agent_name"], "capacity")
        self.assertEqual(request["definition"]["environment_variables"]["AGENT_MANIFEST"], "/app/agents/capacity.yaml")

    @patch("azure.ai.projects.AIProjectClient")
    def test_publish_reuses_identical_revision(self, client_class):
        request = version_request(load_agent(AGENT), IMAGE, REVISION)
        existing = SimpleNamespace(metadata=request["metadata"], version="draft-1")
        client_class.return_value.agents.list_versions.return_value = [existing]
        result = create_draft("https://foundry.example.services.ai.azure.com/api/projects/management", request, credential=object())
        self.assertIs(result, existing)
        client_class.return_value.agents.create_version.assert_not_called()

    @patch("azure.ai.projects.AIProjectClient")
    def test_publish_creates_hosted_draft(self, client_class):
        request = version_request(load_agent(AGENT), IMAGE, REVISION)
        client_class.return_value.agents.list_versions.return_value = []
        create_draft("https://foundry.example.services.ai.azure.com/api/projects/management", request, credential=object())
        call = client_class.return_value.agents.create_version.call_args
        self.assertTrue(call.kwargs["draft"])
        self.assertEqual(call.kwargs["metadata"], request["metadata"])
        self.assertEqual(call.kwargs["definition"].as_dict(), request["definition"])


if __name__ == "__main__":
    unittest.main()
