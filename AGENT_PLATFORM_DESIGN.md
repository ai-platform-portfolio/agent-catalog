# Reusable agent platform design

**Status:** Foundry-hosted implementation in draft PR2, 8 November 2026. The prior isolated AKS run remains [provisional evaluation evidence](evals/results/ai-190-first-run.md); no hosted agent or incident automation is deployed yet.

## Goal

Build a repeatable way to define, configure, evaluate, publish and operate custom agents. Start with a platform/SRE agent because its diagnoses and proposed fixes can be reviewed against engineering experience. Reuse the same creation workflow for later agents without copying an SRE-specific deployment. Host the agent in Foundry Agent Service; use AKS for narrow diagnostic and retrieval services as they are built.

The first agent should investigate application and infrastructure incidents, identify the owning source repository, and prepare an evidence-backed draft pull request when a durable source fix is justified. An incident may instead end with an inconclusive finding, an external-provider escalation or a separately governed mitigation. The agent must not invent a fix merely to produce a PR.

## Current platform and limits

- The GitOps bootstrap owns `foundry-management-portfolio` in France Central, with a `management` project and `chat` deployment of `gpt-6.1-sol` (`2026-09-29`, `DataZoneStandard`). Local key authentication and public data-plane access are disabled. A France Central VNet with a delegated agent subnet and private endpoint is peered with UK South AKS. The SRE agent uses `medium` reasoning. One reconstructed diagnosis scored provisionally; broader diagnosis and coding quality remain unmeasured.
- AKS is running with its GitOps packages, including Argo Events, the LGTM observability stack (Loki logs, Grafana, Tempo traces and Mimir metrics), OpenTelemetry, Langfuse, Holmes, Testkube, SonarQube, Argo CD, Kargo, Backstage and Azure Service Operator. The recorded-case run verified agent traces in Tempo and Langfuse. Live diagnostic tools and automatic incident handling remain unimplemented.
- UK South AKS resolves the Foundry endpoints to France Central private IPs and establishes TLS. The ASO-owned Holmes grant was removed with the replaced account and had not reconciled at the last check, so authenticated model inference on the new account remains unproven. The Foundry project identity lacks ACR pull access. Holmes remains a comparison candidate, not an assumed dependency.
- A separate session is moving the model gateway into this organization for Azure and Gemini on Google Cloud. Its interface, ownership and deployment are not yet established here; this design does not presume that integration is complete.

## Proposed architecture

```text
Incident or approved issue
    -> authenticated event intake and deduplication
    -> versioned Foundry-hosted agent
    -> narrowly scoped diagnostic tools
         -> AKS, GitOps and observability evidence
    -> hypothesis testing and ownership decision
    -> isolated coding workspace, if a source fix is justified
    -> repository tests, policy checks and relevant preview/plan
    -> draft PR with evidence and human review
    -> existing merge and delivery controls
```

**Hosting direction:** Foundry Agent Service owns the hosted container and its per-agent identity. The catalog owns immutable image releases and agent definitions. This keeps the investigator process independent of AKS availability, although AKS diagnostic and temporary PostgreSQL knowledge services will be unavailable during a cluster outage. A later reliability exercise can add external diagnostic adapters without moving the agent. [Hosted agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent), [networking options](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/networking-options)

**Foundry's role:** create and version the agent in the `management` project, host its container, supply its endpoint and agent identity, and provide managed inference access. The first implementation uses the Invocations protocol for a structured incident request. Its draft version must be verified active and invoked before this is claimed live. [Hosted agent lifecycle](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent)

**The harness** is the surrounding engineering system that makes an agent dependable: event handling, tool contracts and permissions, working state, coding isolation, retries, budgets, validation and review. Build only the capabilities required by the first agent, with contracts reusable by later agents.

