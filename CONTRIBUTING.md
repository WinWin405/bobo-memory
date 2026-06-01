# Contributing to bobo-memory

Thank you for your interest in [bobo-memory](https://github.com/WinWin405/bobo-memory)!

## Ways to contribute

- **Bug reports** — open an [issue](https://github.com/WinWin405/bobo-memory/issues) with steps to reproduce, expected vs actual behavior, and your environment (OS, Python version).
- **Feature ideas** — describe the use case and how it fits the project philosophy (file-based memory, BYO LLM, no mandatory vector DB).
- **Pull requests** — keep changes focused; match existing code style; include tests when behavior changes.

## Development setup

```bash
git clone https://github.com/WinWin405/bobo-memory.git
cd bobo-memory
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[viewer]"
pytest bobo_memory/tests/
```

## Pull request checklist

- [ ] Tests pass: `pytest bobo_memory/tests/`
- [ ] No secrets or local `.bobo/` runtime data in the commit
- [ ] README / docs updated if user-facing behavior changed

## Documentation

Detailed guides (Chinese) live under `docs/`:

- [TUTORIAL_zh.md](docs/TUTORIAL_zh.md)
- [INTEGRATION_zh.md](docs/INTEGRATION_zh.md)
- [AGENT_BRIEF_zh.md](docs/AGENT_BRIEF_zh.md)

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
