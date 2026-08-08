Launch a third-party CLI agent (claudecode / codex / ...) directly as a teammate. Its brain is the CLI subprocess rather than a local LLM, and it sends messages / claims tasks through the auto-injected team MCP tools.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `cli-coder-1`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must be unique within the team |
| **display_name** | public | Human-readable label for the CLI member (e.g. "Claude CLI Coder"); presentational only |
| **desc** | public | Optional. Public roster description of this CLI member — how peers recognise it in list_members / the team roster. Injected into other members' system prompts — never put private content here |
| **prompt** | private | **Required**. The private system prompt this CLI member adopts to act as this member. Visible only to this member, never shown in peers' roster |
| **cli_agent** | internal | **Required**. The CLI kind to launch (e.g. `claude` / `codex`); must match a static config entry pre-declared in `TeamAgentSpec.external_cli_agents` — the launch command, working directory and MCP injection all live in that entry, and this field only references it by name |

CLI members reject `model_name` (the model lives on the CLI side). The framework launches the CLI subprocess from the declared config and auto-injects the team collaboration tools (read_inbox / claim_task / send_message / ...), so it participates as a first-class member.

**Capability requirement**: `TeamAgentSpec.external_cli_agents` must be non-empty (at least one CLI kind declared). With no CLI kinds declared, this tool is not listed in the available tools.

You must call build_team first. Call order: build_team → spawn_external_cli → create_task. Members exist before tasks. spawn_external_cli only creates the member record (status: UNSTARTED); when it gets started depends on the team's dispatch mode (see the "Task Dispatch" section of your system prompt). `prompt` is a long-term role setup — do not bind it to specific tasks (those are delivered via create_task / send_message).
