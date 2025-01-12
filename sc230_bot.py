import asyncio
import collections
from collections.abc import Callable
from enum import Enum
import glob
import itertools
import json
import logging
import os
import sys
import time
import traceback
from typing import Optional, override

import discord
from discord.ext import commands
import serial

LOCKED_EMOJI = "\N{LOCK}"
UNLOCKED_EMOJI = "\N{BLACK RIGHT-POINTING TRIANGLE}\uFE0F"

COMMAND_HANDLED_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
COMMAND_FAILED_EMOJI = "\N{CROSS MARK}"

logger = logging.getLogger(os.path.splitext(os.path.basename(__file__))[0])

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
			inotify.add_watch(__file__, flags.CLOSE_WRITE)
			def on_readable(*args, **kwargs):
				logger.info(f"{os.path.basename(__file__)} modified, restarting")
				raise SystemExit
			self.loop.add_reader(inotify, on_readable)

class Sc230Context(commands.Context[Sc230Bot]):
	async def send_raw(self, lines: list[str]):
		enforce_is_admin(self.message.author)
		async with SerialGuard(self.message):
			responses = [f"```{await serialClient.send_raw(line)}```" for line in lines]
			await self.reply("\n".join(responses))

	async def send_keys(self, keys: str):
		async with SerialGuard(self.message):
			keys = keys.replace("\n", "").replace(" ", "").upper()
			await serialClient.send_keys(keys.encode("ascii"))

class Peekable:
	def __init__(self, iterable):
		self.iter = iter(iterable)
		self.queue = collections.deque()

	def next(self):
		if self.queue:
			return self.queue.popleft()
		else:
			return next(self.iter)

	def peek(self, n = 0):
		if n < len(self.queue):
			return self.queue[n]
		for _ in range(n - len(self.queue) + 1):
			try:
				v = next(self.iter)
				self.queue.append(v)
			except StopIteration:
				return None
		return self.queue[n]

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
	return user.id in config["admin_ids"]

def enforce_is_admin(user: discord.User):
	if not is_admin(user):
		raise CommandError("you do not have permission")

def replace_special_chars(str: str) -> str:
	return str.translate({
		0x10: "\N{UPWARDS ARROW}",
		0x11: "\N{DOWNWARDS ARROW}",
	})

async def walk_ids(head: int, tail: int) -> list[int]:
	assert SerialGuard.lock.locked(), "trying to walk_ids without serial lock held"
	assert ProgramGuard.level > 0, "trying to walk_ids outside of program mode"

	if head == -1:
		return []

	ids = [head]
	while tail != "-1" and head != tail:
		match (await serialClient.send_raw(f"FWD,{head}")).split(","):
			case ["FWD", "-1"]:
				assert False, "forward id is -1???"
			case ["FWD", next]:
				head = int(next)
		ids.append(head)
	return ids

class SerialError(Exception):
	def __init__(self, ty, msg, *rest):
		self.ty = ty
		self.msg = msg
		self.rest = rest

class SerialGuard:
	lock = asyncio.Lock()

	def __init__(self, msg: discord.Message, typing: bool = False):
		self.msg = msg
		self.pendingMsg = None
		self.typing = None
		if typing:
			self.typing = msg.channel.typing()

	async def __aenter__(self):
		if self.lock.locked():
			self.pendingMsg = await self.msg.reply("waiting for other command(s) to finish")
		if self.typing:
			await self.typing.__aenter__()
		await self.lock.acquire()
		logger.debug(f"serial locked for author={self.msg.author.name!r} content={self.msg.content!r}")

	async def __aexit__(self, *_):
		logger.debug("serial unlocked")
		self.lock.release()
		if self.typing:
			await self.typing.__aexit__(*_)
		if self.pendingMsg:
			await self.pendingMsg.delete()

class ProgramGuard:
	level = 0

	@classmethod
	async def __aenter__(self, ):
		self.level += 1
		if self.level > 1:
			return
		await serialClient.send_raw(b"EPG")
		await serialClient.send_raw(b"PRG")

	@classmethod
	async def __aexit__(self, *_):
		self.level -= 1
		if self.level == 0:
			await serialClient.send_raw(b"EPG")

