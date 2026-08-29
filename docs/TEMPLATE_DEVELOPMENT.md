# Template Development Guide

This document describes how to develop and extend the service-template Copier template.

## Architecture Overview

The template uses [Copier](https://copier.readthedocs.io/) for project generation with Jinja2 templating.

### Key Files

```
service-template/
├── copier.yml              # Copier configuration, questions, tasks
├── *.jinja                 # Jinja templates (rendered during copy)
│   ├── Makefile.jinja
│   ├── README.md.jinja
│   ├── AGENTS.md.jinja
│   ├── ARCHITECTURE.md.jinja
│   ├── services.yml.jinja
│   └── .env.example.jinja
├── infra/
│   ├── compose.base.yml.jinja
│   └── compose.dev.yml.jinja
├── services/               # Service directories (conditionally included)
├── tests/copier/           # Template tests
└── docs/
    ├── TEMPLATE_DEVELOPMENT.md  # This file
    └── TESTING.md
```

## Template Variables

Defined in `copier.yml`:

| Variable | Type | Description |
|----------|------|-------------|
| `project_name` | str | Project name (lowercase, hyphens) |
| `project_slug` | str | Computed: `project_name` with underscores |
| `project_description` | str | Short project description |
| `author_name` | str | Author name |
| `author_email` | str | Author email |
| `modules` | str | Comma-separated list of modules |
| `python_version` | str | Python version (3.11 or 3.12) |
| `node_version` | str | Node.js version (for frontend) |

### Module Selection

Users select modules via `modules` parameter:

```bash
copier copy . my-project --data modules=backend,tg_bot
```

Available modules:
- `backend` - FastAPI REST API + PostgreSQL
- `tg_bot` - Telegram bot (FastStream)
- `notifications` - Notification worker
- `frontend` - Node.js frontend

## Conditional Generation

### In Jinja Templates

Use `{% if 'module' in modules %}` for conditional content:

```jinja
{% if 'backend' in modules %}
  backend:
    image: {{ project_slug }}-backend:latest
{% endif %}

{% if 'tg_bot' in modules or 'notifications' in modules %}
  redis:
    image: redis:7-alpine
{% endif %}
```

### Service Directories

Services are excluded before copy via conditional `_exclude` patterns in `copier.yml`:

```yaml
_exclude:
  - "{% if 'tg_bot' not in modules %}services/tg_bot{% endif %}"
  - "{% if 'notifications' not in modules %}services/notifications_worker{% endif %}"
  - "{% if 'frontend' not in modules %}services/frontend{% endif %}"
```

## Update Behavior

### Files Preserved on Update

Defined in `_skip_if_exists`:

```yaml
_skip_if_exists:
  - .env
  - .env.example
  - shared/spec/models.yaml
  - shared/spec/events.yaml
  - "services/*/src/app/**"
  - "services/*/src/controllers/**"
```

### Files Excluded from Copy

Defined in `_exclude`:

- `.git`, `__pycache__`, `.venv`, `node_modules`
- Template development files: `copier.yml`, `docs/COPIER_MIGRATION_PLAN.md`
- Jinja source files (rendered versions are copied)

## Adding a New Module

1. **Add service directory** in `services/<module_name>/`

2. **Update `copier.yml`**:
   - Add to `_exclude` for pre-copy exclusion when not selected
   - Update `modules` help text

3. **Update Jinja templates**:
   - `services.yml.jinja` - add service definition
   - `infra/compose.base.yml.jinja` - add service container
   - `infra/compose.dev.yml.jinja` - add dev overrides
   - `.env.example.jinja` - add required env vars
   - `README.md.jinja` - add to modules table
   - `AGENTS.md.jinja` - add service link
   - `ARCHITECTURE.md.jinja` - add service description

4. **Add tests** in `tests/copier/test_template_generation.py`

## Adding a New Template Variable

1. **Define in `copier.yml`**:

```yaml
my_variable:
  type: str
  help: "Description of the variable"
  default: "default_value"
```

2. **Use in templates** with `{{ my_variable }}`

3. **For computed variables**, use `when: false`:

```yaml
my_computed:
  type: str
  default: "{{ other_var | some_filter }}"
  when: false  # Don't ask, just compute
```

## Testing Changes

Always run template tests after changes:

```bash
# Run all copier tests
make tooling-tests
COMPOSE_PROJECT_NAME=tooling docker compose -f infra/compose.tests.unit.yml \
  run --rm tooling pytest tests/copier/ -v

# Run specific test
COMPOSE_PROJECT_NAME=tooling docker compose -f infra/compose.tests.unit.yml \
  run --rm tooling pytest tests/copier/test_template_generation.py::TestBackendOnlyGeneration -v
```

## Common Patterns

### Whitespace Control

Use `-%}` and `{%-` to control whitespace:

```jinja
{% if 'backend' in modules -%}
content without leading/trailing newlines
{%- endif %}
```

### Service Name Mapping

Module name may differ from service name:
- Module: `notifications` → Service: `notifications_worker`

Ensure consistency across all templates.

### Dependencies

When adding services with dependencies:

```jinja
{% if 'tg_bot' in modules or 'notifications' in modules %}
  redis:
    image: redis:7-alpine
{% endif %}
```

## Debugging

### Test Generation Locally

```bash
# Generate to temp directory
copier copy . /tmp/test-project \
  --data project_name=test \
  --data modules=backend,tg_bot \
  --defaults

# Inspect results
ls -la /tmp/test-project/
cat /tmp/test-project/services.yml
```

### Check for Jinja Artifacts

Generated files should not contain `{{`, `{%`, or `{#`:

```bash
grep -r "{{" /tmp/test-project/ --include="*.yml" --include="*.py"
```

## Release Process

1. Update version in relevant files (if versioning)
2. Run full test suite: `make tests`
3. Test manual generation with all module combinations
4. Tag release (if using git tags for versioning)
