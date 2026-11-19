# openjiuwen.agent_evolving.signal

`openjiuwen.agent_evolving.signal` provides evolution signal detection and fingerprint utilities for online/offline experience generation.

**Documentation Index**:

| Module | Description |
|--------|-------------|
| [base](signal/base.md) | Signal primitives (`EvolutionSignal`, `EvolutionCategory`, `EvolutionTarget`, `make_evolution_signal`, `make_signal_fingerprint`) |
| [from_conv](signal/from_conv.md) | Conversation signal detection (`SignalDetector`, `ConversationSignalDetector`) |
| [from_eval](signal/from_eval.md) | Evaluation-to-signal helpers (`from_evaluated_case`, `from_evaluated_cases`) |
| [team](signal/team.md) | Team signal detection and helpers (`TeamSignalDetector`, `TeamSignalType`, `UserIntent`, `TrajectoryIssue`) |
| [review_feedback](signal/review_feedback.md) | Structured reviewer-feedback attribution, safe actions, and standard `EvolutionSignal` conversion |
