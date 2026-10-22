Spawn a teammate from an installed expert template. The new member runs with the expert's persona, skills and tools — you do not write its role. Like `spawn_teammate` this only creates the member record (status UNSTARTED); the existing triggers (`create_task` / `send_message` ...) start it.

## Parameters

| Parameter | Visibility | Usage |
|---|---|---|
| **expert** | internal | **Required**. A single expert's **package name** (`name` in `view_experts`); with `group`, a **member name** of that group |
| **group** | internal | Expert group name. Omit for a single expert; required for a group member (member names are unique only within their group) |
| **member_name** | public | **Required**. Unique member name in the team (e.g. `finance-1`, `clip-2`); **must start with a lowercase letter, followed only by lowercase letters, digits and hyphens**. One expert can be spawned several times under different names |
| **display_name** | public | Optional. Defaults to the expert's display name; when a teammate already uses it, " (member_name)" is appended |
| **desc** | public | Optional. **Added** public description, appended after the expert's own description (never replaces it). Use it for how this instance differs from its siblings, e.g. "covers shots 1–10" |
| **prompt** | **private** | Optional. **Added** standing agreement, appended after the expert's persona (never replaces it). Do not restate the persona or put concrete tasks here |
| **model_name** | internal | Optional. Only when the user names a model; same rules as `spawn_teammate`, or `spawn_teammate` with `cli_agent` for an external-runtime expert |
| **external_cli** | internal | Optional object, **only for external-runtime experts** (runtime other than `native`); passing it for a native expert is an error. Its fields mean and validate exactly like `spawn_teammate.external_cli` |
| **isolation** | internal | Optional, as in `spawn_teammate` (`"worktree"`). Not supported for external-runtime experts |
| **permissions** | internal | Optional, as in `spawn_teammate` (tighten only). Not supported for external-runtime experts |

## How to Use

1. Find the expert with `view_experts` / `view_expert_groups` and check `spawnable`.
2. Call `spawn_expert`; for several instances of one expert use different `member_name`s and say in `desc` what each one covers.
3. Hand out work with `create_task` / `send_message`.

- A group's `leader` cannot be spawned as a teammate.
- When this team was assembled from an expert group, naming that group in `group` spawns another instance of a group member from the team's own assembled template — identical to the predefined member of the same name, without re-reading the package on disk.
- An external-runtime expert needs its CLI kind declared for this team (same as `spawn_teammate`'s `cli_agent`); model, authentication fallback and built-in model rules are also those of `spawn_teammate` with `cli_agent`, with the fallback and built-in model given inside the `external_cli` object.
- The expert's capability snapshot is stored on the member record: upgrading or uninstalling the package later does not change members already spawned.