class SerialProtocol:
	allowedKeys = b"MFHSLC1234567890.E><^P"

	def __init__(self):
		self.serial = serial.Serial(
			config["serial_port"],
			baudrate=config["serial_baud"],
			stopbits=1,
			bytesize=8,
			parity=serial.PARITY_NONE,
			xonxoff=False,
			rtscts=False,
			dsrdtr=False,
			timeout=0,
			write_timeout=0,
		)

	def read_line(self) -> bytes:
		res = bytes()
		while True:
			read = self.serial.read(1)
			if len(read) == 0:
				import time
				time.sleep(0.1)
				continue
			res += read
			if read == b"\r":
				break
		logger.debug(f"serial read  {res!r}")
		res = res.strip()
		match res.split(b","):
			case [b"ERR", *rest]:
				raise SerialError("command error", rest)
			case [b"NG", *rest]:
				raise SerialError("invalid state for command", rest)
			case [b"FER", *rest]:
				raise SerialError("framing error", rest)
			case [b"ORER", *rest]:
				raise SerialError("overrun error", rest)
			case _:
				return res

	def write_line(self, line: bytes):
		assert type(line) is bytes, "type error"
		line += b"\r"
		logger.debug(f"serial write {line!r}")
		while True:
			written = self.serial.write(line)
			line = line[written:]
			if len(line) == 0:
				break

	async def send_raw(self, line: str | bytes):
		def inner():
			nonlocal line
			if type(line) is str:
				line = line.encode("ascii")
			self.write_line(line)
			return self.read_line().decode("ascii")
		return await asyncio.to_thread(inner)

	async def send_key(self, key: int | str | bytes, mode = "P"):
		if type(key) in [str, bytes]:
			key = ord(key)
		assert key in self.allowedKeys, f"{chr(key)!r} is not a valid key"
		await self.send_raw(f"KEY,{chr(key)},{mode}")

	async def send_keys(self, keys: str | bytes):
		if type(keys) is str:
			keys = keys.encode("ascii")

		parsed = []
		try:
			it = Peekable(keys)
			while True:
				key = bytes([it.next()])

				holdCount = 0
				peekIndex = 0
				while True:
					match it.peek(peekIndex):
						case c if c and chr(c) == "+":
							holdCount += 1
							peekIndex += 1
						case _:
							break
				if holdCount > 0:
					parsed.append((key, holdCount))
					for _ in range(holdCount):
						it.next()
						pass
				else:
					parsed.append(key)
		except StopIteration:
			pass

		pressed = {}
		try:
			for keyspec in parsed:
				match keyspec:
					case (char, count):
						curCount = pressed.get(char, 0)
						if curCount == 0:
							await self.send_key(char, "H")
						pressed[char] = curCount + count
					case char:
						await self.send_key(char)
						for key, count in pressed.items():
							if count == 1:
								await self.send_key(key, "R")
							if count > 0:
								pressed[key] = count - 1
		except:
			for key in pressed:
				try:
					await self.send_key(key, "R")
				except:
					pass
			raise

	@staticmethod
	def format_channel(id, name, freq, locked = None):
		freq = serialClient.parse_frequency(freq)
		if name.endswith("MHz"):
			name = ""
		else:
			name = f" ({name})"
		if locked != None:
			locked = LOCKED_EMOJI if locked != "0" else UNLOCKED_EMOJI
			locked = " " + locked
		return f"{id} - {freq}{name}{locked}"

	@staticmethod
	def format_frequency(freq: str) -> str:
		"Format frequency to be sent over protocol"
		freq = int(float(freq) * 1e4)
		return f"{freq:08}"

	@staticmethod
	def parse_frequency(freq: str) -> str:
		"Parse frequency read from protocol"
		freq = int(freq) / 1e4
		return f"{freq}MHz"

	@staticmethod
	def sanitize_string(str: str) -> str:
		return str.translate({
			ord(","): "",
			ord("\r"): "",
			ord("\n"): " ",
		})[:16]

