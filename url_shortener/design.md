# Architecture and engineering decisions

The service uses typed FastAPI endpoints with an OpenAPI contract and a local SQLite database. A primary key enforces unique codes, and the redirect updates the counter atomically. The service never fetches the destination. SQLite initialization adds a nullable expiry column under a write transaction, preserving existing records and counts.

The delivery runner evaluates an explicit dependency graph. A stage starts after its predecessors pass. Implementation yields an allowlisted configuration bound to the chosen scenario and policy. Test and documentation stages run in parallel and join at release approval. Candidate tests run against a temporary database without altering operator data. The model receives scenario context and earlier artifacts. It generates no executable patch; the service implements the allowed behaviors ahead of time.

A revision creates a new run linked to its predecessor. When the source and model are unchanged, the new run reuses requirements analysis and reruns the policy-dependent stages. Run history records model artifacts, failure reasons, reviewer decisions, approvals, and source hashes. The approval gate verifies the source snapshot again. Export requires an approved, current run.

Model outputs must match a JSON contract. On failure, a stage retries once. Model retries receive a larger token budget. Persistent failures stop for manual review. A failed validation rolls back by withholding the staged candidate from export. When the model is unavailable, a reviewer can explicitly select the deterministic backend on revision.

## Trade-offs and limits

- Preview filtering uses explicit headers. It cannot infer human identity, detect every preview, or deduplicate visits.
- Model prose is a review artifact, and automated tests validate the candidate's behavior. Human review still owns semantic accuracy.
- Approval names are self-reported, and the local event database is editable. A shared deployment would require authenticated actors, protected audit storage, and deployment reconciliation.
- Rollback applies before deployment. The prototype does not run a live database restore or undo a configuration already applied by an operator.
- The service binds to loopback for review. A public service deployment would need rate limiting, controls for link abuse, analytics authorization, and operational monitoring.
