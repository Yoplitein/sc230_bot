from dataclasses import dataclass
from enum import Enum, Flag
import itertools

import discord
from discord.ext import commands

from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids
from ..bot import Sc230Context, CommandError, CommandHandled, StatusGuard, enforce_is_admin, bot

logger = getLogger(__name__)

class Icon1(int, Flag):
	none = 0, "none"
	sys = 1 << 0, "Sys"
	s1 = 1 << 1, "1"
	s2 = 1 << 2, "2"
	s3 = 1 << 3, "3"
	s4 = 1 << 4, "4"
	s5 = 1 << 5, "5"
	s6 = 1 << 6, "6"
	s7 = 1 << 7, "7"
	s8 = 1 << 8, "8"
	s9 = 1 << 9, "9"
	s0 = 1 << 10, "0"
	att = 1 << 11, "Att"
	pri = 1 << 12, "Pri"
	keylock = 1 << 13, "Keylock"
	batt = 1 << 14, "Batt"

	def __new__(cls, value, label):
		obj = int.__new__(cls, value)
		obj._value_ = value
		obj.label = label
		return obj

class Icon2(int, Flag):
	none = 0, "none"
	grp = 1 << 0, "Grp"
	s1 = 1 << 1, "1"
	s2 = 1 << 2, "2"
	s3 = 1 << 3, "3"
	s4 = 1 << 4, "4"
	s5 = 1 << 5, "5"
	s6 = 1 << 6, "6"
	s7 = 1 << 7, "7"
	s8 = 1 << 8, "8"
	s9 = 1 << 9, "9"
	s0 = 1 << 10, "0"
	am = 1 << 11, "AM"
	nfm = 1 << 12, "NFM"
	fm = 1 << 13, "FM"
	lo = 1 << 14, "L/O"
	f = 1 << 15, "Func"
	cc = 1 << 16, "CC"

	def __new__(cls, value, label):
		obj = int.__new__(cls, value)
		obj._value_ = value
		obj.label = label
		return obj

@dataclass
class Status:
	line1: str
	line2: str
	icon1: Icon1
	icon2: Icon2
	squelch: bool
	mute: bool
	weatherAlert: int

	def __str__(self):
		icon1 = " ".join(v.label for v in self.icon1) or "<no icons>"
		icon2 = " ".join(v.label for v in self.icon2) or "<no icons>"
		squelch = f"squelch {self.squelch and "open" or "closed"}"
		mute = self.mute and "muted" or "unmuted"
		weatherAlert = self.weatherAlert > 0 and f"\nWeather alert: {self.weatherAlert}" or ""
		return f"{self.line1}\n{self.line2}\n{icon1}\n{icon2}\n{squelch}, {mute}{weatherAlert}"

def parse_status(line: str) -> Status:
	def read_fixed() -> str:
		# status lines may contain commas
		nonlocal line
		res = line[:16]
		line = line[17:]
		return res
	def read_to_comma() -> str:
		nonlocal line
		index = line.find(",")
		if index == -1:
			res = line
			line = ""
			return res
		res = line[:index]
		line = line[index + 1:]
		return res

	assert read_to_comma() == "STS"
	line1 = read_fixed()
	read_to_comma() # line 1  display mode
	line2 = read_fixed()
	read_to_comma() # line 2 display mode
	icon1Str = read_to_comma()
	icon2Str = read_to_comma()
	read_to_comma() # reserved
	squelch = read_to_comma()
	mute = read_to_comma()
	read_to_comma() # battery status
	weatherAlert = read_to_comma()
	logger.debug(f"{line1=} {line2=} {icon1Str=} {icon2Str=} {squelch=} {mute=} {weatherAlert=}")

	line1 = line1.strip()
	line2 = line2.strip()
	icon1 = Icon1(0)
	for (index, v) in enumerate(icon1Str):
		if v == "1":
			icon1 |= Icon1(1 << index)
	icon2 = Icon2(0)
	for (index, v) in enumerate(icon2Str):
		if v == "1":
			icon2 |= Icon2(1 << index)
	squelch = int(squelch) != 0
	mute = int(mute) != 0
	weatherAlert = int(weatherAlert)

	return Status(
		line1,
		line2,
		icon1,
		icon2,
		squelch,
		mute,
		weatherAlert,
	)

@category("inspection")
@bot.command(ignore_extra=False)
async def status(_, ctx: Sc230Context):
	"""
		print status lines (see also webcam)
	"""
	async with SerialGuard(ctx.message):
		line = await serial.send_raw(b"STS")
		status = parse_status(line)
		await ctx.reply(status)
		raise CommandHandled

