import asyncio
import sys
from typing import Callable, override
import os

import discord
from discord.ext import commands

from . import config, serial, getLogger, COMMAND_FAILED_EMOJI, COMMAND_HANDLED_EMOJI
from .serial import SerialError, SerialGuard

logger = getLogger(__name__)

class CustomHelp(commands.DefaultHelpCommand):
	def __init__(self):
		super().__init__(
			show_parameter_descriptions=True,
			no_category="Misc",
		)

	# fix command signatures being mutually exclusive with parameter descriptions
	@override
	def get_command_signature(self, command):
		return commands.HelpCommand.get_command_signature(self, command)

class Sc230Bot(commands.Bot):
	def __init__(self):
		intents = discord.Intents.default()
		intents.message_content = True
		super().__init__(
			intents=intents,
			command_prefix=None, # set after config is parsed
			help_command=CustomHelp()
		)

		self.rawInputUsers = set()
		self.keyInputUsers = set()

	async def get_context(self, msg):
		return await super().get_context(msg, cls=Sc230Context)

	async def setup_hook(self):
		if "--auto-restart" in sys.argv:
			from inotify_simple import INotify, flags
			inotify = INotify()
			inotify.add_watch(os.path.dirname(__file__), flags.CLOSE_WRITE)
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
	def __init__(self, msg: discord.Message, format: Callable[[], dict[str, str]], interval: float = 1):
		self.msg = msg
		self.format = format
		self.interval = interval
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

	async def task_func(self):
		kwargs = self.format()
		kwargs.pop("delete_after", None)
		self.statusMsg = await self.msg.reply(**kwargs)
		try:
			while True:
				await asyncio.sleep(self.interval)
				kwargs = self.format()
				kwargs.pop("delete_after", None)
				await self.statusMsg.edit(**kwargs)
		finally:
			kwargs = self.format()
			if "delete_after" in kwargs:
				await self.statusMsg.edit(**kwargs)
			else:
				await self.statusMsg.delete()

class CommandError(Exception):
	def __init__(self, msg: str, **kwargs):
		self.msg = msg
		self.__dict__.update(kwargs)

class CommandHandled(Exception):
	pass

def is_admin(user: discord.User):
	return user.id in config.get("admin_ids")

def enforce_is_admin(user: discord.User):
	if not is_admin(user):
		raise CommandError("you do not have permission")

bot = Sc230Bot()

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
					rest = "" if not err.rest else f"\n{rest=}"
					await ctx.reply(f":boom: serial error: {err.ty} :boom:{rest}")
				case CommandHandled():
					return
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
		ctx = await bot.get_context(msg)
		if msg.author.id in bot.rawInputUsers:
			await ctx.send_raw(msg.content.split("\n"))
			return
		if msg.author.id in bot.keyInputUsers:
			await ctx.send_keys(msg.content)
