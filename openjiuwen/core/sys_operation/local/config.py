# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, List
from pydantic import Field, BaseModel


class LocalWorkConfig(BaseModel):
    """Local working configuration"""
    shell_allowlist: Optional[List[str]] = Field(
        default=["echo", "ls", "dir", "cd", "pwd", "python", "python3", "pip", "pip3", "npm", "node", "git", "cat",
                 "type",
                 "mkdir", "md", "rm", "rd", "cp", "copy", "mv", "move", "grep", "find", "curl", "wget", "ps", "df",
                 "ping"],
        description="List of allowed command prefixes. If None, all commands are allowed (warning: insecure).")

    work_dir: Optional[str] = Field(
        default=None,
        description="Local working directory path")
