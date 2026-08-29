from unittest.mock import MagicMock

from framework.generators.routers import RoutersGenerator
from framework.spec.operations import DomainSpec


class TestDualTransport:
    def test_router_generates_publish_on_success(self, tmp_path):
        op_spec = {
            "input": "UserCreate",
            "output": "UserRead",
            "rest": {"method": "POST", "status": 201},
            "events": {"publish_on_success": "user.created"},
        }

        domain_data = {
            "config": {"rest": {"prefix": "/users", "tags": ["users"]}},
            "operations": {"create_user": op_spec},
        }

        domain = DomainSpec.from_yaml("users", domain_data)
        domain.service_name = "backend"

        specs = MagicMock()
        specs.domains = {"backend/users": domain}

        generator = RoutersGenerator(specs, tmp_path)
        generated_files = generator.generate()

        assert len(generated_files) == 3
        router_file = next(path for path in generated_files if path.name == "users.py")
        content = router_file.read_text()

        assert "from faststream.redis import RedisBroker" in content

        assert "get_broker: Callable[[], RedisBroker]," in content

        assert "broker: RedisBroker = Depends(get_broker)" in content

        assert 'await broker.publish(result, "user.created")' in content
        assert "await session.commit()" not in content

    def test_router_skips_broker_if_no_events(self, tmp_path):
        op_spec = {
            "output": "UserRead",
            "rest": {"method": "GET", "path": "/{user_id}"},
            "params": [{"name": "user_id", "type": "int"}],
        }

        domain_data = {
            "config": {"rest": {"prefix": "/users"}},
            "operations": {"get_user": op_spec},
        }

        domain = DomainSpec.from_yaml("users", domain_data)
        domain.service_name = "backend"

        specs = MagicMock()
        specs.domains = {"backend/users": domain}

        generator = RoutersGenerator(specs, tmp_path)
        generated_files = generator.generate()

        router_file = next(path for path in generated_files if path.name == "users.py")
        content = router_file.read_text()

        assert "from faststream.redis import RedisBroker" not in content
        assert "get_broker" not in content
        assert "broker.publish" not in content
