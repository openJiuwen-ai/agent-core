# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Receipt diagnostics remain facts, never implicit target or effect approval."""

import json
import logging
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from openjiuwen.harness.tools.browser_move.controllers.action import _build_batch_interact_script, _compact_batch_step
from openjiuwen.harness.tools.browser_move.playwright_runtime import browser_logging, execution_journal as journal
from tests.unit_tests.harness.tools.browser_move.test_browser_local_continuity import execute_stub
from tests.unit_tests.harness.tools.browser_move.test_browser_shared_state_delivery import persistent_session


@pytest.mark.parametrize('target', [False, True])
def test_keyboard_receipt_distinguishes_target_binding_without_claiming_a_business_effect(target):
    step = {'op': 'press', 'key': 'Enter'}
    if target:
        step['selector'] = '#query'
    result = execute_stub([step], '', failure='none')['result']
    receipt = _compact_batch_step(result['steps'][0])
    assert receipt['keyboard'] == {'key': 'Enter', 'binding': 'explicit_target' if target else 'active_element'}
    assert receipt['execution_stage'] == 'action_ack'
    assert receipt['executed'] is True


def test_first_step_ack_survives_second_step_attempt_error_with_stage_diagnostics():
    result = execute_stub([{'op': 'fill', 'selector': '#query', 'value': 'weather'},
                           {'op': 'press', 'selector': '#query', 'key': 'Enter'}],
                          'transport closed', failure='press')['result']
    first, second = [_compact_batch_step(step) for step in result['steps']]
    assert first['executed'] is True and first['execution_stage'] == 'action_ack'
    assert second.get('executed') is None and second['execution_stage'] == 'action_attempt'
    session, _ = persistent_session({'execution_journal': []})
    inputs = SimpleNamespace(tool_name='browser_batch_interact', tool_call=SimpleNamespace(id='llm-submit'),
                             tool_args={'steps': [{'op': 'fill', 'value': 'weather'},
                                                  {'op': 'press', 'key': 'Enter'}]})
    journal.prepare(session, inputs)
    recorded = journal.record_result(session, inputs, {'success': False}, result)
    steps = recorded['execution']['steps']
    assert steps[0]['executed'] is True
    assert steps[1]['execution_state'] == 'dispatched_unknown'
    assert steps[1]['keyboard']['binding'] == 'explicit_target'
    assert steps[1]['execution_stage'] == 'action_attempt'


@pytest.mark.parametrize('slow_focus', [False, True])
def test_implicit_focus_receipt_is_structural_bounded_and_does_not_leak_field_value(slow_focus):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for generated executor integration')
    code = _build_batch_interact_script({'steps': [{'op': 'press', 'key': 'Enter'}], 'generation_id': 'g1'})
    runner = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let presses = 0;
global.document = {activeElement: {tagName: 'INPUT', isContentEditable: false, value: 'private value',
  getAttribute: name => ({role: 'searchbox', type: 'search'}[name] || null)}};
const page = {url: () => 'https://example.test', title: async () => 'Search',
  context: () => ({pages: () => [page]}), evaluate: async fn => input.slow ? new Promise(() => {}) : fn(),
  waitForTimeout: ms => new Promise(resolve => setTimeout(resolve, ms)), keyboard: {press: async () => {presses++}}};
(async () => {const result = await eval('(' + input.code + ')')(page);
  console.log(JSON.stringify({result, presses}))})();
"""
    result = subprocess.run([node, '-e', runner], input=json.dumps({'code': code, 'slow': slow_focus}), text=True,
                            encoding='utf-8', capture_output=True, check=True, timeout=10)
    value = json.loads(result.stdout)
    assert value['presses'] == 1
    receipt = _compact_batch_step(value['result']['steps'][0])
    if slow_focus:
        assert receipt.get('focus_before') is None
    else:
        assert receipt['focus_before'] == {'tag': 'input', 'role': 'searchbox', 'type': 'search', 'editable': False}
    assert receipt['keyboard']['binding'] == 'active_element'
    assert 'private value' not in json.dumps(receipt)


def test_completed_read_wait_receipt_does_not_keep_an_attempt_stage():
    result = execute_stub([{'op': 'sleep', 'ms': 0}], '', failure='none')['result']
    receipt = _compact_batch_step(result['steps'][0])
    assert receipt['ok'] is True
    assert receipt['execution_stage'] == 'step_ack'


def test_browser_logging_appends_on_reopen_and_retargets_without_leaking_to_previous_file(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_logging, '_BROWSER_LOGGER_NAME', 'isolated-browser-test')
    logger = logging.getLogger('isolated-browser-test')
    first, second = tmp_path / 'baseline.log', tmp_path / 'new-run.log'
    first.write_text('frozen baseline\n', encoding='utf-8')
    monkeypatch.setenv('OPENJIUWEN_BROWSER_AGENT_LOG_FILE', str(first))
    try:
        browser_logging.get_browser_agent_logger().info('first live event')
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        browser_logging.get_browser_agent_logger().info('after process reopen')
        monkeypatch.setenv('OPENJIUWEN_BROWSER_AGENT_LOG_FILE', str(second))
        browser_logging.get_browser_agent_logger().info('isolated new run')
        assert 'frozen baseline' in first.read_text(encoding='utf-8')
        assert 'after process reopen' in first.read_text(encoding='utf-8')
        assert 'isolated new run' not in first.read_text(encoding='utf-8')
        assert 'isolated new run' in second.read_text(encoding='utf-8')
        monkeypatch.setenv('OPENJIUWEN_BROWSER_AGENT_LOG_FILE', 'off')
        browser_logging.get_browser_agent_logger().info('disabled event')
        assert 'disabled event' not in second.read_text(encoding='utf-8')
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_logging_removes_stale_owned_handlers_even_after_finding_current_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_logging, '_BROWSER_LOGGER_NAME', 'isolated-browser-handlers')
    current_path, old_path = tmp_path / 'current.log', tmp_path / 'previous.log'
    monkeypatch.setenv('OPENJIUWEN_BROWSER_AGENT_LOG_FILE', str(current_path))
    logger = browser_logging.get_browser_agent_logger()
    old_handler = logging.FileHandler(old_path, encoding='utf-8')
    setattr(old_handler, browser_logging._BROWSER_HANDLER_MARKER, True)
    logger.addHandler(old_handler)
    try:
        browser_logging.get_browser_agent_logger().info('current session event')
        assert len(logger.handlers) == 1
        assert old_handler.stream is None
        assert 'current session event' in current_path.read_text(encoding='utf-8')
        assert not old_path.read_text(encoding='utf-8')
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
