#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.exception_utils import ExceptionUtils


class JsonUtils:
    @staticmethod
    def safe_json_loads(json_string, default=None, **kwargs):
        if default is None:
            try:
                return json.loads(json_string, **kwargs)
            except json.JSONDecodeError as e:
                ExceptionUtils.raise_exception(StatusCode.JSON_LOADS_ERROR, f"JSON decode error", e)
            except TypeError as e:
                ExceptionUtils.raise_exception(StatusCode.JSON_LOADS_ERROR, f"JSON type error", e)
            except ValueError as e:
                ExceptionUtils.raise_exception(StatusCode.JSON_LOADS_ERROR, f"JSON value error", e)
            except Exception as e:
                ExceptionUtils.raise_exception(StatusCode.JSON_LOADS_ERROR, f"JSON operation error", e)
        else:
            result = default
            try:
                result = json.loads(json_string, **kwargs)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
            except TypeError as e:
                logger.error(f"JSON type error: {e}")
            except ValueError as e:
                logger.error(f"JSON value error: {e}")
            except Exception as e:
                logger.error(f"JSON operation error: {e}")
            return result


    @staticmethod
    def safe_json_dumps(obj, default=None, **kwargs):
        if default is None:
            try:
                return json.dumps(obj, **kwargs)
            except TypeError as e:
                ExceptionUtils.raise_exception(StatusCode.JSON_DUMPS_ERROR, f"JSON serialization type error", e)
            except ValueError as e:
                ExceptionUtils.raise_exception(StatusCode.JSON_DUMPS_ERROR, f"JSON serialization value error", e)
            except Exception as e:
                ExceptionUtils.raise_exception(StatusCode.JSON_DUMPS_ERROR, f"JSON serialization error", e)
        else:
            result = default
            try:
                result = json.dumps(obj, **kwargs)
            except TypeError:
                logger.error(f"JSON serialization type error")
            except ValueError:
                logger.error(f"JSON serialization value error")
            except Exception:
                logger.error(f"JSON serialization error")
            return result
