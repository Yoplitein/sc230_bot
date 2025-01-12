import discord
from discord.ext import commands

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids
from ..bot import Sc230Context, CommandError, BadSubcommandError, CommandHandled, StatusGuard, enforce_is_admin, bot

@category("programming")
@bot.group(invoke_without_command=True, aliases=["channels", "chan", "chans", "c"])
async def channel(_, ctx: Sc230Context):
	"""
		channel management

		a channel is an individual frequency. contained within a group,
		which is in turn contained in a system

		the device has a limit of 2500 channels per group
	"""
	raise BadSubcommandError(ctx)

@channel.command(name="list", ignore_extra=False, aliases=["ls"])
async def list_(ctx: Sc230Context, groupId: int = commands.parameter(displayed_name="group id")):
	"""
		list channels in a given group
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		(_, _, groupName, _, _, _, _, _, head, tail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
		[head, tail] = map(int, [head, tail])

		channels = []
		for id in await walk_ids(head, tail):
			match (await serial.send_raw(f"CIN,{id}")).split(","):
				case ["CIN", name, freq, _, _, _, _, locked, *_]:
					channels.append(serial.format_channel(id, name, freq, locked))
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

@channel.command(ignore_extra=False)
async def add(
	ctx: Sc230Context,
	groupId: int = commands.parameter(displayed_name="group id"),
	*,
	frequencySpecs: str = commands.parameter(displayed_name="frequency specs"),
):
	"""
		add new channels (frequencies) to a group

		Parameters
		---
		frequencySpecs
			a list of frequencies (and optional names) to add to the group
			each frequency should be on its own line, any following text is taken as the name
			limit 16 characters for the name
	"""
	frequencies = []
	for line in frequencySpecs.split("\n"):
		line = line.split(maxsplit=1)
		freq = serial.format_frequency(line[0])
		name = line[1:]
		if name:
			name = serial.sanitize_string(name[0])
			frequencies.append((freq, name))
		else:
			frequencies.append(freq)
	if not frequencies:
		raise CommandError("you must specify at least one frequency")

	processedFreqs = 0
	def format_status():
		return dict(content=f"{processedFreqs}/{len(frequencies)} processed")

	async with SerialGuard(ctx.message, typing=True), ProgramGuard(), StatusGuard(ctx, format_status):
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

			if (await serial.send_raw(f"GIN,{groupId}")).split(",", 1)[1] == "ERR":
				raise CommandError(f"group {groupId} does not exist")
			[_, id] = (await serial.send_raw(f"ACC,{groupId}")).split(",")
			if id == "-1":
				raise CommandError("could not create channel")
			try:
				match (await serial.send_raw(f"CIN,{id},{name},{freq},,,,,,,,")).split(","):
					case ["CIN", "OK"]:
						pass
					case ["CIN", "ERR"]:
						raise CommandError("failed to update channel", freq=freq)
				match (await serial.send_raw(f"CIN,{id}")).split(","):
					case ["CIN", _, setFreq, *_] if setFreq == freq:
						pass
					case _:
						raise CommandError("out of band", freq=freq)
			except CommandError as err:
				freq = serial.parse_frequency(err.freq)
				errors.append(f"{freq}: {err.msg}")
				await serial.send_raw(f"DCH,{id}")
			processedFreqs += 1

		numErrors = len(errors)
		if errors:
			errors = f"\n{"\n".join(errors)}"
		else:
			errors = ""
		await ctx.message.reply(f"created {len(frequencies) - numErrors} new channels out of {len(frequencies)} given{errors}")
		raise CommandHandled

@channel.command(ignore_extra=False, aliases=["del"])
async def delete(ctx: Sc230Context, *, ids: str = commands.parameter(displayed_name="channel ids", )):
	"""
		delete a batch of channels
	"""
	ids = list(int(v) for v in ids.split())
	async with SerialGuard(ctx.message), ProgramGuard():
		for id in ids:
			await serial.send_raw(f"DCH,{id}")

@channel.command(ignore_extra=False)
async def name(
	ctx: Sc230Context,
	id: int = commands.parameter(displayed_name="channel id"),
	*,
	newName: str = commands.parameter(displayed_name="new name"),
):
	"""
		set a channel's name

		Parameters
		---
		newName
			new name to give this channel. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},{newName},,,,,,,,,")

@channel.command(ignore_extra=False)
async def lock(
	ctx: Sc230Context,
	id: int = commands.parameter(displayed_name="channel id"),
	locked: bool = commands.parameter(),
):
	"""
		set a channel's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},,,,,,,{locked & 1},,,")

@channel.command(ignore_extra=False, aliases=["freq"])
async def frequency(
	ctx: Sc230Context,
	id: int = commands.parameter(displayed_name="channel id"),
	freq: float = commands.parameter(),
):
	"""
		set a channel's frequency
	"""
	freq = serial.format_frequency(freq)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},,{freq},,,,,,,,")
