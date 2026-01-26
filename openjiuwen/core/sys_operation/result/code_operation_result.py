# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, Literal, Dict, Any

from pydantic import BaseModel, Field

from openjiuwen.core.sys_operation.result import BaseResult


class ExecuteCodeData(BaseModel):
    """Code Execution Result Data Model"""
    code_content: str = Field(..., description="Original code executed")
    language: str = Field(..., description="Programming language of the original code")
    exit_code: int = Field(default=0, description="Execution exit code")
    stdout: str = Field(default="", description="The code's standard output (stdout) stream")
    stderr: str = Field(default="", description="The code's standard error (stderr) stream")

    class Config:
        arbitrary_types_allowed = True


class ExecuteCodeChunkData(BaseModel):
    """Data structure for chunked execute code"""
    text: str = Field(default="", description="Raw content of the output chunk")
    type: Literal["stdout", "stderr"] = Field(..., description="Type of the output chunk")
    chunk_index: int = Field(..., description="Index of current chunk (starting from 0)")
    exit_code: int = Field(default=0, description="Execution exit code")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Data for execution")

    class Config:
        arbitrary_types_allowed = True


class ExecuteCodeResult(BaseResult[ExecuteCodeData]):
    """ExecuteCmdResult"""
    pass


class ExecuteCodeStreamResult(BaseResult[ExecuteCodeChunkData]):
    """ExecuteCodeStreamResult"""
    pass
