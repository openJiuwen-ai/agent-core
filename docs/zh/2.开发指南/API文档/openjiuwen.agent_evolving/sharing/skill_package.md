# openjiuwen.agent_evolving.checkpointing.skill_package

Skill 目录打包/解包与 `skill_id` frontmatter 管理。这些函数被 sharing 模块导出，用于首次上传 Skill 包时获取 skill_id、打包字节等。

---

## func openjiuwen.agent_evolving.checkpointing.skill_package.read_skill_id_from_content(content) -> str

从 SKILL.md frontmatter 中读取 `skill_id`，不存在时返回空字符串。

**参数**：

* **content**(str)：SKILL.md 文件内容。

**返回**：

**str**，skill_id；不存在时返回 `""`。

---

## func openjiuwen.agent_evolving.checkpointing.skill_package.ensure_skill_id_in_content(content) -> Tuple[str, str]

确保 SKILL.md 文件内容的 frontmatter 中包含 `skill_id` 字段。如果 frontmatter 中已有 `skill_id`，则原样返回内容和已有 ID；否则自动生成一个 `sk_` 前缀的全局唯一 ID，并将其插入到 frontmatter 中——若 frontmatter 块已存在则在块内追加 `skill_id:` 行，若不存在则在文件开头创建新的 frontmatter 块。

**参数**：

* **content**(str)：SKILL.md 文件的完整文本内容。

**返回**：

**Tuple[str, str]**，`(updated_content, skill_id)` 元组。content 已包含 skill_id 时返回原内容和已有 ID；否则返回插入 skill_id 后的新内容及自动生成的 ID。

**样例**：

```python
>>> from openjiuwen.agent_evolving.sharing import ensure_skill_id_in_content, read_skill_id_from_content
>>>
>>> content = "---\nname: bash_tool\n---\n\n# Bash Tool\n"
>>> updated, skill_id = ensure_skill_id_in_content(content)
>>> print(skill_id)
>>> print(read_skill_id_from_content(updated))
sk_xxxxxxxxxxxx
sk_xxxxxxxxxxxx
```

---

## func openjiuwen.agent_evolving.checkpointing.skill_package.pack_skill_directory(skill_dir, *, skill_md_relpath=None, skill_md_content=None) -> bytes

将 Skill 目录打包为 tar.gz 字节流。打包时自动排除 evolution 本地产物：`evolution/`、`archive/`、`__pycache__/`、`.git/` 目录，`evolutions.json` 文件，以及所有隐藏文件（以 `.` 开头）。当同时提供 `skill_md_relpath` 和 `skill_md_content` 时，tarball 中对应路径的文件将使用提供的替代内容而非磁盘上的原始文件——Hub 共享利用此机制排除本地投影的演进索引块，确保上传的 Skill 包不包含仅在本机有效的演进索引。

**参数**：

* **skill_dir**(Path)：Skill 目录的绝对或相对路径，作为打包根目录。
* **skill_md_relpath**(str，可选)：SKILL.md 在 Skill 目录中的相对路径（如 `"SKILL.md"`），与 `skill_md_content` 配合使用时，tarball 中该路径的文件将被替代内容覆盖。默认值：`None`。
* **skill_md_content**(str，可选)：替代的 SKILL.md 文本内容，仅在 `skill_md_relpath` 同时提供时生效；为 `None` 时使用磁盘上的原始文件。默认值：`None`。

**返回**：

**bytes**，完整的 tar.gz 包字节，可直接用于 Hub 上传或本地解压。

**样例**：

```python
>>> from pathlib import Path
>>> from openjiuwen.agent_evolving.sharing import pack_skill_directory, unpack_skill_package
>>>
>>> skill_dir = Path("/path/to/skills/bash_tool")
>>> package_bytes = pack_skill_directory(skill_dir)
>>> print(len(package_bytes))
1024
```

---

## func openjiuwen.agent_evolving.checkpointing.skill_package.unpack_skill_package(package_bytes, dest_dir) -> None

将 Skill 包 tar.gz 字节流解压到目标目录。目标目录不存在时会自动创建（含父目录）。解压时优先使用 `data_filter` 安全过滤器（Python 3.12+），防止路径穿越等安全风险；在不支持 `data_filter` 的 Python 版本上回退到传统解压方式。

**参数**：

* **package_bytes**(bytes)：由 `pack_skill_directory` 生成的 tar.gz 包字节。
* **dest_dir**(Path)：目标解压目录路径，目录不存在时自动创建。

**样例**：

```python
>>> from pathlib import Path
>>> from openjiuwen.agent_evolving.sharing import pack_skill_directory, unpack_skill_package
>>>
>>> skill_dir = Path("/path/to/skills/bash_tool")
>>> package_bytes = pack_skill_directory(skill_dir)
>>> unpack_skill_package(package_bytes, Path("/tmp/installed_skills/bash_tool"))
```