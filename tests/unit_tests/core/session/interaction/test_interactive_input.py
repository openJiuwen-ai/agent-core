import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


class TestInteractiveInput:
    def test_invalid_raw_inputs(self):
        with pytest.raises(JiuWenBaseException) as cm:
            InteractiveInput(None)
        assert cm.value.error_code == StatusCode.WORKFLOW_INPUT_INVALID.code

    def test_invalid_update(self):
        with pytest.raises(JiuWenBaseException) as cm:
            interactive_input = InteractiveInput()
            interactive_input.update("id", None)
        assert cm.value.error_code == StatusCode.WORKFLOW_INPUT_INVALID.code
