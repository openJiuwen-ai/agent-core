# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
配置管理器

统一管理所有配置，支持从文件加载和保存。
"""
import json
from pathlib import Path
from typing import Optional, Type, TypeVar, Dict

try:
    import yaml
except ImportError:
    yaml = None

from pydantic import BaseModel

from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig

T = TypeVar('T', bound=BaseModel)


class ConfigManager:
    """配置管理器，统一管理所有配置"""
    
    def __init__(self, config_path: Optional[str] = None):
        self._configs: Dict[str, BaseModel] = {}
        if config_path:
            self.load_from_file(config_path)
    
    def load_from_file(self, path: str) -> None:
        """从文件加载配置（支持 JSON 和 YAML）"""
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        
        suffix = path_obj.suffix.lower()
        if suffix == '.json':
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        elif suffix in ['.yaml', '.yml']:
            if yaml is None:
                raise ImportError("需要安装 PyYAML 以支持 YAML 配置文件")
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        else:
            raise ValueError(f"不支持的配置文件格式: {suffix}")
        
        # 根据数据结构创建配置对象
        # 这里假设是知识库配置，可以根据实际情况扩展
        kb_config = KnowledgeBaseConfig(**data)
        self._configs['knowledge_base'] = kb_config
    
    def save_to_file(self, path: str) -> None:
        """保存配置到文件"""
        if 'knowledge_base' not in self._configs:
            raise ValueError("没有可保存的配置")
        
        kb_config: KnowledgeBaseConfig = self._configs['knowledge_base']
        data = kb_config.model_dump()
        
        path_obj = Path(path)
        suffix = path_obj.suffix.lower()
        if suffix == '.json':
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        elif suffix in ['.yaml', '.yml']:
            if yaml is None:
                raise ImportError("需要安装 PyYAML 以支持 YAML 配置文件")
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        else:
            raise ValueError(f"不支持的配置文件格式: {suffix}")
    
    def get_config(self, config_type: Type[T]) -> Optional[T]:
        """获取指定类型的配置"""
        for key, config in self._configs.items():
            if isinstance(config, config_type):
                return config
        return None
    
    def get_knowledge_base_config(self) -> KnowledgeBaseConfig:
        """获取知识库配置"""
        config = self._configs.get('knowledge_base')
        if not config:
            raise ValueError("知识库配置未加载")
        return config
    
    def update_config(self, config: BaseModel) -> None:
        """更新配置"""
        type_name = type(config).__name__
        self._configs[type_name] = config
