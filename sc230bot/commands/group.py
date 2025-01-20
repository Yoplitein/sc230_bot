import discord
from discord.ext import commands

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..bot import Sc230Context, CommandError, BadSubcommandError, CommandHandled, StatusGuard, enforce_is_admin, bot
from ..serial import ProgramGuard, SerialError, SerialGuard
from ..serial.messages import CreateGroup, DeleteGroup, Group, System

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
		system = await serial.send_message(System(id=systemId), query=True)
		groups = []
		async for group in Group.get_all(system):
			locked = LOCKED_EMOJI if group.lockout != "0" else UNLOCKED_EMOJI
			groups.append(f"* {group.id} - {group.name} {locked}")
		if groups:
			groups = "\n".join(groups)
		else:
			groups = "*no groups found*"
		await ctx.reply(f"## {system.name}\n{groups}")

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
		group = await serial.send_message(CreateGroup(systemId=systemId))
		if group.groupId == -1:
			raise CommandError("could not create group")
		if name:
			await serial.send_message(Group(id=group.groupId, name=name), update=True)
		await ctx.reply(f"created new group with id {group.groupId}")
		raise CommandHandled

@group.command(ignore_extra=False, aliases=["del"])
async def delete(ctx: Sc230Context, *, ids: str = commands.parameter(displayed_name="group ids")):
	"""
		delete a group
	"""
	ids = list(int(v) for v in ids.split())
	async with SerialGuard(ctx.message), ProgramGuard():
		for id in ids:
			await serial.send_message(DeleteGroup(id=id))

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
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_message(Group(id=id, name=newName), update=True)

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
		await serial.send_message(Group(id=id, lockout=locked), update=True)
