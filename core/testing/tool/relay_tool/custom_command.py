"""Customer-provided host commands for power control."""

from core.logging import smart_log
from core.testing.tool.dut_tool.command_batch import CommandExecutionError, CommandRunner

from . import Relay


class CustomCommandRelay(Relay):
    def __init__(self, *, power_on_command: str, power_off_command: str) -> None:
        super().__init__()
        self._commands = {"power_on": power_on_command, "power_off": power_off_command}
        self._runner = CommandRunner()

    def pulse(self, direction: str = "power_off", *, port=None, **kwargs) -> None:
        command = self._commands[direction]
        if not str(command).strip():
            raise ValueError(f"Custom relay command is empty: {direction}")
        result = self._runner.run(command, shell=True)
        smart_log(
            "Custom relay %s exited with %s\nstdout: %s\nstderr: %s",
            direction,
            result.returncode,
            result.stdout,
            result.stderr,
            level="info" if result.returncode == 0 else "error",
        )
        if result.returncode != 0:
            raise CommandExecutionError(f"Custom relay {direction} exited with code {result.returncode}")
