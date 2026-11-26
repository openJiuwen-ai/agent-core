# AIGC 标识工具

安装可选依赖 `openjiuwen[aigc-marker]` 后，由宿主在受控本地工作空间按需注册工具。
格式处理器移植自小艺 Work 的 `aigc-marker-win` 1.2.2。

```python
from openjiuwen.core.runner import Runner  # Initialize the SDK before importing harness tools.
from openjiuwen.harness.tools import MarkerProvenance, create_aigc_marker_tools

tools = create_aigc_marker_tools(
    workspace,
    provenance=MarkerProvenance(
        content_producer=service_producer_id,
        content_propagator=service_propagator_id,
        id_prefix=service_id_prefix,
    ),
    agent_id=agent.card.id,
)
for tool in tools:
    agent.ability_manager.add_ability(tool.card, tool)
```

- `aigc_mark_file(file_path, skip_visible=False)` 给工作空间内的现有文件原地添加显式和隐式标识。传入 `skip_visible=True` 只写隐式标识。成功返回 `success=True`，结果的 `data.changed` 表示文件内容是否变化。
- `aigc_verify_file(file_path)` 只读检查 TC260-PG-20258A 所覆盖的 18 种文本扩展名的隐式元数据，返回字段、存储位置和校验状态。它的结论只覆盖本地隐式标识。

两个工具均接收绝对路径。打标支持 OOXML、UOF、PDF、OFD、Markdown、XMind、POSM/POSF、HTML 和技能原有的图片、音频和视频格式。调用方应检查返回的 `success` 再交付文件。来源 ID 由宿主注入，模型只提供业务参数。音视频需要部署方提供 FFmpeg，优先读取 `CLAW_FFMPEG_EXE` 和 `CLAW_FFPROBE_EXE`，再查 PATH。需要中文水印的格式可用 `CLAW_AIGC_FONT_PATH` 指定 CJK 字体。

已有完整隐式标识的图片、音频和视频沿用技能行为，无需再次改写。这些文件的显式标识需要另行检查，`data.requested_mode` 记录本次调用的请求模式。

处理器在当前 Python 进程及其 FFmpeg 子进程中访问文件。只对该进程有权处理的受控本地工作空间注册。在沙箱部署中，应把工具注册到能在沙箱内直接访问工作空间的运行进程。
