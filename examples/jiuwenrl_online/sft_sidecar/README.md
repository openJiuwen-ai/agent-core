# SFT Sidecar

Build a jiuwenswarm sidecar image from the current `agent-core` source and the
local `jiuwenclaw` source, then run 5 SWE-bench cases through the existing
`run_swe_task_rollout.py` controller.

## Build

```bash
bash examples/jiuwenrl_online/sft_sidecar/build_sidecar_image.sh
```

The default is `USE_CONDA=1`, which uses the host `openjiuwen-sft`
environment at runtime. On a host without conda, build and run with:

```bash
USE_CONDA=0 bash examples/jiuwenrl_online/sft_sidecar/build_sidecar_image.sh
USE_CONDA=0 bash examples/jiuwenrl_online/sft_sidecar/run_swe_sidecar_5_cases.sh
```

In this mode the image installs the source packages and their Python
dependencies into its system Python, and the controller skips conda activation
and conda mounts. The SWE task containers also receive
`SFT_DOCKER_USE_HOST_CONDA=0`.

## Run 5 cases

```bash
bash examples/jiuwenrl_online/sft_sidecar/run_swe_sidecar_5_cases.sh
```

Dry-run command generation:

```bash
SFT_SIDECAR_DRY_RUN=1 bash examples/jiuwenrl_online/sft_sidecar/run_swe_sidecar_5_cases.sh
```

The run script uses the sidecar image as the controller process and keeps the
SWE case containers on the existing Docker backend. The controller container
mounts `/data1/lll` at the same path, activates `openjiuwen-sft`, injects the
current `agent-core` and `jiuwenclaw` paths through `PYTHONPATH`, and mounts the
host Docker CLI/socket so the existing rollout code can launch SWE images.