@category("inspection")
@bot.command(ignore_extra=False)
async def tree(_, ctx: Sc230Context):
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
	async with SerialGuard(ctx.message, typing=True), ProgramGuard(), StatusGuard(ctx, format_status, interval=2.5):
		systemHead = int((await serial.send_raw(b"SIH")).split(",")[1])
		systemWalker = serial.IdWalker(systemHead)
		for systemId in systemWalker:
			doneSystems += 1

			(_, _, systemName, _, _, locked, _, _, _, _, rev, fwd, groupHead, groupTail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
			locked = locked != "0"
			[groupHead, groupTail] = map(int, [groupHead, groupTail])
			rev, fwd = int(rev), int(fwd)
			systemWalker.add(rev, fwd)

			locked = LOCKED_EMOJI if locked else UNLOCKED_EMOJI
			embed = discord.Embed(title=f"{systemId} - {systemName} {locked}")
			systems.append(embed)

			noGroups = True
			groupWalker = serial.IdWalker(groupHead)
			for groupId in groupWalker:
				noGroups = False
				doneGroups += 1

				(_, _, groupName, _, locked, rev, fwd, _, chanHead, chanTail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
				locked = locked != "0"
				[chanHead, chanTail] = map(int, [chanHead, chanTail])
				rev, fwd = int(rev), int(fwd)
				groupWalker.add(rev, fwd)

				locked = LOCKED_EMOJI if locked else UNLOCKED_EMOJI
				embed.add_field(name=f"{groupId} - {groupName} {locked}", value="", inline=False)

				channels = []
				channelWalker = serial.IdWalker(chanHead)
				for channelId in channelWalker:
					doneChannels += 1

					(_, name, freq, _, _, _, _, locked, _, _, _, rev, fwd, _, _) = (await serial.send_raw(f"CIN,{channelId}")).split(",")
					rev, fwd = int(rev), int(fwd)
					channelWalker.add(rev, fwd)
					formatted = serial.format_channel(channelId, name, freq, locked)
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
		await ctx.reply("no systems")
	for batch in itertools.batched(systems, 10):
		await ctx.reply(embeds=batch)
	raise CommandHandled

@category("inspection")
@bot.command(ignore_extra=False)
async def locked(_, ctx: Sc230Context):
	"""
		fixme
	"""
	raise CommandError("fixme")
	async with SerialGuard(ctx.message), ProgramGuard():
		lines = []
		systemHead = int((await serial.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serial.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			# await serial.send_raw(f"QGL,{systemId},0000000000")
			# await serial.send_raw(f"SIN,{systemId},,,,0,,,,")
			(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
			[groupHead, groupTail] = map(int, [groupHead, groupTail])
			lines.append(f"## {systemId} - {systemName}")
			for groupId in await walk_ids(groupHead, groupTail):
				# await serial.send_raw(f"GIN,{groupId},,,0")
				(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
				[chanHead, chanTail] = map(int, [chanHead, chanTail])
				lines.append(f"### {groupId} - {groupName}")
				for channelId in await walk_ids(chanHead, chanTail):
					(_, name, freq, *_) = (await serial.send_raw(f"CIN,{channelId}")).split(",")
					lines.append(f"* {serial.format_channel(channelId, name, freq)}")
		await ctx.reply("\n".join(lines))
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

	def __new__(cls, label, keys, description = None):
		obj = str.__new__(cls, label)
		obj._value_ = label
		obj.keys = keys
		obj.description = description
		return obj

@category("scanning")
@bot.command(ignore_extra=False)
async def search(_, ctx: Sc230Context, *, option: SearchOption):
	"""
		scan preprogrammed frequency ranges
	"""
	async with SerialGuard(ctx.message):
		await serial.send_raw(b"EPG")
		if option == "weather":
			await serial.send_keys(b"M>>>>>^")
		else:
			await serial.send_keys(b"M>>^^")
			await serial.send_keys(option.keys)
		await serial.send_key(ord('^'))
search.help += f"\n\noption must be one of:" + \
	"\n  ".join(itertools.chain([""], (
		f"{o.value}{o.description and " - " or ""}{o.description or ""}" for o in SearchOption
	)))

@category("scanning")
@bot.command(ignore_extra=False)
async def freq(_, ctx: Sc230Context, *, frequency: float):
	"""
		tune in to a specific frequency (to nearest 5kHz)
	"""
	frequency = serial.format_frequency(frequency)
	async with SerialGuard(ctx.message):
		match (await serial.send_raw(f"QSH,{frequency},0,AUTO,0,2,0,1,0,0")).split(","):
			case ["QSH", "OK"]:
				pass
			case ["QSH", "ERR" | "NG"]:
				raise CommandError("couldn't tune in, is the scanner busy?")

@category("locking")
@bot.command(ignore_extra=False)
async def lockout(_, ctx: Sc230Context, *, frequencies: str):
	"""
		globally lock out specific frequencies

		Parameters
		---
		frequencies
			a list of frequencies separated by whitespace
	"""
	frequencies = list(map(serial.format_frequency, frequencies.split()))
	async with SerialGuard(ctx.message), ProgramGuard():
		locked = 0
		errs = 0
		for freq in frequencies:
			match (await serial.send_raw(f"LOF,{freq}")).split(","):
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
		await ctx.reply(f"locked {locked} freqs{errs}")

@category("locking")
@bot.command(ignore_extra=False)
async def unlockall(_, ctx: Sc230Context):
	"""
		unlock all locked out systems/groups/channels
	"""
	async with SerialGuard(ctx.message, typing=True), ProgramGuard():
		while True:
			match (await serial.send_raw(b"GLF")).split(","):
				case ["GLF", "-1"]:
					break
				case ["GLF", freq]:
					await serial.send_raw(f"ULF,{freq}")

		await serial.send_raw(f"QSL,0000000000")

		systemHead = int((await serial.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serial.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			await serial.send_raw(f"QGL,{systemId},0000000000")
			await serial.send_raw(f"SIN,{systemId},,,,0,,,,")
			(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
			[groupHead, groupTail] = map(int, [groupHead, groupTail])
			for groupId in await walk_ids(groupHead, groupTail):
				await serial.send_raw(f"GIN,{groupId},,,0")
				(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
				[chanHead, chanTail] = map(int, [chanHead, chanTail])
				for channelId in await walk_ids(chanHead, chanTail):
					await serial.send_raw(f"CIN,{channelId},,,,,,,0,,,")

@bot.command(ignore_extra=False)
async def factoryreset(ctx: Sc230Context):
	"""
		admin only. restores all settings to factory defaults
	"""
	enforce_is_admin(ctx.author)
	async with SerialGuard(ctx.message, typing=True), ProgramGuard():
		await serial.send_raw(b"CLR")

@bot.command(ignore_extra=False)
async def sweep(ctx: Sc230Context, *, all: str = commands.parameter(default=False, displayed_default="false")):
	"""
		admin only. delete all bot-related messages in the channel \N{BROOM}\N{DASH SYMBOL}

		Parameters
		---
		all
			by default only bot messages and those they're in reply to are swept.
			`$sweep all` instead sweeps all messages
	"""
	enforce_is_admin(ctx.author)

	# should be enforced by `on_message` but just to be safe
	assert ctx.channel.id in config.get("control_channels")

	all = all == "all"

	messagesSwept = 0
	def format_status(*, last = False):
		return dict(content=f"{last and "done\n" or ""}{messagesSwept} messages swept", delete_after=5)
	statusGuard = StatusGuard(ctx, format_status, reply=False)

	repliedTo = set()
	def should_delete(msg: discord.Message):
		if msg.pinned:
			return False
		if msg.id == statusGuard.statusMsg.id:
			return False
		if all:
			return True
		return (
			msg.author.id == ctx.bot.user.id or
			any(react.me for react in msg.reactions) or
			msg.id in repliedTo
		)

	async with statusGuard, ctx.channel.typing():
		queue = [ctx.message]
		async for foundMsg in ctx.channel.history(limit=None):
			if foundMsg.id == ctx.message.id:
				continue
			if not should_delete(foundMsg):
				continue
			if foundMsg.reference:
				repliedTo.add(foundMsg.reference.message_id)

			queue.append(foundMsg)
			if len(queue) >= 100:
				await ctx.channel.delete_messages(queue)
				messagesSwept += len(queue)
				queue.clear()
		if len(queue) > 0:
			await ctx.channel.delete_messages(queue)
			messagesSwept += len(queue)
		raise CommandHandled

@bot.command(ignore_extra=False)
async def restart(ctx: Sc230Context):
	"""
		admin only. restarts bot
	"""
	from .. import RestartProcess
	enforce_is_admin(ctx.author)
	raise RestartProcess
