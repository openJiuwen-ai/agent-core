# openjiuwen.core.retrieval.common.callbacks

## class openjiuwen.core.retrieval.common.callbacks.BaseCallback

用于跟踪索引进度的回调基类。


```python
BaseCallback(seq: Sequence, **kwargs)
```

初始化回调对象。

**参数**：

* **seq**(Sequence)：序列对象（比如 list 或 tuple）。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### __call__

```python
__call__(start_idx: int = -1, end_idx: int = -1, batch: Optional[list[str]] = None, **kwargs) -> None
```

回调函数（空实现）。

**参数**：

* **start_idx**(int, 可选)：起始索引。默认值：-1。
* **end_idx**(int, 可选)：结束索引。默认值：-1。
* **batch**(list[str], 可选)：批次数据。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### property call_counter

```python
call_counter -> int
```

获取当前回调对象的调用计数器。

**返回**：

**int**，返回调用计数。

## class openjiuwen.core.retrieval.common.callbacks.TqdmCallback

用于跟踪嵌入进度的 Tqdm 回调类。


```python
TqdmCallback(seq: Sequence, use_rich: bool = False, desc: str = "Indexing", **kwargs)
```

初始化 Tqdm 回调对象。

**参数**：

* **seq**(Sequence)：序列对象（比如 list 或 tuple）。
* **use_rich**(bool, 可选)：是否使用 rich 风格的进度条。默认值：False。
* **desc**(str, 可选)：进度条描述。默认值："Indexing"。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### __call__

```python
__call__(start_idx: int = -1, end_idx: int = -1, batch: Optional[list[str]] = None, **kwargs) -> None
```

更新 Tqdm 进度条。

**参数**：

* **start_idx**(int, 可选)：起始索引。默认值：-1。
* **end_idx**(int, 可选)：结束索引。默认值：-1。
* **batch**(list[str], 可选)：批次数据。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### __len__

```python
__len__() -> int
```

返回序列长度。

**返回**：

**int**，返回序列长度。
