

import unittest

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput


class InteractiveInputTest(unittest.TestCase):
    def test_invalid_raw_inputs(self):
        with self.assertRaises(JiuWenBaseException) as cm:
            InteractiveInput(None)
        self.assertEqual(cm.exception.error_code, StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.code)
        self.assertEqual(cm.exception.message, StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.errmsg)

    def test_invalid_update(self):
        with self.assertRaises(JiuWenBaseException) as cm:
            interactive_input = InteractiveInput()
            interactive_input.update("id", None)
        self.assertEqual(cm.exception.error_code, StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.code)
        self.assertEqual(cm.exception.message, StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.errmsg)