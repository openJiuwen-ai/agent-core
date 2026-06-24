取消一个仍在运行的后台异步任务。

## 何时使用
- 某个后台任务（如 swarmflow 编排）跑飞了或不再需要，你想主动终止它。

## 参数
- task_id：要取消的任务 id（可先用 async_tasks_list 查到）。

## 注意
- 取消后该任务状态变为 error（cancelled），不会再回灌结果。
