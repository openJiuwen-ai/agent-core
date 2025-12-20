#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Tuple, Optional

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.utils.prompt.template.template import Template
from jiuwen.core.runtime.thread_safe_dict import ThreadSafeDict


class PromptMgr:
    def __init__(self) -> None:
        self._repo: ThreadSafeDict[str, Template] = ThreadSafeDict()

    def add_prompt(self, template_id: str, template: Template) -> None:
        if template_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_PROMPT_ADD_FAILED.code,
                                      StatusCode.RUNTIME_PROMPT_ADD_FAILED.errmsg.format(
                                          reason='template_id is invalid, can not be None'))
        if template is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_PROMPT_ADD_FAILED.code,
                                      StatusCode.RUNTIME_PROMPT_ADD_FAILED.errmsg.format(
                                          reason='template is invalid, can not be None'))
        self._repo[template_id] = template

    def add_prompts(self, templates: List[Tuple[str, Template]]) -> None:
        if templates is None:
            return
        for id, template in templates:
            self.add_prompt(id, template)

    def remove_prompt(self, template_id: str) -> Optional[Template]:
        return self._repo.pop(template_id, None)

    def get_prompt(self, template_id: str) -> Optional[Template]:
        if template_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_PROMPT_GET_FAILED.code,
                                      StatusCode.RUNTIME_PROMPT_GET_FAILED.errmsg.format(
                                          reason='template_id is invalid, can not be None'))
        return self._repo.get(template_id)
