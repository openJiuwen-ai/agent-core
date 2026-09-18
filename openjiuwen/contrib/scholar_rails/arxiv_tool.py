# coding: utf-8
"""arXiv 检索与引用注册工具 —— 反幻觉文献基础设施。

设计目标（对应论文的 "grounded literature" 与框架贡献点）:
1. 所有引用必须来自真实可调取的 arXiv 记录（带 arxiv id / title / authors / year）。
2. 通过 CitationRegistry 注册表把"可引用集合"显式化，写作阶段只允许引用注册表
   中存在的 key，从机制上杜绝 LLM 编造参考文献。
3. 零第三方依赖（仅 urllib + xml.etree），保证评测环境可复现。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Optional

_ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_OPENSEARCH_NS = "{http://a9.com/-/spec/opensearch/1.1/}"

# 简单的内存级缓存与限速，避免触发 arXiv 频率限制
_LAST_REQUEST_AT = 0.0
_MIN_INTERVAL_SEC = 3.0  # arXiv 建议 ≥3s


@dataclass
class PaperRecord:
    """一条已验证的文献记录。"""
    arxiv_id: str                 # e.g. "1706.03762"（不含版本号）
    title: str
    authors: list[str]
    year: int
    abstract: str = ""
    url: str = ""
    venue: str = ""               # 若已知（如 "ICLR 2024"），否则空
    bibtex_key: str = ""          # 由注册表分配，如 "vaswani2017attention"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ArxivError(RuntimeError):
    """arXiv API 访问失败。"""


def _rate_limit() -> None:
    global _LAST_REQUEST_AT
    now = time.time()
    wait = _MIN_INTERVAL_SEC - (now - _LAST_REQUEST_AT)
    if wait > 0:
        time.sleep(wait)
    _LAST_REQUEST_AT = time.time()


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "jiuwen-scholar/1.0 (research paper generation; contact: noreply@example.com)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def search_arxiv(
    query: str,
    *,
    max_results: int = 10,
    sort_by: str = "relevance",
    timeout: float = 30.0,
) -> list[PaperRecord]:
    """按关键字检索 arXiv，返回已验证的 PaperRecord 列表。

    Args:
        query: arXiv 查询串（支持 ti:/abs:/all: 前缀，也接受裸关键字）。
        max_results: 返回条数上限（≤50）。
        sort_by: relevance | lastUpdatedDate | submittedDate。
    """
    if not query.strip():
        return []
    if not re.search(r"\b(ti|abs|all|au|cat):", query):
        query = f'all:"{query}"'
    params = {
        "search_query": query,
        "start": 0,
        "max_results": min(max_results, 50),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }
    url = f"{_ARXIV_API}?{urllib.parse.urlencode(params)}"
    _rate_limit()
    try:
        raw = _http_get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise ArxivError(f"arXiv query failed: {exc}") from exc
    return _parse_feed(raw)


def fetch_arxiv_by_id(arxiv_id: str, *, timeout: float = 30.0) -> Optional[PaperRecord]:
    """按 arxiv id 精确拉取一条记录（用于核验引用真实性）。"""
    arxiv_id = arxiv_id.strip().replace("arXiv:", "").split("v")[0]
    if not arxiv_id:
        return None
    url = f"{_ARXIV_API}?id_list={urllib.parse.quote(arxiv_id)}"
    _rate_limit()
    try:
        raw = _http_get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise ArxivError(f"arXiv fetch failed for {arxiv_id}: {exc}") from exc
    records = _parse_feed(raw)
    return records[0] if records else None


def _parse_feed(raw: bytes) -> list[PaperRecord]:
    root = ET.fromstring(raw)
    records: list[PaperRecord] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        m = re.search(r"arxiv\.org/abs/([^v]+)(v\d+)?", entry_id)
        if not m:
            continue
        arxiv_id = m.group(1)
        title = re.sub(r"\s+", " ", (entry.findtext(f"{_ATOM_NS}title") or "")).strip()
        abstract = re.sub(r"\s+", " ", (entry.findtext(f"{_ATOM_NS}summary") or "")).strip()
        published = (entry.findtext(f"{_ATOM_NS}published") or "").strip()
        year = int(published[:4]) if published[:4].isdigit() else 0
        authors = [
            (a.findtext(f"{_ATOM_NS}name") or "").strip()
            for a in entry.findall(f"{_ATOM_NS}author")
        ]
        authors = [a for a in authors if a]
        records.append(
            PaperRecord(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                year=year,
                abstract=abstract,
                url=f"https://arxiv.org/abs/{arxiv_id}",
            )
        )
    return records


def make_bibtex_key(record: PaperRecord) -> str:
    """根据作者与年份生成稳定的 bibtex key。"""
    surname = "anon"
    if record.authors:
        surname = re.sub(r"[^A-Za-z]", "", record.authors[0].split()[-1]).lower() or "anon"
    # 取标题第一个实词
    words = [w for w in re.findall(r"[A-Za-z]+", record.title.lower()) if len(w) > 3]
    first = words[0] if words else "paper"
    return f"{surname}{record.year}{first}"


class CitationRegistry:
    """引用注册表：写作阶段唯一可信的引用来源。

    用法:
        reg = CitationRegistry()
        reg.add_records(records)                # 注册检索结果
        reg.persist(path)                       # 落盘（可追溯）
        reg = CitationRegistry.load(path)       # 写作阶段加载
        reg.assert_citable("vaswani2017attention")  # 校验某个 key 是否可引用
    """

    def __init__(self) -> None:
        self._by_key: dict[str, PaperRecord] = {}

    def add_records(self, records: Iterable[PaperRecord]) -> list[str]:
        keys = []
        for r in records:
            if not r.bibtex_key:
                r.bibtex_key = make_bibtex_key(r)
            key = r.bibtex_key
            # 冲突时追加 arxiv id 后缀保证唯一
            if key in self._by_key and self._by_key[key].arxiv_id != r.arxiv_id:
                key = f"{key}_{r.arxiv_id.replace('.', '').replace('/', '')}"
                r.bibtex_key = key
            self._by_key[key] = r
            keys.append(key)
        return keys

    def add_record(self, record: PaperRecord) -> str:
        return self.add_records([record])[0]

    def keys(self) -> list[str]:
        return sorted(self._by_key)

    def get(self, key: str) -> Optional[PaperRecord]:
        return self._by_key.get(key)

    def is_citable(self, key: str) -> bool:
        return key in self._by_key

    def assert_citable(self, key: str) -> None:
        if key not in self._by_key:
            raise KeyError(
                f"citation key '{key}' not in registry; "
                "only verified arXiv records may be cited (anti-hallucination gate)"
            )

    def validate_keys(self, keys: Iterable[str]) -> tuple[list[str], list[str]]:
        """拆分 (可引用, 未注册) 两组 key，用于写作阶段审计。"""
        ok, missing = [], []
        for k in keys:
            (ok if k in self._by_key else missing).append(k)
        return ok, missing

    def to_bibtex(self) -> str:
        """导出整个注册表为 .bib 文本。"""
        entries = []
        for key in self.keys():
            r = self._by_key[key]
            authors = " and ".join(r.authors) if r.authors else "Anonymous"
            entries.append(
                "@misc{"
                + key
                + ",\n"
                + f"  title = {{{r.title}}},\n"
                + f"  author = {{{authors}}},\n"
                + f"  year = {{{r.year}}},\n"
                + f"  eprint = {{{r.arxiv_id}}},\n"
                + f"  archivePrefix = {{arXiv}},\n"
                + f"  url = {{{r.url}}}\n"
                + "}"
            )
        return "\n\n".join(entries) + "\n"

    def persist(self, path: str | Path) -> None:
        data = {k: r.to_dict() for k, r in sorted(self._by_key.items())}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "CitationRegistry":
        reg = cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key, rd in data.items():
            rec = PaperRecord(**rd)
            rec.bibtex_key = key
            reg._by_key[key] = rec
        return reg

    def __len__(self) -> int:  # noqa: D105
        return len(self._by_key)
