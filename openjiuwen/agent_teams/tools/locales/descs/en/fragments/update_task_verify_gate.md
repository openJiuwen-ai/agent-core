
**Set / clear the verify gate:**
- Pass reviewer (an array of structured objects) to assign reviewers to a task; this works in any state, and an empty array clears the gate
- A reviewer must not be the task's own assignee (no self-verification)
- Once a task has reviewers, the assignee's completion moves it to in_review to await a verdict; the scheduling framework summons the reviewers on its own, so you never need to chase them
- max_review_rounds caps the rework loop (≥1) and is only meaningful for a task that has reviewers — or is getting them in this same call; when the rounds run out without a pass, it escalates to you for a decision
- For reviewer types and how to write their instructions, see the "Task Dispatch" section of your system prompt
