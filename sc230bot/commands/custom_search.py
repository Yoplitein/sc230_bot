from typing import Optional
import discord
from discord.ext import commands

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..bot import Sc230Context, CommandError, BadSubcommandError, CommandHandled, StatusGuard, enforce_is_admin, bot
from ..serial import ProgramGuard, SerialError, SerialGuard
from ..serial.messages import CSGroupFlag, CSGroupId, CustomSearchGroup, EnabledCSGroups, Modulation, SearchStep

@category("scanning")
@bot.command(ignore_extra=False)
async def freqrange(
	_,
	ctx: Sc230Context,
	minfreq: float = commands.parameter(displayed_name="min frequency"),
	maxfreq: float = commands.parameter(displayed_name="max frequency"),
):
	"""
		scan through a range of frequencies

		overrides custom search group 0
	"""
	name = "$" + ctx.author.display_name
	async with SerialGuard(ctx.message):
		async with ProgramGuard():
			await serial.send_message(CustomSearchGroup(
				id=CSGroupId.group0,
				name=name,
				min=minfreq,
				max=maxfreq,
				step=SearchStep.auto,
				modulation=Modulation.auto,
				attenuation=False,
				delay=2,
				dataSkip=False
			), update=True)
			await serial.send_message(EnabledCSGroups(enabled=CSGroupFlag.group0))
		await serial.send_keys("F+S.>E")

@category("scanning")
@bot.group(invoke_without_command=True, aliases=["cs"])
async def customsearch(_, ctx: Sc230Context):
	"""
		scanning mode where up to ten frequency ranges are probed simultaneously
	"""
	raise BadSubcommandError(ctx)

@customsearch.command(ignore_extra=False, aliases=["s"])
async def scan(
	ctx: Sc230Context,
	*,
	groups: str = commands.parameter(default="0123456789", displayed_default="all"),
):
	"""
		switch to custom search mode with the given groups enabled

		Parameters
		---
		groups
			a series of group ids 0-9, optionally separated by whitespace
	"""
	groups = groups.replace(" ", "")
	if not all(v in "0123456789" for v in groups):
		raise CommandError("search groups must be given as numbers 0-9")
	groups = CSGroupFlag.from_digits(groups)

	async with SerialGuard(ctx.message):
		async with ProgramGuard():
			await serial.send_message(EnabledCSGroups(enabled=groups))
		await serial.send_keys("F+S.>E")

@customsearch.command(ignore_extra=False)
async def enabled(ctx: Sc230Context):
	"""
		print which custom search groups are currently enabled
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		resp = await serial.send_message(EnabledCSGroups(), query=True)
		if resp.enabled != CSGroupFlag.none:
			enabled = ", ".join(str(v.id.value) for v in resp.enabled)
			msg = f"custom search group(s) {enabled} are enabled"
		else:
			msg = "no custom search groups are enabled"
		await ctx.reply(msg)

@customsearch.command(name="list", ignore_extra=False, aliases=["ls"])
async def list_(ctx: Sc230Context, *, groups: Optional[str]):
	"""
		print custom search groups
	"""
	if groups is None:
		from functools import reduce
		groups = reduce(lambda l, r: l | r, CSGroupFlag)
	else:
		groups = CSGroupFlag.from_digits(groups.replace(" ", ""))

	async with SerialGuard(ctx.message), ProgramGuard():
		lines = []
		for group in groups:
			group = await serial.send_message(CustomSearchGroup(id=group.id), query=True)
			modulation = "auto" if group.modulation == Modulation.auto else group.modulation.value
			lines.append(f"* {group.id.value} - **{group.name}**")
			lines.append(f"  * **{group.min}** to **{group.max}**")
			lines.append(f"  * **frequency step**: {group.step}")
			lines.append(f"  * **modulation**: {modulation}")
			lines.append(f"  * **attenuation**: {group.attenuation and "on" or "off"}")
			lines.append(f"  * **data skip**: {group.dataSkip and "on" or "off"}")
		await ctx.reply("\n".join(lines))
		raise CommandHandled

@customsearch.command(ignore_extra=False, aliases=["freq"])
async def frequency(
	ctx: Sc230Context,
	group: int = commands.parameter(displayed_name="group id"),
	minfreq: float = commands.parameter(displayed_name="min frequency"),
	maxfreq: float = commands.parameter(displayed_name="max frequency"),
):
	"""
		set custom search group min/max frequencies. note that on group 0 this may be overridden by `$freqrange`

		Parameters
		---
		minfreq
			lowest frequency this search group will scan (inclusive)
		maxfreq
			highest frequency this search group will scan (inclusive)

	"""
	if group < 0 or group > 9:
		raise CommandError("search groups must be given as numbers 0-9")
	group = CSGroupId(group)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_message(CustomSearchGroup(id=group, min=minfreq, max=maxfreq), update=True)

@customsearch.command(ignore_extra=False)
async def name(
	ctx: Sc230Context,
	group: int = commands.parameter(displayed_name="group id"),
	*,
	name: str,
):
	"""
		set custom search group name

		Parameters
		---
		name
			new name to give the group. limit 16 characters
	"""
	if group < 0 or group > 9:
		raise CommandError("search groups must be given as numbers 0-9")
	group = CSGroupId(group)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_message(CustomSearchGroup(id=group, name=name), update=True)

supportedBands = {
	(25.0, 54.0): ("VHF low/Petrol/CB/6m+10m ham", "VHF lo/Petrol/CB"),
	(108.0, 174.0): ("VHF high/military/air/2m ham", "VHF hi/mil/air"),
	(216.0, 224.98): ("1.25m ham", "1.25m ham"),
	(400.0, 512.0): ("UHF TV/70cm ham", "UHF TV/70cm ham"),
	(806.0, 823.9875): ("Public Service", "Public 1"),
	(849.0125, 868.9875): ("Public Service", "Public 2"),
	(894.0125, 956.0): ("Public Service", "Public 3"),
	(1240.0, 1300.0): ("25cm ham", "25cm ham"),
}
@customsearch.command(ignore_extra=False, aliases=["all"])
async def spectrum(ctx: Sc230Context):
	"""
		programs a set of search groups covering the hardware's frequency range, and begins scanning through them
	"""
	async with SerialGuard(ctx.message, typing=True):
		async with ProgramGuard():
			for (group, ((minfreq, maxfreq), (_, name))) in enumerate(supportedBands.items()):
				group += 1
				await serial.send_message(CustomSearchGroup(
					id=CSGroupId(group),
					name=name,
					min=minfreq,
					max=maxfreq,
					step=SearchStep.auto,
					modulation=Modulation.auto,
					attenuation=False,
					delay=2,
					dataSkip=False,
				))
			await serial.send_message(EnabledCSGroups(enabled=CSGroupFlag.group0))
		await serial.send_keys("F+S.>E")

@category("info")
@bot.command(ignore_extra=False)
async def spectrum(_, ctx: Sc230Context):
	freqmins = []
	freqmaxs = []
	descs = []
	for ((min, max), (description, _)) in supportedBands.items():
		min = serial.parse_frequency(serial.format_frequency(min), pretty=True)
		max = serial.parse_frequency(serial.format_frequency(max), pretty=True)
		freqmins.append(min)
		freqmaxs.append(max)
		descs.append(description)

	embed = discord.Embed()
	embed.add_field(name="Start", value="\n".join(freqmins))
	embed.add_field(name="End", value="\n".join(freqmaxs))
	embed.add_field(name="Description", value="\n".join(descs))
	await ctx.reply("The hardware supports the following bands:", embed=embed)
