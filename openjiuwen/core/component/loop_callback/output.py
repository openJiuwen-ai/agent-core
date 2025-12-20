#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any

from openjiuwen.core.component.loop_callback.loop_callback import LoopCallback
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.runtime.utils import is_ref_path, extract_origin_key, NESTED_PATH_SPLIT
from openjiuwen.core.graph.executable import Output


class OutputCallback(LoopCallback):
    def __init__(self, outputs_format: dict[str, Any], round_result_root: str = None,
                 result_root: str = None):
        self._outputs_format = outputs_format
        self._result_root = result_root
        self._round_result_root = round_result_root if round_result_root else "round"

    def first_in_loop(self, runtime: BaseRuntime) -> Output:
        _results: list[Any] = []
        runtime.state().update({self._round_result_root: _results})
        return None

    def _generate_output(self, runtime: BaseRuntime, results: list[Any], root: list[str], output_format: Any):
        if isinstance(output_format, dict):
            output = {}
            for key, value in output_format.items():
                path = root.copy()
                path.append(key)
                output[key] = self._generate_output(runtime, results, path, value)
            return output
        if isinstance(output_format, str) and is_ref_path(output_format):
            ref_str = extract_origin_key(output_format)
            path = ref_str.split(NESTED_PATH_SPLIT)
            if path[0] == runtime.node_id():
                if len(results) == 0:
                    return None
                data = results[-1]
                for key in root:
                    data = data.get(key)
                return data

        output = []
        for result in results:
            data = result
            for key in root:
                data = data.get(key)
            output.append(data)
        return output

    def out_loop(self, runtime: BaseRuntime) -> Output:
        results: list[Any] = runtime.state().get(self._round_result_root)
        return self._generate_output(runtime, results, [], self._outputs_format)

    def start_round(self, runtime: BaseRuntime) -> Output:
        return None

    def end_round(self, runtime: BaseRuntime, loop_times: int) -> Output:
        results: list[Any] = runtime.state().get(self._round_result_root)
        if not isinstance(results, list):
            raise RuntimeError("error results in round process")
        if len(results) >= loop_times:
            return None
        results.append(runtime.state().get_inputs(self._outputs_format))
        runtime.state().update({self._round_result_root: results})
        return None
