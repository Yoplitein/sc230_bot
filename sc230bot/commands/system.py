import discord

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI
from ..bot import Sc230Context, CommandError, CommandHandled, StatusGuard, enforce_is_admin, bot
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids

@bot.command(ignore_extra=False)
async def systems(ctx: Sc230Context):
	"""
		list systems and their IDs
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

@bot.command(ignore_extra=False)
async def systemadd(ctx: Sc230Context, *, name: str = ""):
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

@bot.command(ignore_extra=False)
async def systemdel(ctx: Sc230Context, *, id: int):
	"""
		delete a system
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"DSY,{id}")

@bot.command(ignore_extra=False)
async def systemname(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a system's name. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"SIN,{id},{newName},,,,,,,")

@bot.command(ignore_extra=False)
async def systemlock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a system's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"SIN,{id},,,,{locked & 1},,,,")
