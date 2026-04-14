Create an isolated git worktree and switch the current session into it.

## When to Use

- Need to modify code in an independent copy, avoiding branch conflicts and file contention with other members
- Need to make experimental changes without affecting the main repository

## When NOT to Use

- Only need to create or switch branches -- use git commands
- No parallel modification of the same repository involved

## Requirements

- Must be inside a git repository
- Must not already be in a worktree session (exit_worktree first)

## Behavior

- Creates a new branch and worktree under `.agent_teams/worktrees/` based on HEAD
- Switches the session's working directory (CWD) to the new worktree
- All subsequent file operations and shell commands execute inside the worktree, leaving the main repo unaffected
- Use exit_worktree to leave (keep to retain or remove to delete)

## Parameters

- `name` (optional): Worktree name. A random name is generated if not provided.
