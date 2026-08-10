# openjiuwen.extensions.vendor_specific

Vendor-Specific Model Services - vendor-specific model service extensions.

This subpackage provides convenience and reference implementations for developers. Currently, it contains only one deprecated alias re-export; for official capabilities, please use the corresponding implementation in `openjiuwen.core`.

## aliyun_reranker

Defined in `aliyun_reranker.py`.

| Class | Description |
|---|---|
| `AliyunReranker` | **Deprecated** - Please use `openjiuwen.core.retrieval.DashscopeReranker` instead. This module prints a deprecation warning via logger upon import; `AliyunReranker` is actually an alias re-export of `DashscopeReranker` and provides no additional capabilities. |

## Notes

The `extensions/harness/` and `extensions/common/` subdirectories, also marked by AS-14, have no active `.py` source files (the former's `__init__.py` is a 0-byte empty namespace, and the latter contains only `.pyc` bytecode under `__pycache/`), so no API documentation is generated for them. This is the same type of legacy as `deepagents/` (only `.pyc`) in Appendix A.2, not a real omission.
