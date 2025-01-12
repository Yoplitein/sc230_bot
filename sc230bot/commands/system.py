import discord
from discord.ext import commands

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..bot import Sc230Context, CommandError, BadSubcommandError, CommandHandled, StatusGuard, enforce_is_admin, bot
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids

@category("programming")
@bot.group(invoke_without_command=True, aliases=["systems", "sys", "s"])
async def system(_, ctx: Sc230Context):
	"""
		system management

		a system is a collection of groups, which in turn contain channels (individual frequencies)

		the device has a limit of 200 systems
	"""
	raise BadSubcommandError(ctx)

@system.command(name="list", ignore_extra=False, aliases=["ls"])
async def list_(ctx: Sc230Context):
	"""
		list systems and their ids
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		head = int((await serial.send_raw(b"SIH")).split(",")[1])
		tail = int((await serial.send_raw(b"SIT")).split(",")[1])

		systems = []
		for id in await walk_ids(head, tail):
			match (await serial.send_raw(f"SIN,{id}")).split(","):
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

@system.command(ignore_extra=False)
async def add(ctx: Sc230Context, *, name: str = commands.parameter(default="", displayed_default="device generated")):
	"""
		add a new system, optionally setting its name
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		[_, id] = (await serial.send_raw(b"CSY,CNV")).split(",")
		if id == "-1":
			raise CommandError("could not create system")
		if name:
			name = serial.sanitize_string(name)
			await serial.send_raw(f"SIN,{id},{name},,,,,,,")
		await ctx.message.reply(f"created new system with id {id}")
		raise CommandHandled

@system.command(ignore_extra=False, aliases=["del"])
async def delete(ctx: Sc230Context, *, id: int):
	"""
		delete a system
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"DSY,{id}")

@system.command(ignore_extra=False)
async def name(
	ctx: Sc230Context,
	id: int = commands.parameter(displayed_name="system id"),
	*,
	newName: str = commands.parameter(displayed_name="new name"),
):
	"""
		set a system's name

		Parameters
		---
		newName
			new name to give this system. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"SIN,{id},{newName},,,,,,,")

@system.command(ignore_extra=False)
async def lock(
	ctx: Sc230Context,
	id: int = commands.parameter(displayed_name="system id"),
	locked: bool = commands.parameter(),
):
	"""
		set a system's lockout status

		Parameters
		---
		locked
			system will be locked out if true (skipped during scanning)
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"SIN,{id},,,,{locked & 1},,,,")
