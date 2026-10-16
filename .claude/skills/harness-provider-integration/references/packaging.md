# 打包、分发与动态加载

## 两条安装路径，同一个发现机制

| 场景 | 安装方式 | openjiuwen 怎么看见它 |
|---|---|---|
| 冻结版宿主（jiuwenswarm 桌面应用） | 按 `runtime-manifest.json` 下载平台 wheel → 校验 sha256 → 解压到 provider 私有的 `site-packages` → 追加到 `sys.path` | 解压出的 `*.dist-info/entry_points.txt` |
| 源码 / 普通 Python 环境 | `pip install <平台 wheel>`，开发时 `pip install -e .` | 安装元数据里的 entry point |

这是 jiuwenswarm 给 Claude Code / Codex 用的托管运行时流程（`jiuwenswarm/common/external_cli_runtime.py`），
只是动态加载的单位从**厂商 SDK** 提升到 **provider 包**：内置的 claudecode / codex provider 随 openjiuwen
发布，冻结版只下载它们的厂商 SDK；第三方 provider 本身不在 openjiuwen 里，所以下载的是 provider 包，
厂商 CLI 打在包里。

openjiuwen 侧的解析顺序：内置 provider（封闭集合，永远优先）→ `register_harness_provider` 的进程内
注册 → `sys.path` 上任何发行包在 entry point 组 **`openjiuwen.harness_providers`** 里声明的同名入口。
入口值是 `HarnessProvider` 类（无参实例化）或实例；加载失败、实例化失败、不满足 SPI 都抛带原因的
`ValueError`。每次解析都重新读元数据，所以运行中安装/解压后下一次解析即可见（解压后先调
`importlib.invalidate_caches()`，jiuwenswarm 的激活函数已这样做）。已导入的包被原地替换（pip 升级，或
解压新 wheel 覆盖原目录）后，下一次解析按 `RECORD` 指纹发现变化并重新导入：新会话用新代码，运行中的成员
继续用旧代码直到重启。前提是包导入时没有全局副作用（不 monkeypatch openjiuwen、不在导入时
`register_harness_provider`），包内模块在模块顶部互相导入（函数内延迟导入会在替换后拿到新版本），且是纯
Python。editable 安装改源码不改 `RECORD`，仍需重启进程；openjiuwen 自身升级也需重启。

## 可选：包内自带 CLI（参照 claude-agent-sdk / openai-codex-cli-bin）

是否自带由打包的用户决定：终端用户自己会装 CLI 时不必打包，wheel 保持纯 Python、一个包覆盖所有平台；
面向可能没装 CLI 的桌面用户时再打包。

```
openjiuwen_harness_<name>/
  _bundled/
    runtime.json        {"layoutVersion": 1, "cli": "<cli>", "cliVersion": "...", "platform": "macos-arm64", "entrypoint": "bin/<cli>"}
    bin/<cli>           Windows 上为 <cli>.exe；同目录放 CLI 依赖的辅助可执行（kimi 的 fd、codex 的 rg 之类）
  bundled.py            bundled_runtime()：读 runtime.json，返回入口路径；with_path_dir() 把 bin/ 前置到子进程 PATH
```

harness 选择可执行文件的顺序（`bundled.select_executable`）：配置的 `cli_path` → `command[0]` 在
`PATH` 上找到的 CLI → 包内自带 CLI。自带的只是最后兜底，只有用到它时才把它的 `bin/` 前置到子进程
`PATH`。兜底时 `runtime.json` 损坏（入口缺失、布局版本不认识）则启动失败为 `process_start_failed`，
不假装 CLI 可用。源码目录里没有 `_bundled`（`.gitignore` 已排除）。

能打包的前提：厂商为每个目标平台提供**自包含可执行**（opencode、kimi-code 都是单文件 Mach-O/ELF/PE）。
依赖 Node 等外部运行时的 CLI（npm 包）不能只拷一个文件：要么用厂商的独立发行版（SEA / bun 编译版），
要么把运行时一起放进 `_bundled/bin` 并让入口是能直接执行的启动器，并在 e2e 里证明它在干净机器上可跑。

