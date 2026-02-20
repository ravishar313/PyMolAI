# AGENTS.md - PyMolAI Setup Runbook for Claude Code, Codex, and Cursor

This guide is for coding agents and assistant workflows that need to install and configure PyMolAI for end users.

## 1) What You Are Setting Up

PyMolAI is an AI assistant layer integrated into the PyMOL Qt desktop UI.

Core points:
- OpenRouter key enables AI agent turns.
- OpenBio key is optional and only enables OpenBio gateway tools.
- Internal PyMOL tools (`run_pymol_command`, `capture_viewer_snapshot`) are always available.

## 2) Minimal End-User Setup Flow

1. Install project in a virtual environment.
2. Launch PyMOL GUI.
3. Open `Display -> PyMolAI Settings -> OpenRouter API Key...` and save/test key.
4. For OpenBio access, sign up at https://openbio.tech/ and create an OpenBio API key.
5. Optionally open `Display -> PyMolAI Settings -> OpenBio API Key...` and save/test key.

## 3) Agent-Safe Setup Scripts

### macOS prerequisites (Homebrew)

The C++ extension requires native libraries not bundled in the Python package.
Install them before the pip/uv install step:

```bash
brew install netcdf glew glm
# libxml2, freetype, and libpng are typically already present;
# install them too if the build fails looking for those headers.
```

## macOS

```bash
git clone https://github.com/ravishar313/PyMolAI
cd PyMolAI
# Create venv with uv (Python 3.10+ for full claude-agent-sdk support)
uv venv .venv
source .venv/bin/activate

# Build and install — include netcdf in PREFIX_PATH alongside homebrew paths
PREFIX_PATH=/opt/homebrew:/opt/homebrew/opt/libxml2:/opt/homebrew/opt/netcdf \
  uv pip install --python .venv/bin/python --reinstall .

# PyQt5 must be installed explicitly. PyQt6 is also detected by PyMOL's Qt
# loader but has incompatible enum namespacing with this codebase.
uv pip install --python .venv/bin/python PyQt5

# Verify
.venv/bin/python -c "import keyring, openai; print('ok: keyring/openai')"
.venv/bin/python -c "import claude_agent_sdk; print('ok: claude-agent-sdk')"
.venv/bin/python -c "from PyQt5 import QtWidgets; print('ok: PyQt5')"
```

## Windows (PowerShell)

```powershell
git clone https://github.com/ravishar313/PyMolAI
cd PyMolAI
uv venv .venv --python 3.10
.\.venv\Scripts\Activate.ps1
$env:PREFIX_PATH = "C:\path\to\deps"
uv pip install --python .venv\Scripts\python.exe --reinstall .
uv pip install --python .venv\Scripts\python.exe PyQt5
python -c "import keyring, openai; print('ok: keyring/openai')"
python -c "import claude_agent_sdk; print('ok: claude-agent-sdk')"
python -c "from PyQt5 import QtWidgets; print('ok: PyQt5')"
```

## 4) Provider/Key Behavior

Environment variables:
- `OPENROUTER_API_KEY`
- `ANTHROPIC_AUTH_TOKEN`
- `OPENBIO_API_KEY`
- `OPENBIO_BASE_URL`
- `PYMOL_AI_OPENROUTER_KEY_SOURCE`
- `PYMOL_AI_OPENBIO_KEY_SOURCE`

Behavior contract:
- Without OpenRouter key (or Anthropic auth token mapping), AI mode is disabled.
- Without OpenBio key, OpenBio tools are not registered, but the app still works.

Key storage:
- UI save uses `keyring` and system keychain.
- Runtime can load saved keys into process environment at startup.
- Env key takes precedence when explicitly set.

## 5) Architecture (High-Level)

- Runtime bootstraps keys from env/keyring.
- Claude SDK loop maps OpenRouter env and builds tool server.
- Tool registration:
  - Always: `run_pymol_command`, `capture_viewer_snapshot`
  - Conditional: `openbio_api_*` gateway tools only when OpenBio key is present
- OpenBio API base defaults to `https://api.openbio.tech` unless overridden by `OPENBIO_BASE_URL`.

## 6) Troubleshooting Decision Tree

## A) "AI disabled" in runtime
- Check `OPENROUTER_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) exists.
- Check `PYMOL_AI_DISABLE` is not `1`.
- Re-test via OpenRouter key dialog.

## B) Missing Claude SDK functionality
- Ensure Python is 3.10+.
- Verify `claude-agent-sdk` import succeeds.

## C) Key validation fails in dialogs
- Confirm provider/key pairing (OpenRouter vs OpenBio keys are not interchangeable).
- Check network/proxy restrictions.
- Retry with known-good key.

## D) OpenBio errors or blocked calls
- Confirm `OPENBIO_API_KEY` is set.
- Confirm base URL (`OPENBIO_BASE_URL`, if overridden).
- Check firewall/proxy/edge restrictions for `api.openbio.tech`.

## E) Keychain unavailable
- Install/configure OS keychain backend for `keyring`.
- If unavailable, use env vars as temporary fallback.

## F) macOS build fails: `netcdf.h` not found
- Run: `brew install netcdf`
- Add `/opt/homebrew/opt/netcdf` to `PREFIX_PATH` in the install command.

## G) macOS build fails: `GL/glew.h` not found
- Run: `brew install glew glm`
- `/opt/homebrew` in `PREFIX_PATH` covers these; ensure it is present.

## H) PyMOL crashes on launch: `AttributeError: type object 'Qt' has no attribute '...'`
- Cause: PyQt6 is installed but PyQt5 is not. PyMOL falls back to PyQt6 when PyQt5
  is absent, but the chat UI code uses PyQt5-style flat enum access which is
  incompatible with PyQt6's namespaced enums.
- Fix: `uv pip install --python .venv/bin/python PyQt5`
- PyMOL's Qt loader tries PyQt5 first; once installed it will be selected automatically.

## 7) Support Boundaries and Safety

- Never log or commit plaintext API keys.
- Never hardcode keys into scripts.
- Prefer UI save flow and keychain storage for end users.
- Use masked key displays for status messaging.

## 8) Quick Diagnostics

Check Python and AI deps:

```bash
python -c "import sys; print(sys.version)"
python -c "import keyring, openai; print('deps ok')"
python -c "import claude_agent_sdk; print('claude sdk ok')"
```

Check environment visibility:

```bash
python - <<'PY'
import os
for k in [
    'OPENROUTER_API_KEY',
    'ANTHROPIC_AUTH_TOKEN',
    'OPENBIO_API_KEY',
    'OPENBIO_BASE_URL',
    'PYMOL_AI_OPENROUTER_KEY_SOURCE',
    'PYMOL_AI_OPENBIO_KEY_SOURCE',
]:
    v = os.getenv(k)
    print(k, 'set' if v else 'unset')
PY
```

Expected outcomes:
- OpenRouter key set -> AI can run.
- OpenBio key set -> OpenBio gateway tools become available.
- OpenBio key unset -> only OpenBio tools are unavailable; core behavior remains.

## Attribution

- Upstream open-source PyMOL rights and trademark notices remain with Schrodinger, LLC.
- PyMolAI fork-specific integration/packaging documentation is maintained in this fork with explicit attribution in `LICENSE`, `AUTHORS`, and `DEVELOPERS`.
- Maintainer contact:
  - Website: https://proteinlanguagemodel.com/
  - X/Twitter: https://x.com/ravishar313
