---
id: S_01_planning-reasoning
title: Symphony 在线规划的模型推理设置
type: S
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
modules: [openjiuwen/symphony/orchestration]
files:
  - openjiuwen/symphony/orchestration/routing.py
  - openjiuwen/symphony/orchestration/planning/fast.py
  - openjiuwen/symphony/orchestration/planning/beam.py
  - openjiuwen/symphony/orchestration/graph/matcher/ontology.py
---

# 在线规划与关系匹配

自动路由、Skill 的 Fast 和 Beam 规划及专家任务规划沿用宿主注入模型的推理配置。宿主未指定思考档位时，在线请求使用模型默认行为。这一约定同样适用于路由重判和 Beam 最终重排。

构图时，本体匹配请求显式要求关闭思考。可识别的模型端拒绝继续走模型客户端的参数兼容回退。
