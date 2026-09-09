from __future__ import annotations

import asyncio
import json
import os
import signal
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from ai_usage_monitor.models.schemas import ProviderUsage


class ProviderError(Exception):
    """A deliberately sanitized provider failure safe to persist and expose."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        if code not in {
            "provider_failed",
            "authentication_missing",
            "authentication_invalid",
            "authentication_expired",
            "authentication_scope_missing",
            "cli_unavailable",
            "cli_start_failed",
            "cli_ended",
            "malformed_response",
            "timeout",
            "upstream_error",
        }:
            code = "provider_failed"
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class UsageProvider(ABC):
    name: str

    @abstractmethod
    async def get_usage(self) -> ProviderUsage:
        raise NotImplementedError


Matcher = Callable[[dict[str, Any]], bool]


class JSONLCommandClient:
    """Small JSONL subprocess transport with bounded output and no secret logging."""

    def __init__(self, *, max_line_bytes: int = 1_000_000) -> None:
        self.max_line_bytes = max_line_bytes

    async def request(
        self,
        command: Sequence[str],
        *,
        messages: Sequence[dict[str, Any]],
        matcher: Matcher,
        timeout: float,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env if env is not None else os.environ.copy(),
                cwd=cwd,
                limit=self.max_line_bytes + 1,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as exc:
            raise ProviderError("cli_unavailable") from exc
        except OSError as exc:
            raise ProviderError("cli_start_failed", retryable=True) from exc

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        async def discard(stream: asyncio.StreamReader) -> None:
            while await stream.read(32_768):
                pass

        stderr_task = asyncio.create_task(discard(process.stderr))

        async def exchange() -> dict[str, Any]:
            for message in messages:
                encoded = json.dumps(message, separators=(",", ":")).encode() + b"\n"
                process.stdin.write(encoded)
            await process.stdin.drain()

            total = 0
            while True:
                try:
                    line = await process.stdout.readline()
                except ValueError as exc:
                    raise ProviderError("malformed_response") from exc
                if not line:
                    raise ProviderError("cli_ended", retryable=True)
                total += len(line)
                if len(line) > self.max_line_bytes or total > 16 * self.max_line_bytes:
                    raise ProviderError("malformed_response")
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    continue
                if isinstance(value, dict) and matcher(value):
                    return value

        try:
            return await asyncio.wait_for(exchange(), timeout=timeout)
        except TimeoutError as exc:
            raise ProviderError("timeout", retryable=True) from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ProviderError("cli_ended", retryable=True) from exc
        finally:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()

            def stop(sig: int) -> None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, sig)
                    elif process.returncode is None:
                        process.kill()
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Sandboxed macOS can reject killpg after the group has
                    # exited. If the child is still alive, signal it directly.
                    if process.returncode is None:
                        try:
                            process.send_signal(sig)
                        except ProcessLookupError:
                            pass

            # Drain after matching too: unread pipes otherwise block process.wait().
            stdout_task = asyncio.create_task(discard(process.stdout))
            wait_task = asyncio.create_task(process.wait())
            cleanup = asyncio.gather(
                stdout_task, stderr_task, wait_task, return_exceptions=True
            )
            stop(signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(cleanup), timeout=1)
            except TimeoutError:
                stop(signal.SIGKILL)
                try:
                    await asyncio.wait_for(asyncio.shield(cleanup), timeout=1)
                except TimeoutError:
                    cleanup.cancel()
                    await asyncio.gather(cleanup, return_exceptions=True)
