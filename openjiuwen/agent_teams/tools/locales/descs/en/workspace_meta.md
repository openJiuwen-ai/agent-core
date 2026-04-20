Metadata tool for the team shared workspace: **file lock management** and **git version history queries**. Use when multiple members collaborate on shared files.

## Scope

This tool does **not** perform file I/O. The shared workspace is exposed as the `.team/` mount point — use the standard `read_file` / `write_file` / `glob` tools against `.team/...` paths for reads and writes.

## action

- **lock** — acquire an exclusive file lock (requires `path`)
- **unlock** — release a file lock (requires `path`)
- **locks** — list all currently active (non-expired) locks
- **history** — query the file's git version history (requires `path`); returns the most recent 10 commits with hash, author, date, and message

## Lock Semantics

- **Exclusive**: only one holder per file at a time
- **Timeout**: default 300 seconds; other members may reclaim an expired lock
- **Re-entrant**: calling `lock` again from the same holder refreshes the timeout
- **Advisory**: `write_file` does **not** automatically check locks. Before writing to a shared file, members should explicitly `lock` → `write_file` → `unlock` to avoid overwriting each other
- **Acquire failure**: returns `Locked by {holder_name}` indicating the current holder

## When to Use

- Before writing to files under `.team/` in multi-member collaboration: `lock`, write, then `unlock`
- Use `history` to inspect revisions of a shared file
- Use `locks` to see who is currently holding what (useful for coordination)

## When NOT to Use

- Read-only access — no lock needed
- Files in a member's private worktree (`.agent_teams/worktrees/<slug>/`) — worktree isolation already prevents conflicts
