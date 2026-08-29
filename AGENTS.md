# Agents Playbook / Documentation Map

This file serves as the entry point for AI Agents exploring the repository. Use this map to load only the context you need.

## Navigation

- **Philosophy & Goals:** `docs/MANIFESTO.md` (Read this first to understand *why*)
- **System Design:** `docs/ARCHITECTURE.md` (Read this to understand *how*)
- **Rules & Standards:** `CONTRIBUTING.md` (Strict rules for coding)
- **Service Registry:** `services.yml` (List of all active services)

## Bootstrapping New Projects

**FOR AI AGENTS:** If you are asked to initialize a new project using this template, you **MUST** follow these exact steps.

### 1. The Command
Run `uvx copier` with the following flags to ensure non-interactive execution and correct module selection. This works in fresh environments where `copier` is not installed as a standalone command.

```bash
uvx copier copy gh:vladmesh/service-template . \
  --data project_name="my-project" \
  --data modules="tg_bot" \
  --defaults \
  --vcs-ref=HEAD \
  --overwrite
```

Copier defaults to the latest git tag for git sources. `--vcs-ref=HEAD` keeps bootstrap output on the current template state instead of an older release tag.

**Key Flags:**
- `--defaults`: **CRITICAL**. Uses default values for non-specified answers, preventing interactive prompts that hang execution.
- `--vcs-ref=HEAD`: Required because Copier otherwise uses the latest git tag for git sources.
- `--overwrite`: Resolves conflicts automatically (essential if the directory is not empty).

### 2. Available Modules
Pass these as a comma-separated string to `--data modules=...`:

- `backend`: (Optional) FastAPI REST API + PostgreSQL.
- `tg_bot`: Telegram Bot service (Note: internal name is `tg_bot`, NOT `telegram_bot` or `telegram_worker`). Can be used as a standalone bot if `backend` is NOT selected.
- `notifications`: Worker for email/telegram notifications.
- `frontend`: Node.js frontend placeholder.

**Example scenarios:**
- "Standalone telegram bot": `--data modules="tg_bot"`
- "Full stack app": `--data modules="backend,frontend"`

### 3. Post-Bootstrap Checklist
After running the command:
1.  **Read `AGENTS.md` in the new project** (it will be different from this one).
2.  **Check `services.yml`** to confirm your services are listed.
3.  **Run `make generate-from-spec`** to regenerate code from specs if needed.

## CRITICAL: Environment Variables

**STRICT RULE: NO DEFAULT VALUES FOR ENVIRONMENT VARIABLES**

- **NEVER** use default values in `os.getenv("VAR", "default")` or similar patterns
- If a required environment variable is missing, the application **MUST FAIL IMMEDIATELY** with a clear error
- Use this pattern:
  ```python
  value = os.getenv("REQUIRED_VAR")
  if not value:
      raise RuntimeError("REQUIRED_VAR is not set; please add it to your environment variables")
  ```
- **Rationale:** Default values hide configuration errors and cause silent failures in production
- **Example:** `REDIS_URL`, `API_BASE_URL`, `DATABASE_URL` must all be explicitly configured
- All required environment variables must be documented in `.env.example`

## Service Modules

Detailed documentation for each service can be found in its respective directory. Only load these if you are working on that specific service.

- **Backend:** `services/backend/AGENTS.md`
- **Telegram Bot:** `services/tg_bot/AGENTS.md`
- **Infrastructure:** `infra/README.md` (if available)

## Operational Commands

Agents should interact with the system primarily through `make`.

- **Verify:** `make lint && make test` (framework unit + tooling); `make test-copier` for the
  generation matrix (slow, CI runs it).
- **Broad check under Secretary (one canonical form):**
  `python3 -m secretary check broad --reuse --module pytest --module-arg tests/unit --module-arg tests/tooling --module-arg tests/copier --module-arg=-m --module-arg "not slow"`
  — `make test` plus the non-slow copier generation tests (~3 min): a change under `template/` is
  only exercised by the copier layer, so the framework suite alone proves nothing about it. Run it
  once, on a **committed** tree (`test_copier_update_on_fresh_project` runs `copier update` from
  this checkout and fails on a dirty one), and quote `check show` with the same arguments; do not wrap make
  targets in `--command`, that receipt is never reusable. `slow` copier tests and the generation
  typecheck matrix stay with GitHub CI.
- **Generate Code:** `make generate-from-spec`
- **Generate OpenAPI:** `make openapi` (Outputs to `services/<service>/docs/openapi.json`)

## Language Agnosticism

