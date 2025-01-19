import asyncio
import collections
import inspect
import sys
from typing import Callable, override
import os

import discord
from discord.ext import commands

from . import config, serial, RestartProcess, getLogger, COMMAND_FAILED_EMOJI, COMMAND_HANDLED_EMOJI
from .serial import SerialError, SerialGuard

logger = getLogger(__name__)

class CustomHelp(commands.DefaultHelpCommand):
	def __init__(self):
		super().__init__(
			show_parameter_descriptions=True,
			no_category="misc",
		)

	# fix command signatures being mutually exclusive with parameter descriptions
	@override
	def get_command_signature(self, command):
		return commands.HelpCommand.get_command_signature(self, command)

	@override
	def add_command_arguments(self, command, /) -> None:
		# stolen (and lightly modified) from https://github.com/Rapptz/discord.py/blob/v2.4.0/discord/ext/commands/help.py#L1150

		arguments = command.clean_params.values()
		if not arguments:
			return
		if all(p.description is None for p in arguments):
			return

		self.paginator.add_line(self.arguments_heading)
		max_size = self.get_max_size(arguments)

		get_width = discord.utils._string_width
		for argument in arguments:
			if argument.description is None:
				continue

			name = argument.displayed_name or argument.name
			width = max_size - (get_width(name) - len(name))
			indent = f'{self.indent * " "}'
			entry = f'{indent}{name:<{width}} - {argument.description or self.default_argument_description}'
			multiline = "\n" in entry
			if argument.displayed_default is not None:
				sep = multiline and "\n" or " "
				entry += f'{sep}(default: {argument.displayed_default})'
			entry = entry.replace("\n", f"\n{indent}  ")

			for line in entry.split("\n"):
				self.paginator.add_line(line)


class Sc230Bot(commands.Bot):
	def __init__(self):
		intents = discord.Intents.default()
		intents.message_content = True
		super().__init__(
			intents=intents,
			command_prefix=None, # set after config is parsed
			help_command=CustomHelp()
		)
		self.help_command.command_attrs["help"] = "helps u bro"
		logger.debug(f"{self.help_command.command_attrs=}")

	async def get_context(self, msg):
		return await super().get_context(msg, cls=Sc230Context)

	async def setup_hook(self):
		if "--auto-restart" in sys.argv:
			from pathlib import Path
			from inotify_simple import INotify, flags

			root = Path(__file__).parent
			assert root.is_dir()
			dirs = []
			queue = collections.deque([root])
			while queue:
				head = queue.popleft()
				dirs.append(head)
				for dir in head.iterdir():
					if dir.name == "__pycache__":
						continue
					if dir.is_dir():
						queue.append(dir)
			pwd = os.path.abspath(".")
			dirs = [dir.relative_to(pwd) for dir in dirs]

			inotify = INotify()
			for dir in dirs:
				logger.debug(f"auto restart watching `{dir}`")
				inotify.add_watch(dir, flags.CLOSE_WRITE)

			def on_readable():
				file = inotify.read()[0].name
				logger.info(f"{file} modified, restarting")
				raise SystemExit
			self.loop.add_reader(inotify, on_readable)

class Sc230Context(commands.Context[Sc230Bot]):
	async def send_raw(self, lines: list[str]):
		enforce_is_admin(self.message.author)
		async with SerialGuard(self.message):
			responses = [f"```{await serial.send_raw(line)}```" for line in lines]
			await self.reply("\n".join(responses))

	async def send_keys(self, keys: str):
		async with SerialGuard(self.message):
			keys = keys.replace("\n", "").replace(" ", "").upper()
			await serial.send_keys(keys.encode("ascii"))

class StatusGuard:
	def __init__(
			self,
			ctx: Sc230Context,
			format: Callable[[], dict[str, str]],
			interval: float = 1,
			reply: bool = True,
	):
		self.ctx = ctx
		self.format = format
		self.interval = interval
		self.reply = reply
		self.task = None
		self.statusMsg: discord.Message = None

	async def __aenter__(self):
		self.task = asyncio.create_task(self.task_func())

	async def __aexit__(self, *_):
		if self.task:
			self.task.cancel()
			try:
				await self.task
			except asyncio.CancelledError:
				pass
			except:
				logger.exception("StatusGuard task failed")

	def get_format(self, last = False):
		kwargs = {}
		if last and "last" in inspect.getfullargspec(self.format).kwonlyargs:
			kwargs["last"] = True
		return self.format(**kwargs)

	async def task_func(self):
		kwargs = self.get_format()
		kwargs.pop("delete_after", None)
		self.statusMsg = await self.ctx.send(**kwargs, reference=self.reply and self.ctx.message or None)
		try:
			while True:
				await asyncio.sleep(self.interval)
				kwargs = self.get_format()
				kwargs.pop("delete_after", None)
				await self.statusMsg.edit(**kwargs)
		finally:
			kwargs = self.get_format(last=True)
			if "delete_after" in kwargs:
				await self.statusMsg.edit(**kwargs)
			else:
				await self.statusMsg.delete()

class CommandError(Exception):
	def __init__(self, msg: str, **kwargs):
		self.msg = msg
		self.__dict__.update(kwargs)

