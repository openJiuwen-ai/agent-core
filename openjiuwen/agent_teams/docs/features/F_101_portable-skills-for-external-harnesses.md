# 三方 Harness 的 Portable Skills

- 日期：2026-09-11
- Refs: #751
- 范围：`harness_providers/skills.py`、三方 config/start、manifest 工厂、CLI member 配置

## 配置与生命周期

skills 是可迁移文件能力，不再属于三方 provider 拒绝的 DeepAgent 扩展项。
复用 manifest 的 SkillSpec：dir 指向 bundle 或 library，非空 enabled_skills 按声明名筛选。
mode 的 all/auto_list 保留校验，但加载和调用完全交给目标 CLI，不模拟 DeepAgent 的工具协议。

```python
create_harness(manifest, provider="codex", config={
    "cwd": "/project",
    "skill_conflict": "replace",  # default: skip
})
```

也可在 provider config.skills 中直接给目录字符串或 SkillSpec 形状的字典，显式 config 优先于 manifest。
构造只保存声明；start 在 SDK 启动前按最终 cwd 复制。本地 team Claude/Codex 的 ExternalCliAgentSpec
增加 skills/skill_conflict，spawn 透传到相同 provider；原有未设置 skills 的成员行为不变。

| Provider | 项目扫描目录 |
|---|---|
| claudecode | `.claude/skills/<name>/` |
| codex | `.agents/skills/<name>/` |
| dsh | `.dsh/skills/<name>/` |

name 使用 SKILL.md front matter.name，缺省使用目录名。项目扫描目录中的冲突既比较目录名，也比较
已有 SKILL.md 的 name；不区分大小写，避免跨平台同名差异。冲突策略只作用于项目目录，不改全局 skills。

## 完整复制与冲突策略

- 复制整个 bundle，包括 scripts、assets、隐藏资源和可执行位。
- skip（默认）：已有同名则完全保留，不更新任何文件。
- replace：先在项目临时目录复制完整新 bundle，再替换目标目录；旧目录中源已删除的文件不保留。
  切换失败恢复旧目录；复制失败不会触碰旧目录。多源重名按 manifest 顺序，skip 先到者保留，replace 后到者替换。
- 同名已存在于多个不同目录时，replace 明确拒绝歧义，不批量删除未知目录。
- 内部 symlink 物化；越界、循环或非普通文件拒绝；源与目标相同则 no-op，互相包含则拒绝递归复制。
- 宿主进程内并发成员复制串行化。项目外 symlink 扫描根拒绝写入。
- 复制完成的技能是项目文件，stop/SDK 启动失败后也保留。不是临时注入，不修改用户 home。

Claude 有待复制 skills 时显式设置 SDK skills=all、setting_sources=user/project/local。
DSH sdk-minimal 不自带 skill 组件，此时 overlay 加载原生 skill/skill-filesystem/tool-skill。
Claude SSH/custom transport 暂不上传目录，配置 portable skills 时明确报错，不能静默拷到本地主机。

## 验证

- 单测覆盖完整资源/执行权限、三方目标路径、默认 skip、完整 replace、声明名冲突、筛选、复制失败、
  rename 回滚、symlink 边界、自复制、同进程并发、manifest 惰性构造与 SDK 启动前复制、成员配置透传。
- 三方真机分别执行 skip/replace，用 skill 的资源文件中不出现在 prompt 的随机标记验证实际读取的是
  旧/新 bundle；6 个用例通过。另验证 sdk-minimal 加載原生技能插件成功。