config = None
serialClient: SerialProtocol = None
bot = Sc230Bot()
async def main():
	global config, serialClient, bot

	with open("config.json", "r") as f:
		config = f.read().strip()
		config = json.loads(config)
	for k in ["control_channels", "admin_ids"]:
		config[k] = list(map(int, config[k]))

	serialClient = SerialProtocol()

	discord.utils.setup_logging(root = False)
	consoleHandler = logging.getLogger(discord.__name__).handlers[-1]
	logger.addHandler(consoleHandler)
	logger.setLevel(int(os.getenv("LOG_LEVEL", logging.INFO)))

	infoCmds = commands.Cog()
	infoCmds.__cog_name__ = "info"
	for name in config["info_commands"]:
		def pythonpls(name):
			async def cmd(_, ctx: Sc230Context):
				files = glob.glob(name + "*.txt")
				files.sort()
				for file in files:
					with open(file, "r") as f:
						contents = f.read().strip()
						await ctx.reply(contents)
			cmd = commands.Command(cmd, cog=infoCmds, name=name)
			cmd.params.clear() # misdetects `ctx` as a parameter, so fix that up
			infoCmds.__cog_commands__ += (cmd,)
		pythonpls(name)

	prefixes = config["command_prefix"]
	if type(prefixes) is str:
		prefixes = [prefixes]
	bot.command_prefix = commands.when_mentioned_or(*prefixes)
	try:
		async with bot:
			await bot.add_cog(infoCmds)
			await bot.start(config["token"])
	finally:
		logger.info("exiting")

@bot.event
async def on_ready():
	logger.info("ready")
	for channel in config["control_channels"]:
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

	if msg.channel.id not in config["control_channels"]:
		if not prefixed:
			return

		enabledChannels = []
		for id in config["control_channels"]:
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

@bot.command(ignore_extra=False)
async def status(ctx: Sc230Context):
	"""
		print status lines (see also webcam)
	"""
	async with SerialGuard(ctx.message):
		body = (await serialClient.send_raw(b"STS"))
		(_, l1, l2, l3, l4, *_) = body.split(",")
		l1 = l1.strip()
		l2 = l2.strip()
		l3 = l3.strip()
		l4 = l4.strip()
		[l1, l2, l3, l4] = [replace_special_chars(v) for v in [l1, l2, l3, l4]]
		await ctx.message.reply(f"```\n{l1}\n{l2}\n{l3}\n{l4}\n```")
		raise CommandHandled

class SearchOption(str, Enum):
	public = ("public", "", "public safety")
	news = ("news", ">")
	ham = ("ham", ">>", "amateur radio")
	marine = ("marine", ">>>")
	rail = ("rail", ">>>>")
	air = ("air", ">>>>>")
	cb = ("cb", ">>>>>>", "Citizens Band")
	gmrs = ("gmrs", ">>>>>>>", "Family Radio Service/General Mobile Radio Service")
	racing = ("racing", ">>>>>>>>", "NASCAR and stuff")
	special = ("special", ">>>>>>>>>", "idk man the manual doesn't say")
	weather = ("weather", "special", "National Weather Service broadcasts")

	def __new__(cls, label, keys, description=None):
		obj = str.__new__(cls, label)
		obj._value_ = label
		obj.keys = keys
		obj.description = description
		return obj

@bot.command(ignore_extra=False)
async def search(ctx: Sc230Context, *, option: SearchOption):
	"""
		scan preprogrammed frequency ranges
	"""
	async with SerialGuard(ctx.message):
		await serialClient.send_raw(b"EPG")
		if option == "weather":
			await serialClient.send_keys(b"M>>>>>^")
		else:
			await serialClient.send_keys(b"M>>^^")
			await serialClient.send_keys(option.keys)
		await serialClient.send_key(ord('^'))
search.help += f"\n\noption must be one of:" + \
	"\n  ".join(itertools.chain([""], (
		f"{o.value}{o.description and " - " or ""}{o.description or ""}" for o in SearchOption
	)))

@bot.command(ignore_extra=False)
async def freq(ctx: Sc230Context, *, freq: float):
	"""
		tune in to a specific frequency (to nearest 5kHz)
	"""
	freq = serialClient.format_frequency(freq)
	async with SerialGuard(ctx.message):
		match (await serialClient.send_raw(f"QSH,{freq},0,AUTO,0,2,0,1,0,0")).split(","):
			case ["QSH", "OK"]:
				pass
			case ["QSH", "ERR"]:
				raise CommandError("couldn't tune in, is the scanner busy?")

@bot.command(ignore_extra=False)
async def freqrange(ctx: Sc230Context, minfreq: float, maxfreq: float):
	"""
		scan through a range of frequencies

		overrides custom search group 0
	"""
	minfreq = serialClient.format_frequency(minfreq)
	maxfreq = serialClient.format_frequency(maxfreq)
	username = f"${serialClient.sanitize_string(ctx.author.display_name)}"
	async with SerialGuard(ctx.message):
		async with ProgramGuard():
			await serialClient.send_raw(f"CSG,{"1" * 9}0")
			await serialClient.send_raw(f"CSP,0,{username},{minfreq},{maxfreq},0,AUTO,0,2,0")
		await serialClient.send_keys(b"F+S.>E")

