---
id: B_20261126_2185_dashscope-connector-ownership
title: DashscopeEmbedding 共享 TCPConnector 被首个 ClientSession 关闭
type: B
author: linghb
author_email: 957733934@qq.com
modules:
  - openjiuwen/core
files:
  - openjiuwen/core/retrieval/embedding/dashscope_embedding.py
  - tests/unit_tests/core/retrieval/embedding/test_dashscope_embedding.py
designs: []
design_note: 单参数修复,不改变契约与公开接口,无需独立设计。
---

# DashscopeEmbedding 共享 TCPConnector 被首个 ClientSession 关闭

## 现象与根因

`DashscopeEmbedding` 配置自定义 SSL(`verify` 为证书路径/SSLContext,
或 `EMBEDDING_SSL_CERT`)时,构造函数创建实例级共享的
`aiohttp.TCPConnector`(`dashscope_embedding.py:99`),`__del__` 在析构时
显式 close,作者意图明确是跨调用复用。但两处异步路径
(`embed_documents` / `embed_multimodal`)将该共享 connector 传入
`aiohttp.ClientSession(connector=...)` 时未设 `connector_owner=False`:

- aiohttp `ClientSession.__init__` 默认 `connector_owner=True`;
- session `close()`(async with 退出)在 owner 为真时
  `await self._connector.close()`,connector `_closed=True` 且连接池清空;
- 此后所有调用复用已关闭 connector,`_create_connection` 内
  `if self._closed: raise ClientConnectionError("Connector is closed")`。

结果:配置了自定义 SSL 的实例,第一次 embed 成功,第二次起全部抛
"Connector is closed"。已对照 venv 内 aiohttp 3.14.5 源码逐行确认
(client.py:349 / client.py:1592 / connector.py:513-516 / connector.py:1133)。
未配置自定义 SSL 时 `aio_connector=None`,ClientSession 自建自有
connector,不受影响——这解释了该缺陷一直未被发现:仅命中需要显式 SSL
配置的部署形态。

实测复现(aiohttp 3.14.5):
`ClientSession(connector=c)` 退出后 `c.closed == True`,
`ClientSession(connector=c, connector_owner=False)` 退出后
`c.closed == False`,可继续使用,显式 `await c.close()` 仍生效。

## 修复决策

两处 session 构造补 `connector_owner=False`,connector 生命周期完全归
实例所有(`__del__` 的显式 close 语义不变)。不引入每次调用新建
connector 的方案——那会放弃连接复用并改变 `__del__` 语义;不引入长驻
session——那会扩大改动面(需处理关闭时序),与"最小确定性修复"冲突。

## 回归证据

基线为 `46014823e`(develop HEAD),环境为 Windows、Python 3.11、aiohttp
3.14.5(venv 实装版本)、uv 同步测试环境。

新增测试 `TestDashscopeEmbeddingConnectorOwnership` 共 2 项
(全部 mock `_get_embeddings`,无网络):

1. `test_embed_documents_reuses_connector_across_sessions`:两次
   `embed_documents` 共享同一 connector 实例,session 退出后
   `connector.closed` 保持 False,且每次 `ClientSession` 构造都带
   `connector_owner=False`(用 autospec spy 捕获构造 kwargs)。
   修复前失败:connector 被首个 session 关闭,且伴随
   `RuntimeWarning: coroutine 'TCPConnector.close' was never awaited`。
2. `test_embed_multimodal_keeps_connector_open`:同语义覆盖 multimodal
   路径。修复前同样失败。

修复后 2 项通过,dashscope 全文件 29 项通过;retrieval 全量回归:

```powershell
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/core/retrieval -q
# 972 passed, 1 failed (test_html_file_parser 1 项为干净 develop 上同样失败的预存在问题)
```

## 遗留

无。`embed_query` 走 `embed_documents`、`embed_documents_sync` 等同步
路径走 dashscope SDK 自带 session,均不受影响。