class BadSubcommandError(Exception):
	def __init__(self, ctx: Sc230Context):
		if ctx.view.eof:
			self.msg = "no subcommand given"
		else:
			ctx.view.skip_ws()
			name = ctx.view.get_word()
			self.msg = f"unknown subcommand `{name}`"

class CommandHandled(Exception):
	pass

def is_admin(user: discord.User):
	return user.id in config.get("admin_ids")

def enforce_is_admin(user: discord.User):
	if not is_admin(user):
		raise CommandError("you do not have permission")

bot: Sc230Bot = Sc230Bot()

@bot.event
async def on_ready():
	logger.info("ready")
	for channel in config.get("control_channels"):
		channel = bot.get_channel(channel)
		if not channel: continue
		await channel.send("scanner bot ready", delete_after=5.0)

@bot.event
async def on_command_completion(ctx: Sc230Context):
	await ctx.message.add_reaction(COMMAND_HANDLED_EMOJI)

@bot.event
async def on_command_error(ctx: Sc230Context, err: BaseException):
	def usage():
		signature = ctx.command.signature
		if signature:
			signature = f" {signature}"
		return f"\nUsage: `{ctx.clean_prefix}{ctx.command.name}{signature}`"
	match err:
		case commands.CommandNotFound():
			ctx.view.index = 0
			ctx.view.skip_string(ctx.prefix)
			cmdName = ctx.view.get_word()
			await ctx.reply(f"Command `{cmdName}` does not exist")
		case commands.MissingRequiredArgument():
			await ctx.reply(f"Missing required argument: {err.param.displayed_name or err.param.name}{usage()}")
		case commands.TooManyArguments():
			await ctx.reply(f"Too many arguments{usage()}")
		case commands.BadArgument():
			cause = ""
			if err.__cause__ is not None:
				cause = f" caused by `{type(err.__cause__).__name__}`: {str(err.__cause__)}"
			await ctx.reply(f"{err}{cause}{usage()}")
		case commands.CheckFailure():
			logger.exception("check failure", exc_info=err)
			await ctx.reply(str(err))
		case commands.CommandInvokeError():
			err = err.original
			match err:
				case CommandError():
					await ctx.reply(err.msg)
				case SerialError():
					await ctx.reply(f":boom: serial error: {err.msg} :boom:")
				case CommandHandled():
					return
				case RestartProcess():
					raise err
				case BadSubcommandError():
					subcommands = {
						ctx.command.all_commands.get(name)
						for name in ctx.command.all_commands.keys()
					}
					subcommands = {cmd.name:cmd for cmd in subcommands}
					subcommands = [
						[name] + cmd.aliases
						for (name, cmd) in subcommands.items()
					]
					subcommands = ["/".join(names) for names in subcommands]
					subcommands.sort()
					subcommands = "\n".join(f"* {name}" for name in subcommands)
					await ctx.reply(f"{err.msg}, expected one of:\n```\n{subcommands}\n```")
				case _:
					logger.exception("unhandled command invoke error", exc_info=err)
					cause = ""
					if err.__cause__ is not None:
						cause = f" caused by `{type(err.__cause__).__name__}`: {str(err.__cause__)}"
					await ctx.reply(f":boom: unexpected `{type(err).__name__}`{cause} :boom:")
		case commands.CommandError():
			logger.exception("unhandled command error", exc_info=err)
			cause = ""
			if err.__cause__ is not None:
				cause = f" caused by `{type(err.__cause__).__name__}`: {str(err.__cause__)}"
			await ctx.reply(f":boom: unexpected `{type(err).__name__}`{cause} :boom:")
		case _:
			logger.exception("very unhandled command error", exc_info=err)
			await ctx.reply(f":boom: (very) unexpected `{type(err).__name__}` :boom:")
	await ctx.message.add_reaction(COMMAND_FAILED_EMOJI)

@bot.event
async def on_message(msg: discord.Message):
	if msg.author.id == bot.user.id:
		return

	prefixes = await bot.get_prefix(msg)
	if type(prefixes) is not list:
		prefixes = []
	prefixed = any(msg.content.startswith(prefix) for prefix in prefixes)

	if msg.guild is None:
		if prefixed:
			await msg.reply("I do not work in DMs")
		return

	if msg.channel.id not in config.get("control_channels"):
		if not prefixed:
			return

		enabledChannels = []
		for id in config.get("control_channels"):
			channel = bot.get_channel(id)
			if not channel or channel.guild.id != msg.guild.id:
				continue
			enabledChannels.append(channel.mention)
		enabledChannels = ", ".join(enabledChannels)
		if not enabledChannels:
			await msg.reply(f"I am not configured to accept commands in this guild")
		else:
			await msg.reply(f"I only accept commands in {enabledChannels}")
		return

	if prefixed:
		await bot.process_commands(msg)
	else:
		from .commands import input
		ctx = await bot.get_context(msg)
		if input.isRawInputUser(msg.author.id):
			await ctx.send_raw(msg.content.split("\n"))
			return
		if input.isKeyInputUser(msg.author.id):
			await ctx.send_keys(msg.content)
			await ctx.message.add_reaction(COMMAND_HANDLED_EMOJI)
