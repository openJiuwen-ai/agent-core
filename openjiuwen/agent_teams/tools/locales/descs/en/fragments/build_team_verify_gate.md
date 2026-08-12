## enable_task_verification (the verify gate)

Controls whether this instance runs the verify gate (the reviewer system). Two-layer
semantics — the user's config is a ceiling; you choose within it:

- User config false → whatever you pass is inert, the verify gate is force-disabled
- User config true → your call:
  - omitted / true: on. Production code, formal design, core features — assign reviewers to critical tasks
  - false: off. Prototypes, quick experiments, one-off throwaway work — tasks complete directly, with no review

**This tool's result carries the value that actually took effect**
(`task_verification=...`): when it comes back false, any reviewer written into
create_task / update_task is ignored, so stop assigning reviewers to tasks.
