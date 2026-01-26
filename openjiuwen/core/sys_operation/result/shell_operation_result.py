# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, Literal, Dict, Any

from pydantic import BaseModel, Field

from openjiuwen.core.sys_operation.result import BaseResult


class ExecuteCmdData(BaseModel):
    """Data structure for execute cmd"""

    command: str = Field(..., description="Original shell command executed")
    cwd: str = Field(default=".", description="Current working directory")
    exit_code: int = Field(default=0, description="Command exit code")
    stdout: str = Field(default="", description="The command's standard output (stdout) stream")
    stderr: str = Field(default="", description="The command's standard error (stderr) stream")

    class Config:
        arbitrary_types_allowed = True


class ExecuteCmdChunkData(BaseModel):
    """Data structure for chunked execute cmd"""

    text: str = Field(default="", description="Raw content of the output chunk")
    type: Literal["stdout", "stderr"] = Field(..., description="Type of the output chunk")
    chunk_index: int = Field(..., description="Index of current chunk (starting from 0)")
    exit_code: int = Field(default=0, description="Command exit code")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Data for command")

    class Config:
        arbitrary_types_allowed = True


class ExecuteCmdResult(BaseResult[ExecuteCmdData]):
    """ExecuteCmdResult"""
    pass


class ExecuteCmdStreamResult(BaseResult[ExecuteCmdChunkData]):
    """ExecuteCmdStreamResult"""
    pass
