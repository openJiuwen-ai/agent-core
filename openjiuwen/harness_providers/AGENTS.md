# harness_providers maintenance guide

`openjiuwen.harness_providers` hosts the built-in implementations of the
provider-neutral `openjiuwen.harness_protocol` SPI plus the two glue layers a
host needs to drive them: the DeepAgent-style I/O adapter and the manifest
factory. Contracts live in `harness_protocol`; nothing here changes them.

## Module map

```
harness_providers/
├── base.py         # SerializedTurnHarness: shared lifecycle / turn queue / interaction / checkpoint skeleton
├── stream.py       # BoundedEventBuffer + BufferedEventCursor (single-consumer, BLOCK backpressure)
├── io_adapter.py   # HarnessIOAdapter: protocol <-> DeepAgent OutputSchema / InteractiveInput contract
├── factory.py      # create_harness(manifest, provider=...) / build_harness_context(...) / resolve_provider
├── skills.py       # Portable bundle copying to CLI project discovery roots; skip/replace conflicts
├── inputs.py       # harness_input_text: HarnessInput -> prompt text
├── jsonsafe.py     # to_json_safe: vendor objects -> protocol JSON values
├── trajectory.py   # HarnessTrajectoryRecorder: protocol events -> trajectory spans (host glue, like io_adapter)
├── telemetry/      # otlp_receiver.py: process-wide loopback OTLP receiver shared by providers
├── native/         # DeepAgentHarness over the in-process DeepAgent interaction loop (+ NativeHarnessProvider)
├── claudecode/     # ClaudeCodeHarness over claude-agent-sdk (config / options / mapping / failure_classifier / observation)
├── codex/          # CodexHarness over openai-codex (config / options / mapping / failure_classifier / observation / rollout_trace)
└── dsh/            # DshHarness over deepseek-harness (moved from agent_teams.external.dsh; see dsh/AGENTS.md)
```

Provider names accepted by the factory: `native`, `native_v2`, `claudecode`, `codex`, `dsh`.
The provider card names are `deepagent`, `native_v2`, `claude-code`, `codex`, `deepseek-harness`.
`native_v2` is resolved lazily to `agent_teams.harness.protocol_adapter.NativeV2HarnessProvider`;
its implementation stays in the team package and reuses NativeHarness manifest construction.

Design records: spec `openjiuwen/harness/docs/specs/S_19_harness-providers.md`, feature
`openjiuwen/harness/docs/features/F_03_harness-providers-and-manifest-factory.md`, team wiring
`openjiuwen/agent_teams/docs/specs/S_27_external-harness-member-runtime.md` and
`openjiuwen/agent_teams/docs/features/F_96_protocol-harness-providers-and-member-migration.md`.

## Invariants

1. **One turn skeleton.** Every provider subclasses `SerializedTurnHarness` and
   implements only `_open_session` / `_close_session` / `_execute_turn` (+
   `_steer` / `_interrupt_turn` when the card declares STEER / abort). The base
   class owns the state machine, the pending queue, `STARTED`/terminal event
   pairing, interaction bookkeeping (`_request_interaction`,
   `_cancel_pending_interactions`) and checkpoint publishing
   (`_publish_checkpoint`, `_restored_checkpoint_data`). Do not re-implement
   these per provider.
2. **Capabilities are truthful.** A card declares only what the SDK can do
   end to end; unsupported commands raise `UnsupportedHarnessCapabilityError`.
   DSH keeps an empty capability set; Claude Code / Codex declare STEER,
   GRACEFUL_ABORT, PERSISTENT_SESSION, CHECKPOINT, MCP_TOOLS; the DeepAgent
   harness declares STEER and FORCE_ABORT.
3. **Vendor SDKs stay optional.** Config / provider / package imports never
   import a vendor SDK; `_open_session` loads it lazily and a missing SDK
   surfaces as `HarnessError`. Startup failures raise `ProviderStartupError`
   with a normalized `TurnError` so hosts classify without parsing text.
4. **Failure vocabulary is shared.** `TurnError.category` is one of
   `auth_required / quota_exceeded / rate_limited / server_unavailable /
   network_timeout / process_start_failed / sdk_error / unknown`;
   `provider_data` carries `sdk_error_type` / `http_status`. The team
   reliability layer maps this one-to-one.
