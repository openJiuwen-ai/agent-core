# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Import-boundary tests for message_queue_pulsar.

Verifies that the module can be imported without pulsar-client installed,
and that _require_pulsar() raises a clear error when pulsar is absent.
"""

import subprocess
import sys


def test_import_without_pulsar():
    """Module must import successfully even when pulsar-client is absent."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib, sys\n"
                "blocked = {'pulsar'}\n"
                "class Blocker:\n"
                "    def find_module(self, name, path=None):\n"
                "        if name in blocked or name.startswith('pulsar.'):\n"
                "            return self\n"
                "        return None\n"
                "    def load_module(self, name):\n"
                "        raise ImportError(f'Blocked: {name}')\n"
                "sys.meta_path.insert(0, Blocker())\n"
                "import openjiuwen.extensions.message_queue.message_queue_pulsar as mod\n"
                "assert mod.pulsar is None, 'pulsar should be None when not installed'\n"
                "print('OK')\n"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Failed: {result.stderr}"
    assert "OK" in result.stdout


def test_require_pulsar_raises_without_sdk():
    """_require_pulsar() must raise ImportError when pulsar is absent."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib, sys\n"
                "blocked = {'pulsar'}\n"
                "class Blocker:\n"
                "    def find_module(self, name, path=None):\n"
                "        if name in blocked or name.startswith('pulsar.'):\n"
                "            return self\n"
                "        return None\n"
                "    def load_module(self, name):\n"
                "        raise ImportError(f'Blocked: {name}')\n"
                "sys.meta_path.insert(0, Blocker())\n"
                "from openjiuwen.extensions.message_queue.message_queue_pulsar import _require_pulsar\n"
                "try:\n"
                "    _require_pulsar()\n"
                "    raise AssertionError('Expected ImportError')\n"
                "except ImportError as e:\n"
                "    assert 'pip install' in str(e), f'Bad message: {e}'\n"
                "    print('OK')\n"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Failed: {result.stderr}"
    assert "OK" in result.stdout
