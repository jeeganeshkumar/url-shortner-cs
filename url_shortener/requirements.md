# Greenfield acceptance criteria

- POST /links accepts a JSON object with an absolute HTTP(S) destination and returns 201 with an eight-character code and relative redirect path. Invalid requests return 400.
- GET /{code} returns 302 to the stored destination and atomically increments that code's count. Unknown codes return 404.
- GET /links/{code}/stats returns the aggregate count without incrementing it. Links and counts survive service restarts.
- The workflow records requirement, design, implementation, test, and documentation evidence and pauses at the release gate for a human decision.

Day 1 counts every successful redirect request, including bots and repeat requests. Destinations use ASCII URLs and analytics store aggregate counts only.