5. **Provider-private seams do not leak.** `ClaudeCodeHarness(transport_factory=...)`
   is the only constructor-only hook, for hosts that own the SDK transport (ssh).
   Vendor observation channels (Claude request-body logs through the shared
   loopback receiver, Codex rollout trace and raw response notifications) stay
   inside the provider: they switch on only when the host declares
   `MODEL_REQUEST_OBSERVATION` and surface as `ModelRequestEvent`s. Tool items a
   request caused are held until that request is reported and cite it in
   `causation_ids`; a request whose vendor record does not arrive within
   `request_observation_wait_s` is reported from its reply
   (`input_observed=False`). Hosts record trajectories with
   `HarnessTrajectoryRecorder`, never by reading vendor data.
6. **User input is an interaction.** Claude `AskUserQuestion`, Codex
   `request_user_input` (App Server request `item/tool/requestUserInput`,
   parsed from the raw `_approval_handler` params because the SDK has no
   generated type for it) and DeepAgent `ask_user` interrupts become
   `UserInputRequest`s; the turn stays open until the host answers. When the
   host declares USER_INPUT / TOOL_APPROVAL the Claude harness switches
   `permission_mode` to `default` so the SDK actually consults `can_use_tool`;
   the Codex harness adds `features.default_mode_request_user_input=true`
   because the tool is off in the CLI's default mode.
7. **The IO adapter is the only DeepAgent-facing projection.** `HarnessIOAdapter`
   emits `llm_output` / `llm_reasoning` / `tool_call` / `tool_result` /
   `__interaction__` chunks and resolves `InteractiveInput` against pending
   interactions; `agent_teams.external.member_runtime` composes it instead of
   projecting events itself. A `tool_result` chunk keeps the structured `result`
   and adds `rendered_result` (the text the model read) as a separate field when
   the provider supplies it; DeepAgent's `_ObservationRail` carries it through
   `ItemLifecycleEvent.data` and the tool-result `ContentBlock.data`.
8. **Provider extensions are ratified before they commit.** A switch that
   changes the provider's persistent identity (today: the Claude Code / Codex
   authentication fallback) goes through
   `SerializedTurnHarness._confirm_provider_extension`, which sends a
   `ProviderInteractionRequest(request_type="auth_fallback")`. A host without
   `PROVIDER_INTERACTION` implicitly agrees; a host that declines makes the
   harness reconnect the native endpoint (same session / thread) and fail the
   turn with the original `auth_required` error. If reconnecting the original
   endpoint fails, the next accepted input attempts one reconnect before
   dispatch. Keep the same session/thread; never replay the failed input or
   silently use the declined fallback. `HarnessIOAdapter` only
   declares `PROVIDER_INTERACTION` when a `provider_interaction_handler` is
   bound; `agent_teams.external.member_runtime` binds one that answers
   `auth_fallback` from the team DB promotion.
9. **The manifest is DeepAgent-first.** `create_harness` hot-loads the full
   `AgentTemplateSpec` for `native`; third-party providers only take the model
   endpoint and, through `build_harness_context`, the rendered prompt sections
   and MCP servers. Portable `skills` are copied before SDK startup into the
   CLI project discovery directory: .claude/skills, .agents/skills, .dsh/skills.
   `skill_conflict` defaults to skip; replace stages a complete bundle before
   renaming the existing directory. Never remove copied skills at stop.
   Manifests carrying `tools` / `rails` / `subagents` are still rejected.

## Change requirements

- New provider: subclass `SerializedTurnHarness`, add a `*HarnessProvider`,
  register the name in `factory.resolve_provider` / `PROVIDER_NAMES`, add
  fake-SDK unit tests under `tests/unit_tests/harness_providers/` and a real
  CLI suite under `tests/system_tests/harness_providers/` reusing
  `_contract.py`.
- Mapping changes need the corresponding fake-SDK test updated; keep raw SDK
  objects out of `ProviderEvent` payloads (`to_json_safe` first).
- Public protocol changes are made in `openjiuwen/harness_protocol` first.
