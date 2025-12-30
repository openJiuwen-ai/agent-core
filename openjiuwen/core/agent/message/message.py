# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional, Union, List
from datetime import datetime

from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput


class MessageType(Enum):
    """Message type enum"""
    # User interaction
    USER_INPUT = "user_input"  # User input message

    # Agent interaction
    AGENT_RESPONSE = "agent_response"  # Agent response message
    AGENT_HANDOFF = "agent_handoff"  # Agent handoff

    # Task execution
    TASK_COMPLETED = "task_completed"  # Task completed
    TASK_INTERRUPTED = "task_interrupted"  # Task interrupted

    # Event notification
    ERROR = "error"  # Error message
    INFO = "info"  # Info message


class MessagePriority(Enum):
    """Message priority enum"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


class SourceType(Enum):
    """Message source type enum"""
    USER = "user"  # User
    AGENT = "agent"  # Agent
    TASK = "task"  # Task
    WORKFLOW = "workflow"  # Workflow
    SYSTEM = "system"  # System


@dataclass
class MessageSource:
    """Message source info"""
    conversation_id: str  # Conversation ID
    source_type: SourceType  # Source type
    user_id: Optional[str] = None


@dataclass
class MessageContent:
    """Message content - explicit fields, no magic strings, clear types"""
    # Text content
    query: Optional[str] = None
    
    # Interactive input (for interrupt resume)
    interactive_input: Optional['InteractiveInput'] = None
    
    # Stream data - unified list type, no type inconsistency
    stream_data: List[Any] = field(default_factory=list)  # List[OutputSchema]
    
    # Task result - explicit type, not Any
    task_result: Optional[Any] = None  # TaskResult, use Any to avoid circular import
    
    # Extension fields (for truly uncertain data)
    extensions: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure list and dict fields are not None"""
        if self.stream_data is None:
            self.stream_data = []
        if self.extensions is None:
            self.extensions = {}
    
    def get_query(self) -> str:
        """Get query text - unified handling for all cases"""
        # Prefer query
        if self.query is not None:
            return self.query
        
        # Extract text from interactive_input if present
        if self.interactive_input is not None:
            return self._extract_interactive_text(self.interactive_input)
        
        # Default to empty string
        return ""
    
    @staticmethod
    def _extract_interactive_text(interactive_input: 'InteractiveInput') -> str:
        """Extract text from InteractiveInput"""
        if interactive_input.raw_inputs is not None:
            return str(interactive_input.raw_inputs)
        
        if interactive_input.user_inputs:
            # Get first value
            return str(list(interactive_input.user_inputs.values())[0])
        
        return ""


@dataclass
class MessageContext:
    """Message context info"""
    correlation_id: Optional[str] = None  # Correlation ID (for message chain tracking)
    conversation_id: Optional[str] = None  # Conversation ID
    task_id: Optional[str] = None  # Related task ID
    workflow_id: Optional[str] = None  # Related workflow ID

    def __post_init__(self):
        pass


