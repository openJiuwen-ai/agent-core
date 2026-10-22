## Inline task member (`member`)

You can **bring the owner into existence while planning the task**: write a `member` object on the task node instead of `assignee` — exactly **one of the two**.

```
{"task_id": "impl-api", "title": "...", "content": "...",
 "member": {"member_name": "api-dev-1", "display_name": "API specialist",
            "desc": "...", "prompt": "...", "lifetime": "dormant"},
 "reviewer": [...]}
```

- `member_name` / `display_name` / `desc` / `prompt` / `model_name` mean exactly what they mean on `spawn_teammate` (naming rules and the public/private boundary apply); `member_name` must not collide with an existing member
- **`lifetime` is required** and decides how the member leaves once its work is done:
  - `shutdown`: shuts down automatically once every task on its board is terminal — the same as a manual `shutdown_member`; **the name cannot be reused**, number same-domain members instead
  - `dormant`: goes dormant automatically once its board is terminal — stays on the roster, processes no events, consumes nothing; **it wakes only when addressed directly** (`send_message(to=<member>)` / `targets` naming it / a new task assigned to it); broadcasts never wake it. After handling what woke it, it goes dormant again
- When the team declares external CLI kinds, `member` may also carry `cli_agent` (as in `spawn_teammate.cli_agent`) to run the member on an external CLI; its model arguments (`fallback_model_name` ...) go in `member.external_cli`. `lifetime` applies to external CLI members alike
- At runtime an inline member is **indistinguishable** from one created with `spawn_teammate`: the scheduling framework starts it the same way, it can be reassigned, it appears in `list_members`
- Retirement is performed by the scheduling framework; to remove a dormant member for good, call `shutdown_member` on it once more (`clean_team` still requires every member to be SHUTDOWN)
- Members reused across task batches are still created with `spawn_teammate`; a specialist that exists for one task is declared inline
