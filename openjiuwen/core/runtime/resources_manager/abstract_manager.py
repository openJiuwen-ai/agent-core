# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Generic, Optional, TypeVar, Callable, Union

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode

T = TypeVar('T')
Provider = TypeVar('Provider', bound=Callable[..., any])


class AbstractManager(Generic[T]):
    def __init__(self):
        self._resources: dict[str, T] = {}
        self._providers: dict[str, Provider] = {}
    
    def _validate_id(self, resource_id: str, error_code: StatusCode, operation: str) -> None:
        if resource_id is None or resource_id.strip() == "":
            raise JiuWenBaseException(
                error_code.code,
                error_code.errmsg.format(
                    reason=f"{operation}_id cannot be empty"
                ) if hasattr(error_code, 'errmsg') else f"{operation}_id cannot be empty"
            )
    
    def _validate_resource(self, resource: any, error_code: StatusCode, reason: str) -> None:
        if resource is None:
            raise JiuWenBaseException(
                error_code.code,
                error_code.errmsg.format(reason=reason) if hasattr(error_code, 'errmsg') else reason
            )
    
    def _handle_exception(self, exception: Exception, error_code: StatusCode, operation: str) -> None:
        if isinstance(exception, JiuWenBaseException):
            raise exception
        
        raise JiuWenBaseException(
            error_code.code,
            error_code.errmsg.format(reason=str(exception)) if hasattr(error_code, 'errmsg') else str(exception)
        )
    
    def _add_resource(self, resource_id: str, resource: Union[T, Provider], 
                     add_error_code: StatusCode, validate_func: Optional[Callable] = None) -> None:
        try:
            if callable(resource):
                self._providers[resource_id] = resource
            else:
                if validate_func:
                    processed_resource = validate_func(resource)
                    self._resources[resource_id] = processed_resource
                else:
                    self._resources[resource_id] = resource
        except JiuWenBaseException:
            raise
        except Exception as e:
            self._handle_exception(e, add_error_code, "add")
    
    def _get_resource(self, resource_id: str, 
                     get_error_code: StatusCode, 
                     create_resource_func: Optional[Callable] = None) -> Optional[T]:
        try:
            resource = self._resources.get(resource_id)
            if resource:
                return resource
            
            # Try to create from provider
            provider = self._providers.get(resource_id)
            if provider:
                if create_resource_func:
                    resource = create_resource_func(provider)
                else:
                    resource = provider()
                return resource
                
            return None
        except JiuWenBaseException:
            raise
        except Exception as e:
            self._handle_exception(e, get_error_code, "get")
            return None
    
    def _remove_resource(self, resource_id: str, 
                        remove_error_code: StatusCode) -> Optional[T]:
        try:
            resource = self._resources.pop(resource_id, None)
            self._providers.pop(resource_id, None)
            return resource
        except Exception as e:
            self._handle_exception(e, remove_error_code, "remove")
            return None