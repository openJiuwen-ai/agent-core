from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Callable


_TERM_RE = re.compile(r"[A-Za-z0-9_]{3,}")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_QUERY_TRANSLATIONS: tuple[tuple[str, str], ...] = (
    ("访问令牌", "access token"),
    ("刷新令牌", "refresh token"),
    ("身份验证", "authentication"),
    ("权限控制", "authorization permission access control"),
    ("权限校验", "authorization permission validation"),
    ("访问控制", "access control authorization permission"),
    ("单元测试", "unit test"),
    ("集成测试", "integration test"),
    ("系统测试", "system test"),
    ("端到端", "end to end e2e"),
    ("上下文窗口", "context window"),
    ("上下文", "context"),
    ("压缩", "compression compress"),
    ("规则压缩", "rule compression"),
    ("消息卸载", "message offload offloader"),
    ("查询词", "query terms"),
    ("查询", "query"),
    ("相关性", "relevance score scoring"),
    ("优先级", "priority"),
    ("保护", "protect preserve retain"),
    ("保留", "keep retain preserve"),
    ("删除", "delete remove drop"),
    ("过滤", "filter"),
    ("排序", "sort order ranking"),
    ("评分", "score scoring"),
    ("检索", "retrieve retrieval search"),
    ("搜索", "search"),
    ("匹配", "match matching"),
    ("路径", "path"),
    ("文件名", "filename file name"),
    ("文件", "file"),
    ("目录", "directory folder path"),
    ("代码", "code source"),
    ("源码", "source code"),
    ("函数", "function"),
    ("方法", "method function"),
    ("类", "class"),
    ("变量", "variable"),
    ("参数", "parameter argument args"),
    ("返回值", "return value"),
    ("返回", "return"),
    ("导入", "import"),
    ("导出", "export"),
    ("模块", "module"),
    ("包", "package"),
    ("接口", "interface api"),
    ("配置", "config configuration settings"),
    ("环境变量", "environment variable env"),
    ("缓存", "cache"),
    ("状态", "state status"),
    ("会话", "session"),
    ("工作流", "workflow"),
    ("代理", "agent"),
    ("工具", "tool"),
    ("调用", "call invoke invocation"),
    ("重试", "retry"),
    ("超时", "timeout"),
    ("异步", "async asynchronous"),
    ("同步", "sync synchronous"),
    ("并发", "concurrent concurrency"),
    ("线程", "thread"),
    ("进程", "process"),
    ("队列", "queue"),
    ("任务", "task job"),
    ("事件", "event"),
    ("日志", "log logging"),
    ("错误日志", "error log"),
    ("堆栈", "stack trace traceback"),
    ("调用栈", "stack trace traceback"),
    ("异常", "exception error"),
    ("错误", "error"),
    ("失败", "failed failure fail"),
    ("警告", "warning warn"),
    ("崩溃", "crash panic fatal"),
    ("致命", "fatal critical"),
    ("严重", "critical severe"),
    ("调试", "debug"),
    ("排查", "debug troubleshoot investigate"),
    ("定位", "locate diagnose find"),
    ("诊断", "diagnose diagnostic"),
    ("修复", "fix repair resolve"),
    ("解决", "fix resolve"),
    ("问题", "issue problem bug"),
    ("缺陷", "bug defect"),
    ("回归", "regression"),
    ("行为", "behavior"),
    ("性能", "performance"),
    ("延迟", "latency delay"),
    ("吞吐", "throughput"),
    ("内存", "memory"),
    ("泄漏", "leak"),
    ("数据库", "database db"),
    ("事务", "transaction"),
    ("连接", "connection connect"),
    ("请求", "request"),
    ("响应", "response"),
    ("客户端", "client"),
    ("服务端", "server"),
    ("接口请求", "api request"),
    ("接口响应", "api response"),
    ("状态码", "status code"),
    ("错误码", "error code status code"),
    ("序列化", "serialize serialization"),
    ("反序列化", "deserialize deserialization"),
    ("编码", "encoding encode"),
    ("解码", "decoding decode"),
    ("中文", "chinese cjk"),
    ("英文", "english"),
    ("语言", "language"),
    ("翻译", "translate translation"),
    ("分词", "tokenize tokenization segment"),
    ("令牌", "token"),
    ("密码", "password"),
    ("密钥", "key secret"),
    ("秘钥", "key secret"),
    ("凭证", "credential credentials"),
    ("认证", "auth authentication"),
    ("鉴权", "auth authorization"),
    ("授权", "authorization"),
    ("权限", "permission authorization"),
    ("安全", "security secure"),
    ("漏洞", "vulnerability security"),
    ("注入", "injection inject"),
    ("敏感", "sensitive"),
    ("脱敏", "redact mask sanitize"),
    ("刷新", "refresh"),
    ("过期", "expired expire expiration"),
    ("登录", "login signin"),
    ("登出", "logout signout"),
    ("用户", "user"),
    ("角色", "role"),
    ("依赖", "dependency dependencies"),
    ("版本", "version"),
    ("构建", "build"),
    ("编译", "compile compilation"),
    ("安装", "install"),
    ("导入错误", "import error"),
    ("语法", "syntax"),
    ("类型", "type"),
    ("测试", "test"),
    ("断言", "assert assertion"),
    ("覆盖率", "coverage"),
    ("夹具", "fixture"),
    ("模拟", "mock"),
    ("补丁", "patch"),
    ("差异", "diff"),
    ("提交", "commit"),
    ("分支", "branch"),
    ("合并", "merge"),
    ("冲突", "conflict"),
    ("拉取", "pull fetch"),
    ("推送", "push"),
    ("文档", "docs documentation"),
    ("示例", "example sample"),
    ("说明", "description docs"),
    ("命令", "command"),
    ("脚本", "script"),
    ("终端", "terminal shell"),
    ("输出", "output"),
    ("输入", "input"),
    ("结果", "result"),
    ("内容", "content"),
    ("字段", "field"),
    ("属性", "attribute property"),
    ("键", "key"),
    ("值", "value"),
    ("数组", "array list"),
    ("列表", "list array"),
    ("字典", "dict dictionary map"),
    ("对象", "object"),
    ("为空", "none null empty"),
    ("空值", "none null"),
    ("边界", "boundary edge case"),
    ("兼容", "compatibility compatible"),
    ("回退", "fallback"),
    ("默认", "default"),
    ("开关", "flag option toggle"),
    ("启用", "enable"),
    ("禁用", "disable"),
    ("初始化", "initialize init"),
    ("加载", "load"),
    ("保存", "save persist"),
    ("写入", "write"),
    ("读取", "read"),
    ("更新", "update"),
    ("创建", "create"),
    ("追加", "append"),
    ("截断", "truncate"),
)
_PATH_ARGUMENT_KEYS = frozenset(
    {
        "file",
        "file_path",
        "filepath",
        "filename",
        "path",
        "paths",
    }
)
_NON_USER_INPUT_ARGUMENT_KEYS = frozenset(
    {
        "trusted_dirs",
        "preferred_response_language",
        "files_updated_by_user",
        "timezone",
        "timestamp",
        "encoding",
        "offset",
        "limit",
        "start",
        "end",
        "channel",
        "channels",
        "frontend",
        "session_id",
        "context_id",
        "request_id",
    }
)


