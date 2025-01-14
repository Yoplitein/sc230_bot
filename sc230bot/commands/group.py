import discord
from discord.ext import commands

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids
from ..bot import Sc230Context, CommandError, BadSubcommandError, CommandHandled, StatusGuard, enforce_is_admin, bot

@category("programming")
@bot.group(invoke_without_command=True, aliases=["groups", "g"])
async def group(_, ctx: Sc230Context):
	"""
		group management

		a group is a collection of channels, or frequencies. contained within a system

		the device has a limit of 20 groups per system
	"""
	raise BadSubcommandError(ctx)

@group.command(name="list", ignore_extra=False, aliases=["ls"])
async def list_(ctx: Sc230Context, systemId: int = commands.parameter(displayed_name="system id")):
	"""
		list groups and ids for the given system
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		(_, _, systemName, _, _, _, _, _, _, _, _, _, head, tail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
		[head, tail] = map(int, [head, tail])

		groups = []
		for id in await walk_ids(head, tail):
			match (await serial.send_raw(f"GIN,{id}")).split(","):
				case ["GIN", _, name, _, locked, *_]:
					locked = LOCKED_EMOJI if locked != "0" else UNLOCKED_EMOJI
					groups.append(f"* {id} - {name} {locked}")
				case resp:
					logger.debug(f"weird response for group {id}: {resp!r}")
		if groups:
			groups = "\n".join(groups)
			await ctx.reply(f"## {systemName}\n{groups}")
		else:
			await ctx.reply("no groups found")

		raise CommandHandled

@group.command(ignore_extra=False)
async def add(
	ctx: Sc230Context,
	systemId: int = commands.parameter(displayed_name="system id"),
	*,
	name: str = commands.parameter(default="",
	displayed_default="device generated"),
):
	"""
		add a new group to a system, optionally setting its name
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		if (await serial.send_raw(f"SIN,{systemId}")).split(",", 1)[1] == "ERR":
			raise CommandError(f"system {systemId} does not exist")
		[_, id] = (await serial.send_raw(f"AGC,{systemId}")).split(",")
		if id == "-1":
			raise CommandError("could not create group")
		if name:
			await serial.send_raw(f"GIN,{id},{name},,")
		await ctx.reply(f"created new group with id {id}")
		raise CommandHandled

@group.command(ignore_extra=False, aliases=["del"])
async def delete(ctx: Sc230Context, *, ids: str = commands.parameter(displayed_name="group ids")):
	"""
		delete a group
	"""
	ids = list(int(v) for v in ids.split())
	async with SerialGuard(ctx.message), ProgramGuard():
		for id in ids:
			await serial.send_raw(f"DGR,{id}")

@group.command(ignore_extra=False)
async def name(
	ctx: Sc230Context,
	id: int = commands.parameter(displayed_name="group id"),
	*,
	newName: str = commands.parameter(displayed_name="new name"),
):
	"""
		set a group's name

		Parameters
		---
		newName
			new name to give this group. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"GIN,{id},{newName},,")

@group.command(ignore_extra=False)
async def lock(
	ctx: Sc230Context,
	id: int = commands.parameter(displayed_name="group id"),
	locked: bool = commands.parameter(),
):
	"""
		set a group's lockout status

		Parameters
		---
		locked
			group will be locked out if true (skipped during scanning)
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"GIN,{id},,,{locked & 1}")
