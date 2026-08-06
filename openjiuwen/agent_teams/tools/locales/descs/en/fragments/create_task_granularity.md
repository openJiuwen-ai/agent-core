## Tips

- Wrap single tasks in an array — tasks is always an array
- Describe goals, not steps: content should contain goals, acceptance criteria, and constraints
- Single owner: each task should be one independently deliverable outcome
- In-batch edges have exactly one spelling: the downstream task depends_on the upstream one; depended_by is reserved for slotting into an existing DAG

## Granularity Examples

Using "user authentication" as an example:

- **Too fine**: splitting into "Design User table", "Implement POST /login", "Implement JWT signing", "Write unit tests" — each is a step not a deliverable outcome; acceptance becomes action-based
- **Right-sized**: one task "Implement user login (signup + login + session)" with goal, acceptance criteria (API covers signup/login/refresh), constraints (bcrypt + JWT). Single owner delivers; accepted via API behavior
- **Too coarse**: one task "Build the entire user module" — scope too wide; parallel execution forces re-splitting later; single owner is costly, acceptance vague
