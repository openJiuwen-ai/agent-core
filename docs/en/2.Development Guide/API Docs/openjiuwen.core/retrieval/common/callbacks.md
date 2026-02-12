# openjiuwen.core.retrieval.common.callbacks

## class openjiuwen.core.retrieval.common.callbacks.BaseCallback

Base callback class for tracking indexing progress.


```python
BaseCallback(seq: Sequence, **kwargs)
```

Initialize callback object.

**Parameters**:

* **seq**(Sequence): Sequence object (e.g., list or tuple).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### __call__

```python
__call__(start_idx: int = -1, end_idx: int = -1, batch: Optional[list[str]] = None, **kwargs) -> None
```

Callback function (empty implementation).

**Parameters**:

* **start_idx**(int, optional): Start index. Default: -1.
* **end_idx**(int, optional): End index. Default: -1.
* **batch**(list[str], optional): Batch data. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### property call_counter

```python
call_counter -> int
```

Get call counter for current callback object.

**Returns**:

**int**, returns the call count.

## class openjiuwen.core.retrieval.common.callbacks.TqdmCallback

Tqdm callback class for tracking embedding progress.


```python
TqdmCallback(seq: Sequence, use_rich: bool = False, desc: str = "Indexing", **kwargs)
```

Initialize Tqdm callback object.

**Parameters**:

* **seq**(Sequence): Sequence object (e.g., list or tuple).
* **use_rich**(bool, optional): Whether to use rich-style progress bar. Default: False.
* **desc**(str, optional): Progress bar description. Default: "Indexing".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### __call__

```python
__call__(start_idx: int = -1, end_idx: int = -1, batch: Optional[list[str]] = None, **kwargs) -> None
```

Increment tqdm progress bar by 1.

**Parameters**:

* **start_idx**(int, optional): Start index. Default: -1.
* **end_idx**(int, optional): End index. Default: -1.
* **batch**(list[str], optional): Batch data. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### __len__

```python
__len__() -> int
```

Return sequence length.

**Returns**:

**int**, returns the sequence length.
