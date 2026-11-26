# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``resolve_deep_agent_parts`` must not silently adopt an incompatible ``SysOperation``.

The factory keys its get-or-create lookup on ``"{card.name}_{card.id}"``. An entry
already registered under that id carries whatever sandbox policy it was built
with, so reusing it for an agent that asked for a different
``restrict_to_work_dir`` would run that agent under the wrong policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SandboxGatewayConfig,
    SysOperation,
    SysOperationCard,
)
from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.cwd import init_cwd
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent


def _make_card() -> AgentCard:
    """A fresh identity per call: ``AgentCard.id`` defaults to a new uuid, so each test owns its ``sysop_id``."""
    return AgentCard(name="reuse_probe", description="sys_operation reuse probe")


def _make_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test-base",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )


def _build(card: AgentCard, **kwargs: Any) -> DeepAgent:
    """``create_deep_agent`` with the boilerplate every test here shares."""
    return create_deep_agent(model=_make_model(), card=card, auto_create_workspace=False, **kwargs)


def _factory_work_config(restrict_to_work_dir: bool) -> LocalWorkConfig:
    """The exact ``LocalWorkConfig`` the factory registers for a LOCAL agent."""
    return LocalWorkConfig(shell_allowlist=None, restrict_to_sandbox=restrict_to_work_dir)


def _sandbox_card(sysop_id: str) -> SysOperationCard:
    """A valid SANDBOX-mode card; the per-id isolation prefix keeps its key template unique."""
    return SysOperationCard(
        id=sysop_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(prefix=sysop_id),
            launcher_config=PreDeployLauncherConfig(base_url="http://127.0.0.1:18080"),
        ),
    )


@pytest.mark.parametrize("restrict_to_work_dir", [True, False])
def test_rebuild_with_same_policy_reuses_sys_operation_cleanly(
    monkeypatch: pytest.MonkeyPatch, restrict_to_work_dir: bool
) -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    try:
        first = _build(card, restrict_to_work_dir=restrict_to_work_dir)
        first_sys_op = first.deep_config.sys_operation
        assert first_sys_op is not None
        assert Runner.resource_mgr.get_sys_operation(sysop_id) is first_sys_op

        # A matching rebuild must be a pure cache hit: no strict-add attempt, so no
        # "resource already exist" error can be logged for it.
        add_attempts: list[SysOperationCard] = []
        real_add = Runner.resource_mgr.add_sys_operation

        def _spy_add(requested: Any, **kwargs: Any) -> Any:
            add_attempts.append(requested)
            return real_add(requested, **kwargs)

        monkeypatch.setattr(Runner.resource_mgr, "add_sys_operation", _spy_add)
        second = _build(card, restrict_to_work_dir=restrict_to_work_dir)

        assert add_attempts == []
        assert second.deep_config.sys_operation is first_sys_op
        assert second.deep_config.restrict_to_work_dir is restrict_to_work_dir
        assert first_sys_op.work_config == _factory_work_config(restrict_to_work_dir)
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


def test_rebuild_tightening_policy_rejects_unrestricted_sys_operation() -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    try:
        unrestricted = _build(card, restrict_to_work_dir=False)
        stale_sys_op = unrestricted.deep_config.sys_operation
        assert stale_sys_op is not None
        assert stale_sys_op.work_config == _factory_work_config(False)

        # Same identity, now asking for a sandbox: adopting the stale entry would let
        # the agent read outside its workspace while restrict_to_work_dir=True.
        with pytest.raises(ValueError) as excinfo:
            _build(card, restrict_to_work_dir=True)

        message = str(excinfo.value)
        assert sysop_id in message
        assert "LocalWorkConfig" in message
        assert "remove_sys_operation(" not in message
        assert "owner" in message
        # Rejected, not replaced or mutated: the live entry is still its owner's to dispose of.
        assert Runner.resource_mgr.get_sys_operation(sysop_id) is stale_sys_op
        assert stale_sys_op.work_config == _factory_work_config(False)
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


def test_rebuild_relaxing_policy_rejects_restricted_sys_operation() -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    try:
        restricted = _build(card, restrict_to_work_dir=True)
        stale_sys_op = restricted.deep_config.sys_operation
        assert stale_sys_op is not None
        assert stale_sys_op.work_config == _factory_work_config(True)

        # Same identity, now unrestricted: adopting the stale entry would lock the
        # agent out of paths it was told it may read while restrict_to_work_dir=False.
        with pytest.raises(ValueError) as excinfo:
            _build(card, restrict_to_work_dir=False)

        assert sysop_id in str(excinfo.value)
        assert Runner.resource_mgr.get_sys_operation(sysop_id) is stale_sys_op
        assert stale_sys_op.work_config == _factory_work_config(True)
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


