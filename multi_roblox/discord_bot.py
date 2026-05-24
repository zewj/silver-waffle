"""Discord bot for remote control of the manager.

Runs discord.py on a background thread with its own asyncio loop so it
doesn't interfere with the Qt main loop. Commands:

    !instances           list running instances (label, account, pid, status)
    !screenshot [target] capture; target = "all" (default), a label, or an index
    !ping                health check

The bot requires an `allowed_user_ids` allowlist. With an empty allowlist
it refuses every command — without it, anyone in the Discord server
could screenshot the user's accounts.
"""
from __future__ import annotations

import asyncio
import io
import logging
import threading
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)


class BotUnavailable(RuntimeError):
    pass


def is_available() -> bool:
    try:
        import discord  # noqa: F401
        return True
    except ImportError:
        return False


def install_hint() -> str:
    return "Discord bot needs discord.py. Install with: pip install 'discord.py>=2.3'"


class ManagerBot:
    """Wraps a discord.py bot + the background asyncio loop driving it."""

    def __init__(
        self,
        token: str,
        manager,
        allowed_user_ids: Iterable[int] = (),
        screenshot_fn: Optional[Callable[[int], Optional[bytes]]] = None,
    ):
        if not is_available():
            raise BotUnavailable(install_hint())
        if not token:
            raise BotUnavailable("Bot token is empty.")

        self.token = token
        self.manager = manager
        self.allowed_user_ids = {int(x) for x in allowed_user_ids if x}
        self.screenshot_fn = screenshot_fn

        import discord
        from discord.ext import commands
        intents = discord.Intents.default()
        intents.message_content = True  # required to read text command bodies
        self._discord = discord
        self._bot = commands.Bot(command_prefix="!", intents=intents,
                                 help_command=commands.DefaultHelpCommand())
        self._register_commands()

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = threading.Event()
        self._start_error: Optional[BaseException] = None

    # ---- command registration ------------------------------------------

    def _register_commands(self):
        bot = self._bot

        @bot.event
        async def on_ready():
            log.info("discord bot logged in as %s (id=%s)", bot.user, bot.user.id)

        @bot.event
        async def on_command_error(ctx, error):
            # Don't spam the channel with traceback embeds; log and reply briefly.
            log.warning("command error in %s: %s", ctx.invoked_with, error)
            try:
                await ctx.reply(f"Error: {error}", mention_author=False)
            except Exception:
                pass

        @bot.command(name="ping", help="Health check; replies with pong.")
        async def cmd_ping(ctx):
            if not self._authorized(ctx):
                return
            await ctx.reply("pong", mention_author=False)

        @bot.command(name="instances", aliases=["list", "ls"],
                     help="List all running Roblox instances.")
        async def cmd_instances(ctx):
            if not self._authorized(ctx):
                return
            if not self.manager.instances:
                await ctx.reply("No instances running.", mention_author=False)
                return
            lines = ["**Running instances:**"]
            for idx, inst in enumerate(self.manager.instances):
                account = inst.account.label() if inst.account else "(signed-in)"
                afk = "🟢" if inst.antiafk_on else "⚪"
                lines.append(
                    f"`{idx}` **{inst.label}** · {account} · "
                    f"`{inst.status}` · pid={inst.pid or '—'} · AFK {afk}"
                )
            await ctx.reply("\n".join(lines), mention_author=False)

        @bot.command(name="screenshot", aliases=["ss", "pic"],
                     help="Capture one or all instance windows. "
                          "Usage: !screenshot [all | <label> | <index>]")
        async def cmd_screenshot(ctx, *, target: str = "all"):
            if not self._authorized(ctx):
                return
            if self.screenshot_fn is None:
                await ctx.reply("Screenshot capture isn't configured.",
                                mention_author=False)
                return

            targets = self._resolve_targets(target)
            if not targets:
                await ctx.reply(f"No instances match `{target}`.",
                                mention_author=False)
                return

            await ctx.typing()
            files = []
            skipped = []
            for inst in targets:
                if not inst.hwnd:
                    skipped.append(f"{inst.label} (no window)")
                    continue
                # Run the capture in a thread so the asyncio loop isn't blocked
                # by the (synchronous) ctypes calls + PNG encode.
                png = await asyncio.to_thread(self.screenshot_fn, inst.hwnd)
                if png:
                    files.append(self._discord.File(
                        io.BytesIO(png),
                        filename=f"{_safe(inst.label)}.png",
                    ))
                else:
                    skipped.append(f"{inst.label} (capture failed)")

            if not files:
                msg = "Couldn't capture any screenshots."
                if skipped:
                    msg += " Skipped: " + ", ".join(skipped)
                await ctx.reply(msg, mention_author=False)
                return

            reply = f"Captured {len(files)} screenshot(s)."
            if skipped:
                reply += f"\nSkipped: {', '.join(skipped)}"
            await ctx.reply(reply, files=files, mention_author=False)

    # ---- helpers --------------------------------------------------------

    def _authorized(self, ctx) -> bool:
        if not self.allowed_user_ids:
            log.warning("bot received command but allowed_user_ids is empty; refusing")
            return False
        return ctx.author.id in self.allowed_user_ids

    def _resolve_targets(self, spec: str) -> list:
        spec = (spec or "").strip()
        if not spec or spec.lower() == "all":
            return list(self.manager.instances)
        # By label (case-insensitive, exact).
        for inst in self.manager.instances:
            if inst.label.lower() == spec.lower():
                return [inst]
        # By 0-based index.
        try:
            return [self.manager.instances[int(spec)]]
        except (ValueError, IndexError):
            return []

    # ---- lifecycle ------------------------------------------------------

    def start(self, ready_timeout: float = 15.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="discord-bot")
        self._thread.start()
        # Wait briefly to surface obvious failures (bad token, no network)
        # before the dialog returns success to the user.
        self._started.wait(timeout=ready_timeout)
        if self._start_error is not None:
            err = self._start_error
            self._start_error = None
            raise err

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def runner():
            try:
                async with self._bot:
                    # Mark started once we connect; on_ready may not have
                    # fired yet but the login itself succeeded.
                    asyncio.get_event_loop().call_soon(self._started.set)
                    await self._bot.start(self.token)
            except Exception as e:
                log.exception("discord bot stopped with error")
                self._start_error = e
                self._started.set()

        try:
            self._loop.run_until_complete(runner())
        except Exception:
            log.exception("discord bot loop crashed")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 5.0) -> None:
        if not self.running or self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop)
            fut.result(timeout=timeout)
        except Exception:
            log.exception("error stopping discord bot")
        if self._thread:
            self._thread.join(timeout=timeout)


def _safe(s: str) -> str:
    """Make a string filename-safe for the screenshot attachment."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s) or "instance"
