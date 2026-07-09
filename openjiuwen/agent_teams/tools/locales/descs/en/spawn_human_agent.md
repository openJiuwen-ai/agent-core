Add a real person as a team member (Human in the Team). The human member is driven by the real user via HumanAgentInbox; the framework prepares an avatar (LLM + tools) that accepts tasks and sends/receives messages, but the actual decisions and output come from the person.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `product-owner`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must be unique within the team |
| **display_name** | public | Human-readable label for the human member (e.g. "Product Owner"); presentational only |
| **desc** | public | Role profile and responsibilities of the human member; injected into other members' system prompts and returned by list_members — never put private content here |

Human members **reject** `model_name` and `prompt` — the model and startup prompt are managed by the framework template, and this tool does not expose those parameters. `desc` / `display_name` are honoured for presentation and persisted desc.

**Capability requirement**: requires `TeamAgentSpec.enable_hitt=True` and the current build_team instance to leave HITT engaged. When the capability is off, this tool is not even listed in the available tools (and on a runtime downgrade it returns a rejection suggesting spawn_teammate instead).

You must call build_team first. Call order: build_team → spawn_human_agent → create_task. Members exist before tasks. spawn_human_agent only creates the member record (status: UNSTARTED); when it gets started depends on the team's dispatch mode (see the "Task Dispatch" section of your system prompt). `desc` is a long-term role profile — do not bind it to specific tasks (those are delivered via create_task / send_message).
