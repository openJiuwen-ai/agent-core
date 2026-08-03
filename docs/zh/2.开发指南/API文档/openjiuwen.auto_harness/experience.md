# openjiuwen.auto_harness.experience

Auto Harness 经验库模块，提供经验记录的持久化存储和活跃上下文合成能力。经验以 JSONL 格式归档，支持关键词搜索和时间衰减排序，合成器将近期经验浓缩为可注入 prompt 的 markdown 片段。

子模块：
- `experience_store`：JSONL 经验归档存储
- `synthesizer`：活跃上下文合成器

---

## class openjiuwen.auto_harness.experience.experience_store.ExperienceStore

```python
class ExperienceStore:
    """JSONL-backed experience archive with keyword search."""

    def __init__(self, experience_dir: str) -> None
```

基于 JSONL 文件的经验归档存储，支持关键词搜索。经验记录在写入前会进行去重检查（24 小时内相同 topic + type 视为重复）。

**参数**：
* **experience_dir**(`str`)：经验存储目录路径，目录不存在时自动创建。

### record(experience: Experience) -> str

```python
async def record(self, experience: Experience) -> str
```

在去重检查后持久化经验记录。若在 24 小时窗口内已存在相同 topic 和 type 的记录，则拒绝写入并返回空字符串。

**参数**：
* **experience**(`Experience`)：要持久化的经验对象。

**返回**：成功时返回经验 ID，被去重拒绝时返回空字符串。

---

### search(query: str, top_k: int = 5) -> List[Experience]

```python
async def search(self, query: str, top_k: int = 5) -> List[Experience]
```

基于关键词在 topic、summary、details 字段中搜索经验。评分由关键词命中数和新鲜度衰减加权组成，按综合分数降序排列。

**参数**：
* **query**(`str`)：搜索关键词或主题描述。
* **top_k**(`int`)：最大返回条数，默认 5。

**返回**：按相关性排序的经验列表。

---

### list_recent(limit: int = 20) -> List[Experience]

```python
async def list_recent(self, limit: int = 20) -> List[Experience]
```

按时间戳降序返回最近的经验记录。

**参数**：
* **limit**(`int`)：最大返回条数，默认 20。

**返回**：按时间降序排列的经验列表。

---

### get(experience_id: str) -> Optional[Experience]

```python
async def get(self, experience_id: str) -> Optional[Experience]
```

根据 ID 获取单条经验记录。

**参数**：
* **experience_id**(`str`)：经验唯一标识。

**返回**：匹配的经验对象，未找到时返回 `None`。

---

## class openjiuwen.auto_harness.experience.synthesizer.ActiveContextSynthesizer

```python
class ActiveContextSynthesizer:
    """Synthesize recent experiences into an active-context string."""

    def __init__(self, experience_dir: str) -> None
```

将近期经验合成为活跃上下文字符串。按经验类型（优化经验 / 失败教训 / 关键洞察）分组，结合时间衰减权重生成 markdown 格式的摘要，用于注入 Agent prompt。

**参数**：
* **experience_dir**(`str`)：经验存储目录路径。

### synthesize(experiences: List[Experience], max_tokens: int = 2000) -> str

```python
async def synthesize(self, experiences: List[Experience], max_tokens: int = 2000) -> str
```

将经验列表合成为 markdown 摘要。按经验类型分组，组内按时间衰减权重排序，在 token 预算内依次填充各类型段落。

**参数**：
* **experiences**(`List[Experience]`)：待合成的经验列表。
* **max_tokens**(`int`)：最大 token 预算，默认 2000。

**返回**：合成的 markdown 字符串，无经验时返回空字符串。

---

### load_and_synthesize(top_k: int = 30) -> str

```python
async def load_and_synthesize(self, top_k: int = 30) -> str
```

便捷方法：先从存储中加载最近的经验，再进行合成。

**参数**：
* **top_k**(`int`)：加载的最近经验条数，默认 30。

**返回**：合成的 markdown 字符串。
