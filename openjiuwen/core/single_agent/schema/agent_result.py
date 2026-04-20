from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from openjiuwen.core.controller.schema.task import TaskStatus


class Part(BaseModel):
    text: Optional[str] = None
    raw: Optional[bytes] = None
    url: Optional[str] = None
    data: Any = None
    filename: Optional[str] = None
    media_type: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    artifactId: Optional[str] = None
    name: Optional[str] = None  # Semantic name, e.g. "summary", "chart"
    description: Optional[str] = None
    parts: List[Part] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    task_id: Optional[str] = None
    sessionId: Optional[str] = None
    status: TaskStatus = None
    artifacts: List[Artifact] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
