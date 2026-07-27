# AGENTS — Backend API

## Location

- Код, тесты и зависимости живут в `services/backend`. Не разносите `Dockerfile`, `src/` или `tests/` по другим каталогам.
- Любые команды запускайте через `make` и docker-compose (см. `CONTRIBUTING.md`). Локальный Python не используем.

## Import Rules

**PYTHONPATH** в Docker: `/app`

Используйте **fully qualified absolute imports** от корня проекта:

```python
# Внутри backend — fully qualified
from services.backend.src.controllers.users import UsersController
from services.backend.src.core.db import get_async_db
from services.backend.src.generated.protocols import UsersControllerProtocol

# Shared-пакет
from shared.generated.schemas import UserCreate, UserRead
from shared.generated.events import get_broker
```

**Исключение** — relative imports допустимы внутри одного пакета (например, `src/app/api/`):
```python
# В src/app/api/router.py
from .routers.users import router as users_router
```

**Запрещено:**
```python
# НЕ ДЕЛАЙТЕ ТАК:
from src.controllers.users import ...        # src — не пакет верхнего уровня
from backend.src.controllers import ...      # нет такого пакета
from .controllers.users import ...           # relative import через границу пакета
```

## Spec-First Workflow

Backend использует spec-first подход: модели и протоколы генерируются из YAML-спецификаций.

**Расположение спеков:**
- `shared/spec/models.yaml` — общие Pydantic-модели (схемы данных)
- `services/backend/spec/*.yaml` — domain operations (REST endpoints + events)

**Workflow:**
1. Отредактируйте спек (`shared/spec/models.yaml` или `services/backend/spec/<domain>.yaml`)
2. Запустите `make validate-specs` (проверяет YAML до генерации)
3. Запустите `make generate-from-spec`
4. Сгенерированные протоколы появятся в `services/backend/src/generated/protocols.py`
5. Реализуйте методы в `services/backend/src/controllers/<domain>.py`

**Правило:** Никогда не создавайте `BaseModel` вручную — схемы генерируются из `models.yaml`. Роутеры (`APIRouter`) пишутся вручную (см. раздел «Роутеры» ниже).

**Привилегии не приходят из API.** Бэкенд публикуется на хостовый порт и не требует
аутентификации, поэтому поле, дающее права, не должно попадать во входные варианты модели. У
`User` это `is_admin`: он исключён из `Create` и `Update` через `exclude`, остаётся в `Read` и
выставляется вне API. Добавляя признак привилегии новой сущности, исключайте его так же — иначе
любой, кто дотянется до API, выпишет его себе.

**Чеклист для новой CRUD-сущности:**
1. Добавьте модели в `shared/spec/models.yaml`: `EntityCreate`, `EntityUpdate`, `EntityRead`.
2. Добавьте domain spec в `services/backend/spec/<domain>.yaml` с list/get/create/update/delete.
3. Запустите `make generate-from-spec` и проверьте протоколы в `src/generated/protocols.py`.
4. Добавьте ORM-модель в `src/app/models/` и экспортируйте её из пакета models.
5. Добавьте repository в `src/app/repositories/` и экспортируйте его из пакета repositories.
6. Реализуйте controller в `src/controllers/<domain>.py`.
7. Добавьте router в `src/app/api/routers/<domain>.py` и подключите его в `src/app/api/router.py`.
8. Добавьте unit/API-тесты для happy path и основных ошибок.
9. Запустите `make makemigrations name="add_<domain>"` после изменения ORM-моделей.

## Directory Structure

```
services/backend/
├── spec/              # YAML спецификации (Source of Truth)
├── src/
│   ├── app/           # Бизнес-логика: repositories, models, lifespan
│   ├── controllers/   # Реализации протоколов (ваш код)
│   └── generated/     # НЕ РЕДАКТИРОВАТЬ: protocols.py, routers
├── migrations/        # Alembic миграции
└── tests/
```

> **Про `shared/shared/`**: двойная вложенность — стандартная Python packaging convention. `shared/` — корень проекта (содержит `pyproject.toml`), а `shared/shared/` — импортируемый пакет (`import shared`). Это как `requests/requests/` или `flask/flask/`. Импорты всегда `from shared.generated.schemas import ...`.

## Роутеры

Роутеры пишутся вручную в `src/app/api/routers/<domain>.py` и подключаются в `src/app/api/router.py`.

**Пример спека с list-операцией** (`spec/todos.yaml`):
```yaml
domain: todos
config:
  rest:
    prefix: "/todos"
    tags: ["todos"]

operations:
  list_todos:
    output: list[TodoRead]       # list[Model] для коллекций
    rest:
      method: GET
      path: ""

  create_todo:
    input: TodoCreate
    output: TodoRead
    rest:
      method: POST
      path: ""
      status: 201

  update_todo:
    input: TodoUpdate            # PATCH для partial updates
    output: TodoRead
    params:
      - name: todo_id
        type: int
    rest:
      method: PATCH
      path: "/{todo_id}"

  get_todo:
    output: TodoRead
    params:
      - name: todo_id
        type: int
    rest:
      method: GET
      path: "/{todo_id}"

  delete_todo:
    output: TodoRead
    params:
      - name: todo_id
        type: int
    rest:
      method: DELETE
      path: "/{todo_id}"
```

