import discord

from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids
from ..bot import Sc230Context, CommandError, CommandHandled, StatusGuard, enforce_is_admin, bot

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
