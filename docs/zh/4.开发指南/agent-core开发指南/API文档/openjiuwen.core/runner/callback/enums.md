# openjiuwen.core.runner.callback.enums

定义回调框架中用于控制执行流的枚举类型。

## enum FilterAction

过滤器可返回的动作，用于控制回调是否执行。

* **CONTINUE**：继续执行回调。
* **STOP**：停止整个事件处理。
* **SKIP**：跳过当前回调，继续下一个。
* **MODIFY**：修改参数后继续执行。

## enum ChainAction

回调在链式执行中可返回的动作，用于控制链的执行流。

* **CONTINUE**：继续执行链中下一个回调。
* **BREAK**：中断链并返回当前结果。
* **RETRY**：重试当前回调。
* **ROLLBACK**：回滚已执行的回调。

## enum HookType

可注册的生命周期钩子类型。

* **BEFORE**：在事件处理前执行。
* **AFTER**：在事件处理完成后执行。
* **ERROR**：在发生错误时执行。
* **CLEANUP**：在清理阶段执行。
