Add a real person as a team member (Human in the Team) **without preparing any internal avatar**. A passive human member is a pure roster identity plus a message-bus address: team-side messages and task assignments are relayed straight to the person over their external channel (SDK callback); the person acts back through the external protocol — speech goes onto the message bus, and tool operations (view tasks, claim, complete, verify, send messages) are relayed as tool calls the runtime executes under their member identity, with exactly the same effect as a human member's (avatar's) tool operations.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `product-owner`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must be unique within the team |
| **display_name** | public | Human-readable label for the passive human member (e.g. "Product Owner"); presentational only |
| **desc** | public | Role profile and responsibilities of the passive human member; injected into other members' system prompts and returned by list_members — never put private content here |

Passive human members **reject** `model_name` and `prompt` — there is no avatar, hence no model and no startup prompt; this tool does not expose those parameters.

**Capability requirement**: requires `TeamAgentSpec.enable_hitt=True` and the current build_team instance to leave HITT engaged. When the capability is off, this tool is not even listed in the available tools (and on a runtime downgrade it returns a rejection suggesting spawn_teammate instead).

You must call build_team first. Call order: build_team → spawn_passive_human → create_task. Members exist before tasks. A passive human member is READY from the moment it is registered (there is no process to start), and **may be assigned tasks via create_task / update_task** — the person receives the notification on their external channel and completes the work by relaying tool calls. `desc` is a long-term role profile — do not bind it to specific tasks (those are delivered via create_task / send_message).
