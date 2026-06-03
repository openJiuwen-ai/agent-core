# Mobile GUI examples

These scripts drive an Android device or emulator through [uiautomator2](https://github.com/openatx/uiautomator2) and a **vision-capable** LLM that grounds UI actions from screenshots.


| Script | Purpose |
| --- | --- |
| [`run_mobile_gui_agent.py`](run_mobile_gui_agent.py) | Run the mobile GUI agent end-to-end. |
| [`run_mobile_gui_agent_with_skills.py`](run_mobile_gui_agent_with_skills.py) | Same agent with **skill discovery** enabled; copies bundled skills (`scheduling`, `github-com`) into a temp workspace so the agent can read and follow them via multimodal skill rails. |
| [`run_mobile_gui_subagent.py`](run_mobile_gui_subagent.py) | Run a **coordinator DeepAgent** with browser, code, and mobile GUI subagents; the parent delegates through `task_tool` when a specialized subagent fits the task. |


All commands below assume your current working directory is the **repository root** (`agent-core-mobile`).

---

## 1. Install Android Studio and an emulator (from scratch)

1. **Download and install [Android Studio](https://developer.android.com/studio)** (Windows, macOS, or Linux).
2. **Open Android Studio → More Actions → SDK Manager** (or *Settings → Languages & Frameworks → Android SDK*) and install:
  - **Android SDK Platform** for a recent API level (e.g. API 34 or 35).
  - **Android SDK Platform-Tools** (includes `adb`).
3. **Add `adb` to your `PATH`** so a terminal can run `adb`:
  - **Windows (typical):**  
   `"%LOCALAPPDATA%\Android\Sdk\platform-tools"`  
   (Or wherever the SDK lives; check *SDK Manager → Android SDK Location*.)
  - **macOS/Linux (typical):**  
  `$HOME/Android/Sdk/platform-tools`
4. **Create a virtual device:** *Device Manager* (AVD Manager) → **Create device** → pick a phone profile → choose a system image (e.g. Google APIs / x86_64 or arm64) → finish.
5. **Start the emulator** from Device Manager and wait until it fully boots to the home screen.
6. **Verify `adb` sees the device:**
  ```bash
   adb devices
  ```
   You should see a line such as `emulator-5554    device`.  
   The examples default to serial `emulator-5554`. If yours differs, set `DEVICE_SERIAL` (see [Environment variables](#environment-variables)).

**Physical device (optional):** Enable **Developer options** and **USB debugging**, connect via USB (or wireless debugging), accept the RSA prompt, then confirm the serial with `adb devices` and set `DEVICE_SERIAL` accordingly.

---

## 2. Python environment and dependencies

You need **Python 3** and the optional `mobile-gui` extra (installs `uiautomator2` and `Pillow`), plus the package from this repo.

**Using `uv` (recommended if you use the project lockfile):**

```bash
uv sync --extra mobile-gui
```

**Using `pip` from the repo root:**

```bash
pip install -e ".[mobile-gui]"
```

If the first connection to a new emulator/device fails with uiautomator2/atx-agent errors, try (with the device online and `adb devices` showing it):

```bash
python -m uiautomator2 init
```

(Re-run once per environment or after wiping the emulator.)

---

## 3. Configure the LLM (`examples/mobile_gui/.env`)

Environment is loaded by `[example_utils.load_example_env](example_utils.py)`: it reads the repo-root `.env`, legacy `examples/.env` (if present), then `examples/mobile_gui/.env` (most specific wins).

Create **`examples/mobile_gui/.env`** by copying `[.env.example](.env.example)` with at least:


| Variable          | Required | Description                                                                                                   |
| ----------------- | -------- | ------------------------------------------------------------------------------------------------------------- |
| `LLM_API_KEY`     | Yes*     | API key for your provider.                                                                                    |
| `LLM_API_BASE`    | Yes*     | API base URL (default: OpenAI-compatible `https://api.openai.com/v1`).                                        |
| `LLM_MODEL_NAME`  | Yes*     | Must support **images** in the chat API you use.                                                              |
| `LLM_PROVIDER`    | No       | Provider label for `init_model` (default: `OpenAI`). Alias: `LLM_PROVIDER`.                                   |


The process exits early if `LLM_API_KEY` is NOT set.

---

## 4. Run `run_mobile_gui_agent.py`

From the **repository root**:

```bash
uv run python examples/mobile_gui/run_mobile_gui_agent.py
```

Or, with dependencies already installed:

```bash
python examples/mobile_gui/run_mobile_gui_agent.py
```

Useful environment variables: [Environment variables](#environment-variables).

---

## 5. Run `run_mobile_gui_agent_with_skills.py`

Like [`run_mobile_gui_agent.py`](#4-run-run_mobile_gui_agentpy), but turns on skill discovery and seeds `examples/mobile_gui/skills/` into the workspace before the run. Use this when the task should follow a bundled skill (e.g. GitHub or scheduling flows).

```bash
uv run python examples/mobile_gui/run_mobile_gui_agent_with_skills.py
```

---

## 6. Run `run_mobile_gui_subagent.py`

Runs a parent DeepAgent that can delegate to **browser**, **code**, and **mobile GUI** subagents. The coordinator handles simple requests itself and calls `task_tool` when a subagent is a better fit. Browser delegation needs Playwright/MCP configured like other browser examples.

Optional coordinator prompt overrides in `examples/mobile_gui/.env`:

| Variable | Description |
| --- | --- |
| `MOBILE_COORDINATOR_SYSTEM_PROMPT` | Full override; empty value disables extra coordinator text. |
| `MOBILE_COORDINATOR_DEFAULT_HINT=0` | Disable the built-in default routing hint. |

```bash
uv run python examples/mobile_gui/run_mobile_gui_subagent.py
```

Subagent iteration limits: `OTHER_SUBAGENT_MAX_ITERATIONS` (default `25`), `MOBILE_SUBAGENT_MAX_ITERATIONS` (default `30`).

---

## Environment variables (shared)


| Variable         | Default                | Description                          |
| ---------------- | ---------------------- | ------------------------------------ |
| `DEVICE_SERIAL`  | `emulator-5554`        | `adb` serial passed to uiautomator2. |
| `MOBILE_TASK`    | (built-in demo string) | Natural-language goal for the agent. |
| `MAX_ITERATIONS` | `30`                   | Upper bound on agent iterations.     |


Additional tuning for screenshots and grounding (used inside the mobile GUI tools) is available via variables such as `VLM_GROUNDING_MAX_WIDTH`; see `[openjiuwen/harness/tools/mobile_gui/config.py](../../openjiuwen/harness/tools/mobile_gui/config.py)`.

---

## Troubleshooting

- `**adb devices` shows `unauthorized`:** Unlock the device/emulator and accept the USB debugging authorization dialog.
- `**device not found` / connection errors:** Confirm `DEVICE_SERIAL` matches `adb devices` exactly.
- **Missing packages:** Install the `mobile-gui` extra; the script prints install hints if `uiautomator2` or `Pillow` is missing.
- **LLM errors or poor behavior:** Confirm the model supports multimodal inputs and that `API_BASE`/`MODEL_NAME` match your provider.
