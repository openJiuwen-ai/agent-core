Switch an external CLI member (claudecode / codex / ...) to another built-in model, or change its reasoning effort, without respawning it. Built-in models run on the CLI's own login (e.g. a subscription); the models and efforts you can choose are listed in `<builtin_model_catalog>` (see the `model` parameter).

| Parameter | Usage |
|---|---|
| **member_name** | **Required**. The external CLI member to switch |
| **model** | Optional. A model from the catalog entry of the member's `cli_agent`. Omit to keep the current model and change only `effort` |
| **effort** | Optional. One of the chosen model's `efforts`. Omitted with `model` set: the model's `default_effort`; omitted without `model`: unchanged |

Pass at least one of `model` / `effort`.

**When to use**: match the member's model to its current work — move to a stronger model or a higher effort when a task turns out harder than expected or the member keeps failing; drop to a lighter model or a lower effort for routine, high-volume work to save time and quota.

**Effect**: the choice is saved first and kept across restarts. A running member switches before its next turn — the turn in progress finishes on the previous model; a member that is not running starts on the new model. Only members on the CLI's own login qualify: a member spawned with `model_name`, or one that already fell back to a team model pool endpoint, is rejected.
