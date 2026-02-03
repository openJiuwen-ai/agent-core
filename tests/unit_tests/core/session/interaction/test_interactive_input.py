import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


class TestInteractiveInput:
    def test_invalid_raw_inputs(self):
        with pytest.raises(BaseError) as cm:
            InteractiveInput(None)
        assert cm.value.code == StatusCode.INTERACTION_INPUT_INVALID.code

    def test_invalid_update(self):
        with pytest.raises(BaseError) as cm:
            interactive_input = InteractiveInput()
            interactive_input.update("id", None)
        assert cm.value.code == StatusCode.INTERACTION_INPUT_INVALID.code
