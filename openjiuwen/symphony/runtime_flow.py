"""Lazy Flow ownership for a configured Symphony runtime."""

from __future__ import annotations

import asyncio
from weakref import WeakSet

from openjiuwen.symphony.flow.engine import SymphonyFlowEngine
from openjiuwen.symphony.flow.render import SkillPackAdapter
from openjiuwen.symphony.flow.review import LLMPackageReviewAgent, PackageReviewGate


class RuntimeFlow:
    """Create persistent Flow resources only when an enabled operation is used."""

    def __init__(self, model, config, storage):
        self.config = config
        self._model = model
        self._storage = storage
        self._engine = None
        self._scopes = WeakSet()
        self._closed = False

    @property
    def enabled(self):
        return self.config.enabled

    def status(self):
        return {"enabled": self.enabled, "started": self._engine is not None, "closed": self._closed}

    def _get_engine(self):
        if self._closed:
            raise RuntimeError("Symphony runtime is closed")
        if not self.enabled:
            raise RuntimeError("Symphony Flow is disabled")
        self._storage.check(write=True)
        if self._engine is None:
            self._engine = SymphonyFlowEngine(
                self._storage.root / "flow", config=self.config, llm_client=self._model,
                gate=PackageReviewGate(LLMPackageReviewAgent(self._model)) if self._model is not None else None,
                skill_adapter=SkillPackAdapter(),
            )
        return self._engine

    def create_scope(self, *, store):
        engine = self._get_engine()
        scoped = SymphonyFlowEngine(store.root, config=self.config, llm_client=self._model, store=store,
                                     packager=engine.packager, gate=engine.gate, skill_adapter=engine.skill_adapter)
        self._scopes.add(scoped)
        return scoped

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._get_engine(), name)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        engines = [*self._scopes, *([self._engine] if self._engine is not None else [])]
        self._scopes.clear()
        results = await asyncio.gather(*(engine.close() for engine in engines), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
