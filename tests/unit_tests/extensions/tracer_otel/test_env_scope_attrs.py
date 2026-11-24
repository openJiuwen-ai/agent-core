# -*- coding: utf-8 -*-
"""issue #1755：请求级环境作用域属性（workspace/environment/project）。"""
from unittest.mock import MagicMock

from openjiuwen.core.session.tracer.workflow_tracer import _env_scope_metadata
from openjiuwen.extensions.tracer_otel.handler import _set_env_scope_attrs
from openjiuwen.extensions.tracer_otel.semconv import (
    OJ_ENVIRONMENT_ID,
    OJ_PROJECT_ID,
    OJ_WORKSPACE_ID,
)


def _span(existing=None):
    span = MagicMock()
    span.attributes = dict(existing or {})
    return span


def test_set_env_scope_attrs_writes_three_attributes():
    span = _span()
    _set_env_scope_attrs(span, {"workspace_id": "ws1", "environment_id": "env1", "project_id": "p1"})
    body = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert body == {OJ_WORKSPACE_ID: "ws1", OJ_ENVIRONMENT_ID: "env1", OJ_PROJECT_ID: "p1"}


def test_set_env_scope_attrs_skips_missing_values():
    span = _span()
    _set_env_scope_attrs(span, {"workspace_id": "ws1", "environment_id": "", "project_id": None})
    body = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert body == {OJ_WORKSPACE_ID: "ws1"}


def test_set_env_scope_attrs_keeps_existing_value():
    span = _span({OJ_WORKSPACE_ID: "first"})
    _set_env_scope_attrs(span, {"workspace_id": "second", "project_id": "p1"})
    body = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert OJ_WORKSPACE_ID not in body
    assert body[OJ_PROJECT_ID] == "p1"


def test_set_env_scope_attrs_empty_metadata_is_noop():
    span = _span()
    _set_env_scope_attrs(span, None)
    _set_env_scope_attrs(span, {})
    span.set_attribute.assert_not_called()


def test_env_scope_metadata_drops_empty_keys():
    session = MagicMock()
    session.get_env_scope.return_value = {
        "workspace_id": "ws1", "environment_id": None, "project_id": "",
    }
    assert _env_scope_metadata(session) == {"workspace_id": "ws1"}


def test_env_scope_metadata_without_getter_is_empty():
    assert _env_scope_metadata(object()) == {}


def test_env_scope_metadata_getter_error_is_empty():
    session = MagicMock()
    session.get_env_scope.side_effect = RuntimeError("boom")
    assert _env_scope_metadata(session) == {}
