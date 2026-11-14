# Readiness investigation

A running process and a successful liveness probe do not prove readiness. Compare
the desired replica count with ready replicas, then examine recent target events.
A readiness HTTP 404 suggests comparing the configured probe path and port with
the application's implemented routes. Check the live response before drawing a
conclusion; configuration and historical incidents alone do not prove a current
fault or recovery.

A code proposal needs current workload and event evidence, a supported repository
ownership link and an exact reviewed base revision. Limit it to the declared
editable paths and validation command. Keep authentication, networking, credentials
and deployment permissions outside the proposal. Create a draft PR only after the
exact proposal passes validation; never merge or apply the change automatically.
