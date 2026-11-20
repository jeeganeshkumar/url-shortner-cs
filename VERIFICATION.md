# Verification

Run `python -m unittest discover -s url_shortener/tests -v` to exercise API creation, redirects, validation, persistence, the OpenAPI endpoint, body-size enforcement, legacy migration, expiry, and click-policy behavior. Candidate tests use temporary SQLite databases and the FastAPI application.

The [README](README.md) contains the commands for deterministic scenario replay, live model generation, human decisions, approval, and fault recovery.

## Verified submission behavior

| Check | Result |
|---|---|
| Fresh local Git clone with a new virtual environment | Pinned requirements installed and all five tests passed. |
| Three deterministic scenarios | Greenfield and brownfield reached approval. Ambiguous paused for input, then passed after a decision and a subsequent policy revision. |
| Live ambiguous scenario with `llama3.2:3b` | The runner requested a policy, validated preview exclusion, reused requirements when the policy changed, reran dependent stages, rejected approval of the superseded run, and exported the approved replacement. |
| Failure and recovery | The injected failure triggered one retry and one staged-candidate rollback. Export was rejected. A replacement run passed. |
| OpenAPI contract | `/docs` responded successfully and `/openapi.json` described link creation and redirects. |

The isolated deterministic verification recorded five successful validations out of six attempts, including the deliberately failed run. The two completed live validations passed. These figures describe a single demonstration. No same-run recovery was observed, so the stage-recovery metric was null.
