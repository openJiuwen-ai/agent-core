# Local Python SFT Exercises

This directory contains five tiny local Python projects used by
`run_sft_verl_e2e_local.sh`.

Each project is a self-contained session target:

- `sort_numbers`
- `linked_list`
- `valid_parentheses`
- `fibonacci`
- `merge_intervals`

Every project has:

- one buggy implementation file named `problem.py`
- one pytest file under `tests/`

The local E2E flow runs each project in a separate jiuwenswarm session and
uploads one `sft-sample-v1` trajectory per session.