## 构建 wheel：`build_runtime.py`

```bash
# 不自带 CLI：一个纯 Python wheel，登记到所有目标平台
python $SKILL_DIR/scripts/build_runtime.py --project ./openjiuwen-harness-opencode \
    --platform macos-arm64 --platform macos-x86_64 --platform windows-x86_64 \
    --base-url https://downloads.example.com/harness/
# 自带 CLI：每个平台一次
python $SKILL_DIR/scripts/build_runtime.py --project ./openjiuwen-harness-opencode \
    --platform macos-arm64 --binary ~/.opencode/bin/opencode \
    --base-url https://downloads.example.com/harness/ [--mirror-base-url ...]
python $SKILL_DIR/scripts/build_runtime.py --project ./openjiuwen-harness-kimi \
    --platform macos-arm64 --binary ~/.kimi-code/bin/kimi --helper ~/.kimi-code/bin/fd
```

平台键与 jiuwenswarm 一致：`macos-arm64 / macos-x86_64 / windows-x86_64 / windows-arm64 / linux-x86_64 /
linux-arm64 / linux-musl-*`，产物累积在 `<project>/dist/`。不带 `--binary` 时只做构建、算 sha256、写清单
与冒烟。带 `--binary` 时：

1. 读可执行文件头（Mach-O / ELF / PE 与 CPU 类型），与 `--platform` 不符就拒绝——防止把 macOS 二进制
   打进 Windows wheel；
2. 在暂存副本里布置 `_bundled/`（源码目录不被污染），`pip wheel` 构建；
3. 改写为平台标签 `py3-none-<tag>`、`Root-Is-Purelib: false`，重算 `RECORD`，bundled 可执行强制 0755；
4. 计算 sha256，更新 `runtime-manifest.json`；
5. 目标是本机平台时做**冻结宿主冒烟**：安全解压到临时 `site-packages`、挂上 `sys.path`、
   `available_providers()` 含该名、`resolve_provider` 加载的确实是解压出的模块、bundled CLI
   `--version` 能跑。非本机平台的二进制只做格式检查，在该平台上再验。

`--cli-version` 缺省时只在本机平台上读 `<cli> --version`。SDK 模式不带 `--binary`，用 `--extra-wheel`
把厂商 SDK 的平台 wheel（及其不随宿主提供的依赖）列进同一平台的产物清单。

## `runtime-manifest.json`

与 jiuwenswarm `resources/external_cli_runtime.json` 同构（`schema_version: 1`，平台键、产物字段
`name / version / url / mirror_url / sha256` 一致），顶层键为 `providers`：

```json
{
  "schema_version": 1,
  "providers": {
    "opencode": {
      "distribution": "openjiuwen-harness-opencode",
      "version": "0.1.0",
      "entry_point": "opencode",
      "required_modules": ["openjiuwen_harness_opencode"],
      "executables": {"macos-arm64": "openjiuwen_harness_opencode/_bundled/bin/opencode"},
      "platforms": {
        "macos-arm64": [
          {"name": "openjiuwen-harness-opencode", "version": "0.1.0",
           "filename": "openjiuwen_harness_opencode-0.1.0-py3-none-macosx_11_0_arm64.whl",
           "url": "https://downloads.example.com/harness/openjiuwen_harness_opencode-0.1.0-py3-none-macosx_11_0_arm64.whl",
           "mirror_url": "", "sha256": "..."}
        ]
      }
    }
  }
}
```

- `required_modules` / `executables` / `entry_point` 是 jiuwenswarm 解压后、替换正式目录前的完整性
  校验：模块目录在、本平台可执行文件在、`*.dist-info/entry_points.txt` 在 `openjiuwen.harness_providers`
  组里声明了该名字；缺任何一项都不安装。claude / codex 的条目用同一套字段。
- 一个平台的产物列表必须是**完整闭包**：解压式安装不解析依赖。openjiuwen 及其依赖由宿主提供，不列。
  CLI 模式通常只有 provider wheel 一项。
