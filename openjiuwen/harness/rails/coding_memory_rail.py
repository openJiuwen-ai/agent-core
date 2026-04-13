# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Coding Memory Rail - 面向 Coding Agent 场景的记忆 Rail.

与 personal MemoryRail 并行，通过 memory.scenario 配置切换。
- 记忆类型: user / feedback / project / reference
- 存储格式: 独立 .md 文件 + frontmatter 元数据
- 检索方式: 向量 + BM25 混合检索，每个 user turn 自动召回 top5
- 工具集: coding_memory_read / coding_memory_write / coding_memory_edit
"""

import os
import asyncio
from typing import Optional, Set

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
)
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.prompts.sections.coding_memory import (
    build_coding_memory_section,
)
from openjiuwen.core.memory.lite.coding_memory_tools import (
    get_decorated_tools,
    init_memory_manager_async,
)
from openjiuwen.core.memory.lite.frontmatter import parse_frontmatter
from openjiuwen.core.memory.lite.manager import MemoryIndexManager


class CodingMemoryRail(DeepAgentRail):
    """Coding Agent 专用记忆 Rail.
    
    特性:
    1. 自动召回: 每个 user turn 启动预取任务，非阻塞
    2. 互斥注入: 有召回结果 → 注入 top5 全文; 无结果 → 降级注入 MEMORY.md 索引
    3. 数据隔离: coding_memory/ 目录与 personal memory/ 完全隔离
    """
    
    priority = 80  # 与 MemoryRail 相同优先级
    
    # 召回限制
    MAX_RECALL_RESULTS = 5          # 最多召回 5 条记忆
    MAX_RECALL_TOTAL_BYTES = 10240  # 召回内容总大小上限 10KB
    
    def __init__(
        self,
        coding_memory_dir: str,
        embedding_config: "EmbeddingConfig",
        language: str = "cn",
    ):
        """初始化 CodingMemoryRail.
        
        Args:
            coding_memory_dir: coding_memory 目录路径
            embedding_config: 嵌入模型配置
            language: 语言 ("cn" | "en")
        """
        super().__init__()
        self._coding_memory_dir = coding_memory_dir
        self._embedding_config = embedding_config
        self._language = language
        
        # MemoryIndexManager 相关
        self._manager: Optional[MemoryIndexManager] = None
        self._manager_initialized = False
        
        # 召回状态
        self._recalled_content: Optional[str] = None
        self._total_memories: int = 0
        self._prefetch_task: Optional[asyncio.Task] = None
        
        # 工具管理
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()
        
        # SystemPromptBuilder 引用
        self.system_prompt_builder = None
    
    def init(self, agent) -> None:
        """初始化 Rail，注册工具.
        
        Args:
            agent: DeepAgent 实例
        """
        super().init(agent)
        
        # 获取 system_prompt_builder
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        
        # 保存 agent_id
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        self._agent_id = agent_id
        
        # 注册工具（参考 MemoryRail 的工具注册方式）
        self._register_coding_memory_tools(agent)
    
    def uninit(self, agent) -> None:
        """清理 Rail，注销工具.
        
        Args:
            agent: DeepAgent 实例
        """
        # 注销工具（参考 MemoryRail 的清理方式）
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[CodingMemoryRail] Failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )
        
        for tool_id in list(self._owned_tool_ids):
            try:
                Runner.resource_mgr.remove_tool(tool_id)
            except Exception as exc:
                logger.warning(
                    f"[CodingMemoryRail] Failed to remove tool '{tool_id}' "
                    f"from resource_mgr: {exc}"
                )
        
        self._owned_tool_ids.clear()
        self._owned_tool_names.clear()
        self._manager_initialized = False
        
        # 从 system_prompt_builder 移除 memory section
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("memory")
            self.system_prompt_builder = None
    
    def _register_coding_memory_tools(self, agent) -> None:
        """注册 Coding Memory 工具到 agent.
        
        参考 MemoryRail 的工具注册方式，使用 ability_manager 和 Runner.resource_mgr。
        
        Args:
            agent: DeepAgent 实例
        """
        if not hasattr(agent, "ability_manager"):
            logger.warning("[CodingMemoryRail] Agent has no ability_manager")
            return
        
        try:
            tools = get_decorated_tools()
            
            for tool in tools:
                try:
                    tool_card = getattr(tool, "card", None)
                    if not tool_card:
                        logger.warning(f"[CodingMemoryRail] Tool {tool.__name__} has no card")
                        continue
                    
                    # 使用 Runner.resource_mgr 注册工具
                    existing_tool = Runner.resource_mgr.get_tool(tool_card.id)
                    if existing_tool is None:
                        Runner.resource_mgr.add_tool(tool)
                        self._owned_tool_ids.add(tool_card.id)
                    
                    # 使用 ability_manager 添加工具能力
                    result = agent.ability_manager.add(tool_card)
                    if result.added:
                        self._owned_tool_names.add(tool_card.name)
                        logger.info(f"[CodingMemoryRail] Registered tool: {tool_card.name}")
                
                except Exception as exc:
                    logger.warning(
                        f"[CodingMemoryRail] Failed to register tool {tool.__name__}: {exc}"
                    )
        
        except Exception as e:
            logger.error(f"[CodingMemoryRail] Failed to register coding memory tools: {e}")
    
    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Invoke 开始前调用.
        
        1. 初始化 MemoryIndexManager（首次）
        2. 启动预取任务（非阻塞）
        
        Args:
            ctx: AgentCallbackContext
        """
        # 初始化 Coding Memory Manager（首次）
        if not self._manager_initialized:
            await self._init_coding_memory_manager(ctx)
            self._manager_initialized = True
        
        # 重置召回状态
        self._recalled_content = None
        self._prefetch_task = None
        
        # 检查是否为只读模式（cron/heartbeat）
        is_read_only = isinstance(ctx.inputs, InvokeInputs) and (
            ctx.inputs.is_cron() or ctx.inputs.is_heartbeat()
        )
        
        # 启动预取任务（非阻塞，与主流程并行）
        if not is_read_only and self._manager:
            query = self._extract_last_user_query(ctx)
            if query:
                self._prefetch_task = asyncio.create_task(
                    self._auto_recall(query)
                )
    
    async def _init_coding_memory_manager(self, ctx: AgentCallbackContext) -> None:
        """初始化 Coding Memory Index Manager.
        
        参考 MemoryRail 的 _init_memory_manager 实现。
        
        Args:
            ctx: Agent callback context.
        """
        agent_id = "default"
        
        try:
            if hasattr(ctx.agent, "card") and ctx.agent.card:
                agent_id = getattr(ctx.agent.card, "id", "default")
            
            manager = await init_memory_manager_async(
                workspace=self.workspace,
                agent_id=agent_id,
                embedding_config=self._embedding_config,
                sys_operation=self.sys_operation,
            )
            
            if manager:
                self._manager = manager
                self._manager_initialized = True
                logger.info(
                    f"[CodingMemoryRail] Coding memory manager initialized: "
                    f"agent_id={agent_id}, dir={self._coding_memory_dir}"
                )
            else:
                logger.warning("[CodingMemoryRail] Coding memory manager initialization failed")
        
        except Exception as e:
            logger.error(f"[CodingMemoryRail] Failed to initialize coding memory manager: {e}")
    
    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Model Call 前调用.
        
        1. 注入行为指令 prompt
        2. 非阻塞检查预取任务结果
        3. 互斥注入: 有召回结果 → 注入全文; 无结果 → 降级注入索引
        
        Args:
            ctx: AgentCallbackContext
        """
        if self.system_prompt_builder is None:
            return
        
        # 移除旧的 memory section
        self.system_prompt_builder.remove_section("memory")
        
        lang = self.system_prompt_builder.language
        
        # 检查只读模式
        is_read_only = isinstance(ctx.inputs, InvokeInputs) and (
            ctx.inputs.is_cron() or ctx.inputs.is_heartbeat()
        )
        
        # 构建基础 section（行为指令）
        section = build_coding_memory_section(
            language=lang,
            read_only=is_read_only,
            memory_dir=self._coding_memory_dir,
        )
        
        # 只读模式: 仅注入 MEMORY.md 索引
        if is_read_only:
            index = await self._read_memory_index()
            if index:
                header = "## 当前记忆索引\n\n" if lang == "cn" else "## Current memory index\n\n"
                section.content[lang] += "\n\n" + header + index
            self.system_prompt_builder.add_section(section)
            return
        
        # 非阻塞检查预取结果
        if self._prefetch_task is not None and self._recalled_content is None:
            if self._prefetch_task.done():
                try:
                    self._recalled_content, self._total_memories = self._prefetch_task.result()
                except Exception:
                    self._recalled_content = None
                self._prefetch_task = None
            # 如果未完成，本次降级，下次 model call 再检查
        
        # 互斥注入: 召回结果 vs 索引
        if self._recalled_content:
            # 有召回结果 → 注入全文
            header = "## 已加载的相关记忆\n\n" if lang == "cn" else "## Loaded relevant memories\n\n"
            footer = (
                f"\n\n（共 {self._total_memories} 条记忆，用 coding_memory_read 读取其他。）"
                if lang == "cn" else
                f"\n\n({self._total_memories} total. Use coding_memory_read for others.)"
            )
            section.content[lang] += "\n\n" + header + self._recalled_content + footer
        else:
            # 无召回结果 → 降级注入索引
            index = await self._read_memory_index()
            if index:
                header = "## 当前记忆索引\n\n" if lang == "cn" else "## Current memory index\n\n"
                section.content[lang] += "\n\n" + header + index
        
        self.system_prompt_builder.add_section(section)
    
    async def _auto_recall(self, query: str) -> tuple[Optional[str], int]:
        """自动召回相关记忆.
        
        Args:
            query: 用户查询文本
            
        Returns:
            (召回内容, 总记忆数)
        """
        if not self._manager:
            return None, 0
        
        # 执行混合检索（opts 参数方式）
        opts = {
            "max_results": self.MAX_RECALL_RESULTS,
        }
        results = await self._manager.search(query, opts=opts)
        
        # 统计总记忆数
        total = await self._count_memory_files(self._coding_memory_dir)
        
        if not results:
            return None, total
        
        # 组装召回内容
        parts = []
        total_bytes = 0
        
        for r in results:
            # 跳过 MEMORY.md 本身
            if r.get("path") == "MEMORY.md":
                continue
            
            # 读取文件内容
            r_path = r.get("path", "")
            content = await self._read_file_safe(
                os.path.join(self._coding_memory_dir, r_path)
            )
            if not content:
                continue
            
            content_bytes = len(content.encode("utf-8"))
            
            # 检查大小限制
            if total_bytes + content_bytes > self.MAX_RECALL_TOTAL_BYTES:
                remaining = self.MAX_RECALL_TOTAL_BYTES - total_bytes
                if remaining > 200:  # 至少保留 200 字节才截断
                    content = content[:remaining] + "\n\n... (truncated)"
                    fm = parse_frontmatter(content)
                    title = fm.get("name", r_path) if fm else r_path
                    parts.append(f"### {title} [{r_path}]\n\n{content}")
                break
            
            # 正常添加
            fm = parse_frontmatter(content)
            title = fm.get("name", r_path) if fm else r_path
            parts.append(f"### {title} [{r_path}]\n\n{content}")
            total_bytes += content_bytes
        
        if not parts:
            return None, total
        
        return "\n\n---\n\n".join(parts), total
    
    async def _read_memory_index(self) -> str:
        """读取 MEMORY.md 索引文件.
        
        Returns:
            索引内容，文件不存在返回空字符串
        """
        if not self.sys_operation:
            return ""
        try:
            index_path = os.path.join(self._coding_memory_dir, "MEMORY.md")
            result = await self.sys_operation.fs().read_file(index_path)
            if result and hasattr(result, 'data') and result.data:
                lines = result.data.content.split("\n")[:200]
                return "\n".join(lines).strip()
            return ""
        except Exception:
            return ""
    
    def _extract_last_user_query(self, ctx) -> Optional[str]:
        """从上下文中提取最后一条用户消息.
        
        Args:
            ctx: AgentCallbackContext
            
        Returns:
            用户查询文本，不存在返回 None
        """
        messages = getattr(ctx.inputs, "messages", None) or []
        
        for msg in reversed(messages):
            if hasattr(msg, "role") and msg.role == "user":
                content = msg.content
                
                if isinstance(content, str):
                    return content
                
                if isinstance(content, list):
                    # 处理多模态内容
                    texts = [
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    return " ".join(texts) if texts else None
        
        return None
    
    async def _read_file_safe(self, filepath: str) -> str:
        """安全读取文件.
        
        Args:
            filepath: 文件路径
            
        Returns:
            文件内容，失败返回空字符串
        """
        if not self.sys_operation:
            return ""
        try:
            result = await self.sys_operation.fs().read_file(filepath)
            if result and hasattr(result, 'data') and result.data:
                return result.data.content
            return ""
        except Exception:
            return ""
    
    async def _count_memory_files(self, memory_dir: str) -> int:
        """统计目录下的 .md 记忆文件数（排除 MEMORY.md）.
        
        Args:
            memory_dir: 记忆目录
            
        Returns:
            文件数量
        """
        if not self.sys_operation:
            return 0
        try:
            result = await self.sys_operation.fs().list_files(
                memory_dir,
                recursive=False
            )
            if result and hasattr(result, 'data') and result.data:
                count = 0
                for f in result.data.list_items:
                    if f.is_directory:
                        continue
                    if not f.name.lower().endswith(".md"):
                        continue
                    if f.name.casefold() == "memory.md":
                        continue
                    count += 1
                return count
            return 0
        except Exception:
            return 0


__all__ = [
    "CodingMemoryRail",
]