# Agent catalog

This repository contains agent-specific definitions, handlers and evaluations.
The reusable manifest, Foundry publisher, model route and protocol host live in
[`application-library/agent-factory`](https://github.com/ai-platform-portfolio/application-library/tree/main/agent-factory).
The catalog pins that package to a full Git commit; it does not copy its code.
`agents/sre.yaml` is the first consumer.

`fix/preview-environments.yaml` declares the first SRE coding policy: the
target repository, editable directories, validation command and coding intent.
The shared factory's `fix-chart/` supplies the isolated fetch, proposal,
validation and draft-PR publication phases. An operator supplies a reviewed
diagnosis, exact base ref/SHA and immutable image for each incident. This
policy is source intent; it does not itself trigger a Job or grant GitHub write
access. Review the generated diff and PR checks before deciding whether the
fix addresses the incident.

`alerts/sre.yaml` enables the optional alert-to-coding handoff and requires
`workload_state` and `recent_events` for an actionable triage result. Render it
with `fix/preview-environments.yaml` so supervised and event-driven runs share
one repository/edit/check policy. The operator supplies target namespace,
declared source branch, immutable image/revision, active Foundry version and
existing GitHub App metadata. The handoff can publish only a draft PR after
validation; it never merges. This is catalog intent, not evidence that a
persistent alert registration or live automatic draft PR is deployed.

The installed Argo Events bus can trigger scoped, read-only investigations of
Kubernetes readiness failures. The reusable
[`alert-chart`](https://github.com/ai-platform-portfolio/application-library/tree/main/agent-factory/alert-chart)
owns the EventSource, Sensor, Job and RBAC; `alerts/sre.yaml` supplies only SRE
intent. Deploy it with a concrete target namespace, digest-pinned catalog image,
full source revision and active Foundry version. An Event UID deduplicates repeat
updates while its Job exists (24 hours); the Sensor admits at most two events per
minute. The model receives fixed incident text and current read-only evidence,
never Event message content. This watches Kubernetes readiness Events, not Mimir
Alertmanager; other alert sources remain separate integrations. Chart rendering
alone does not prove that the EventSource, Sensor and broker work live.

## Add an agent

Add `agents/<id>.yaml`, an importable handler in `agent_runtime/`, a versioned
evaluation case and focused handler tests. The package supplies hosted
Invocations defaults and validates every manifest in CI. The existing
`ops-shared` Docker workflow publishes one immutable amd64 catalog image;
each Foundry agent version selects its manifest with `PORTFOLIO_MANIFEST_PATH`.

For the SRE agent, run validation and the recorded offline case:

```sh
python -m portfolio_agent_factory validate

export FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
python -m agent_runtime --agent agents/sre.yaml \
  --case evals/cases/ai-190-foundry-private-endpoint.yaml
```

The replay supplies recorded read-only evidence only after a requested tool
call. The model never receives the case reference or rubric. The hosted SRE
handler now returns one requested tool name or a final answer. The AKS broker
executes only manifest-listed read-only tools for one approved namespace and
returns bounded evidence. Without a broker or target namespace/workload, the
handler still answers from supplied context and must abstain when evidence is
insufficient. The recorded v1/v2 evaluation results predate this live tool
path and remain historical evidence.

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

The first library and catalog PRs delivered code and CI, followed by a private
publisher and live acceptance step. The read-only broker is a separate release
candidate; alert intake, PostgreSQL retrieval, coding workspaces and fix PRs
remain later work.

Offline case replay now comes from the pinned `application-library/agent-factory`
package. This repository supplies SRE cases and the hosted handler; its CI
validates every case and tests the shared replay contract without a live model
call. A live replay emits a JSON report with deterministic checks and a pending
human review. Passing those checks alone is not a release decision.

## Read-only AKS investigation

`agents/sre.yaml` declares four fixed tools: workload state, recent events,
bounded logs and fixed service metrics. `incidents/sre-staging.json` is a
read-only baseline input, not an injected outage. The
`evals/cases/sre-staging-baseline.yaml` case records the observed healthy
Deployment and Pod state for offline abstention checks. The shared
`agent-factory/diagnostics-chart` runs a short-lived broker Job using the
separate GitOps-managed diagnostic identity. Set only agent id, immutable image
digest, full revision, incident path and namespace. The broker rejects an
incident targeting another namespace and never sends its Kubernetes token to
Foundry. The hosted response labels tool evidence as caller-supplied and
untrusted; no diagnosis or recovery is implied by this baseline alone.

The current production endpoint serves version 1. Test a new image as a
candidate-specific Foundry version before routing production traffic to it;
the first-release command intentionally refuses an implicit revision change.

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
uncertainty. If the Job fails, uninstall this short-lived Helm release and run
the same command again; the same digest/revision reuses the Foundry version.
A different revision is blocked until a reviewed promotion workflow exists.

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
