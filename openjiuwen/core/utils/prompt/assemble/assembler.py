#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, List, Dict

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.utils.llm.messages import BaseMessage
from openjiuwen.core.utils.prompt.assemble.variables.textable import TextableVariable
from openjiuwen.core.utils.prompt.assemble.variables.variable import Variable


class Assembler:
    """class for creating prompt based on a given template"""

    def __init__(self,
                 template_content: Union[List[Dict], List[BaseMessage], str],
                 **variables):
        self.template_content = template_content
        self.template_formater: List[Variable] = self.__get_formater_list()
        self.variables = self.__get_variables_with_verify(variables)

    @property
    def input_keys(self) -> List[str]:
        """Get the list of argument names for updating all the variables"""
        keys = []
        for variable in self.variables.values():
            keys.extend(variable.input_keys)
        return list(set(keys))

    def __get_formater_list(self):
        """get template content formater"""
        template_formater_list = []
        if isinstance(self.template_content, str):
            template_formater_list.append(TextableVariable(self.template_content, name="__inner__"))
        else:
            for msg in self.template_content:
                if isinstance(msg, BaseMessage):
                    if not isinstance(msg.content, str):
                        template_formater_list.append(None)
                        continue
                    template_formater_list.append(TextableVariable(msg.content, name="__inner__"))
                else:
                    if not isinstance(msg.get("content"), str):
                        template_formater_list.append(None)
                        continue
                    template_formater_list.append(TextableVariable(msg.get("content"), name="__inner__"))
        return template_formater_list

    def __get_variables_with_verify(self, variables):
        """verify input variables and summarize with template content variables"""
        input_keys = []
        for formater in self.template_formater:
            if not formater:
                continue
            input_keys.extend(formater.input_keys)
        input_keys = list(set(input_keys))
        for name, variable in variables.items():
            if name not in input_keys:
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_ERROR.code,
                    message=f"Variable {name} is not defined in the Template."
                )
            if not isinstance(variable, Variable):
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_ERROR.code,
                    message=f"Variable {name} must be instantiated as a `Variable` object."
                )
        for placeholder in input_keys:
            if placeholder in variables:
                variables[placeholder].name = placeholder
            else:
                variables[placeholder] = TextableVariable(name=placeholder, text=f"{{{{{placeholder}}}}}")
        return variables

    def assemble(self, **kwargs) -> Union[str, List[dict]]:
        """Update the variables and format the template into a string-type or message-type prompt"""
        kwargs = {k: v for k, v in kwargs.items() if v is not None and k in self.input_keys}
        all_kwargs = {}
        for k in self.input_keys:
            if k not in kwargs:
                # replace placeholder with initial content if not exist
                all_kwargs[k] = f"{{{{{k}}}}}"
        all_kwargs.update(**kwargs)
        self._update(**all_kwargs)
        return self._format()

    def _update(self, **kwargs) -> None:
        """Update the variables based on the arguments passed in as key-value pairs"""
        missing_keys = set(self.input_keys) - set(kwargs.keys())
        if missing_keys:
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_ASSEMBLER_TEMPLATE_FORMAT_ERROR.code,
                message=f"Missing keys for updating the assembler: {list(missing_keys)}"
            )
        unexpected_keys = set(kwargs.keys()) - set(self.input_keys)
        if unexpected_keys:
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_ASSEMBLER_TEMPLATE_FORMAT_ERROR.code,
                message=f"Unexpected keys for updating the assembler: {list(unexpected_keys)}"
            )
        for variable in self.variables.values():
            input_kwargs = {k: v for k, v in kwargs.items() if k in variable.input_keys}
            variable.eval(**input_kwargs)

    def _format(self) -> Union[str, List[dict], List[BaseMessage]]:
        """Substitute placeholders in the template with variables values and get formatted prompt."""
        format_kwargs = {var.name: var.value for var in self.variables.values()}
        for idx, formater in enumerate(self.template_formater):
            if not formater:
                continue
            formatted_prompt = formater.eval(**format_kwargs)
            if isinstance(self.template_content, str):
                self.template_content = formatted_prompt
                break
            elif isinstance(self.template_content, list) and isinstance(self.template_content[idx], BaseMessage):
                self.template_content[idx].content = formatted_prompt
            else:
                self.template_content[idx]["content"] = formatted_prompt
        return self.template_content
