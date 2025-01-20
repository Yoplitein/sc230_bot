import discord
from discord.ext import commands

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..bot import Sc230Context, CommandError, BadSubcommandError, CommandHandled, StatusGuard, enforce_is_admin, bot
from ..serial import ProgramGuard, SerialError, SerialGuard
from ..serial.messages import CreateSystem, DeleteSystem, System

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
		systems = []
		async for sys in System.get_all():
			locked = sys.lockout and LOCKED_EMOJI or UNLOCKED_EMOJI
			systems.append(f"* {sys.id} - {sys.name} {locked}")
		if systems:
			await ctx.reply("\n".join(systems))
		else:
			await ctx.reply("no systems found")
		raise CommandHandled

@system.command(ignore_extra=False)
async def add(ctx: Sc230Context, *, name: str = commands.parameter(default="", displayed_default="device generated")):
	"""
		add a new system, optionally setting its name
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		system = await serial.send_message(CreateSystem())
		if system.id == -1:
			raise CommandError("could not create system")
		if name:
			await serial.send_message(System(id=system.id, name=name), update=True)
		await ctx.reply(f"created new system with id {system.id}")
		raise CommandHandled

@system.command(ignore_extra=False, aliases=["del"])
async def delete(ctx: Sc230Context, *, ids: str = commands.parameter(displayed_name="system ids")):
	"""
		delete a system
	"""
	ids = list(int(v) for v in ids.split())
	async with SerialGuard(ctx.message), ProgramGuard():
		for id in ids:
			await serial.send_message(DeleteSystem(id=id))

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
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_message(System(id=id, name=newName), update=True)

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
		await serial.send_message(System(id=id, lockout=locked), update=True)
