@AGENTS.md

# Claude Code Notes

- Keep shared project rules in `AGENTS.md` so all coding agents use the
  same architecture guidance.
- Use this file only for Claude-specific imports or workflow notes.
- Detailed rules by topic: see `.claude/rules/` (code-style, testing,
  architecture, git-workflow, security, prompt-tool-rails).
- Permissions, env vars, and model defaults: see `.claude/settings.json`.
- Run `/memory` to manage auto memory.
- Run `/context` to see which files are loaded in the current session.
