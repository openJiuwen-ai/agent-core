Launch a third-party CLI agent (claudecode / codex / ...) directly as a teammate. Its brain is the CLI subprocess rather than a local LLM, and it communicates through the auto-injected team MCP tools.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `cli-coder-1`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must be unique within the team |
| **display_name** | public | Human-readable label for the CLI member; presentational only |
| **desc** | public | Optional public roster description, injected into other members' system prompts — never put private content here |
| **prompt** | private | **Required**. The private system prompt this CLI member adopts; visible only to this member |
| **cli_agent** | internal | **Required**. The CLI kind to launch; it must match an entry in `TeamAgentSpec.external_cli_agents` |

CLI members reject `model_name` because the model lives on the CLI side. The framework launches the declared subprocess and injects team collaboration tools so it participates as a first-class member.

**Capability requirement**: `TeamAgentSpec.external_cli_agents` must be non-empty. With no declared CLI kind, this tool is not listed.

You must call build_team first. spawn_external_cli only creates the member record (status: UNSTARTED); after the member is in place, follow the branch already selected in the system prompt: use `send_message` to start participation on the **Debate branch**, and use `create_task` only on the **Task-collaboration branch**. Members must exist before messages or tasks can land on them. Startup depends on the team's dispatch mode. `prompt` is a long-term role setup, not a request-specific instruction.
