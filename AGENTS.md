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
4. Optionally open `Display -> PyMolAI Settings -> OpenBio API Key...` and save/test key.

## 3) Agent-Safe Setup Scripts

## macOS

```bash
cd /path/to/pymol
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install .
python -c "import keyring, openai; print('ok: keyring/openai')"
python -c "import claude_agent_sdk; print('ok: claude-agent-sdk')"
# Maintainer-tested reinstall form:
source .venv/bin/activate && PREFIX_PATH=/opt/homebrew:/opt/homebrew/opt/libxml2 uv pip install --python .venv/bin/python --reinstall .
```

## Windows (PowerShell)

```powershell
cd C:\path\to\pymol
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install .
python -c "import keyring, openai; print('ok: keyring/openai')"
python -c "import claude_agent_sdk; print('ok: claude-agent-sdk')"
# Optional uv reinstall form:
$env:PREFIX_PATH = "C:\path\to\deps"; uv pip install --python .venv\Scripts\python.exe --reinstall .
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
