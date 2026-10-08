# AI Specification Reflection

Writing this spec was mostly a reconciliation exercise, not a from-scratch
draft: the actual FastAPI app had already moved well past two earlier
planning documents (an aspirational working spec and the C04 tool-review
submission), so the useful work was reading the real models, routers, and
service functions and catching every place the code had quietly diverged
from what was written down — free-status transitions that contradicted an
earlier "protected workflow" decision, a sprint-close design that turned out
to require an explicit next sprint rather than auto-creating one, a
`blocked_reason` field that reintroduced a pattern an earlier decision had
explicitly rejected. An AI assistant was good at holding all of that context
at once and flagging contradictions instead of silently picking a side,
which is exactly the failure mode this assignment warns about.

The hardest part was the required `SprintReport` entity and AI report
endpoint, which don't exist in the codebase at all — the assistant had to
design a plausible, honest contract for something unbuilt without pretending
it already worked, and without inventing implementation details the course
explicitly defers to a later lecture. The main lesson: an AI assistant
writing a spec against real code is only trustworthy if it's made to read
the code first and cite the specific function or line that justifies each
claim; asking it to reason about "how a Kanban tool like this probably
works" instead would have reproduced the earlier document's staleness rather
than fixing it.
