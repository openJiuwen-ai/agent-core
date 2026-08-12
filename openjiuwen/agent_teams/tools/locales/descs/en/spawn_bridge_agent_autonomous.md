Bridge an external independent agent (e.g. claudecode / codex / hermes) into the team as a member. Locally it is a full teammate, while concrete output comes from the remote agent reached over the protocol; the local LLM only schedules and passes remote output through.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `remote-claude-1`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must be unique within the team |
| **display_name** | public | Human-readable label for the bridge member (e.g. "Remote Claude"); presentational only |
| **desc** | public | Optional public roster description, injected into other members' system prompts — never put private content here |
| **prompt** | private | **Required**. The system prompt the remote agent adopts; visible only to this member, never shown in peers' roster |
| **mailbox_inject_mode** | internal | Optional. `passthrough` (default) minimally relays; `rephrase` wraps full sender context |
| **protocol** | internal | Optional protocol identifier; an empty string means no adapter is wired yet |
| **adapter_config** | internal | Optional adapter configuration passed to BridgeProtocolAdapter.connect |
| **model_name** | internal | Optional local scheduler model; it does not control the remote model |

**Capability requirement**: requires `TeamAgentSpec.enable_bridge=True` and the current build_team instance to leave Bridge engaged. When disabled, this tool is not listed.

You must call build_team first. spawn_bridge_agent only creates the member record (status: UNSTARTED); after the member is in place, follow the branch already selected in the system prompt: use `send_message` to start participation on the **Debate branch**, and use `create_task` only on the **Task-collaboration branch**. Members must exist before messages or tasks can land on them. Startup depends on the team's dispatch mode. `prompt` is a long-term role setup and remote briefing, not a request-specific instruction.
