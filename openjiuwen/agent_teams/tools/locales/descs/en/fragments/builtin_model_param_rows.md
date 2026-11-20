| **external_cli.builtin_model** | internal | Optional. A model from `<builtin_model_catalog>` for this `cli_agent`; runs on the CLI's own login. Mutually exclusive with `model_name` |
| **external_cli.effort** | internal | Optional. Reasoning effort, one of the chosen `external_cli.builtin_model`'s `efforts`; requires `external_cli.builtin_model`. Omit to take its `default_effort` |
