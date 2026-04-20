"""LSP Tool input/output schemas using Pydantic discriminated unions."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class LspOperation(str, Enum):
    """LSP operation types exposed to AI agents."""

    GO_TO_DEFINITION = "goToDefinition"
    FIND_REFERENCES = "findReferences"
    DOCUMENT_SYMBOL = "documentSymbol"
    WORKSPACE_SYMBOL = "workspaceSymbol"
    GO_TO_IMPLEMENTATION = "goToImplementation"
    PREPARE_CALL_HIERARCHY = "prepareCallHierarchy"
    INCOMING_CALLS = "incomingCalls"
    OUTGOING_CALLS = "outgoingCalls"


class GoToDefinitionInput(BaseModel):
    """Input for goToDefinition operation."""

    operation: Literal["goToDefinition"] = "goToDefinition"
    file_path: str = Field(..., description="The absolute or relative path to the file")
    line: int = Field(ge=1, description="The line number (1-based, as shown in editors)")
    character: int = Field(default=1, ge=1,
                           description="The character offset (1-based, as shown in editors; defaults to 1)")


class FindReferencesInput(BaseModel):
    """Input for findReferences operation."""

    operation: Literal["findReferences"] = "findReferences"
    file_path: str = Field(..., description="The absolute or relative path to the file")
    line: int = Field(ge=1, description="The line number (1-based, as shown in editors)")
    character: int = Field(default=1, ge=1,
                           description="The character offset (1-based, as shown in editors; defaults to 1)")
    include_declaration: bool = Field(default=True,
                                      description="When true, the declaration location itself "
                                                  "is included in the results (default: true)")


class DocumentSymbolInput(BaseModel):
    """Input for documentSymbol operation."""

    operation: Literal["documentSymbol"] = "documentSymbol"
    file_path: str = Field(..., description="The absolute or relative path to the file")


class WorkspaceSymbolInput(BaseModel):
    """Input for workspaceSymbol operation."""

    operation: Literal["workspaceSymbol"] = "workspaceSymbol"
    file_path: str = Field(default="",
                           description="The absolute or relative path to the file "
                                       "(optional; ignored for workspaceSymbol)")
    query: str = Field(default="",
                       description="Search query string; when empty, returns all available symbols "
                                   "(used by workspaceSymbol only)")


class GoToImplementationInput(BaseModel):
    """Input for goToImplementation operation."""

    operation: Literal["goToImplementation"] = "goToImplementation"
    file_path: str = Field(..., description="The absolute or relative path to the file")
    line: int = Field(ge=1, description="The line number (1-based, as shown in editors)")
    character: int = Field(default=1, ge=1,
                           description="The character offset (1-based, as shown in editors; defaults to 1)")


class PrepareCallHierarchyInput(BaseModel):
    """Input for prepareCallHierarchy operation."""

    operation: Literal["prepareCallHierarchy"] = "prepareCallHierarchy"
    file_path: str = Field(..., description="The absolute or relative path to the file")
    line: int = Field(ge=1, description="The line number (1-based, as shown in editors)")
    character: int = Field(default=1, ge=1,
                           description="The character offset (1-based, as shown in editors; defaults to 1)")


class IncomingCallsInput(BaseModel):
    """Input for incomingCalls operation."""

    operation: Literal["incomingCalls"] = "incomingCalls"
    file_path: str = Field(..., description="The absolute or relative path to the file")
    line: int = Field(ge=1, description="The line number (1-based, as shown in editors)")
    character: int = Field(default=1, ge=1,
                           description="The character offset (1-based, as shown in editors; defaults to 1)")


class OutgoingCallsInput(BaseModel):
    """Input for outgoingCalls operation."""

    operation: Literal["outgoingCalls"] = "outgoingCalls"
    file_path: str = Field(..., description="The absolute or relative path to the file")
    line: int = Field(ge=1, description="The line number (1-based, as shown in editors)")
    character: int = Field(default=1, ge=1,
                           description="The character offset (1-based, as shown in editors; defaults to 1)")


LspToolInput = Annotated[
    Union[
        GoToDefinitionInput,
        FindReferencesInput,
        DocumentSymbolInput,
        WorkspaceSymbolInput,
        GoToImplementationInput,
        PrepareCallHierarchyInput,
        IncomingCallsInput,
        OutgoingCallsInput,
    ],
    Field(discriminator="operation"),
]


class LspToolOutput(BaseModel):
    """LSP Tool output."""

    operation: LspOperation
    result: str
    file_path: str | None = None
    result_count: int | None = None
    error: str | None = None
