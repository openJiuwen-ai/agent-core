import pytest

from openjiuwen.core.common.exception.code_template import (
    generate_status_code,
    generate_error_message_template,
    generate_status_code_spec,
    render_enum_member,
)
from openjiuwen.core.common.exception.codes import StatusCode
from tests.unit_tests.core.common.status_code_docgen import generate_markdown


def test_status_code_template():
    tpl = generate_status_code(
        scope="TOOL",
        subject="INPUT",
        failure_type="PARAM_ERROR",
    )

    print(tpl)

    tpl = generate_status_code(
        scope="AGENT",
        subject="INVOKE",
        detail="LLM",
        failure_type="CALL_FAILED",
    )

    print(tpl)


def test_status_spec():
    tpl = generate_status_code(
        scope="TOOL",
        subject="INPUT",
        failure_type="PARAM_ERROR",
    )
    spec = generate_status_code_spec(
        template=tpl,
        code=182010,
    )

    print(render_enum_member(spec))

    tpl = generate_status_code(
        scope="AGENT",
        detail="LLM",
        subject="INVOKE",
        failure_type="CALL_FAILED",
    )

    spec = generate_status_code_spec(
        template=tpl,
        code=123010,
    )

    print(render_enum_member(spec))

    tpl = generate_status_code(
        scope="WORKFLOW",
        subject="EXECUTION",
        failure_type="TIMEOUT",
    )

    spec = generate_status_code_spec(
        template=tpl,
        code=100110,
    )

    print(render_enum_member(spec))


@pytest.mark.skip(reason="should not write markdown file")
def test_status_doc():
    doc = generate_markdown(StatusCode)

    with open("STATUS_CODE.md", "w", encoding="utf-8") as f:
        f.write(doc)


def test_status_message():
    tpl = generate_error_message_template(
        scope="AGENT",
        subject="GROUP_ADD",
        failure_type="RUNTIME_ERROR",
    )

    print(tpl.template)

    # tpl = generate_error_message_template(
    #     scope="WORKFLOW",
    #     subject="EXECUTION",
    #     failure_type="TIMEOUT",
    #     with_reason=False,
    # )
    #
    # print(tpl.template)
    #
    # tpl = generate_error_message_template(
    #     scope="AGENT",
    #     subject="TASK_TYPE",
    #     failure_type="NOT_SUPPORTED",
    # )
    #
    # print(tpl.template)