@pytest.mark.parametrize(
    ("work_config", "restrict_to_work_dir"),
    [
        (LocalWorkConfig(shell_allowlist=None, sandbox_root=["/opt/other-root"], restrict_to_sandbox=True), True),
        (LocalWorkConfig(shell_allowlist=["ls"], restrict_to_sandbox=False), False),
        (LocalWorkConfig(shell_allowlist=None, dangerous_patterns=[r"rm\s+-rf"], restrict_to_sandbox=False), False),
    ],
    ids=["sandbox_root", "shell_allowlist", "dangerous_patterns"],
)
def test_pre_registered_custom_work_config_is_rejected(
    work_config: LocalWorkConfig, restrict_to_work_dir: bool
) -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    try:
        Runner.resource_mgr.add_sys_operation(
            SysOperationCard(id=sysop_id, mode=OperationMode.LOCAL, work_config=work_config)
        )
        pre_registered = Runner.resource_mgr.get_sys_operation(sysop_id)
        assert pre_registered is not None
        assert pre_registered.work_config == work_config

        # Only the custom field differs: the requested restrict flag matches the entry's.
        with pytest.raises(ValueError) as excinfo:
            _build(card, restrict_to_work_dir=restrict_to_work_dir)

        assert sysop_id in str(excinfo.value)
        assert Runner.resource_mgr.get_sys_operation(sysop_id) is pre_registered
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


def test_pre_registered_sandbox_mode_sys_operation_is_rejected() -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    try:
        Runner.resource_mgr.add_sys_operation(_sandbox_card(sysop_id))
        pre_registered = Runner.resource_mgr.get_sys_operation(sysop_id)
        assert pre_registered is not None
        assert pre_registered.mode == OperationMode.SANDBOX

        with pytest.raises(ValueError) as excinfo:
            _build(card, restrict_to_work_dir=True)

        message = str(excinfo.value)
        assert sysop_id in message
        assert "operation mode" in message
        assert f"'{OperationMode.SANDBOX.value}'" in message
        assert f"'{OperationMode.LOCAL.value}'" in message
        assert Runner.resource_mgr.get_sys_operation(sysop_id) is pre_registered
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


def _incompatible_winner_card(sysop_id: str, winner: str) -> SysOperationCard:
    if winner == "mode":
        return _sandbox_card(sysop_id)
    return SysOperationCard(id=sysop_id, mode=OperationMode.LOCAL, work_config=_factory_work_config(True))


@pytest.mark.parametrize("winner", ["config", "mode"])
def test_registration_race_with_incompatible_winner_is_rejected(monkeypatch: pytest.MonkeyPatch, winner: str) -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    resource_mgr = Runner.resource_mgr
    real_get = resource_mgr.get_sys_operation
    real_add = resource_mgr.add_sys_operation
    lookups: list[str | None] = []
    winning_instance: list[SysOperation] = []
    try:
        assert real_get(sysop_id) is None

        def racing_get(sys_operation_id: str | None = None, **kwargs: Any) -> SysOperation | None:
            lookups.append(sys_operation_id)
            if len(lookups) == 1:
                return None  # first lookup: the id is still free
            return real_get(sysop_id)  # second lookup: the winner's instance is registered by now

        def racing_add(requested: SysOperationCard, **kwargs: Any) -> Any:
            # The competing caller wins the strict add, so this caller's add is rejected.
            assert real_add(_incompatible_winner_card(sysop_id, winner)).is_ok()
            winner_obj = real_get(sysop_id)
            assert winner_obj is not None
            winning_instance.append(winner_obj)
            result = real_add(requested, **kwargs)
            assert result.is_err()
            return result

        monkeypatch.setattr(resource_mgr, "get_sys_operation", racing_get)
        monkeypatch.setattr(resource_mgr, "add_sys_operation", racing_add)

        with pytest.raises(ValueError) as excinfo:
            _build(card, restrict_to_work_dir=False)

        assert sysop_id in str(excinfo.value)
        assert lookups == [sysop_id, sysop_id]
        assert len(winning_instance) == 1
        surviving = real_get(sysop_id)
        assert surviving is winning_instance[0]
        if winner == "mode":
            assert surviving.mode == OperationMode.SANDBOX
        else:
            assert surviving.mode == OperationMode.LOCAL
            assert surviving.work_config == _factory_work_config(True)
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


def test_registration_race_with_compatible_winner_reuses_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    resource_mgr = Runner.resource_mgr
    real_get = resource_mgr.get_sys_operation
    real_add = resource_mgr.add_sys_operation
    lookups: list[str | None] = []
    add_calls: list[SysOperationCard] = []
    winning_instance: list[SysOperation] = []
    try:
        assert real_get(sysop_id) is None

        def racing_get(sys_operation_id: str | None = None, **kwargs: Any) -> SysOperation | None:
            lookups.append(sys_operation_id)
            if len(lookups) == 1:
                return None
            return real_get(sys_operation_id, **kwargs)

        def racing_add(requested: SysOperationCard, **kwargs: Any) -> Any:
            add_calls.append(requested)
            winner_card = SysOperationCard(
                id=sysop_id,
                mode=OperationMode.LOCAL,
                work_config=_factory_work_config(True),
            )
            assert real_add(winner_card).is_ok()
            winner_obj = real_get(sysop_id)
            assert winner_obj is not None
            winning_instance.append(winner_obj)
            result = real_add(requested, **kwargs)
            assert result.is_err()
            return result

        monkeypatch.setattr(resource_mgr, "get_sys_operation", racing_get)
        monkeypatch.setattr(resource_mgr, "add_sys_operation", racing_add)

        agent = _build(card, restrict_to_work_dir=True)

        assert lookups[:2] == [sysop_id, sysop_id]
        assert len(add_calls) == 1
        assert len(winning_instance) == 1
        assert agent.deep_config.sys_operation is winning_instance[0]
        assert real_get(sysop_id) is winning_instance[0]
        assert winning_instance[0].work_config == _factory_work_config(True)
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