def extract_query_terms(
    user_content: str,
    tool_name: str | None = None,
    tool_arguments: object | None = None,
    translate_query_text: Callable[[str], str] | None = None,
) -> frozenset[str]:
    """Extract stable lowercase terms for rule-compression relevance scoring."""

    terms: set[str] = set()
    _add_terms(terms, user_content)
    if _contains_cjk(user_content):
        _add_terms(terms, _translate_cjk_query_text(user_content))
        if translate_query_text is not None:
            translated = translate_query_text(user_content)
            if translated and translated != user_content:
                _add_terms(terms, translated)
    if tool_name:
        _add_terms(terms, tool_name)
    _add_terms_from_value(terms, _parse_json_if_possible(tool_arguments))
    return frozenset(terms)


def _add_terms(terms: set[str], value: str) -> None:
    for match in _TERM_RE.finditer(value):
        start = match.start()
        if start > 0 and value[start - 1] in "\\/":
            continue
        terms.add(match.group(0).lower())


def _contains_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def _translate_cjk_query_text(value: str) -> str:
    return " ".join(translation for phrase, translation in _CJK_QUERY_TRANSLATIONS if phrase in value)


def _add_terms_from_value(terms: set[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        _add_terms(terms, value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _add_terms_from_value(terms, key)
            if isinstance(key, str) and key.lower() in _PATH_ARGUMENT_KEYS:
                continue
            if isinstance(key, str) and key.lower() in _NON_USER_INPUT_ARGUMENT_KEYS:
                continue
            _add_terms_from_value(terms, item)
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _add_terms_from_value(terms, item)
        return
    _add_terms(terms, str(value))


def _parse_json_if_possible(value: object | None) -> object | None:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
