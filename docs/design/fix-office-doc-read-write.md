# 修改说明：Office 文档 read_file / write_file / edit_file 支持

| 项 | 内容 |
|----|------|
| 仓库 | `agent-core` |
| 基线分支 | `origin/dev-stable` |
| 功能分支 | `fix/office-doc-read-write-support` |
| Commit | `5caf3bb5` — `fix(fs): 支持 read_file/write_file 结构化读写 Office 文档（含 .doc）` |
| 影响模块 | `openjiuwen/harness` 文件工具 |

---

## 1. 问题现象

对已存在的 Office 文件（尤其是桌面 `.docx`）调用工具链时：

1. `read_file` 能成功抽出文本。
2. 随后 `write_file` 报错：`File has not been read yet. Call read_file ... first`。
3. 模型按提示反复 `read_file`，仍无法写入，形成死循环。
4. 对旧版 `.doc`，`read_file` 直接拒绝，模型改走 bash + Word COM 绕行。

若强行用纯文本方式覆盖 `.docx`，还会破坏 ZIP 容器，导致文件损坏。

---

## 2. 根因

| # | 原因 | 后果 |
|---|------|------|
| 1 | `ReadFileTool._record_read_state()` 通过 `_is_text_read_for_edit` **排除 Office**，读成功也不写入 `_FILE_READ_REGISTRY` | `write_file` / `edit_file` 的「先读后写」守卫永远失败 |
| 2 | `write_file` / `edit_file` 走 `fs().write_file` 的 UTF-8 覆盖 | 损坏 `.docx` 等 ZIP/OLE 二进制容器 |
| 3 | `_read_office_doc` 对 `.doc` 硬抛「不支持，请转 docx」 | 无法用文件工具处理旧版 Word |

**定界**：openjiuwen（agent-core）harness 文件工具；非 RelayClaw API / 前端。

---

## 3. 修改目标

让 Agent **直接**使用 `read_file` / `write_file`（以及 `edit_file`）读写常见 Office 文档，无需 bash / python-docx / Word COM 脚本绕行。

### 格式支持矩阵

| 扩展名 | 读 | 写 / 编辑 | 实现方式 |
|--------|----|-----------|----------|
| `.docx` | ✓ | ✓ | python-docx，按文本段落重建 |
| `.doc` | ✓ | ✓ | Windows + Microsoft Word COM（**子进程隔离**），`SaveAs(..., FileFormat=0)` 保持 OLE |
| `.xlsx` | ✓ | ✓ | openpyxl |
| `.pptx` | ✓ | ✓ | python-pptx |
| `.xls` / `.ppt` | ✗ | ✗ | 明确报错，提示转为 `.xlsx` / `.pptx` |

---

## 4. 改动清单

### 4.1 `openjiuwen/harness/tools/filesystem.py`

- **读登记**：`_is_text_read_for_edit` 不再排除 Office；`_read_raw_text_for_edit_state` 对 Office 使用提取文本计行与快照。
- **结构化读写**：新增 `_read_office_plain_text`、`_write_office_document`。
- **`.doc`**：新增 `_invoke_word_com_worker` / `_read_doc_via_word` / `_write_doc_via_word`；Word COM 放在短生命周期子进程中，避免进程内 `Quit` RPC 异常拖垮宿主。
- **`WriteFileTool`**：Office 扩展走结构化写回；已存在文件仍须逻辑上完整 `read_file`；写后按提取文本刷新 registry。
- **`EditFileTool`**：对支持的 Office 扩展做「提取文本 → 字符串替换 → 结构化写回」。
- **`ReadFileTool._read_doc`**：`.doc` 走 Word COM 提取，不再一律拒绝。

### 4.2 `openjiuwen/harness/prompts/tools/filesystem.py`

- `read_file` / `write_file` / `edit_file` 描述同步：标明支持 `.docx/.doc/.xlsx/.pptx`，说明 `.doc` 依赖 Windows + Microsoft Word，并要求不要 bash 绕行。

### 4.3 `tests/unit_tests/harness/test_office_doc_read_write.py`

- docx：先读再写、先读再 edit、新建无需先读。
- 纯文本：未读仍拒绝写入。
- `.doc`：本机有 Word + pywin32 时做创建/读写 roundtrip；否则 skip。

### 4.4 本文档

- `docs/design/fix-office-doc-read-write.md`

---

## 5. 行为约定与限制

1. **全文覆盖**：`write_file` 对 Office 是按文本内容**重建**文档，不是原地改 OOXML/OLE 节点；复杂样式、页眉页脚、嵌入图片等可能丢失。
2. **先读后写**：覆盖已存在文件前，须通过 `read_file` 覆盖完整行区间（与纯文本文件同一套守卫）。
3. **`.doc` 环境**：仅 Windows；需安装 Microsoft Word 与 `pywin32`。非 Windows 或缺依赖时返回明确错误。
4. **追加语义**：工具本身是「整文件写回」；追加需先读出全文，再把新内容拼进 `content` 后调用 `write_file`（或对可匹配片段使用 `edit_file`）。
5. **附带风险（上层）**：部分环境在 `write_file` 后仍可能触发 `image_watermark` 给 `.docx` 插入「AI生成」文案，属 watermark 扩展行为，不在本次文件工具修复范围内。

---

## 6. 验证结果

### 单测

```bash
# agent-core 仓库
pytest tests/unit_tests/harness/test_office_doc_read_write.py -v
```

- docx / 纯文本相关用例：通过。
- `.doc` 用例：本机有 Word 时通过；无 Word 则 skip。

### 端到端（OfficeClaw / jiuwenclaw）

对桌面文件发起：「使用 write_file 在结尾追加『你好』，再用 read_file 读取」。

| 文件 | 期望路径 | 结果 |
|------|----------|------|
| `123.docx` | `read_file` → `write_file` → `read_file` | 符合预期，无 bash 绕行 |
| `zzm.doc` | 同上 | 符合预期，无 bash / 手工 Word COM 绕行 |

API 侧对应请求 `sawError=false`，任务正常 complete。

---

## 7. 使用示例

```text
# 已存在的 Office 文件
1. read_file(file_path="C:\\path\\to\\file.docx")
2. write_file(file_path="C:\\path\\to\\file.docx", content="<原内容>\n你好")
3. read_file(file_path="C:\\path\\to\\file.docx")   # 确认

# 新建
write_file(file_path="C:\\path\\to\\new.docx", content="hello\nworld")
```

`.doc` 用法相同；运行环境须为 Windows 且已安装 Microsoft Word。

---

## 8. 后续建议

1. 将本分支合入 `dev-stable` 后，同步升级依赖本 SDK 的 jiuwenclaw / 发行包中的 openjiuwen。
2. 如需严格「追加且保留全部样式」，可另立需求做 OOXML 级增量编辑（本次为文本重建方案）。
3. 若桌面 `.docx` 仍出现「AI生成」，单独排查 `image_watermark` 对 Office 产物的挂钩策略。
