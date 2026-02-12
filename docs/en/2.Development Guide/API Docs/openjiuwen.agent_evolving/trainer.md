# openjiuwen.agent_evolving.trainer

`openjiuwen.agent_evolving.trainer` provides self-evolving training orchestration: evaluation -> forward -> updater generates updates -> writes back to operators -> re-evaluation, with checkpoint and resume support.

---

## class openjiuwen.agent_evolving.trainer.trainer.Trainer

Orchestrates "evaluate -> generate updates -> writeback" self-evolution cycle, depends on `Updater` and `BaseEvaluator`, optional checkpoint and resume.

```text
class Trainer(
    *,
    updater: Updater,
    evaluator: BaseEvaluator,
    extractor: Optional[TracerTrajectoryExtractor] = None,
    callbacks: Optional[Callbacks] = None,
    num_parallel: int = TuneConstant.default_parallel_num,
    early_stop_score: float = TuneConstant.default_early_stop_score,
    checkpoint_dir: Optional[str] = None,
    resume_from: Optional[str] = None,
    checkpoint_every_n_epochs: int = 1,
    checkpoint_on_improve: bool = True,
    checkpoint_manager: Any = None,
)
```

**Parameters**:

* **updater**(Updater): Generates parameter updates based on trajectories and evaluation results.
* **evaluator**(BaseEvaluator): Scores model outputs against expected answers.
* **extractor**(TracerTrajectoryExtractor, optional): Extracts trajectories from Session; default `TracerTrajectoryExtractor()`.
* **callbacks**(Callbacks, optional): Training lifecycle hooks.
* **num_parallel**(int, optional): Parallelism for inference and evaluation. Default: `TuneConstant.default_parallel_num`.
* **early_stop_score**(float, optional): Stops when validation score reaches this value. Default: `TuneConstant.default_early_stop_score`.
* **checkpoint_dir**(str, optional): Checkpoint directory; None disables checkpointing.
* **resume_from**(str, optional): Checkpoint path to resume from.
* **checkpoint_every_n_epochs**(int, optional): Save checkpoint every N epochs. Default: `1`.
* **checkpoint_on_improve**(bool, optional): Also save on validation improvement. Default: `True`.
* **checkpoint_manager**(Any, optional): Custom checkpoint manager; default uses `DefaultCheckpointManager`.

### set_callbacks(callbacks: Callbacks) -> None

Sets training lifecycle callbacks (e.g., progress printing, metrics reporting).

### train(agent, train_cases=None, val_cases=None, num_iterations=TuneConstant.default_iteration_num, **kwargs) -> BaseAgent

Executes self-evolving training: first does validation baseline evaluation, then multiple rounds of "training forward -> updater update -> validation evaluation -> checkpoint". Uses `agent.get_operators()` to get optimizable operators and applies updates.

**Parameters**:

* **agent**(BaseAgent): Agent to optimize, must implement `get_operators()` and support invoke with session.
* **train_cases**(CaseLoader, optional): Training set; black-box optimizers may not depend on this forward data.
* **val_cases**(CaseLoader, optional): Validation set; uses train_cases if not provided.
* **num_iterations**(int, optional): Maximum training epochs.
* **kwargs**: Pass through to updater.update config.

**Returns**:

**BaseAgent**, agent after training (internal parameters updated by updater).

### forward(agent, cases) -> Tuple[float, List[EvaluatedCase], List[Trajectory], List[Any]]

Single forward pass: inference on cases -> evaluation -> extract trajectories from each Session.

**Returns**:

**(average score, EvaluatedCase list, trajectory list, Session list)**.

### evaluate(agent, cases) -> Tuple[float, List[EvaluatedCase]]

Inference and evaluation only, no trajectory extraction; returns average score and evaluation result list.

### predict_only(agent, cases) -> List[Dict]

Inference only, returns model output list for each case, no Session return.

### predict(agent, cases) -> Tuple[List[Dict], List[Any]]

Calls agent.invoke for each case (with Session), parallelism controlled by num_parallel.

**Returns**:

**(model output list, Session list)**; Session used for subsequent trajectory extraction.

### staticmethod apply_updates(operators: Dict[str, Operator], updates: Updates) -> None

Applies updates from updater to operator dict; for SingleDimUpdater direct writeback updates may be empty, this method skips.

---

## class openjiuwen.agent_evolving.trainer.progress.Progress

Training progress (current/max epochs, best score, current epoch score, etc.), provides epoch/batch iteration.

* **start_epoch**(int): Starting epoch (for resume). Default: `0`.
* **current_epoch**(int): Current epoch. Default: `0`.
* **max_epoch**(int): Maximum epochs. Default: `TuneConstant.default_iteration_num`.
* **current_batch_iter**(int): Current batch step. Default: `0`.
* **max_batch_iter**(int): Number of batch steps per epoch. Default: `1`.
* **best_score**(float): Historical best validation score, value range [0, 1]. Default: `0.0`.
* **best_batch_score**(float): Best batch score within current epoch. Default: `0.0`.
* **current_epoch_score**(float): Current epoch validation score. Default: `0.0`.

### run_epoch() -> Generator[int, None, None]

Iterates 1..max_epoch, updates current_epoch and yields epoch number each round.

### run_batch() -> Generator[int, None, None]

Iterates batch steps, updates current_batch_iter; resets best_batch_score to 0 at start.

---

## class openjiuwen.agent_evolving.trainer.progress.Callbacks

Training lifecycle hooks; subclasses can override for logging, early stopping, metrics reporting, etc.

### on_train_begin(agent, progress, eval_info) -> None

Called when training begins (validation baseline evaluation completed).

### on_train_end(agent, progress, eval_info) -> None

Called when training ends.

### on_train_epoch_begin(agent, progress) -> None

Called at the beginning of each training epoch.

### on_train_epoch_end(agent, progress, eval_info) -> None

Called at the end of each training epoch (best_score updated / parameters written back).