**MCP (Model Context Protocol)** is a standard interface through which an agent can call named tools. A diagnostic MCP service could expose bounded operations such as `get_workload_health`, `get_recent_events`, `query_service_logs`, `query_error_rate` and `compare_deployed_revision`. It should return time-bounded results with source links, timestamps and truncation limits. Do not give the model an unrestricted shell or Kubernetes API token when a narrower tool will answer the question. [Foundry MCP server guidance](https://learn.microsoft.com/en-us/azure/foundry/mcp/build-your-own-mcp-server), [OWASP excessive agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

### How the Foundry-hosted agent investigates

1. The agent asks a diagnostic tool a specific question, such as the health and recent events of a named workload.
2. The tool authenticates the agent, authorizes the requested operation and target, then reads AKS or the existing observability services with its own read-only identity. Authentication to a tool is not authorization to every namespace or API.
3. The tool returns bounded evidence. The agent compares it with deployment history, source and competing hypotheses, and records where each conclusion came from.

The agent runs in Foundry's delegated France Central subnet. A future authenticated diagnostic API can run in AKS with least-privilege Kubernetes access. The existing peering is necessary but does not prove France-to-UK application reachability, agent identity authorization or MCP support. Verify each with a live read-only test. [Foundry networking options](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/networking-options)

Azure SRE Agent is a separate managed product with built-in AKS diagnostics. Its tools are useful as a benchmark, but their existence does not grant the same capability to an arbitrary Foundry custom agent. [Azure SRE Agent connectors](https://learn.microsoft.com/en-us/azure/sre-agent/connectors)

## Incident-to-PR behavior

The agent should follow a reviewable engineering loop:

1. **Triage:** establish severity, impact, affected service, time window and recent changes. Correlate duplicate alerts into one investigation.
2. **Diagnose:** form competing hypotheses, collect evidence that could disprove them, identify uncertainty and distinguish a transient symptom from a persistent defect.
3. **Assign ownership:** change the repository that owns the source. Application code and SQL stay in the application repository; reusable agent code in `application-library`; application templates in `application-helm-chart`; shared platform packages in `gitops`; reusable CI capabilities in `ops-shared`. An incident may require coordinated PRs, but no copied infrastructure workaround.
4. **Implement:** use an isolated, temporary coding workspace with only the target repository and required tools. Add a meaningful regression check and make the smallest durable change. Infrastructure plans are read-only evidence; creating a PR does not authorize an apply, live mutation, merge or production promotion.
5. **Validate:** run relevant tests, static checks and source-policy gates. Use a preview when available and safe. For infrastructure, inspect the exact Terraform/Helm/Argo diff and lifecycle implications. The agent iterates on failed checks within a bounded budget.
6. **Present:** open a draft PR linked to the incident and its Linear work issue, with root-cause evidence, alternative hypotheses, validation results, risks and missing live proof. Human review and existing branch protection remain authoritative. [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

The incident trigger, diagnostic identity and coding identity are separate. Short-lived GitHub App installation tokens can be narrowed by repository and permission; they expire after one hour. Treat issue bodies, logs and repository content as untrusted data because they can contain prompt-injection instructions. Enforce allowlists and authorization outside the model. [GitHub App tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app), [OWASP prompt injection](https://genai.owasp.org/glossary/prompt-injection/)

## Reusable agent-creation contract

`application-library/agent-factory` owns the reusable hosted-agent package. `agent-catalog` is its first consumer and the reviewable source for SRE intent and evaluations. The first manifest collects an id, handler, model, instructions, limits and evaluation suite; later lifecycle slices must add:

| Input | Meaning |
| --- | --- |
| Purpose and success conditions | What the agent may decide and what an acceptable result contains. |
| Trigger contract | Which authenticated events start it, and how duplicates and retries are identified. |
| Model policy | Allowed providers/models, version pin, routing and data boundary. |
| Tool bundle | Named capabilities, per-tool authorization, data limits and side-effect class. |
| Autonomy policy | Actions allowed automatically, actions requiring review, and prohibited actions. |
| Evaluation suite | Versioned cases, expected behavior, thresholds and human adjudication. |
| Release record | Agent/tool/prompt versions, evaluation result, approver and rollback target. |

The factory validates every manifest and prepares an immutable-image draft Foundry version through the official SDK; `agent-catalog` uses the existing `ops-shared` image workflow. Future agent repositories install the same pinned factory package and supply their own manifest, handler and evaluation cases. The publisher must later run inside the private network and be connected to a promotion gate; these code PRs alone do not deploy or route a live agent. Reuse shared platform templates where Kubernetes or CI resources are needed.

## Evaluation and release gates

An **offline evaluation** runs an agent version on curated, versioned cases before release. An **online evaluation** scores sampled real investigations after release, with privacy controls and human review. Both must inspect tool choices and evidence, not only the final prose. [Foundry evaluation](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-overview), [Langfuse datasets](https://langfuse.com/docs/evaluation/get-started/offline)

Start with real portfolio incidents and controlled fault injections: an application exception, incorrect Helm default, failed migration, stale GitOps revision, ASO permission denial, transient Azure API failure, and malicious instructions embedded in an issue or log. Score correct fault domain and repository, evidence quality, reproduction, fix effectiveness, regression coverage, safe tool use, justified abstention, cost and latency. A model-based judge can help review nuanced results, but deterministic checks and human SRE adjudication should decide release readiness. Failed real investigations should become new offline cases.

Roll out in stages: read-only shadow investigations; supervised tool use; automatically prepared draft PRs for bounded cases; broader coverage only after measured results. Merge, deployment and emergency mitigation remain separate governed workflows.

## Managed-product comparison

Benchmark the portfolio agent on the *same* cases against products that are actually accessible, rather than making an unsupported claim of market superiority:

| Product | Documented strength | Comparison limit |
| --- | --- | --- |
| [Azure SRE Agent](https://learn.microsoft.com/en-us/azure/sre-agent/overview) | Azure resource and AKS investigation with governed actions. | Its documented PR creation flow requires a source branch that already contains committed changes; verify the full coding workflow. |
| [Datadog Bits Investigation and Bits Code](https://docs.datadoghq.com/bits_ai/bits_code/) | Telemetry-to-code-fix-to-PR workflow with steering. | Some investigation features are Preview; Bits Code documents no multi-repository investigation support. |
| [GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agent-capabilities/about-agents/about-github-copilot-agents) | Issue-to-branch-to-PR coding and review iteration. | Private incident evidence and cross-repository infrastructure ownership need additional integration. |
| [PagerDuty SRE Workbench](https://docs.pagerduty.com/ai-automation/pd-labs/workbench-user-guide) | Long-running, steerable incident investigation and evidence timeline. | Assess its coding/PR handoff separately. |

These sources describe vendor capabilities, not measured effectiveness or market share. DORA's 2025 research frames AI as an amplifier of existing engineering practices, supporting investment in validation, version control and feedback loops alongside the model. [DORA 2025](https://dora.dev/research/2025/dora-report/)

## Decisions and first proof

1. Start with the selected AI-190 incident as an offline case. Add an authenticated Argo Events alert for shadow runs after its reference diagnosis is documented; do not poll for incidents.
2. Publish a digest-pinned catalog image, create a draft hosted SRE version in Foundry, and verify its status, invocation and trace before routing it to production traffic. The project needs an ACR pull grant and the publisher needs a private path and Foundry Project Manager access.
3. Use the real [AI-190 Foundry private-endpoint incident](evals/cases/ai-190-foundry-private-endpoint.yaml) as the first offline diagnosis case. Prove read-only diagnostic tool calls on that evidence, record their identity, route and trace, then compare the diagnosis with the engineer reference. A preview-only application incident follows to test a different fault domain.
4. Choose whether passing deterministic gates may create a draft PR automatically. Review, merge and deployment remain human-governed.
5. Decide which accessible managed product to use for the first like-for-like benchmark.
6. Before implementing diagnostic failover, define the AKS failure boundary, external trigger and evidence sources, recovery target, duplicate-work protection and maintenance/teardown behaviour. The Foundry-hosted investigator must degrade explicitly when AKS evidence is unavailable.

No hosted agent, diagnostic tool, event route, standby or PR automation is deployed by this document.
