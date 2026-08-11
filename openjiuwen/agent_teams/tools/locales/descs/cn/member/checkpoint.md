为本成员当前对话上下文保存一个命名快照。后续 `spawn_teammate(fork="此名称")` 即可让新成员继承该快照的上下文，无需重新读取文件和搜索代码。

| 参数 | 说明 |
|---|---|
| **name** | 快照名（语义化 slug，如 `code-ready`、`base-class-understood`）。后续 fork 通过此名引用 |
| **description** | 可选；为何在此处打快照（如「基类接口分析完成」），仅用于日志和调试 |

## 何时使用

当本成员完成了**可复用的阶段性工作**后调用——例如：

- 阅读了基类/接口源码，后续多个执行者需要基于此实现不同派生类
- 分析了项目整体架构，后续执行者需要直接在代码理解基础上开工
- 完成了一份关键代码的调研，希望多个成员继承这份上下文

调用后继续工作不受影响——快照保存的是调用时刻的上下文位置。多个成员可以共享同一份快照。

## 示例

```
理解者完成基类阅读后：
checkpoint(name="base-ready", description="基类接口分析完成，可 fork")
```

## 与 fork 配合

```python
# 1. 理解者打快照
checkpoint(name="code-ready")

# 2. Leader 从快照 fork 多个执行者
spawn_teammate(name="dev-1", fork="code-ready", fork_source="understander", ...)
spawn_teammate(name="dev-2", fork="code-ready", fork_source="understander", ...)
```

**快照存的是调用时刻此成员的 `len(messages)`**。之后上下文继续增长不会影响已存快照的语义——fork 从该位置截取，后续消息不在继承范围内。

## 告知 Leader

打完快照后，运行时**会自动通知 leader**——发布框架事件，leader 上下文会收到一条带确切快照名的 announcement-only 公告（明示不需要回复）。你**无需**再单独用 `send_message` 汇报名字；想让 leader 理解快照用途，就把说明写进 `description` 参数（会随公告一起带上）。leader 随时可用 `list_checkpoints` 查看权威清单——不要指望 leader 猜你起的名字。
