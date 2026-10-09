"""Emit the bot's core command registry from the validated host contract."""

from pathlib import Path

from framework.generators.base import BaseGenerator
from framework.host_contract import HostContract, write_registry


class CommandsGenerator(BaseGenerator):
    def __init__(self, specs, repo_root: Path, contract: HostContract) -> None:
        super().__init__(specs, repo_root)
        self.contract = contract

    def generate(self) -> list[Path]:
        written = write_registry(self.repo_root, self.contract)
        return [written] if written is not None else []
