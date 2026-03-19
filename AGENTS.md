# AGENTS.md - PyMolAI Development Guide

This guide is for coding agents and developers working on PyMolAI, an AI assistant layer integrated into the PyMOL Qt desktop UI.

## 1) Project Overview

PyMolAI extends open-source PyMOL with an integrated AI assistant panel for molecular workflows.

**Key Components:**
- `modules/pymol/ai/` — AI runtime, SDK loops, tool execution, API clients
- `modules/pmg_qt/` — Qt desktop UI components (chat panel, dialogs)
- `modules/pymol/` — Core PyMOL Python API
- `layer0-5/`, `layerGraphics/` — C++ native layers (compiled via setup.py)

**Python Version:** Requires Python >=3.9. Full Claude SDK agent path requires Python >=3.10.

---

## 2) Build Commands

### Install (macOS with uv)

```bash
# Prerequisites
brew install netcdf glew glm

# Create venv and install
uv venv .venv
source .venv/bin/activate
PREFIX_PATH=/opt/homebrew:/opt/homebrew/opt/libxml2:/opt/homebrew/opt/netcdf \
  uv pip install --python .venv/bin/python --reinstall .

# PyQt5 is REQUIRED (PyQt6 has enum incompatibilities)
uv pip install --python .venv/bin/python PyQt5

# Install dev dependencies
uv pip install --python .venv/bin/python -e ".[dev]"
```

### Install (Windows PowerShell)

```powershell
uv venv .venv --python 3.10
.\.venv\Scripts\Activate.ps1
$env:PREFIX_PATH = "C:\path\to\deps"
uv pip install --python .venv\Scripts\python.exe --reinstall .
uv pip install --python .venv\Scripts\python.exe PyQt5
```

### Verify Installation

```bash
python -c "import keyring, openai; print('ok: keyring/openai')"
python -c "import claude_agent_sdk; print('ok: claude-agent-sdk')"
python -c "from PyQt5 import QtWidgets; print('ok: PyQt5')"
```

---

## 3) Test Commands

### Run All Tests

```bash
cd testing
pytest
```

### Run a Single Test File

```bash
cd testing
pytest tests/api/test_ai_runtime.py
```

### Run a Single Test Function

```bash
cd testing
pytest tests/api/test_ai_runtime.py::TestAiRuntime::test_basic_init -v
```

### Run Tests with PyMOL (legacy method)

```bash
pymol -cq testing/runall.pml
```

### Test Configuration

- `testing/pytest.ini` — pytest configuration
- `testing/conftest.py` — root conftest, extends pymol path
- `testing/tests/api/conftest.py` — API test fixtures (auto-reinitialize)

### Test Structure

```
testing/
├── pytest.ini
├── conftest.py
├── testing.py          # PyMOLTestCase base class
├── runall.pml          # Legacy test runner script
└── tests/
    ├── api/            # API tests (test_*.py)
    ├── undo/           # Undo functionality tests
    ├── settings/       # Settings tests
    ├── performance/    # Performance benchmarks
    └── helpers/        # Test utilities
```

---

## 4) Code Style Guidelines

### Imports

```python
from __future__ import annotations  # Always first

# Standard library (alphabetical)
import json
import logging
import os
import re
import threading
from typing import Dict, List, Optional, Tuple

# Third-party
from PyQt5 import QtCore, QtWidgets

# Local imports (relative for same package)
from .message_types import UiEvent, UiRole
from .claude_sdk_loop import ClaudeSdkLoop
```

### Type Annotations

Use modern type hints consistently:

```python
from __future__ import annotations
from typing import Dict, List, Optional, Any, Callable

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default

class AiRuntime:
    def __init__(self, cmd) -> None:
        self._logger: logging.Logger = logging.getLogger("pymol.ai")
        self._plans: List[Dict[str, Any]] = []
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Classes | PascalCase | `AiRuntime`, `ClaudeSdkLoop` |
| Functions | snake_case | `run_pymol_command`, `capture_viewer_snapshot` |
| Private methods | `_leading_underscore` | `_sync_height`, `_import_sdk_symbols` |
| Module-level constants | SCREAMING_SNAKE | `DEFAULT_MODEL`, `SYSTEM_PROMPT_BASE` |
| Internal constants | `_LEADING_SCREAMING` | `_READ_ONLY_PREFIXES`, `_RE_PDB_ID` |

### Error Handling

Use custom exceptions with context:

```python
class ClaudeSdkLoopError(RuntimeError):
    def __init__(self, message: str, *, error_class: str = "sdk_error"):
        super().__init__(message)
        self.error_class = error_class

# Reraise with context
try:
    import claude_agent_sdk
except Exception as exc:
    raise ClaudeSdkLoopError(
        "Claude Agent SDK is unavailable.",
        error_class="sdk_unavailable",
    ) from exc
