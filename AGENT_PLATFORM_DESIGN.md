# Reusable agent platform design

**Status:** discussion draft, 7 November 2026. This document describes a proposed design, not a deployed agent or an approved implementation scope.

## Goal

Build a repeatable way to define, configure, evaluate, publish and operate custom agents. Start with a platform/SRE agent because its diagnoses and proposed fixes can be reviewed against engineering experience. Reuse the same creation workflow for later agents without copying an SRE-specific deployment. Use Microsoft Foundry for model access and evaluation while keeping the first agent runtime portable between container hosts.

The first agent should investigate application and infrastructure incidents, identify the owning source repository, and prepare an evidence-backed draft pull request when a durable source fix is justified. An incident may instead end with an inconclusive finding, an external-provider escalation or a separately governed mitigation. The agent must not invent a fix merely to produce a PR.

## Current platform and limits

- `foundry-management-portfolio` was rebuilt on 7 November through the GitOps bootstrap root. Subsequent approved changes enabled selected-IP public access with default deny while retaining the private endpoint and disabled local key authentication. An authenticated model-list request succeeded; no SRE-agent inference test has been completed. AKS and its GitOps packages remain down after teardown. See the workspace's `PLATFORM_LIFECYCLE.md` for current lifecycle evidence.
- The bootstrap declares a `management` Foundry project and `chat` deployment of `gpt-4.1-mini`. Its suitability for SRE diagnosis or code repair is unmeasured. Model selection should use task-specific evaluations, not a default assumption.
- When AKS is running, GitOps installs Argo Events, the LGTM observability stack (Loki logs, Grafana, Tempo traces and Mimir metrics), OpenTelemetry, Langfuse, Holmes, Testkube, SonarQube, Argo CD, Kargo, Backstage and Azure Service Operator. Installation does not prove that an agent can authenticate, reach these services or make a correct diagnosis.
- Holmes is configured as a private, read-only Kubernetes and telemetry investigator using Foundry inference. Its live model call and value as a tool for another agent remain unproven. It is a candidate capability to evaluate, not a dependency to assume.
- A separate session is moving the model gateway into this organization for Azure and Gemini on Google Cloud. Its interface, ownership and deployment are not yet established here; this design does not presume that integration is complete.

## Proposed architecture

```text
Incident or approved issue
    -> authenticated event intake and deduplication
    -> versioned agent container on AKS
    -> narrowly scoped diagnostic tools
         -> AKS, GitOps and observability evidence
    -> hypothesis testing and ownership decision
    -> isolated coding workspace, if a source fix is justified
    -> repository tests, policy checks and relevant preview/plan
    -> draft PR with evidence and human review
    -> existing merge and delivery controls
```

**Hosting direction, 7 November 2026:** deploy the first agent container on the existing AKS cluster when it is running. Keep its image, configuration contract, tool interfaces, incident state and release record independent of Kubernetes so the same image can later run on Azure Container Apps as a small standby. This is a portability requirement, not authorization to provision failover infrastructure now. Keep alert intake and durable incident evidence outside AKS when automatic incident handling is implemented; otherwise an AKS outage could disable the trigger and erase work in progress. A standby would provide degraded investigation using external Azure, GitHub and exported telemetry sources, not guaranteed access to in-cluster LGTM services or an unavailable Kubernetes API. Intentional cluster teardown must not trigger failover. [Container Apps scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app), [AKS control-plane log export](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/kubernetes-monitoring-enable)

**Foundry's role:** use the existing Foundry account for models and evaluate trace integration. Foundry can register an externally hosted agent for trace viewing and evaluation, but that feature is preview and does not host, proxy or invoke the AKS runtime. The catalog and deployment pipeline therefore remain the source of versioned agent intent and container releases; do not claim Foundry-managed agent lifecycle for this deployment. [Foundry external agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/register-external-agent)

**The harness** is the surrounding engineering system that makes an agent dependable: event handling, tool contracts and permissions, working state, coding isolation, retries, budgets, validation and review. Build only the capabilities required by the first agent, with contracts reusable by later agents.

**MCP (Model Context Protocol)** is a standard interface through which an agent can call named tools. A diagnostic MCP service could expose bounded operations such as `get_workload_health`, `get_recent_events`, `query_service_logs`, `query_error_rate` and `compare_deployed_revision`. It should return time-bounded results with source links, timestamps and truncation limits. Do not give the model an unrestricted shell or Kubernetes API token when a narrower tool will answer the question. [Foundry MCP server guidance](https://learn.microsoft.com/en-us/azure/foundry/mcp/build-your-own-mcp-server), [OWASP excessive agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

### How the AKS-hosted agent investigates

1. The agent asks a diagnostic tool a specific question, such as the health and recent events of a named workload.
2. The tool authenticates the agent, authorizes the requested operation and target, then reads AKS or the existing observability services with its own read-only identity. Authentication to a tool is not authorization to every namespace or API.
3. The tool returns bounded evidence. The agent compares it with deployment history, source and competing hypotheses, and records where each conclusion came from.

The agent and its first diagnostic tools can run in AKS, with least-privilege Kubernetes access and a verified outbound route to Foundry inference. Keep tool responses and incident evidence portable so a future standby can use external implementations of the same capabilities. A private endpoint to Foundry controls the inbound path to that service; it does not by itself establish an agent egress route. Verify identity, DNS, network path and tool permissions in a live read-only test. [Foundry networking options](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/networking-options)

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

The template should validate agent intent and produce a pinned container release through a repeatable pipeline, with configuration review before publication. It must not depend on Kubernetes-only environment variables, mounted paths or service names in agent code; host-specific adapters and values supply those details. Prefer supported Foundry APIs for model and evaluation capabilities and existing portfolio CI/chart conventions over another bespoke framework. Reuse shared platform templates where Kubernetes or CI resources are needed. The first SRE agent must prove the template works without embedding SRE-specific defaults in shared packages.

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
2. Package the SRE agent as one container and deploy its first runtime to AKS through reusable application templates. Verify the AKS-to-Foundry inference route and identity; no additional Foundry hosting resource is assumed.
3. Prove one read-only diagnostic tool call on a known AKS fault after the cluster is rebuilt. Record identity, network route, returned evidence and trace. Then compare its diagnosis with an experienced engineer's reference.
4. Choose whether passing deterministic gates may create a draft PR automatically. Review, merge and deployment remain human-governed.
5. Decide which accessible managed product to use for the first like-for-like benchmark.
6. Before implementing failover, define the AKS failure boundary, external trigger and evidence sources, recovery target, duplicate-work protection and maintenance/teardown behaviour. Test the same image on the standby host only in that later scope.

No agent, diagnostic tool, event route, standby or PR automation described here is deployed by this document.
