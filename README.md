# Agent catalog runtime

This repository versions custom agent instructions and offline cases. The first
agent is `agents/sre.yaml`; the runner in `agent_runtime/` is generic. It calls
the configured Azure Foundry Responses endpoint through Microsoft Entra ID.
The model deployment name, endpoint and trace collector are runtime settings,
so a future gateway or model change does not change the case or tool contract.

## First read-only evaluation

`evals/cases/ai-190-foundry-private-endpoint.yaml` is a reconstructed incident.
The runner sends only `incident` to the model, offers the listed read-only tool
names, and returns a fixture only after the model calls that tool. It never sends
`reference` or `rubric`. It limits model requests and tool calls, uses
`reasoning_effort: medium`, and sets `store: false`. A result contains the final
answer, called tool names, elapsed time and OpenTelemetry trace ID. Trace spans
carry metadata but no incident text or fixture contents. The configured collector
can forward traces to Tempo and Langfuse.

Run locally after `pip install -e .`:

```sh
export AZURE_OPENAI_ENDPOINT=https://<approved-resource>.cognitiveservices.azure.com
export AZURE_OPENAI_DEPLOYMENT=chat
export OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector>:4318
python -m agent_runtime run --agent agents/sre.yaml \
  --case evals/cases/ai-190-foundry-private-endpoint.yaml
```

`python -m agent_runtime serve` exposes only `/healthz` for an internal chart
Deployment. An operator invokes a case with `kubectl exec` for this first
acceptance run; there is no incident intake or remotely callable run endpoint.
The image is published by the existing `ops-shared` Docker workflow. Deploy a
digest-pinned preview image through `application-helm-chart`; platform inputs
supply the Foundry-enabled identity and environment settings. A later delivery
PR will automate revision selection and Argo registration. No GitOps app entry
or live diagnostic permission is required for this recorded-case run.

Human review scores the recorded answer against the six 0–2 criteria and hard
failures in the case file. Record the case and agent versions, image digest, model
deployment, answer, tool calls, trace ID and score together. One reconstructed
case is a diagnostic baseline, not a release gate or evidence of live triage.

The runtime has no Azure or Kubernetes mutation tool. A future live diagnostic
adapter must enforce its own read-only authorization and bounded output before
it can be added; model instructions alone are not an access control.
