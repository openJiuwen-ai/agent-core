验证任务（仅验证者可用）。

## 使用场景

- 你被 leader 指派为某任务的验证者后，当该任务的 author 完成工作、任务进入 `in_review`，由你来裁决。
- 用 `view_task(action=in_review)` 查看指派给你验证、正在等待验证的任务；读其产出后调用本工具给出结论。

## 决策

- `decision=pass`：验证通过，任务从 `in_review` 转 `completed`，并解除依赖它的下游任务。
- `decision=fail`：验证不通过，任务打回 `in_progress` 让 author 返工；`feedback` 会定向发给 author 指导返工。

## 约束

- 只能验证指派给你（你在该任务的 reviewer 列表里）且当前处于 `in_review` 的任务。
- 不能验证以你自己为 author 的任务（不可自验）。
- 一个任务有多个验证者时，v1 采「首个裁决即生效」——任一验证者 pass 即完成、任一 fail 即打回。
