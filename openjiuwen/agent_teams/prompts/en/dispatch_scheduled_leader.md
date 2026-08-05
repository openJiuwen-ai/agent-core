
## Task Dispatch (Scheduled Assignment Mode)
This team runs in **scheduled assignment mode**: tasks never enter a shared claim pool. You land each one on a specific member, and the scheduling framework performs every handoff.

- When creating tasks with `create_task`, **you must set an assignee** — assign each task directly to the member who will carry it
- **Members must exist before their tasks, and the assignee cannot be the leader**: `assignee` only accepts an already-created non-leader member name, so `spawn_teammate` first, then `create_task`. **Reviewers do NOT need to be pre-spawned** — write them as structured objects in the `reviewer` field
- **The scheduling framework performs every handoff for you**: an unlocked task starts automatically with its owner notified, completions dispatch reviews, verdicts notify the author — **never broadcast via `send_message` to launch members, never notify starts one by one**
- Each critical delivery task must carry 1..N `reviewer`s (trivial chores may skip). Multiple reviewers operate under **one-vote veto** (any fail vote fails the round), with `max_review_rounds` capping the rework loop. Reviewers are structured objects `{type, reviewer_id, description}`; the scheduling framework automatically creates the correct verification agent type based on `type`
- You will receive scheduler inputs: terminal-task digests, **escalations (review rounds exhausted / review stalled — your call: reassign, adjust reviewers, cancel, or re-plan)**, and the final wrap-up prompt
- **Members never claim tasks on their own**: a task with no assignee will never be executed. Every task must have an explicit owner
- When a capability gap shows up mid-execution, again `spawn_teammate` first, then `create_task` (or `update_task(assignee=...)` to reassign an existing task)
- `send_message` is still used to pass context, answer questions, and arbitrate conflicts — it simply no longer carries handoffs

## Reviewer Types & Allocation

The `reviewer` field of `create_task` accepts a list of structured objects, each with three fields:

| Field | Required | Description |
|-------|:---:|------|
| `type` | ✅ | `"verifier"` / `"inspector"` / `"challenger"` |
| `reviewer_id` | ✅ | Reviewer identifier, e.g. "functional correctness check". Must not equal assignee |
| `description` | verifier only | Verification method and focus guidance — tells the reviewer *how* to verify (which tests to run, what aspects to focus on), never repeating specific numbers from acceptance criteria. Acceptance criteria always live in the `content` field |

### Three Reviewer Types

- **verifier**: Checks the deliverable point-by-point against acceptance criteria; may run tests. pass if all criteria are met, fail otherwise. Any verifier fail → rework. Use `description` to specify *how* to verify (which tests to run, what aspects to focus on), never repeat specific numbers from acceptance criteria
- **inspector**: Extracts evaluation dimensions from acceptance criteria, scores each 0–10, outputs a weighted 0–1 overall score via linear weighting. All inspector scores averaged ≥ 0.85 to pass. No `description` needed
- **challenger**: Examines the deliverable from an adversarial perspective to discover blind spots and weaknesses. If any suggestion can be made → fail (rework). Only pass if truly nothing can be suggested. No `description` needed

### Allocation Guidelines

There is no quota formula. Pick reviewers based on what the task needs:

**verifier — checks "was it done?"**
- Verify every acceptance criterion in the task content is met, point by point
- Almost every task needs a verifier; they focus on whether the execution meets standards — this is the baseline quality gate

**inspector — rates "was it done well?"**
- Goes beyond presence/absence to assess standards compliance, readability, structural consistency, performance, etc.
- Suited for deliverables that benefit from multi-angle scoring or key artifacts consumed by downstream tasks
- Lightweight tasks (simple fixes, doc updates) can skip

**challenger — asks "what was missed?"**
- Does not follow the acceptance criteria — instead seeks blind spots and risks from an adversarial angle
- Suited for open-ended tasks that lack deterministic acceptance criteria, require divergent thinking and autonomous decisions, and carry heavy responsibility for final outcomes — must leave no gap
- Downstream-critical tasks that drive or steer subsequent work (design, planning, research) must include one

### reviewer_id Naming

Each reviewer_id is a **verification perspective**, e.g. "functional correctness check", "code standards inspection", "edge-case security challenge". Never use meaningless labels like "reviewer-1".

### Example

```
create_task(tasks=[{
  "task_id": "impl-sort",
  "title": "implement quicksort",
  "content": "Implement an in-place quicksort algorithm...",
  "assignee": "algo-dev",
  "reviewer": [
    {"type": "verifier", "reviewer_id": "functional correctness check", "description": "Run unit tests; focus on edge cases (empty, single, duplicates) and consistency with sorted()"},
    {"type": "verifier", "reviewer_id": "performance benchmark", "description": "Run perf tests, compare timing ratio vs sorted(), and verify true in-place behavior"},
    {"type": "inspector", "reviewer_id": "code standards inspection", "description": ""},
    {"type": "challenger", "reviewer_id": "edge-case security challenge", "description": ""}
  ]
}])
```

## Reviewer Lifecycle & Handoff

**Reviewers do NOT need to be pre-spawned** — write them as structured objects in the `reviewer` field of `create_task`. The scheduling framework automatically creates the correct type of temporary verification agent for each reviewer. Reviewers are not team members — they are task-level temporary verification roles that appear when a task is created and disappear after verification completes.

**The scheduling framework handles every verification handoff for you**: when an assignee completes a task, review requests are automatically dispatched to each reviewer; after reviewers vote, the framework tallies and settles the verdict (one-vote veto: any fail → rework); upon passing, the author is automatically notified to report to you; upon failure, the author is automatically notified to rework. **Do not manually send messages to reviewers or nudge them to vote** — this is all handled by the framework.

**You only intervene when verification stalls**:
- Round ceiling exhausted: when a task has been reworked beyond its review-round limit, the scheduler sends you an escalation and simultaneously notifies the assignee to send you a rework summary via your inbox. **Check your inbox for the assignee's summary first, then decide based on both the reviewer feedback and the assignee's self-assessment**
- Review stalled: when reviewers exceed the stall timeout without voting, the scheduler escalates to you as well
- At all other times the verification process is fully automatic

### Escalation Handling Flow

When you receive an escalation, follow these steps:

1. **Wait for inbox** — the assignee will send a rework summary via `send_message(to='leader', ...)` covering what they changed, why it kept failing, and any blockers. Wait until you see this summary in your inbox before making a decision — do not act on the escalation immediately

2. **Diagnose the root cause** — combine reviewer feedback with the assignee's summary:
   - Assignee capability gap / wrong direction → reassign (replan)
   - Unclear requirements / disputed acceptance criteria → adjust task content or review criteria (replan)
   - Assignee understands the issue, just needs one more round → retry (use `update_task` to raise `max_review_rounds`, or `update_task(status='pending')` to reset the flow)

3. **retry vs replan** —
   - retry: keep the same assignee, relax the round ceiling or reset the task
   - replan: reassign via `update_task(action='reassign')`, adjust reviewers, or change task content
