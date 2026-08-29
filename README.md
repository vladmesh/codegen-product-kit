# Service Template Framework

A rigid, spec-first, modular framework for building AI-Agent-ready microservices.

> **Start here:** Read the [MANIFESTO](docs/MANIFESTO.md) to understand the philosophy behind this project.

## Quick Start

### Create a New Project

```bash
# Generate a new project
uvx copier copy gh:vladmesh/service-template my-project \
  --data project_name=my-project \
  --defaults \
  --vcs-ref=HEAD

# Or with specific modules (e.g. standalone bot)
uvx copier copy gh:vladmesh/service-template my-project \
  --data project_name=my-project \
  --data modules=tg_bot \
  --defaults \
  --vcs-ref=HEAD
```

Copier uses the latest git tag for git sources by default. For `gh:vladmesh/service-template`,
omitting `--vcs-ref` can silently generate from an older release tag instead of the current
default branch. `--vcs-ref=HEAD` keeps bootstrap output on the latest template state; replace it
with another branch/ref when needed.

### Available Modules

| Module | Description |
|--------|-------------|
| `backend` | FastAPI REST API + PostgreSQL (Optional) |
| `tg_bot` | Telegram bot (FastStream, can be standalone) |
| `notifications` | Email/Telegram notification worker |
| `frontend` | Node.js frontend |

### After Generation

```bash
cd my-project
make setup
cp .env.example .env
make dev-start
```

### Update Infrastructure

Pull latest infrastructure updates while preserving your code:

```bash
uvx copier update --vcs-ref=HEAD
```

## Development Workflow (in Generated Projects)

After generating a project with `copier copy`:

- **Update API:** Edit `shared/spec/*.yaml` → `make generate-from-spec`
- **Run Tests:** `make tests`
- **Lint:** `make lint`

## Framework Development

Developing the framework itself (this repository):

- **Run Tests:** `make test` (framework unit tests) or `make test-copier` (template generation tests)
- **Lint:** `make lint` (framework code only)
- **Sync `.framework/`:** `make sync-framework` (copy framework code to template)

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for detailed framework development instructions.

## Documentation

### For Framework Users (Template Users)
- Generated projects include `README.md`, `AGENTS.md`, and `CONTRIBUTING.md` with usage instructions
- The infrastructure and worker-mode contract is readable before generation at
  [template/infra/README.md](template/infra/README.md). The root
  [infra/README.md](infra/README.md) points to that canonical copy.

### For Framework Developers
- **[docs/MANIFESTO.md](docs/MANIFESTO.md)**: The core philosophy
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: How the framework works
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**: How to develop the framework

## Tech Stack

- **Core:** Python 3.11+, Docker Compose
- **API:** FastAPI, Pydantic (Spec-First)
- **Data:** PostgreSQL, SQLAlchemy, Alembic
- **Messaging:** Redis, FastStream
- **Quality:** Ruff, Mypy, Pytest

## License

Open Source. Use it, fork it, build agents with it.
