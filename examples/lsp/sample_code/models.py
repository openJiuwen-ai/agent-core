"""Data models for the sample project.

This module defines the core data structures used throughout the application.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class DataStatus(Enum):
    """Status enumeration for data processing."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DataModel:
    """Represents a data model with metadata.

    Attributes:
        id: Unique identifier for the data model.
        name: Human-readable name.
        value: The actual data value.
        tags: Optional tags for categorization.
        status: Current processing status.
    """
    id: str
    name: str
    value: Any
    tags: List[str] = field(default_factory=list)
    status: DataStatus = DataStatus.PENDING
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        """Validate and normalize data after initialization."""
        if not self.id:
            raise ValueError("id cannot be empty")
        if not self.name:
            raise ValueError("name cannot be empty")

    def is_valid(self) -> bool:
        """Check if the data model is valid."""
        return self.status != DataStatus.FAILED and self.value is not None

    def update_status(self, new_status: DataStatus) -> None:
        """Update the status of the data model."""
        self.status = new_status

    def add_tag(self, tag: str) -> None:
        """Add a tag to the data model."""
        if tag not in self.tags:
            self.tags.append(tag)

    def to_dict(self) -> Dict[str, Any]:
        """Convert data model to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "value": self.value,
            "tags": self.tags,
            "status": self.status.value,
            "metadata": self.metadata or {},
        }


@dataclass
class ContainerModel:
    """A container model that holds multiple data models."""
    models: List[DataModel] = field(default_factory=list)
    capacity: int = 100

    def add_model(self, model: DataModel) -> bool:
        """Add a model to the container if capacity allows."""
        if len(self.models) < self.capacity:
            self.models.append(model)
            return True
        return False

    def get_model(self, model_id: str) -> Optional[DataModel]:
        """Get a model by its ID."""
        for model in self.models:
            if model.id == model_id:
                return model
        return None

    def remove_model(self, model_id: str) -> bool:
        """Remove a model by its ID."""
        for i, model in enumerate(self.models):
            if model.id == model_id:
                self.models.pop(i)
                return True
        return False


def create_sample_model(name: str, value: Any) -> DataModel:
    """Factory function to create a sample data model.

    Args:
        name: The name for the model.
        value: The value to store.

    Returns:
        A new DataModel instance with default settings.
    """
    import uuid
    return DataModel(
        id=str(uuid.uuid4()),
        name=name,
        value=value,
        status=DataStatus.PENDING,
    )
