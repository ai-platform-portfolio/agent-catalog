# Reusable agent platform design

**Status:** discussion draft, 7 November 2026. This document describes a proposed design, not a deployed agent or an approved implementation scope.

## Goal

Build a repeatable way to define, configure, evaluate, publish and operate custom agents in Microsoft Foundry. Start with a platform/SRE agent because its diagnoses and proposed fixes can be reviewed against engineering experience. Reuse the same creation workflow for later agents without copying an SRE-specific deployment.

The first agent should investigate application and infrastructure incidents, identify the owning source repository, and prepare an evidence-backed draft pull request when a durable source fix is justified. An incident may instead end with an inconclusive finding, an external-provider escalation or a separately governed mitigation. The agent must not invent a fix merely to produce a PR.

## Current platform and limits

- `foundry-management-portfolio` was rebuilt on 7 November through the GitOps bootstrap root. Azure reported the account, model deployment and private endpoint succeeded; public network access and local key authentication were disabled. No model inference or Foundry agent-hosting test has been completed. The Foundry foundation is partially up; AKS and its GitOps packages remain down after teardown. See the workspace's `PLATFORM_LIFECYCLE.md` for current lifecycle evidence.
- The bootstrap declares a `management` Foundry project and `chat` deployment of `gpt-4.1-mini`. Its suitability for SRE diagnosis or code repair is unmeasured. Model selection should use task-specific evaluations, not a default assumption.
- When AKS is running, GitOps installs Argo Events, the LGTM observability stack (Loki logs, Grafana, Tempo traces and Mimir metrics), OpenTelemetry, Langfuse, Holmes, Testkube, SonarQube, Argo CD, Kargo, Backstage and Azure Service Operator. Installation does not prove that an agent can authenticate, reach these services or make a correct diagnosis.
- Holmes is configured as a private, read-only Kubernetes and telemetry investigator using Foundry inference. Its live model call and value as a tool for another agent remain unproven. It is a candidate capability to evaluate, not a dependency to assume.
- A separate session is moving the model gateway into this organization for Azure and Gemini on Google Cloud. Its interface, ownership and deployment are not yet established here; this design does not presume that integration is complete.

## Proposed architecture

```text
Incident or approved issue
    -> authenticated event intake and deduplication
    -> versioned Foundry agent
    -> narrowly scoped diagnostic tools
         -> AKS, GitOps and observability evidence
    -> hypothesis testing and ownership decision
    -> isolated coding workspace, if a source fix is justified
    -> repository tests, policy checks and relevant preview/plan
    -> draft PR with evidence and human review
    -> existing merge and delivery controls
```

**Foundry is the agent control plane:** agent definitions and versions, model access, tool configuration, traces and evaluations should be managed there. **The harness** is the surrounding engineering system that makes an agent dependable: event handling, tool contracts and permissions, working state, coding isolation, retries, budgets, validation and review. Foundry hosting does not remove the need to design these contracts. Foundry supports prompt and hosted agents, custom tools and MCP connections, tracing and evaluation; the choice between prompt and hosted agents needs a capability test on this account. [Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)

