# Hermes-Helper

Hermes-Helper is a gothic-styled Linux control center for cloud-backed or local Hermes Agent runtimes. It replaces the original one-file “gateway monitor” with controls and health checks that report what the system is actually doing.

## What it does

- **Start Server** reads Hermes's active provider. Cloud mode starts only the detached Hermes Gateway; local mode starts Ollama, the configured fallback model, and the gateway.
- Runs the gateway in the background without opening or requiring a terminal window.
- Announces when chat is genuinely ready and reveals a one-click **Launch Chat Terminal** control for optional direct chat.
- Never kills or restarts Hermes merely because the GUI was opened.
- Detects an existing Ollama server and leaves externally managed instances alone.
- Reads the active Hermes provider, model, context length, and output limit without reading credentials.
- Provides a dedicated **Model Settings** tab that applies provider, model, context, and output-token changes through the official Hermes CLI, validates them, and rolls back a failed update.
- Saves multiple named, non-secret **Model Profiles** for fast switching between cloud, local, reasoning, and lightweight configurations.
- Includes an **Agent Studio** with a visual **HEAD ALPHA → specialist branches** tree, reusable team presets, generated orchestration briefs, and safe setup of Hermes's built-in delegation model/concurrency.
- Verifies the Ollama HTTP API, installed models, loaded models, and runtime context only when local inference is active.
- Monitors Hermes and Ollama PID, CPU, memory, and live gateway logs.
- Refuses to treat a root-owned or other-user gateway as a successful managed launch.
- Removes terminal color/control sequences before displaying gateway output.
- Isolates unreadable legacy log files so they appear as targeted diagnostic warnings without freezing unrelated status checks.
- Checks Telegram configuration without revealing the bot token, verifies the Bot API, distinguishes that from an active gateway connection, and can send an explicit test message.
- Opens Hermes's official interactive Telegram setup wizard from the Telegram tab without collecting or displaying the bot token.
- Runs as a native XFCE system-tray application. The dashboard is omitted from the normal taskbar, and closing or minimizing it hides the window without stopping monitoring or services.
- Monitors Linux `hwmon` temperature sensors and names the exact source used for CPU warnings.
- Audits standard user and system startup locations for legacy Hermes launchers.
- Defaults to **no automatic startup**. Optional startup runs only after graphical login and never auto-starts the LLM.
- Exports a secret-free diagnostic report.
- Supports HTTPS + SHA-256 verified self-updates with visible progress and a recoverable backup.

## Install

Requirements: Linux, Python 3.10+, Tkinter, PyGObject/AppIndicator (`python3-gi` and `gir1.2-ayatanaappindicator3-0.1` on Kali/Debian), and an existing Hermes/Ollama setup.

```bash
unzip Hermes-Helper-1.0.5.zip
cd Hermes-Helper
chmod +x install.sh uninstall.sh
./install.sh
```

Launch it from the application menu or run:

```bash
hermes-helper
```

The installer creates:

- `~/.local/share/hermes-helper/` — application files
- `~/.local/bin/hermes-helper` — launcher
- `~/.local/share/applications/hermes-helper.desktop` — application-menu entry

Settings live at `~/.config/hermes-helper/config.json`. Logs, reports, update downloads, and backups live below `~/.local/state/hermes-helper/`.

## First launch

Hermes-Helper auto-discovers the standard `~/.hermes` managed installation. DoctorSUS's legacy managed layout is supported directly:

```text
~/.hermes/hermes-agent/venv/bin/python
~/.hermes/hermes-agent/cli.py --gateway
```

For newer Hermes installs, it uses `hermes gateway start`. If your installation needs something different, set **Gateway command override** in Settings.

The default local fallback model is:

```text
qwen3-4b-2507-abliterated-tools:latest
```

Runtime context defaults to `16,384` tokens while the model can remain advertised to Hermes with a `65,536` context capability.

When `~/.hermes/config.yaml` selects a cloud provider such as `openai-codex`, **Start Server** leaves Ollama unloaded and starts only the user-owned Hermes Gateway. Closing Hermes-Helper does not stop that detached gateway; use **Stop Server** when you want to take Telegram offline.

## Model settings

The **Model Settings** tab reads only Hermes's non-secret `model` configuration. It can update the provider, default model, context length, and maximum output tokens. Hermes-Helper invokes `hermes config set` with argument arrays (never a shell), runs `hermes config check`, and restores all previous model values if any write or validation step fails. Existing sessions retain their current model; restart a long-running gateway when you want it to adopt the new default immediately.

Named Model Profiles are stored in `~/.config/hermes-helper/presets.json` with owner-only permissions. Profiles contain routing names and token limits only—never API keys, OAuth tokens, or other credentials. Loading a profile fills the editor; activating it remains a separate, explicit **Validate & Apply** action.

## Agent Studio

The **Agent Studio** builds reusable delegation teams around Hermes's actual `delegate_task` engine:

- **HEAD ALPHA** uses the selected head model profile and synthesizes the final result.
- The expandable `+ Add Agent Branch` tree defines up to eight focused specialist roles.
- A worker model profile configures Hermes's supported `delegation.provider` and `delegation.model` settings for all branches.
- Parallelism is bounded, nesting is fixed to one level, and Hermes's existing approval policy is left unchanged.
- **Build Brief** generates a grounded orchestration prompt that dispatches branches in bounded waves, waits for real results, reconciles disagreements, and labels uncertainty.

The current Hermes runtime supports one delegation model for all child agents, not a separate model per branch. Hermes-Helper reflects that limitation honestly instead of pretending per-child routing exists. Team application uses only supported `hermes config set` commands, validates with `hermes config check`, and rolls back failed changes.

## Startup repair

The Diagnostics tab lists matching entries in:

- `~/.config/autostart`
- `~/.config/systemd/user`
- `/etc/systemd/system`
- `/usr/lib/systemd/system`
- `/etc/xdg/autostart`

**Back Up Legacy User Startup** moves only writable user-level entries into a timestamped backup. It never silently modifies a system service. System-level pre-login entries are reported so the user can make an informed administrator-level change.

## Thermal monitoring

The CPU reading prefers processor package sensors such as `coretemp / Package id 0`, `k10temp / Tctl`, or `Tdie`. It shows the source, label, and sysfs path beside the temperature. Defaults:

- Warning: `85°C`
- Critical: `95°C`

These are application alerts, not replacements for the CPU's own hardware protection.

## Verified updates

Set the stable release manifest URL in Settings:

```text
https://github.com/doctorsus31337/Hermes-Helper/releases/latest/download/update.json
```

The GitHub release workflow creates both `update.json` and the versioned ZIP. Hermes-Helper requires HTTPS, validates the manifest, limits package size, verifies the complete SHA-256 digest, rejects unsafe archive paths and symlinks, and starts an installer that backs up the current application before replacement. An update check never silently installs anything.

## Command-line health check

```bash
hermes-helper --status-json
```

This prints non-secret health state useful for support or scripting.

## Uninstall

```bash
~/.local/share/hermes-helper/uninstall.sh
```

The uninstaller moves the application files into the desktop trash and preserves settings, reports, logs, and update backups.

## Credits

Created for DoctorSUS by **DoctorSUS & ChatGPT**. Built around the open-source [Hermes Agent](https://github.com/NousResearch/hermes-agent) and [Ollama](https://github.com/ollama/ollama) projects.
