# openjiuwen.core.runner.callback.enums

Enumerations used by the callback framework to control execution flow.

## enum FilterAction

Actions that filters can return to control whether callbacks run.

* **CONTINUE**: Continue with callback execution.
* **STOP**: Stop the entire event processing.
* **SKIP**: Skip the current callback and continue to the next.
* **MODIFY**: Modify arguments and continue.

## enum ChainAction

Actions that callbacks can return during chain execution to control the chain flow.

* **CONTINUE**: Continue to the next callback in the chain.
* **BREAK**: Break the chain and return the current result.
* **RETRY**: Retry the current callback.
* **ROLLBACK**: Roll back all executed callbacks.

## enum HookType

Lifecycle hook types that can be registered.

* **BEFORE**: Executed before event processing.
* **AFTER**: Executed after event processing completes.
* **ERROR**: Executed when an error occurs.
* **CLEANUP**: Executed during the cleanup phase.