```

### Logging

Use the `pymol.ai` logger namespace:

```python
self._logger = logging.getLogger("pymol.ai")
self._logger.info("Starting AI turn")
self._logger.error("API call failed: %s", error_msg)
```

### Docstrings

Use triple-quoted docstrings with description:

```python
def capture_viewer_snapshot(cmd, width: int = 800, height: int = 600) -> dict:
    """Capture a PNG snapshot of the current PyMOL viewer.
    
    Args:
        cmd: PyMOL cmd module
        width: Image width in pixels
        height: Image height in pixels
        
    Returns:
        dict with 'image_data' (base64) and 'error' (if any)
    """
```

### Qt/UI Patterns

Use PyQt5 with flat enum access:

```python
from pymol.Qt import QtCore, QtGui, QtWidgets
Qt = QtCore.Qt

# Flat enum style (PyQt5 compatible)
self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
self.setFrameShape(QtWidgets.QFrame.NoFrame)
```

**IMPORTANT:** Never use PyQt6-style namespaced enums like `Qt.ScrollBarPolicy.ScrollBarAlwaysOff`.

---

## 5) Project Structure

```
PyMolAI/
├── modules/
│   ├── pymol/
│   │   ├── ai/              # AI integration (runtime, tools, clients)
│   │   ├── wizard/          # PyMOL wizards
│   │   ├── plugins/         # Plugin system
│   │   └── __init__.py      # PyMOL entry point
│   ├── pmg_qt/              # Qt GUI components
│   │   ├── assistant_chat_panel.py
│   │   ├── ai_api_key_dialog.py
│   │   └── forms/           # .ui files
│   ├── chempy/              # Chemistry utilities
│   └── pymol2/              # PyMOL2 API
├── testing/
│   ├── tests/api/           # API tests
│   ├── pytest.ini
│   └── testing.py           # PyMOLTestCase base
├── layer0-5/                # C++ layers
├── layerGraphics/           # OpenGL graphics layer
├── data/                    # PyMOL data files
├── setup.py                 # Build configuration
├── pyproject.toml           # Package metadata
└── AGENTS.md                # This file
```

---

## 6) API Keys & Environment

### Required for AI Mode

- `OPENROUTER_API_KEY` — Enables AI agent turns
- `ANTHROPIC_AUTH_TOKEN` — Alternative to OpenRouter key

### Optional

- `OPENBIO_API_KEY` — Enables OpenBio gateway tools
- `OPENBIO_BASE_URL` — Override OpenBio API endpoint

### Runtime Toggles

- `PYMOL_AI_DISABLE=1` — Disable AI mode
- `PYMOL_AI_DEFAULT_MODEL` — Set default model
- `PYMOL_AI_LOG_STDOUT` — Log to terminal (default: 1)
- `PYMOL_AI_LOGGER=1` — Use Python logger

### Key Storage

- UI dialogs use `keyring` with system keychain
- Environment variables take precedence when explicitly set
- Never log or commit plaintext API keys

---

## 7) Common Tasks

### Adding a New AI Tool

1. Define tool in `modules/pymol/ai/tool_execution.py`
2. Register in `ClaudeSdkLoop._build_tool_server()`
3. Add tests in `testing/tests/api/test_ai_tool_execution.py`

### Adding a New Test

```python
# In testing/tests/api/test_my_feature.py
import pytest
from pymol import cmd

class TestMyFeature:
    def test_basic_case(self):
        cmd.fragment('ala')
        result = cmd.get_names('objects')
        assert 'ala' in result
```

### Debugging AI Runtime

```bash
# Enable verbose logging
PYMOL_AI_LOG_STDOUT=1 PYMOL_AI_LOGGER=1 pymol

# Check key status
python -c "from pymol.ai.api_key_store import get_key_status; print(get_key_status())"
```

---

## 8) Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `netcdf.h` not found | Missing Homebrew dependency | `brew install netcdf` |
| `GL/glew.h` not found | Missing GLEW | `brew install glew glm` |
| Qt enum AttributeError | PyQt6 installed without PyQt5 | `uv pip install PyQt5` |
| claude_agent_sdk import fails | Python < 3.10 | Use Python 3.10+ |
| AI disabled | No API key | Set OPENROUTER_API_KEY |

---

## 9) Support Boundaries

- **Never** log or commit plaintext API keys
- **Never** hardcode keys into scripts
- Prefer UI save flow and keychain storage
- Use masked key displays (`****abcd`) for status

---

## Attribution

- Upstream PyMOL: Schrodinger, LLC
- PyMolAI fork maintainer: https://proteinlanguagemodel.com/
- X/Twitter: [@ravishar313](https://x.com/ravishar313)
