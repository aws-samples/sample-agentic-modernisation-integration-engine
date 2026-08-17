---
inclusion: always
---
# Task Execution Rules

## One task at a time, in the order `tasks.md` lists them

`.kiro/specs/code-insights-platform/tasks.md` lists its tasks in a single ordered `## Tasks` list. That order is the execution order: start the first unchecked task, finish it, verify it, then start the next. Never run two tasks together, and never start a task ahead of one above it in the list.

The order is a topological sort of the `## Task Dependency Graph` in the same file. The graph is not a schedule — it is the record of **why** the order is what it is, and four of its edges are annotated with the failure that produced them. Reordering the list without reading those annotations is how a broken sequence gets reintroduced.

## Verify before moving on

A task is not complete until its own verification passes — `cd backend && make lint && make test` for backend work, `cd frontend && npm run lint && npm run test` for frontend work, plus whatever the task body names specifically. A failure belongs to the task that caused it, and it is cheapest to find there. Do not carry a red build into the next task.

## Read a producer's real interface out of its code

By the time a task runs, every task it depends on has already run and its code is on disk. So when a task consumes something an earlier task built — a class constructor, a method name, what an SSE generator yields, a response envelope key, an on-disk record's fields — **open that file and read it**. Do not infer the interface from your own task's description.

A task body describing an endpoint or a class is a *description*, not a declaration. Where the two disagree, the code wins and the task body is a defect to report. Inference is what produced five wrong method signatures in `routes/ai_streaming.py`, shipping four SSE endpoints that returned HTTP 200 with a `TypeError` inside the stream (Build Constraint 85).

If the interface a task needs genuinely does not exist yet, that is an ordering defect in the spec — report it. It is not a cue to invent one.

## Edit what already exists; never re-create it

- Before creating a file, check whether an earlier task already created it. If so, **edit it**.
- An instruction to "create X" where X already exists on disk is a spec defect to report, not licence to overwrite. Re-creation destroys content silently: Task 2 "created" a data file Task 1 had already seeded and clobbered all 13 entries in it (Build Constraint 36).
- Never build a placeholder expecting a later task to notice and replace it. Nothing guarantees the later task looks, and the placeholder can end up being the shipped version.
- Never create a second file with the same basename in a different directory to dodge a collision (Build Constraint 34).