**MCP (Model Context Protocol)** is a standard interface through which an agent can call named tools. A diagnostic MCP service could expose bounded operations such as `get_workload_health`, `get_recent_events`, `query_service_logs`, `query_error_rate` and `compare_deployed_revision`. It should return time-bounded results with source links, timestamps and truncation limits. Do not give the model an unrestricted shell or Kubernetes API token when a narrower tool will answer the question. [Foundry MCP server guidance](https://learn.microsoft.com/en-us/azure/foundry/mcp/build-your-own-mcp-server), [OWASP excessive agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

### How a Foundry agent investigates AKS

1. The agent asks a diagnostic tool a specific question, such as the health and recent events of a named workload.
2. The tool authenticates the agent with Microsoft Entra, authorizes the requested operation and target, then reads AKS or the existing observability services with its own read-only identity. Authentication to a tool is not authorization to every namespace or API.
3. The tool returns bounded evidence. The agent compares it with deployment history, source and competing hypotheses, and records where each conclusion came from.

The tool may run in or near AKS; the reasoning agent can remain in Foundry. The network path has not been selected. Prefer a private connection where the existing Foundry account supports it. If it cannot privately reach a tool, compare an Entra-protected endpoint with a managed relay or a separately isolated Foundry hosting resource. Verify the actual route, backend bypass protection, token audience and AKS permissions before choosing. Foundry's outbound networking and tool support differ by configuration, and its documentation says bring-your-own virtual network injection is a creation-time choice; the existing account must be inspected rather than assumed to support it. [Foundry networking options](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/networking-options), [toolbox network isolation](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/toolbox-network-isolation)

Azure SRE Agent is a separate managed product with built-in AKS diagnostics. Its tools are useful as a benchmark, but their existence does not grant the same capability to an arbitrary Foundry custom agent. [Azure SRE Agent connectors](https://learn.microsoft.com/en-us/azure/sre-agent/connectors)

## Incident-to-PR behavior

The agent should follow a reviewable engineering loop:

1. **Triage:** establish severity, impact, affected service, time window and recent changes. Correlate duplicate alerts into one investigation.
2. **Diagnose:** form competing hypotheses, collect evidence that could disprove them, identify uncertainty and distinguish a transient symptom from a persistent defect.
3. **Assign ownership:** change the repository that owns the source. Application code and SQL stay in the application repository; reusable application templates in `application-helm-chart`; shared platform packages in `gitops`; reusable CI capabilities in `ops-shared`. An incident may require coordinated PRs, but no copied infrastructure workaround.
4. **Implement:** use an isolated, temporary coding workspace with only the target repository and required tools. Add a meaningful regression check and make the smallest durable change. Infrastructure plans are read-only evidence; creating a PR does not authorize an apply, live mutation, merge or production promotion.
5. **Validate:** run relevant tests, static checks and source-policy gates. Use a preview when available and safe. For infrastructure, inspect the exact Terraform/Helm/Argo diff and lifecycle implications. The agent iterates on failed checks within a bounded budget.
6. **Present:** open a draft PR linked to the incident and its Linear work issue, with root-cause evidence, alternative hypotheses, validation results, risks and missing live proof. Human review and existing branch protection remain authoritative. [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

The incident trigger, diagnostic identity and coding identity are separate. Short-lived GitHub App installation tokens can be narrowed by repository and permission; they expire after one hour. Treat issue bodies, logs and repository content as untrusted data because they can contain prompt-injection instructions. Enforce allowlists and authorization outside the model. [GitHub App tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app), [OWASP prompt injection](https://genai.owasp.org/glossary/prompt-injection/)

## Reusable agent-creation contract

`agent-catalog` should be the reviewable source for agent intent, not a second owner of shared platform infrastructure. A future agent template should collect:

| Input | Meaning |
| --- | --- |
| Purpose and success conditions | What the agent may decide and what an acceptable result contains. |
| Trigger contract | Which authenticated events start it, and how duplicates and retries are identified. |
| Model policy | Allowed providers/models, version pin, routing and data boundary. |
| Tool bundle | Named capabilities, per-tool authorization, data limits and side-effect class. |
| Autonomy policy | Actions allowed automatically, actions requiring review, and prohibited actions. |
| Evaluation suite | Versioned cases, expected behavior, thresholds and human adjudication. |
| Release record | Agent/tool/prompt versions, evaluation result, approver and rollback target. |

The template should generate or validate a Foundry agent version through a repeatable pipeline, with configuration review before publication. Prefer supported Foundry APIs and existing portfolio CI/chart conventions over another bespoke framework. Reuse shared platform templates where Kubernetes or CI resources are needed. The first SRE agent must prove the template works without embedding SRE-specific defaults in shared packages.

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

1. Choose the first trigger: a selected GitHub/Linear issue, an observability alert, or both. A manually selected issue followed by alert-driven shadow runs is a low-noise first sequence.
2. Choose Foundry prompt versus hosted agent after verifying this account's capabilities, tool authentication and network path. Do not create another Foundry resource or expose AKS as an assumed prerequisite.
3. Prove one read-only Foundry-to-AKS diagnostic tool call on a known fault after AKS is rebuilt. Record identity, network route, returned evidence and trace. Then compare its diagnosis with an experienced engineer's reference.
4. Choose whether passing deterministic gates may create a draft PR automatically. Review, merge and deployment remain human-governed.
5. Decide which accessible managed product to use for the first like-for-like benchmark.

No agent, diagnostic tool, event route or PR automation described here is deployed by this document.