def test_explicit_sys_operation_bypasses_registry_lookup_and_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    explicit = SysOperation(
        SysOperationCard(id=f"{sysop_id}_explicit", mode=OperationMode.LOCAL, work_config=_factory_work_config(True))
    )
    resource_mgr = Runner.resource_mgr
    real_get = resource_mgr.get_sys_operation
    real_add = resource_mgr.add_sys_operation
    lookups: list[str | None] = []
    added: list[Any] = []
    try:
        # The most conflicting entry possible sits under the id a lookup would use.
        Runner.resource_mgr.add_sys_operation(_sandbox_card(sysop_id))

        def _tracking_get(sys_operation_id: str | None = None, **kwargs: Any) -> SysOperation | None:
            lookups.append(sys_operation_id)
            return real_get(sys_operation_id, **kwargs)

        def _tracking_add(requested: Any, **kwargs: Any) -> Any:
            added.append(requested)
            return real_add(requested, **kwargs)

        monkeypatch.setattr(resource_mgr, "get_sys_operation", _tracking_get)
        monkeypatch.setattr(resource_mgr, "add_sys_operation", _tracking_add)

        # The caller's instance is adopted verbatim, even though it disagrees with restrict_to_work_dir.
        agent = _build(card, restrict_to_work_dir=False, sys_operation=explicit)

        assert agent.deep_config.sys_operation is explicit
        assert sysop_id not in lookups  # the conflicting entry is never consulted
        assert added == []
        registered = real_get(sysop_id)
        assert registered is not None
        assert registered.mode == OperationMode.SANDBOX
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


def test_enable_sys_operation_false_skips_registry_despite_conflicting_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    resource_mgr = Runner.resource_mgr
    real_get = resource_mgr.get_sys_operation
    try:
        Runner.resource_mgr.add_sys_operation(_sandbox_card(sysop_id))
        pre_registered = real_get(sysop_id)
        assert pre_registered is not None

        def _unexpected(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("enable_sys_operation=False must not touch the sys_operation registry")

        monkeypatch.setattr(resource_mgr, "get_sys_operation", _unexpected)
        monkeypatch.setattr(resource_mgr, "add_sys_operation", _unexpected)

        agent = _build(card, restrict_to_work_dir=True, enable_sys_operation=False)

        assert agent.deep_config.sys_operation is None
        assert real_get(sysop_id) is pre_registered
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)


@pytest.fixture
def sandbox_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace directory and a sibling directory that lies outside it."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret_note.txt").write_text("outside the workspace\n", encoding="utf-8")
    return workspace, outside


@pytest.mark.asyncio
async def test_removing_stale_sys_operation_allows_policy_flip_end_to_end(
    sandbox_tree: tuple[Path, Path],
) -> None:
    workspace, outside = sandbox_tree
    secret = str(outside / "secret_note.txt")
    card = _make_card()
    sysop_id = f"{card.name}_{card.id}"
    try:
        init_cwd(str(workspace), workspace=str(workspace))

        restricted = _build(card, workspace=str(workspace), restrict_to_work_dir=True)
        restricted_sys_op = restricted.deep_config.sys_operation
        assert restricted_sys_op is not None
        denied = await restricted_sys_op.fs().read_file(secret)
        assert denied.code != StatusCode.SUCCESS.code
        assert "outside sandbox" in denied.message

        with pytest.raises(ValueError):
            _build(card, workspace=str(workspace), restrict_to_work_dir=False)
        assert Runner.resource_mgr.get_sys_operation(sysop_id) is restricted_sys_op

        # Explicit disposal is the supported way to change policy under a stable id.
        Runner.resource_mgr.remove_sys_operation(sysop_id)
        assert Runner.resource_mgr.get_sys_operation(sysop_id) is None

        unrestricted = _build(card, workspace=str(workspace), restrict_to_work_dir=False)
        unrestricted_sys_op = unrestricted.deep_config.sys_operation
        assert unrestricted_sys_op is not None
        assert unrestricted_sys_op is not restricted_sys_op
        assert unrestricted_sys_op.work_config == _factory_work_config(False)

        granted = await unrestricted_sys_op.fs().read_file(secret)
        assert granted.code == StatusCode.SUCCESS.code
        assert granted.data is not None
        assert granted.data.content == "outside the workspace\n"
    finally:
        Runner.resource_mgr.remove_sys_operation(sysop_id)
