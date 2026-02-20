# Third-Party AI Services and Libraries

This repository includes PyMolAI integration that can use third-party services and libraries.

## Python Libraries (declared in `pyproject.toml`)

- `openai` (OpenAI-compatible client usage, including OpenRouter integration)
- `keyring` (OS credential/keychain storage)
- `claude-agent-sdk` (Claude SDK tool loop integration, Python 3.10+)

## External Service Endpoints

- OpenRouter API (model routing)
- OpenBio API (optional gateway tools), default base URL:
  - `https://api.openbio.tech`

## Notes

- Service usage is optional and depends on user-provided API keys.
- OpenRouter key is required for AI mode.
- OpenBio key is optional and only enables OpenBio tool access.
- Project licensing terms remain in the repository `LICENSE` file.
- Upstream PyMOL rights/trademark notices belong to Schrodinger, LLC; fork-specific PyMolAI integration attribution is documented in `LICENSE`, `AUTHORS`, and `DEVELOPERS`.