- 包版本变了，旧平台条目作废（脚本按版本重建条目），别混用不同版本的平台产物。

已验证：jiuwenswarm 自己的 `_verify_wheel_artifact` / `_safe_extract_wheel` 直接接受这份清单条目与
wheel；解压目录追加到 `sys.path` 后，在 PATH 上**没有** opencode 的进程里，`create_harness(manifest,
provider="opencode")` 由兜底的包内 opencode 1.17.18 跑通了真实的文本与工具 Turn；PATH 上有 opencode 时
用的是 PATH 上的那个；同一 wheel 也能被 `pip install` 正常安装。

### 作为 agent team 成员

装好的 provider 名字就是团队 `external_cli_agents` 里可用的 `cli_agent`：openjiuwen 把它当第三类
backend，成员拿到 `cwd` / `env` / `skills` / `skill_conflict`（配置了才有 `cli_path` /
`system_prompt_mode`），团队工具由 stdio `openjiuwen-team-mcp` 提供——card 必须声明 `MCP_TOOLS`
并把 `HarnessContext.mcp_servers` 的 stdio 服务器交给 CLI，否则成员没有团队工具。成员模型经 provider 的
`model_config(ModelChoice)` 映射进配置（leader 挑的内置模型，或 card 声明 `ENDPOINT_MODELS` 时团队模型池
的端点模型）；声明 `MODEL_SELECTION` 才能运行中切换，声明 `MODEL_DISCOVERY` 时 jiuwenswarm 自动生成内置
模型目录；声明 `AUTH_FALLBACK` 时 leader 必须给成员配团队模型池里的回退模型，认证失败后自动切过去（先落库），
不声明的 CLI 不能配回退。

jiuwenswarm 设置页“三方 Agent → 添加三方 Agent”可直接上传 wheel（可附 CLI 可执行文件），无需进内置清单。

### 发布到 jiuwenswarm 桌面版

第三方 provider 随桌面版发版进入精选集合，不在运行时由终端用户导入清单：

1. 把各平台 wheel 上传到下载源，`url`（可选 `mirror_url`）填成公开地址；
2. 把 `providers.<name>` 整段并入 jiuwenswarm `jiuwenswarm/resources/external_cli_runtime.json` 的
   `providers` 段（名字在 `agents` 与 `providers` 间唯一，须匹配 `[a-z0-9][a-z0-9_-]*`）；
3. 桌面版里调用 `external_cli.install`（参数 `cli_agent: "<name>"`）下载安装，
   `external_cli.install_status` 查进度与 `runtime.runtime_state`
   （`not_installed / update_required / invalid / current`）。安装目录是
   `runtime/external-cli/<name>/site-packages`，启动时与 claude / codex 一样追加到 `sys.path`。

## 依赖与版本

- `openjiuwen>=<下限>`：脚手架写入生成时的已安装版本；下限必须是提供 entry point 发现的版本或更新。
  冻结宿主解压安装时忽略该声明（宿主自带 openjiuwen），pip 安装时生效。
- 包版本独立演进；**更换 bundled CLI 版本也要升包版本**，否则清单与已安装元数据无法区分新旧。
- 自带的 CLI 会随厂商服务端过期：opencode 1.17.18 的 wheel 安装与启动都正常，但服务端已要求 1.18.0+
  才能用免费额度，Turn 直接失败。发版前用当前 CLI 重建 wheel 并重跑真实 e2e。
  `ADAPTER_VERSION`（卡片 `implementation_version`）随适配行为变化递增。
- 带 CLI 的 wheel 很大（opencode 约 55MB 压缩）：放对象存储或私有索引，填 `--base-url` /
  `--mirror-base-url`；公共 PyPI 有单文件大小上限。

## 不走打包时

```python
from openjiuwen.harness_providers import register_harness_provider
from openjiuwen_harness_<name> import <Class>HarnessProvider

register_harness_provider("<name>", <Class>HarnessProvider)
```

## 不在本包能完成的事

- **升格为内置 provider**：由 openjiuwen 维护者迁入 `openjiuwen/harness_providers/` 并登记内置名单。
