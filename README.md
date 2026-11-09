# Agent catalog

This repository contains agent-specific definitions, handlers and evaluations.
The reusable manifest, Foundry publisher, model route and protocol host live in
[`application-library/agent-factory`](https://github.com/ai-platform-portfolio/application-library/tree/main/agent-factory).
The catalog pins that package to a full Git commit; it does not copy its code.
`agents/sre.yaml` is the first consumer.

## Add an agent

Add `agents/<id>.yaml`, an importable handler in `agent_runtime/`, a versioned
evaluation case and focused handler tests. The package supplies hosted
Invocations defaults and validates every manifest in CI. The existing
`ops-shared` Docker workflow publishes one immutable amd64 catalog image;
each Foundry agent version selects its manifest with `AGENT_MANIFEST`.

For the SRE agent, run validation and the recorded offline case:

```sh
python -m portfolio_agent_factory validate

export FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
python -m agent_runtime --agent agents/sre.yaml \
  --case evals/cases/ai-190-foundry-private-endpoint.yaml
```

The replay supplies recorded read-only evidence only after a requested tool
call. The model never receives the case reference or rubric. The current hosted
SRE handler accepts only `{"incident": {...}}` and has no live diagnostic
tool, so it must abstain when evidence is insufficient. The recorded v1/v2
evaluation results predate Foundry hosting and remain historical evidence.

## Foundry draft version

From a private-network caller with Foundry Project Manager access, inspect the
version request, then explicitly create a draft:

```sh
python -m portfolio_agent_factory publish --agent agents/sre.yaml \
  --image <registry>/portfolio/agent-catalog@sha256:<64-hex-digest> \
  --revision <40-hex-git-commit>

python -m portfolio_agent_factory publish --agent agents/sre.yaml \
  --image <registry>/portfolio/agent-catalog@sha256:<64-hex-digest> \
  --revision <40-hex-git-commit> \
  --project-endpoint "$FOUNDRY_PROJECT_ENDPOINT" --apply
```

The factory reuses a draft with the same revision and image. It does not
promote or change endpoint traffic. The container serves `/invocations` and
`/readiness` on port 8088; the image contains no grader cases. Model calls
default to the Foundry project with Entra tokens for
`https://ai.azure.com/.default`. Optional `MODEL_BASE_URL` and
`MODEL_TOKEN_SCOPE` release settings support a later authenticated gateway
without changing the SRE handler.

The first library and catalog PRs delivered code and CI only. The release
path below adds the missing private publisher and live acceptance step. Alert
intake, AKS diagnostic tools, PostgreSQL retrieval, coding workspaces and fix
PRs remain later work.

## First live release

After the shared GitOps publisher identity and ACR grant reconcile, use the
main-branch `release-record` CI summary for the full image digest and revision.
From an operator workstation with AKS access, render the shared
`application-library/agent-factory/chart` with `agent.id=sre`, the image digest
and the source revision, then install it in the `foundry-publisher` namespace
with `helm upgrade --install --wait --wait-for-jobs`. The chart runs the
shared release command inside AKS, where the Foundry project endpoint is
private. The Job's image contains `smoke/sre.json`; the test deliberately
provides no diagnostic evidence, so its answer must be reviewed for honest
uncertainty. Retry with the same digest/revision reuses the version. A different
revision is blocked until a reviewed promotion workflow exists.

From the workspace root, with the two values copied from the main CI summary:

```sh
IMAGE='localacrk8s.azurecr.io/portfolio/agent-catalog@sha256:<digest>'
REVISION='<full-main-commit>'

helm upgrade --install "sre-${REVISION:0:12}" application-library/agent-factory/chart \
  --namespace foundry-publisher \
  --set agent.id=sre \
  --set-string agent.image="$IMAGE" \
  --set-string agent.revision="$REVISION" \
  --wait --wait-for-jobs --timeout 15m

kubectl logs -n foundry-publisher -l app.kubernetes.io/name=foundry-agent-publisher
```

The Job exports a caller-side span to the existing OTel collector, Tempo and
Langfuse. It proves a successful private invocation and gives a trace ID, but
does not instrument the hosted container. Internal hosted spans need a separate
telemetry connection. Remove the temporary Helm release after recording its
Job result and trace; the Foundry version remains.