When modifying YAML specs or the codegen pipeline, prefer language-neutral abstractions where possible. The framework is designed to eventually support multiple target languages. Details: `docs/rust-migration-analysis.md`, backlog tasks: `docs/backlog.md` (section "Rust Migration Preparation").

**Practical guideline:** Use JSON Schema types in specs (`string`, `integer`, `array`), avoid Python-specific types where a generic approach works.

## Critical Project Knowledge

### Spec-First Architecture

See `ARCHITECTURE.md` for detailed spec format documentation.

**Quick Reference:**
- Domain specs: `services/<svc>/spec/<domain>.yaml` → generates protocols, controllers, event adapters

### Shared Module Architecture

1. **Shared generated:** `shared/shared/generated/` — schemas, events
2. **Service generated:** `services/<svc>/src/generated/` — protocols, event adapters
3. **CRITICAL:** Mount `../shared:/app/shared:delegated` in dev for live reloads
4. **Workflow:** Edit specs → `make generate-from-spec` → changes applied

### FastStream Event Architecture

**Broker Lifecycle — Lazy `get_broker()` Pattern:**

`shared/shared/generated/events.py` exports `get_broker()` — lazy-инициализация брокера. **Не** импортируйте `broker` как атрибут модуля.

1. **Получение брокера:**
   ```python
   from shared.generated.events import get_broker
   broker = get_broker()  # создаёт RedisBroker при первом вызове
   ```
2. **Подключение (FastAPI lifespan):**
   ```python
   from shared.generated.events import get_broker

   @asynccontextmanager
   async def lifespan(app: FastAPI):
       broker = get_broker()
       await broker.connect()
       yield
       await broker.close()
   ```
3. **Публикация событий:** Генерированные функции вызывают `get_broker()` внутри:
   ```python
   from shared.generated.events import publish_command_received
   from shared.generated.schemas import CommandReceived

   event = CommandReceived(...)
   await publish_command_received(event)  # broker должен быть подключён
   ```
4. **Подписка (FastStream workers):** В `python-faststream` сервисах брокер создаётся напрямую в `main()` и передаётся в `create_event_adapter()`:
   ```python
   from faststream import FastStream
   from faststream.redis import RedisBroker
   from .generated.event_adapter import create_event_adapter

   async def main():
       broker = RedisBroker(redis_url)
       create_event_adapter(broker=broker, ...)
       app = FastStream(broker)
       await app.run()
   ```

### Direct Event Publishing Pattern

Сервисы публикуют события напрямую в Redis, НЕ через REST API.

**Пример (Telegram Bot):**
```python
from shared.generated.events import get_broker, publish_command_received
from shared.generated.schemas import CommandReceived

async def post_init(application: Application) -> None:
    await get_broker().connect()

async def post_shutdown(application: Application) -> None:
    await get_broker().close()

# In handler:
event = CommandReceived(command=cmd, args=args, user_id=user_id, timestamp=datetime.now(UTC))
await publish_command_received(event)
```

**Необходимая настройка:**
1. Добавить `shared` как зависимость в `pyproject.toml` сервиса
2. Добавить `redis: service_healthy` в `depends_on` в `services.yml`
3. Обеспечить `REDIS_URL` в окружении

### Service Creation Pattern

1. **Добавить в реестр:** Описать в `services.yml` с нужным типом:
   - `python-fastapi` — HTTP API с FastAPI/uvicorn (порт 8000)
   - `python` — Generic Python service без framework-specific runtime
   - `python-faststream` — Event-driven worker с FastStream (без HTTP)
   - `node` — Node.js сервис (порт 4321)
   - `default` — Generic container placeholder
2. **Опциональные compose-настройки:** `depends_on` и `profiles` в `services.yml`:
   ```yaml
   - name: my_service
     type: python-faststream
     description: My event worker
     depends_on:
       redis: service_healthy
     profiles:
       - workers
   ```
3. **Создать:** Создайте каталог сервиса `services/<name>/` по шаблону из `.framework/framework/templates/scaffold/services/<type>/`
4. **Dev Setup:** Volume mounts настраиваются в `infra/compose.dev.yml`

### Common Pitfalls

1. **Missing Broker Connection:** Публикация событий без `get_broker().connect()` → `AssertionError`. В FastAPI — lifespan, в tg_bot — `post_init`/`post_shutdown`, в FastStream workers — `FastStream(broker).run()`.
2. **Type Mismatches:** Кодогенератор поддерживает `list[type]`, но проверяйте сложные типы
3. **Timezone Awareness:** Используйте `datetime.now(UTC)` для Pydantic `AwareDatetime` полей
4. **Dockerfile Copies:** COPY sources должны оставаться в каталоге сервиса или использовать `shared/`