@bot.command(ignore_extra=False)
async def cs(ctx: Sc230Context, *, groups: str = "0123456789"):
	"""
		switch to custom search mode with the given group IDs enabled, defaults to all
	"""
	groups = groups.replace(" ", "")
	if not all(v in "0123456789" for v in groups):
		raise CommandError("search groups must be given as numbers 0-9")

	async with SerialGuard(ctx.message):
		async with ProgramGuard():
			state = list("1" * 10)
			for v in groups:
				v = int(v)
				state[v - 1] = "0"
			state = "".join(state)
			await serialClient.send_raw(f"CSG,{state}")
		await serialClient.send_keys("F+S.>E")

@bot.command(ignore_extra=False)
async def csgroups(ctx: Sc230Context):
	"""
		print custom search groups
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		lines = []
		for id in range(10):
			id = (id + 1) % 10
			match (await serialClient.send_raw(f"CSP,{id}")).split(","):
				case ["CSP", name, minfreq, maxfreq, *_]:
					minfreq = serialClient.parse_frequency(minfreq)
					maxfreq = serialClient.parse_frequency(maxfreq)
					lines.append(f"* {id} - {name}")
					lines.append(f"  * {minfreq} to {maxfreq}")
				case resp:
					raise CommandError(f"unexpected response {resp=}")
		await ctx.message.reply("\n".join(lines))
		raise CommandHandled

@bot.command(ignore_extra=False)
async def csfreq(ctx: Sc230Context, group: int, minfreq: float, maxfreq: float):
	"""
		set custom search group min/max frequencies. note that on group 0 this may be overridden by `$freqrange`
	"""
	if group < 0 or group > 9:
		raise CommandError("search groups must be given as numbers 0-9")
	minfreq = serialClient.format_frequency(minfreq)
	maxfreq = serialClient.format_frequency(maxfreq)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"CSP,{group},,{minfreq},{maxfreq},,,,,")

@bot.command(ignore_extra=False)
async def csname(ctx: Sc230Context, group: int, *, name: str):
	"""
		set custom search group name
	"""
	if group < 0 or group > 9:
		raise CommandError("search groups must be given as numbers 0-9")
	name = serialClient.sanitize_string(name)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"CSP,{group},{name},,,,,,,")

csallBands = {
	"Petrol/CB/Ham": ("25", "54"),
	"Ham/Fed/Military": ("137", "174"),
	"More Ham/Fed": ("400", "512"),
	"Public": ("806", "956"),
	"GHz ham": ("1240", "1300")
}
@bot.command(ignore_extra=False)
async def csall(ctx: Sc230Context):
	"""
		programs set of search groups covering the hardware's frequency range
	"""
	async with SerialGuard(ctx.message, typing=True):
		async with ProgramGuard():
			enabled = ""
			for (group, (name, (minfreq, maxfreq))) in enumerate(csallBands.items()):
				group += 1
				enabled += str(group)
				minfreq = serialClient.format_frequency(minfreq)
				maxfreq = serialClient.format_frequency(maxfreq)
				await serialClient.send_raw(f"CSP,{group},{name},{minfreq},{maxfreq},,,,,")

			state = list("1" * 10)
			for v in enabled:
				v = int(v)
				state[v - 1] = "0"
			state = "".join(state)
			await serialClient.send_raw(f"CSG,{state}")
		await serialClient.send_keys("F+S.>E")

@bot.command(ignore_extra=False)
async def tree(ctx: Sc230Context):
	"""
		print all systems, their groups, and channels
	"""
	doneSystems = 0
	doneGroups = 0
	doneChannels = 0
	def format_status():
		return dict(content=(
			f"read {doneSystems} systems, " +
			f"{doneGroups} groups, " +
			f"{doneChannels} channels"
		))

	systems = []
	async with SerialGuard(ctx.message, typing=True), ProgramGuard(), StatusGuard(ctx.message, format_status, interval=2.5):
		systemHead = int((await serialClient.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serialClient.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			doneSystems += 1

			(_, _, systemName, _, _, locked, _, _, _, _, _, _, groupHead, groupTail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
			locked = locked != "0"
			[groupHead, groupTail] = map(int, [groupHead, groupTail])

			locked = LOCKED_EMOJI if locked else UNLOCKED_EMOJI
			embed = discord.Embed(title=f"{systemId} - {systemName} {locked}")
			systems.append(embed)

			noGroups = True
			for groupId in await walk_ids(groupHead, groupTail):
				noGroups = False
				doneGroups += 1

				(_, _, groupName, _, locked, _, _, _, chanHead, chanTail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
				locked = locked != "0"
				[chanHead, chanTail] = map(int, [chanHead, chanTail])

				locked = LOCKED_EMOJI if locked else UNLOCKED_EMOJI
				embed.add_field(name=f"{groupId} - {groupName} {locked}", value="", inline=False)

				channels = []
				for channelId in await walk_ids(chanHead, chanTail):
					doneChannels += 1

					(_, name, freq, _, _, _, _, locked, *_) = (await serialClient.send_raw(f"CIN,{channelId}")).split(",")
					formatted = serialClient.format_channel(channelId, name, freq, locked)
					channels.append(f"{formatted}")
				if not channels:
					embed.add_field(name="", value="no channels")
					continue

				col1, col2 = [], []
				try:
					it = iter(channels)
					while True:
						col1.append(next(it))
						col2.append(next(it))
				except StopIteration:
					pass
				embed.add_field(name="", value="\n".join(col1))
				embed.add_field(name="", value="\n".join(col2))
			if noGroups:
				embed.add_field(name="", value="no groups")
	if not systems:
		await ctx.message.reply("no systems")
	for batch in itertools.batched(systems, 10):
		await ctx.message.reply(embeds=batch)
	raise CommandHandled

@bot.command(ignore_extra=False)
async def locked(ctx: Sc230Context):
	"""
		fixme
	"""
	raise CommandError("fixme")
	async with SerialGuard(ctx.message), ProgramGuard():
		lines = []
		systemHead = int((await serialClient.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serialClient.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			# await serialClient.send_raw(f"QGL,{systemId},0000000000")
			# await serialClient.send_raw(f"SIN,{systemId},,,,0,,,,")
			(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
			[groupHead, groupTail] = map(int, [groupHead, groupTail])
			lines.append(f"## {systemId} - {systemName}")
			for groupId in await walk_ids(groupHead, groupTail):
				# await serialClient.send_raw(f"GIN,{groupId},,,0")
				(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
				[chanHead, chanTail] = map(int, [chanHead, chanTail])
				lines.append(f"### {groupId} - {groupName}")
				for channelId in await walk_ids(chanHead, chanTail):
					(_, name, freq, *_) = (await serialClient.send_raw(f"CIN,{channelId}")).split(",")
					lines.append(f"* {serialClient.format_channel(channelId, name, freq)}")
		await ctx.message.reply("\n".join(lines))
		raise CommandHandled

@bot.command(ignore_extra=False)
async def lockout(ctx: Sc230Context, *frequencies: float):
	"""
		lock out specific frequencies
	"""
	frequencies = list(map(serialClient.format_frequency, frequencies))
	logger.debug(f"{frequencies=}")
	async with SerialGuard(ctx.message), ProgramGuard():
		locked = 0
		errs = 0
		for freq in frequencies:
			match (await serialClient.send_raw(f"LOF,{freq}")).split(","):
				case ["LOF", "OK"]:
					locked += 1
				case ["LOF", "ERR"]:
					errs += 1
				case resp:
					raise SerialError(f"unexpected response {resp=}")
		if errs > 0:
			errs = f", {errs} out of band"
		else:
			errs = ""
		await ctx.message.reply(f"locked {locked} freqs{errs}")

@bot.command(ignore_extra=False)
async def unlockall(ctx: Sc230Context):
	"""
		unlock all locked out systems/groups/channels
	"""
	async with SerialGuard(ctx.message, typing=True), ProgramGuard():
		while True:
			match (await serialClient.send_raw(b"GLF")).split(","):
				case ["GLF", "-1"]:
					break
				case ["GLF", freq]:
					await serialClient.send_raw(f"ULF,{freq}")

		await serialClient.send_raw(f"QSL,0000000000")

		systemHead = int((await serialClient.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serialClient.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			await serialClient.send_raw(f"QGL,{systemId},0000000000")
			await serialClient.send_raw(f"SIN,{systemId},,,,0,,,,")
			(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
			[groupHead, groupTail] = map(int, [groupHead, groupTail])
			for groupId in await walk_ids(groupHead, groupTail):
				await serialClient.send_raw(f"GIN,{groupId},,,0")
				(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
				[chanHead, chanTail] = map(int, [chanHead, chanTail])
				for channelId in await walk_ids(chanHead, chanTail):
					await serialClient.send_raw(f"CIN,{channelId},,,,,,,0,,,")

@bot.command(ignore_extra=False)
async def systems(ctx: Sc230Context):
	"""
		list systems and their IDs
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		head = int((await serialClient.send_raw(b"SIH")).split(",")[1])
		tail = int((await serialClient.send_raw(b"SIT")).split(",")[1])

		systems = []
		for id in await walk_ids(head, tail):
			match (await serialClient.send_raw(f"SIN,{id}")).split(","):
				case ["SIN", _, name, _, _, locked, *_]:
					locked = LOCKED_EMOJI if locked != "0" else UNLOCKED_EMOJI
					systems.append(f"* {id} - {name} {locked}")
				case resp:
					logger.warning(f"weird response for system {id}: {resp!r}")
		if systems:
			await ctx.message.reply("\n".join(systems))
		else:
			await ctx.message.reply("no systems found")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def systemadd(ctx: Sc230Context, *, name: str = ""):
	"""
		add a new system, optionally setting its name
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		[_, id] = (await serialClient.send_raw(b"CSY,CNV")).split(",")
		if id == "-1":
			raise CommandError("could not create system")
		if name:
			name = serialClient.sanitize_string(name)
			await serialClient.send_raw(f"SIN,{id},{name},,,,,,,")
		await ctx.message.reply(f"created new system with id {id}")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def systemdel(ctx: Sc230Context, *, id: int):
	"""
		delete a system
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"DSY,{id}")

