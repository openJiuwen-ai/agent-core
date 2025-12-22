import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


class TestInteractiveInput:
    def test_invalid_raw_inputs(self):
        with pytest.raises(JiuWenBaseException) as cm:
            InteractiveInput(None)
        assert cm.value.error_code == StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.code
        assert cm.value.message == StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.errmsg

    def test_invalid_update(self):
        with pytest.raises(JiuWenBaseException) as cm:
            interactive_input = InteractiveInput()
            interactive_input.update("id", None)
        assert cm.value.error_code == StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.code
        assert cm.value.message == StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.errmsg
