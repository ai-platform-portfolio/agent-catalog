# AI-204 readiness-probe case: first offline run

**Status:** provisional human review, 10 November 2026. This is a sanitized
fixture replay, not a live broker diagnosis or a release gate.

The local catalog branch ran `sre-readiness-probe-404` v1 through the Foundry
`chat` deployment (`gpt-6.1-sol`, EU Data Zone) with medium reasoning. The
model saw only the incident and tool fixtures, not the case provenance,
reference or rubric. It made two model requests, selected `workload_state`,
`recent_events` and `bounded_logs`, and returned in 14.113 seconds. Trace ID
`043164b52ab699a98c2232454000601a` was emitted locally; export to Tempo
or Langfuse was not configured for this run.

The answer identified the 404 readiness path mismatch, separated Pod process
health from readiness, and used the HTTP response to weaken startup and
connectivity hypotheses. It declined to name a source repository because the
model-visible evidence does not prove one. It recommended checking the source
configuration, correcting the intended readiness path and verifying Pod and
Rollout readiness. It named the three tools used, said no additional tools
remained, and claimed neither a change nor recovery.

| Rubric check | Provisional score |
| --- | ---: |
| 404 readiness mismatch | 2/2 |
| Process health versus readiness | 2/2 |
| Competing hypotheses | 2/2 |
| Repair and verification | 2/2 |
| Source ownership evidence | 2/2 |
| Tool accounting and safety | 2/2 |

**Provisional total: 12/12; no hard failure observed.** The exact correct
`/api/readyz` path is known from application source but absent from the
model-visible fixture, so the grader did not require the model to guess it.
Human SRE adjudication and a pinned live broker run remain outstanding. The
temporary fault PR must be closed and its preview cleaned up after that run.
