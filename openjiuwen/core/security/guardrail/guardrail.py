# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Base Class

Provides the foundation for building guardrail implementations.
Integrates with the callback framework for event-driven detection.
"""

from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from openjiuwen.core.security.guardrail.models import (
    GuardrailResult,
    RiskAssessment,
)
from openjiuwen.core.security.guardrail.backends import GuardrailBackend


class BaseGuardrail(ABC):
    """Abstract base class for guardrail implementations.

    A guardrail monitors specific events in the agent execution flow and
    performs security detection when those events are triggered. It uses the
    callback framework for event registration and can be configured with a
    custom detection backend.

    Subclasses should define DEFAULT_EVENTS class attribute and can override
    listen_events property for dynamic event configuration.

    Example:
        >>> guardrail = ToolGuardrail()
        >>> guardrail.set_backend(MyDetector())
        >>> await guardrail.register(callback_framework)

    Attributes:
        DEFAULT_EVENTS: Class-level default event names.
        _backend: The detection backend instance.
        _events: List of configured event names to monitor.
        _registered_events: List of successfully registered event names.
        _framework: Reference to the callback framework instance.
    """

    DEFAULT_EVENTS: List[str] = []

    def __init__(
            self,
            backend: Optional[GuardrailBackend] = None,
            events: Optional[List[str]] = None
    ):
        """Initialize the guardrail.

        Args:
            backend: Optional detection backend. Can also be set later via
                set_backend().
            events: Optional list of event names to listen to. If not provided,
                uses DEFAULT_EVENTS from subclass.
        """
        self._backend: Optional[GuardrailBackend] = backend

        # Configure events
        if events is not None:
            self._events: List[str] = events.copy()
        elif self.DEFAULT_EVENTS:
            self._events: List[str] = self.DEFAULT_EVENTS.copy()
        else:
            self._events: List[str] = []

        self._registered_events: List[str] = []
        self._framework: Optional[Any] = None

    @property
    def listen_events(self) -> List[str]:
        """List of event names this guardrail should listen to.

        Returns:
            List of event name strings (copy to prevent external modification).
        """
        return self._events.copy()

    def with_events(self, events: List[str]) -> "BaseGuardrail":
        """Set the event names to listen to (chainable).

        This allows runtime configuration of which events the guardrail
        should monitor. Must be called before register().

        Args:
            events: List of event name strings.

        Returns:
            Self for method chaining.
        """
        self._events = events.copy()
        return self

    def set_backend(self, backend: GuardrailBackend) -> "BaseGuardrail":
        """Set the detection backend.

        Args:
            backend: The detection backend to use.

        Returns:
            Self for method chaining.
        """
        self._backend = backend
        return self

    def get_backend(self) -> Optional[GuardrailBackend]:
        """Get the current detection backend.

        Returns:
            The configured detection backend, or None if not set.
        """
        return self._backend

    async def detect(self, event_name: str, **event_data) -> GuardrailResult:
        """Perform security detection for the triggered event.

        This method is called when a subscribed event is triggered. The default
        implementation delegates to the configured backend if available.
        Subclasses can override this to implement custom detection logic.

        Args:
            event_name: The name of the triggered event.
            **event_data: Event-specific data containing all information
                needed for detection.

        Returns:
            GuardrailResult indicating whether the content is safe.

        Raises:
            ValueError: If no backend is configured and detect is not overridden.
        """
        if not self._backend:
            raise ValueError(
                f"No backend configured for {self.__class__.__name__}. "
                "Either provide a backend via set_backend() or override detect()."
            )

        assessment = await self._backend.analyze(event_data)

        return GuardrailResult(
            is_safe=not assessment.has_risk,
            risk_level=assessment.risk_level,
            risk_type=assessment.risk_type,
            details=assessment.details
        )

    async def register(self, framework: Any) -> None:
        """Register this guardrail with the callback framework.

        This registers the guardrail's detect method as a callback for all
        events specified in listen_events.

        Args:
            framework: AsyncCallbackFramework instance.
        """
        self._framework = framework

        for event in self.listen_events:
            await framework.register(
                event=event,
                callback=self._detect_callback,
                priority=100,
                namespace="guardrail",
                tags={"guardrail", self.__class__.__name__}
            )
            self._registered_events.append(event)

    async def unregister(self) -> None:
        """Unregister this guardrail from the callback framework.

        Removes all registered callbacks. Safe to call even if not registered.
        """
        if self._framework:
            for event in self._registered_events:
                try:
                    await self._framework.unregister(event, self._detect_callback)
                except Exception:
                    # Ignore unregister errors (callback might not exist)
                    pass
        self._registered_events.clear()

    async def _detect_callback(self, event_name: str, **kwargs) -> GuardrailResult:
        """Internal callback wrapper for the callback framework.

        The callback framework passes the event name as event_name parameter.
        This method unpacks it and calls the subclass detect() method.

        Args:
            event_name: Event name (injected by callback framework).
            **kwargs: Event data.

        Returns:
            GuardrailResult from detect().
        """
        return await self.detect(event_name, **kwargs)
