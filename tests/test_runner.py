import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from opentelemetry.sdk.trace import TracerProvider

from agent_runtime.runner import load_agent, load_case, run_case, run_incident


CASE = Path(__file__).resolve().parents[1] / "evals/cases/ai-190-foundry-private-endpoint.yaml"
AGENT = Path(__file__).resolve().parents[1] / "agents/sre.yaml"


class Item:
    def __init__(self, name, call_id):
        self.type = "function_call"
        self.name = name
        self.call_id = call_id
        self.arguments = "{}"
        self.caller = None

    def model_dump(self, exclude_none=True):
        return {"type": self.type, "name": self.name, "call_id": self.call_id, "arguments": self.arguments}


class FakeResponses:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            return SimpleNamespace(status="completed", id="one", output=[Item("get_private_endpoint", "call-1")], output_text="")
        return SimpleNamespace(status="completed", id="two", output=[], output_text="Diagnosis with source owner")


class RunnerTests(unittest.TestCase):
    def test_grader_is_withheld_and_requested_fixture_is_returned(self):
        case = load_case(CASE)
        agent = load_agent(AGENT)
        fake = FakeResponses()
        result = run_case(case, agent, SimpleNamespace(responses=fake), "chat", TracerProvider().get_tracer("test"))

        first = json.dumps(fake.requests[0])
        self.assertNotIn("reference", first)
        self.assertNotIn("rubric", first)
        self.assertNotIn("tool_fixtures", first)
        self.assertEqual(fake.requests[0]["reasoning"], {"effort": "medium"})
        self.assertFalse(fake.requests[0]["store"])
        self.assertEqual(fake.requests[1]["input"][-1]["type"], "function_call_output")
        self.assertEqual(json.loads(fake.requests[1]["input"][-1]["output"])["connection_state"], "Disconnected")
        self.assertEqual(result["tool_calls"], ["get_private_endpoint"])

    def test_unknown_tool_fails_closed(self):
        case = load_case(CASE)
        agent = load_agent(AGENT)
        fake = FakeResponses()
        fake.create = lambda **kwargs: SimpleNamespace(status="completed", id="one", output=[Item("delete_endpoint", "call-1")], output_text="")
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            run_case(case, agent, SimpleNamespace(responses=fake), "chat", TracerProvider().get_tracer("test"))

    def test_hosted_invocation_requests_only_manifest_tool_and_treats_evidence_as_untrusted(self):
        agent = load_agent(AGENT)
        fake = FakeResponses()
        fake.create = lambda **kwargs: SimpleNamespace(status="completed", id="one", output=[Item("workload_state", "call-1")], output_text="")
        incident = {"summary": "Pod restarted", "namespace": "staging", "workload": "web", "available_tools": ["delete_pod"]}
        result = run_incident({"incident": incident},
                              agent, SimpleNamespace(responses=fake), "chat", TracerProvider().get_tracer("test"))
        self.assertEqual(result["status"], "tool_request")
        self.assertEqual(result["tool"], "workload_state")

        def answer(**kwargs):
            self.assertNotIn("workload_state", [tool["name"] for tool in kwargs["tools"]])
            self.assertIn("caller-supplied and untrusted", kwargs["input"][0]["content"])
            return SimpleNamespace(status="completed", id="two", output=[], output_text="Evidence shows one ready replica")

        fake.create = answer
        finished = run_incident({"incident": incident, "history": [{"name": "workload_state", "result": {"ready": 1}}]},
                                agent, SimpleNamespace(responses=fake), "chat", TracerProvider().get_tracer("test"))
        self.assertEqual(finished["status"], "final")
        self.assertIn("ready replica", finished["answer"])

        with self.assertRaisesRegex(ValueError, "Unknown, repeated"):
            run_incident({"incident": incident, "history": [{"name": "delete_pod", "result": {}}]},
                         agent, SimpleNamespace(responses=fake), "chat", TracerProvider().get_tracer("test"))

        def abstain(**kwargs):
            self.assertNotIn("tools", kwargs)
            return SimpleNamespace(status="completed", id="three", output=[], output_text="Insufficient evidence")

        fake.create = abstain
        smoke = run_incident({"incident": {"summary": "Elevated 5xx without diagnostic context"}},
                             agent, SimpleNamespace(responses=fake), "chat", TracerProvider().get_tracer("test"))
        self.assertEqual(smoke["status"], "final")

        with self.assertRaisesRegex(ValueError, "grader or fixture"):
            run_incident({"incident": {"summary": "x", "rubric": {}}},
                         agent, SimpleNamespace(responses=fake), "chat", TracerProvider().get_tracer("test"))


if __name__ == "__main__":
    unittest.main()
