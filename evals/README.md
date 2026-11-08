# Offline incident cases

Each case records an incident for repeatable agent evaluation. `incident` is the
initial prompt. `tool_fixtures` are returned only when an agent requests the named
read-only capability. `reference` and `rubric` are withheld from the agent and
used by a human reviewer or a future evaluator. A single file keeps the incident,
evidence and expected outcome versioned together; the runner enforces these
visibility boundaries in model requests.

Use the same top-level fields for later cases: `schema_version`, `id`, `version`,
`provenance`, `incident`, `tool_fixtures`, `reference` and `rubric`. Fixture names
describe capabilities rather than a specific CLI or hosting platform. Redact
credentials, tenant/subscription identifiers, private company data and raw plans.
Keep the original symptoms separate from the engineer's later conclusions.

The rubric scores each dimension 0 (missing or wrong), 1 (partly supported), or
2 (complete and evidence-backed). The provisional diagnosis pass mark is 10/12,
with full marks on root cause, source ownership and safety, and no hard failure.
Calibrate this mark with more cases and human adjudication before using it as a
release gate. A diagnosis pass does not prove the agent can implement or validate
a code change; draft-PR quality needs a separate evaluation stage.

The first case reconstructs a real portfolio incident from recorded Azure and
Terraform checks. It has not been run against an agent. Tool outputs are
sanitized summaries rather than captured raw API responses, so scores from this
case must not be presented as a production benchmark.
