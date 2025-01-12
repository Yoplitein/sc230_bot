import discord

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids
from ..bot import Sc230Context, CommandError, CommandHandled, StatusGuard, enforce_is_admin, bot

@bot.command(ignore_extra=False)
async def channels(ctx: Sc230Context, groupId: int):
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

@bot.command(ignore_extra=False)
async def chanadd(ctx: Sc230Context, groupId: int, *, frequencySpecs: str):
	"""
		add new channels (frequencies) to a group. each frequency on one line, optionally followed by a name
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

@bot.command(ignore_extra=False)
async def chandel(ctx: Sc230Context, ids: list[int]):
	"""
		delete a channel
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		for id in ids:
			await serial.send_raw(f"DCH,{id}")

@bot.command(ignore_extra=False)
async def channame(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a channel's name. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},{newName},,,,,,,,,")

@bot.command(ignore_extra=False)
async def chanlock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a channel's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},,,,,,,{locked & 1},,,")

@bot.command(ignore_extra=False)
async def chanfreq(ctx: Sc230Context, id: int, freq: float):
	"""
		set a channel's frequency
	"""
	freq = serial.format_frequency(freq)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},,{freq},,,,,,,,")
