Bridge an external independent agent (e.g. claudecode / codex / hermes) into the team as a "member". Locally it is a full teammate (claims tasks, sends/receives messages like any teammate); the concrete work output is produced by the remote agent reached over a pure-text protocol, and the local LLM only schedules — it passes the remote's output through verbatim.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `remote-claude-1`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must be unique within the team |
| **display_name** | public | Human-readable label for the bridge member (e.g. "Remote Claude"); presentational only |
| **desc** | public | Optional. Public roster description of the bridge member — how peers recognise it in list_members / the team roster. Injected into other members' system prompts — never put private content here |
| **prompt** | private | **Required**. The system prompt the remote agent adopts to act as this member (this member's private working setup); sent to the remote via adapter.connect. Visible only to this member, never shown in peers' roster |
| **mailbox_inject_mode** | internal | Optional. How team messages are wrapped when relayed to the remote: `passthrough` (default) prefixes only the sender label; `rephrase` wraps full sender context (role, desc, related task) |
| **protocol** | internal | Optional. Protocol identifier (e.g. `a2a` / `acp` / `claudecode`), reserved for future BridgeProtocolAdapter lookup; empty string means no adapter is wired yet |
| **adapter_config** | internal | Optional. Adapter configuration (endpoint / auth / relay_timeout_s, ...), passed verbatim to BridgeProtocolAdapter.connect |
| **model_name** | internal | Optional. Model name for the local scheduler LLM; the remote agent's model lives on its own side and is not controlled here |

**Capability requirement**: requires `TeamAgentSpec.enable_bridge=True` and the current build_team instance to leave Bridge engaged. When the capability is off, this tool is not even listed in the available tools.

You must call build_team first. Call order: build_team → spawn_bridge_agent → create_task. Members exist before tasks. spawn_bridge_agent only creates the member record (status: UNSTARTED); when it gets started depends on the team's dispatch mode (see the "Task Dispatch" section of your system prompt). `prompt` is a long-term role setup and remote briefing — do not bind it to specific tasks (those are delivered via create_task / send_message).