@bot.command(ignore_extra=False)
async def systemname(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a system's name. limit 16 characters
	"""
	newName = serialClient.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"SIN,{id},{newName},,,,,,,")

@bot.command(ignore_extra=False)
async def systemlock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a system's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"SIN,{id},,,,{locked & 1},,,,")

@bot.command(ignore_extra=False)
async def groups(ctx: Sc230Context, systemId: int):
	"""
		list groups and IDs for the given system
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		(_, _, systemName, _, _, _, _, _, _, _, _, _, head, tail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
		[head, tail] = map(int, [head, tail])

		groups = []
		for id in await walk_ids(head, tail):
			match (await serialClient.send_raw(f"GIN,{id}")).split(","):
				case ["GIN", _, name, _, locked, *_]:
					locked = LOCKED_EMOJI if locked != "0" else UNLOCKED_EMOJI
					groups.append(f"* {id} - {name} {locked}")
				case resp:
					logger.debug(f"weird response for group {id}: {resp!r}")
		if groups:
			groups = "\n".join(groups)
			await ctx.message.reply(f"## {systemName}\n{groups}")
		else:
			await ctx.message.reply("no groups found")

		raise CommandHandled

@bot.command(ignore_extra=False)
async def groupadd(ctx: Sc230Context, systemId: int, *, name: str = ""):
	"""
		add a new group to a system, optionally setting its name
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		if (await serialClient.send_raw(f"SIN,{systemId}")).split(",", 1)[1] == "ERR":
			raise CommandError(f"system {systemId} does not exist")
		[_, id] = (await serialClient.send_raw(f"AGC,{systemId}")).split(",")
		if id == "-1":
			raise CommandError("could not create group")
		if name:
			await serialClient.send_raw(f"GIN,{id},{name},,")
		await ctx.message.reply(f"created new group with id {id}")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def groupdel(ctx: Sc230Context, id: int):
	"""
		delete a group
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"DGR,{id}")

@bot.command(ignore_extra=False)
async def groupname(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a group's name. limit 16 characters
	"""
	newName = serialClient.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"GIN,{id},{newName},,")

@bot.command(ignore_extra=False)
async def grouplock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a group's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"GIN,{id},,,{locked & 1}")

@bot.command(ignore_extra=False)
async def channels(ctx: Sc230Context, groupId: int):
	"""
		list channels in a given group
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		(_, _, groupName, _, _, _, _, _, head, tail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
		[head, tail] = map(int, [head, tail])

		channels = []
		for id in await walk_ids(head, tail):
			match (await serialClient.send_raw(f"CIN,{id}")).split(","):
				case ["CIN", name, freq, _, _, _, _, locked, *_]:
					channels.append(serialClient.format_channel(id, name, freq, locked))
				case resp:
					logger.debug(f"weird response for channel {id}: {resp!r}")
		if channels:
			col1, col2 = [], []
			try:
				it = iter(channels)
				while True:
					col1.append(next(it))
					col2.append(next(it))
			except StopIteration:
				pass
			col1, col2 = "\n".join(col1), "\n".join(col2)
			embed = discord.Embed()
			embed.add_field(name="", value=col1)
			embed.add_field(name="", value=col2)
			await ctx.message.reply(f"## {groupName}", embed=embed)
		else:
			await ctx.message.reply("no channels found")

		raise CommandHandled

@bot.command(ignore_extra=False)
async def chanadd(ctx: Sc230Context, groupId: int, *, frequencySpecs: str):
	"""
		add new channels (frequencies) to a group. each frequency on one line, optionally followed by a name
	"""
	frequencies = []
	for line in frequencySpecs.split("\n"):
		line = line.split(maxsplit=1)
		freq = serialClient.format_frequency(line[0])
		name = line[1:]
		if name:
			name = serialClient.sanitize_string(name[0])
			frequencies.append((freq, name))
		else:
			frequencies.append(freq)
	if not frequencies:
		raise CommandError("you must specify at least one frequency")

	processedFreqs = 0
	def format_status():
		return dict(content=f"{processedFreqs}/{len(frequencies)} processed")

	async with SerialGuard(ctx.message, typing=True), ProgramGuard(), StatusGuard(ctx.message, format_status):
		errors = []
		for freq in frequencies:
			name = ""
			match freq:
				case str(f):
					pass
				case (f, n):
					freq = f
					name = n
				case _:
					assert False, "unexpected case"

			if (await serialClient.send_raw(f"GIN,{groupId}")).split(",", 1)[1] == "ERR":
				raise CommandError(f"group {groupId} does not exist")
			[_, id] = (await serialClient.send_raw(f"ACC,{groupId}")).split(",")
			if id == "-1":
				raise CommandError("could not create channel")
			try:
				match (await serialClient.send_raw(f"CIN,{id},{name},{freq},,,,,,,,")).split(","):
					case ["CIN", "OK"]:
						pass
					case ["CIN", "ERR"]:
						raise CommandError("failed to update channel", freq=freq)
				match (await serialClient.send_raw(f"CIN,{id}")).split(","):
					case ["CIN", _, setFreq, *_] if setFreq == freq:
						pass
					case _:
						raise CommandError("out of band", freq=freq)
			except CommandError as err:
				freq = serialClient.parse_frequency(err.freq)
				errors.append(f"{freq}: {err.msg}")
				await serialClient.send_raw(f"DCH,{id}")
			processedFreqs += 1

		numErrors = len(errors)
		if errors:
			errors = f"\n{"\n".join(errors)}"
		else:
			errors = ""
		await ctx.message.reply(f"created {len(frequencies) - numErrors} new channels out of {len(frequencies)} given{errors}")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def chandel(ctx: Sc230Context, ids: list[int]):
	"""
		delete a channel
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		for id in ids:
			await serialClient.send_raw(f"DCH,{id}")

@bot.command(ignore_extra=False)
async def channame(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a channel's name. limit 16 characters
	"""
	newName = serialClient.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"CIN,{id},{newName},,,,,,,,,")

@bot.command(ignore_extra=False)
async def chanlock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a channel's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"CIN,{id},,,,,,,{locked & 1},,,")

@bot.command(ignore_extra=False)
async def chanfreq(ctx: Sc230Context, id: int, freq: float):
	"""
		set a channel's frequency
	"""
	freq = serialClient.format_frequency(freq)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serialClient.send_raw(f"CIN,{id},,{freq},,,,,,,,")

@bot.command(ignore_extra=False)
async def key(ctx: Sc230Context, *, keys: str):
	"""
		press a series of buttons on the scanner. case insensitive, whitespace ignored

		keycodes:
			M - Menu
			F - Function
			H - Hold
			S - Scan
			L - Lockout (L/O)
			C - Car
			0-9 - Digits
			. - Decimal / No
			E - Enter / Yes
			> - Scroll right
			< - Scroll left
			^ - Enter
			P - Power

		key chords:
			Keys can be sent while other keys are held by following the held key with a `+`.
			E.g. `F+P` will hold Func, press power, and finally release func.
	"""
	await ctx.send_keys(keys)

@bot.command(ignore_extra=False)
async def keyon(ctx: Sc230Context):
	"""
		enable treating all non-command messages as keycodes

		see `key` command's help for list of keycodes
	"""
	if ctx.author.id in bot.rawInputUsers:
		raise CommandError("you are already in raw input mode")
	if ctx.author.id in bot.keyInputUsers:
		raise CommandError("you are already in key input mode")
	bot.keyInputUsers.add(ctx.author.id)

@bot.command(ignore_extra=False)
async def keyoff(ctx: Sc230Context):
	"""
		disable keycode messages mode
	"""
	if ctx.author.id not in bot.keyInputUsers:
		raise CommandError("you are not in key input mode")
	bot.keyInputUsers.remove(ctx.author.id)

@bot.command(ignore_extra=False)
async def sweep(ctx: Sc230Context):
	"""
		admin only. delete all messages in the channel \N{BROOM}\N{DASH SYMBOL}
	"""
	enforce_is_admin(ctx.author)

	# should be enforced by `on_message` but just to be safe
	assert ctx.channel.id in config["control_channels"]

	messagesSwept = 0
	def format_status():
		return dict(content=f"{messagesSwept} messages swept", delete_after=5)
	statusGuard = StatusGuard(ctx.message, format_status)

	async with statusGuard, ctx.channel.typing():
		queue = []
		async for foundMsg in ctx.channel.history(limit=None):
			if foundMsg.pinned or (statusGuard.statusMsg and foundMsg.id == statusGuard.statusMsg.id):
				continue
			queue.append(foundMsg)
			if len(queue) >= 100:
				await ctx.channel.delete_messages(queue)
				messagesSwept += len(queue)
				queue.clear()
		if len(queue) > 0:
			await ctx.channel.delete_messages(queue)
			messagesSwept += len(queue)
		await ctx.message.reply("done", delete_after=5)
		raise CommandHandled

@bot.command(ignore_extra=False)
async def raw(ctx: Sc230Context, *, input: str):
	"""
		admin only. allows transmitting raw protocol data
	"""
	enforce_is_admin(ctx.author)
	await ctx.send_raw(input.split("\n"))
	raise CommandHandled

@bot.command(ignore_extra=False)
async def rawon(ctx: Sc230Context):
	"""
		enable treating all non-command messages as raw protocol data
	"""
	enforce_is_admin(ctx.author)
	if ctx.author.id in bot.rawInputUsers:
		raise CommandError("you are already in raw input mode")
	if ctx.author.id in bot.keyInputUsers:
		raise CommandError("you are already in key input mode")
	bot.rawInputUsers.add(ctx.author.id)

@bot.command(ignore_extra=False)
async def rawoff(ctx: Sc230Context):
	"""
		disable keycode messages mode
	"""
	if ctx.author.id not in bot.rawInputUsers:
		raise CommandError("you are not in raw input mode")
	bot.rawInputUsers.remove(ctx.author.id)

@bot.command(ignore_extra=False)
async def factoryreset(ctx: Sc230Context):
	"""
		admin only. restores all settings to factory defaults
	"""
	enforce_is_admin(ctx.author)
	async with SerialGuard(ctx.message, typing=True), ProgramGuard():
		await serialClient.send_raw(b"CLR")

@bot.command(ignore_extra=False)
async def restart(ctx: Sc230Context):
	"""
		admin only. restarts bot
	"""
	enforce_is_admin(ctx.author)
	raise SystemExit # FIXME: needs to work without `--auto-restart`

if __name__ == "__main__":
	try:
		if "--auto-restart" not in sys.argv or "--child" in sys.argv:
			asyncio.run(main())
		else:
			import subprocess

			args = sys.orig_argv + ["--child"]
			fails = 0
			while True:
				print("Supervisor (re-)starting bot", file=sys.stderr)
				result = subprocess.run(
					args,
					stdin=None,
					stdout=sys.stdout,
					stderr=sys.stderr,
				)
				if result.returncode == 0:
					fails = 0
				else:
					fails += 1
					if fails >= 5:
						print("Supervisor exiting due to restart loop")
						raise SystemExit(1)
					time.sleep(1)
	except KeyboardInterrupt:
		raise SystemExit(1)