**Пример роутера** (`src/app/api/routers/todos.py`):
```python
"""Router for todos."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from services.backend.src.controllers.todos import TodosController
from services.backend.src.core.db import get_async_db
from services.backend.src.generated.protocols import TodosControllerProtocol
from shared.generated.schemas import TodoCreate, TodoRead, TodoUpdate

router = APIRouter(prefix="/todos", tags=["todos"])


def get_controller() -> TodosControllerProtocol:
    return TodosController()


@router.get("", response_model=list[TodoRead])
async def list_todos(
    session: AsyncSession = Depends(get_async_db),  # noqa: B008
    controller: TodosControllerProtocol = Depends(get_controller),  # noqa: B008
) -> list[TodoRead]:
    return await controller.list_todos(session=session)


@router.post("", response_model=TodoRead, status_code=201)
async def create_todo(
    payload: TodoCreate = Body(...),  # noqa: B008
    session: AsyncSession = Depends(get_async_db),  # noqa: B008
    controller: TodosControllerProtocol = Depends(get_controller),  # noqa: B008
) -> TodoRead:
    return await controller.create_todo(session=session, payload=payload)


@router.patch("/{todo_id}", response_model=TodoRead)
async def update_todo(
    todo_id: int = Path(...),  # noqa: B008
    payload: TodoUpdate = Body(...),  # noqa: B008
    session: AsyncSession = Depends(get_async_db),  # noqa: B008
    controller: TodosControllerProtocol = Depends(get_controller),  # noqa: B008
) -> TodoRead:
    return await controller.update_todo(session=session, todo_id=todo_id, payload=payload)


@router.get("/{todo_id}", response_model=TodoRead)
async def get_todo(
    todo_id: int = Path(...),  # noqa: B008
    session: AsyncSession = Depends(get_async_db),  # noqa: B008
    controller: TodosControllerProtocol = Depends(get_controller),  # noqa: B008
) -> TodoRead:
    return await controller.get_todo(session=session, todo_id=todo_id)


@router.delete("/{todo_id}", response_model=TodoRead)
async def delete_todo(
    todo_id: int = Path(...),  # noqa: B008
    session: AsyncSession = Depends(get_async_db),  # noqa: B008
    controller: TodosControllerProtocol = Depends(get_controller),  # noqa: B008
) -> TodoRead:
    return await controller.delete_todo(session=session, todo_id=todo_id)
```

**Подключение в `src/app/api/router.py`:**
```python
from .routers.todos import router as todos_router

api_router.include_router(todos_router)
```

## Database & Migrations

- Миграции в `services/backend/migrations/versions/`.
- Применить миграции: `make migrate` или `make dev-start`.
- Создать новую миграцию: `make makemigrations name="describe_change"`.
- `make migrate` и `make makemigrations` запускают Alembic в одноразовом `backend`-контейнере
  через dev compose overlay. Docker должен быть доступен; target сам поднимает и ждёт `db`.
- `make makemigrations` внутри target сначала делает `alembic upgrade head`, потом
  `revision --autogenerate`.
- Без Docker: поднимите БД снаружи, выполните `uv sync --project services/backend`, затем запускайте
  те же target'ы с `SKIP_INFRA_START=1` и доступным адресом БД:
  `make SKIP_INFRA_START=1 POSTGRES_HOST=localhost POSTGRES_PORT=5432 makemigrations name="..."`.
  Если из текущего окружения резолвится `db`, достаточно `make SKIP_INFRA_START=1 makemigrations name="..."`.
  `DATABASE_URL=...` тоже можно передать make-переменной.
- Workflow: `make makemigrations name="..."` → `make migrate`.

## Event Publishing

Backend подключается к Redis через lazy broker в lifespan:

```python
from shared.generated.events import get_broker

@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = get_broker()
    await broker.connect()
    yield
    await broker.close()
```

Публикация событий из контроллеров:
```python
from shared.generated.events import publish_user_registered
await publish_user_registered(event)
```

## Key Commands

| Команда | Назначение |
|---------|-----------|
| `make generate-from-spec` | Перегенерировать код из спеков |
| `make validate-specs` | Проверить YAML спеки |
| `make lint-specs` | Проверить соответствие спекам |
| `make lint-controllers` | Проверить синхронизацию контроллеров с протоколами |
| `make migrate` | Применить миграции через Docker или локальный Alembic при `SKIP_INFRA_START=1` |
| `make makemigrations name="..."` | Создать Alembic миграцию через Docker или локальный Alembic при `SKIP_INFRA_START=1` |
| `make openapi` | Экспорт OpenAPI JSON |
| `make tests backend` | Запустить тесты backend |
