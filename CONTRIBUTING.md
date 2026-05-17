# Contributing to icerun

Contributions are welcome. Here's how to get started.

## Setup

```bash
git clone https://github.com/roboalchemist/icerun.git
cd icerun

# Install with all optional extras and dev dependencies
uv sync --all-extras

# Or with pip
pip install -e ".[all]"
pip install pytest pytest-asyncio
```

## Running Tests

```bash
uv run pytest tests/ -v
```

All tests must pass before submitting a PR.

## Code Style

- Standard Python with type hints throughout
- Format with `ruff format` (or `black`)
- Lint with `ruff check`

## Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b my-feature`)
3. Make your changes with tests
4. Run the test suite and ensure everything passes
5. Open a PR against `main`

For significant changes, open an issue first to discuss the approach.
