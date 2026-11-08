# Foundry agent catalog

This repository defines and packages custom agents for the `management` Foundry
project. An agent is a reviewed YAML manifest plus an importable Python handler.
The shared factory validates manifests and prepares a draft Hosted Agent version
from an immutable image. The SRE agent is the first consumer, not a hardcoded
platform default.

## Add an agent

1. Add `agents/<id>.yaml` with `kind: hosted`, `protocol: invocations`, a model
   deployment, handler, instructions, evaluation suite and bounded limits.
2. Implement the named handler in `agent_runtime/`. It receives the invocation
   payload, validated manifest, model client, deployment name and tracer. Return
   a JSON-serializable result; keep external tool authorization outside prompts.
3. Add versioned evaluation cases and focused handler tests. CI validates every
   manifest and publishes one digest-addressed amd64 catalog image using
   `ops-shared`. Each Foundry agent version chooses a handler through its own
   `AGENT_MANIFEST` environment variable.

Run validation and offline replay after installing `requirements.lock` and the
local package:

```sh
python -m agent_runtime validate

export FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
python -m agent_runtime run --agent agents/sre.yaml \
  --case evals/cases/ai-190-foundry-private-endpoint.yaml
```

The replay injects recorded read-only evidence only after a tool call. The model
never receives the case's reference or rubric. The current hosted SRE handler
accepts only `{"incident": {...}}` and has no live diagnostic tool, so it must
abstain when evidence is insufficient. The recorded v1/v2 evaluation results
predate Foundry hosting and remain historical baseline evidence.

## Foundry version lifecycle

The image publishes through the existing OIDC Docker workflow. The project
identity needs registry pull access before a hosted version can start. A caller
inside the private network with Foundry Project Manager access can inspect the
exact version request, then explicitly create a **draft** version:

```sh
python -m agent_runtime publish --agent agents/sre.yaml \
  --image <registry>/portfolio/agent-catalog@sha256:<64-hex-digest> \
  --revision <40-hex-git-commit>

python -m agent_runtime publish --agent agents/sre.yaml \
  --image <registry>/portfolio/agent-catalog@sha256:<64-hex-digest> \
  --revision <40-hex-git-commit> \
  --project-endpoint "$FOUNDRY_PROJECT_ENDPOINT" --apply
```

The publisher looks up existing draft versions and reuses a matching source
revision and image digest. It refuses to reuse the same revision with a different
digest. It does not change endpoint routing or promote a draft. The Foundry
protocol host serves `/invocations` and `/readiness` on port 8088; Foundry
injects the project endpoint and a per-agent identity at runtime. Model calls
use Entra tokens scoped to `https://ai.azure.com/.default`. The catalog image
does not contain grader cases or expected answers.

This PR supplies the template, image and publication command. The private
deployer, project ACR grant, live Foundry version, authenticated invocation and
trace verification are separate acceptance work. Alert intake, live AKS tools,
PostgreSQL retrieval, coding workspaces and fix PRs are not yet implemented.
