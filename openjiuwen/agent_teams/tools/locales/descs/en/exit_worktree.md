Exit a worktree session created by enter_worktree and restore the working directory to its original location.

## Scope

Only operates on the worktree created by enter_worktree in the current session. Will NOT touch:
- Worktrees created manually with `git worktree add`
- Other members' worktrees
- The current directory if enter_worktree was never called

Calling outside an enter_worktree session is a no-op.

## When to Use

- Task is complete and you need to leave the worktree
- Need to switch to a different working context

## Parameters

- `action` (required): `"keep"` or `"remove"`
  - `"keep"` -- leave the worktree directory and branch on disk for later use
  - `"remove"` -- delete the worktree directory and its branch; use when work is done or abandoned
- `discard_changes` (optional, default false): only meaningful with `action: "remove"`. When the worktree has uncommitted files or unmerged commits, the tool refuses to remove and lists changes; set to true to confirm discard

## Behavior

- Restores the session's working directory to where it was before enter_worktree
- On action=remove, detects uncommitted changes and new commits first; refuses unless discard_changes=true
- After exit, enter_worktree can be called again to create a fresh worktree
