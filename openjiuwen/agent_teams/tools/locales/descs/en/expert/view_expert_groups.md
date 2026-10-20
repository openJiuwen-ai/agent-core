Browse the expert groups (`agent_group` packages) **installed** on this machine and their members. An expert group is a set of experts that work together; every member is an expert template, and every member except `leader` can be spawned on its own with `spawn_expert(expert=<member>, group=<group>)`.

## When to Use

- You need one kind of expert from a group: `action="list"` to find the group, then `action="get", name=<group>` for its member table.
- You only care about one member: `action="get", name=<group>, member=<member>` expands just that member.
- This team was assembled from a group (marked `bound`): you can spawn more instances of a group member (e.g. `clip-2` next to the existing `clip`).

## Parameters

| Parameter | Usage |
|---|---|
| **action** | `list` (default) lists all; `get` shows one group |
| **query** | `list` only: substring filter over group name, display name and description |
| **capabilities** | `list` only: keep groups that have **all** listed capability tags |
| **name** | Required for `get`: the group name |
| **member** | `get` only, optional: expand this one member |

## Output

- `list`: one line per group — name / display name / spawnable member count / capability tags / one-line description; the group this team is bound to is marked `bound=true`.
- `get`: group info plus the full member table, one line per member — name / display name / runtime / `spawnable` (whether `spawn_expert` can run it; `leader` cannot) / description; members this team already has as predefined members are marked `predefined=true`.

Member names are unique only within their group, so spawning a group member always takes `group` too.