@dataclass
class Message:
    """Unified message class"""
    # Basic info
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    msg_type: MessageType = MessageType.USER_INPUT
    priority: MessagePriority = MessagePriority.NORMAL

    # Source
    source: MessageSource = field(default_factory=lambda: MessageSource("unknown", SourceType.SYSTEM))

    # Content
    content: MessageContent = field(default_factory=MessageContent)

    # Context
    context: MessageContext = field(default_factory=MessageContext)

    # Time info
    created_at: datetime = field(default_factory=datetime.now)

    # Extended metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # AgentGroup routing support
    receiver_id: Optional[str] = None  # Target Agent ID (for point-to-point)
    message_type: Optional[str] = None  # Custom message type (for subscription routing)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    # ========== Factory methods ==========

    @classmethod
    def create_user_message(cls, content: Union[str, InteractiveInput], conversation_id: str = "default",
                            user_id: Optional[str] = None, extensions: Dict[str, Any] = None) -> 'Message':
        """Create user message - unified handling for str and InteractiveInput"""
        source = MessageSource(
            conversation_id=conversation_id,
            source_type=SourceType.USER,
            user_id=user_id
        )
        
        # Assign to different fields by type
        if isinstance(content, InteractiveInput):
            msg_content = MessageContent(interactive_input=content)
        else:
            msg_content = MessageContent(query=str(content))

        if extensions:
            msg_content.extensions = extensions

        context = MessageContext(
            conversation_id=conversation_id,
            correlation_id=str(uuid.uuid4())
        )

        return cls(
            msg_type=MessageType.USER_INPUT,
            source=source,
            content=msg_content,
            context=context
        )

    @classmethod
    def create_agent_response(cls, content: str, conversation_id: str,
                              reply_to_msg_id: Optional[str] = None) -> 'Message':
        """Create Agent response message"""
        source = MessageSource(
            conversation_id=conversation_id,
            source_type=SourceType.AGENT
        )
        msg_content = MessageContent(query=content)
        context = MessageContext(
            conversation_id=conversation_id,
            correlation_id=reply_to_msg_id
        )

        return cls(
            msg_type=MessageType.AGENT_RESPONSE,
            source=source,
            content=msg_content,
            context=context
        )

    @classmethod
    def create_agent_handoff(cls, conversation_id: str, to_agent_id: str,
                             handoff_reason: str) -> 'Message':
        """Create Agent handoff message"""
        source = MessageSource(
            conversation_id=conversation_id,
            source_type=SourceType.AGENT
        )
        msg_content = MessageContent(
            query=handoff_reason,
            extensions={"to_agent_id": to_agent_id}
        )
        context = MessageContext(
            conversation_id=conversation_id
        )

        return cls(
            msg_type=MessageType.AGENT_HANDOFF,
            source=source,
            content=msg_content,
            context=context
        )

    @classmethod
    def create_task_completed(cls, conversation_id: str, task_id: str,
                              task_result: Any,  # TaskResult, use Any to avoid circular import
                              workflow_id: Optional[str] = None,
                              stream_data: Optional[List[Any]] = None) -> 'Message':
        """Create task completed message"""
        source = MessageSource(
            conversation_id=conversation_id,
            source_type=SourceType.TASK
        )

        # Handle stream_data default value
        if stream_data is None:
            stream_data = []
        
        msg_content = MessageContent(
            stream_data=stream_data,
            task_result=task_result
        )

        context = MessageContext(
            conversation_id=conversation_id,
            task_id=task_id,
            workflow_id=workflow_id
        )

        return cls(
            msg_type=MessageType.TASK_COMPLETED,
            source=source,
            content=msg_content,
            context=context
        )

    @classmethod
    def create_task_interrupted(cls, conversation_id: str, task_id: str, reason: str,
                                task_result: Any,  # TaskResult, use Any to avoid circular import
                                workflow_id: Optional[str] = None,
                                stream_data: Optional[List[Any]] = None) -> 'Message':
        """Create task interrupted message"""
        source = MessageSource(
            conversation_id=conversation_id,
            source_type=SourceType.TASK
        )
        
        # Handle stream_data default value
        if stream_data is None:
            stream_data = []
        
        msg_content = MessageContent(
            query=reason,
            stream_data=stream_data,
            task_result=task_result
        )
        context = MessageContext(
            conversation_id=conversation_id,
            task_id=task_id,
            workflow_id=workflow_id
        )

        return cls(
            msg_type=MessageType.TASK_INTERRUPTED,
            source=source,
            content=msg_content,
            context=context,
            priority=MessagePriority.HIGH
        )

    @classmethod
    def create_error_message(cls, conversation_id: str, error_msg: str,
                             source_type: SourceType = SourceType.SYSTEM) -> 'Message':
        """Create error message"""
        source = MessageSource(
            conversation_id=conversation_id,
            source_type=source_type
        )
        msg_content = MessageContent(query=error_msg)

        return cls(
            msg_type=MessageType.ERROR,
            source=source,
            content=msg_content,
            priority=MessagePriority.HIGH
        )

    @classmethod
    def create_info_message(cls, conversation_id: str, info_msg: str,
                            source_type: SourceType = SourceType.SYSTEM) -> 'Message':
        """Create info message"""
        source = MessageSource(
            conversation_id=conversation_id,
            source_type=source_type
        )
        msg_content = MessageContent(query=info_msg)

        return cls(
            msg_type=MessageType.INFO,
            source=source,
            content=msg_content
        )

    # ========== Convenience methods ==========

    def set_correlation(self, correlation_id: str) -> None:
        """Set correlation ID"""
        self.context.correlation_id = correlation_id

    def set_conversation(self, conversation_id: str) -> None:
        """Set conversation ID"""
        self.context.conversation_id = conversation_id

    def is_from_user(self) -> bool:
        """Check if from user"""
        return self.source.source_type == SourceType.USER

    def is_from_agent(self) -> bool:
        """Check if from Agent"""
        return self.source.source_type == SourceType.AGENT

    def is_task_related(self) -> bool:
        """Check if task related"""
        return self.context.task_id is not None

    def is_workflow_related(self) -> bool:
        """Check if workflow related"""
        return self.context.workflow_id is not None

    def get_display_content(self) -> str:
        """Get display content"""
        return self.content.get_query()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict format"""

        def convert_enum(obj):
            if isinstance(obj, Enum):
                return obj.value
            return obj

        result = {}
        for key, value in self.__dict__.items():
            if hasattr(value, '__dict__'):  # Handle nested dataclass
                result[key] = {k: convert_enum(v) for k, v in value.__dict__.items()}
            else:
                result[key] = convert_enum(value)

        # Special handling for datetime
        if isinstance(self.created_at, datetime):
            result['created_at'] = self.created_at.isoformat()

        return result
