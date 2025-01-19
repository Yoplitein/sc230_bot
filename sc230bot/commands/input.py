import sqlite3
import discord
from discord.ext import commands

from . import logger
from .. import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI, category
from ..bot import Sc230Context, CommandError, BadSubcommandError, CommandHandled, StatusGuard, enforce_is_admin, bot
from ..serial import ProgramGuard, SerialError, SerialGuard, walk_ids

db = sqlite3.connect("./input_users.db")
with db:
	if db.execute("SELECT count() FROM sqlite_schema").fetchone() == (0,):
		db.executescript("""
			CREATE TABLE keyusers (
				id INT PRIMARY KEY
			) WITHOUT ROWID;
			CREATE TABLE rawusers (
				id INT PRIMARY KEY
			) WITHOUT ROWID;
		""")

def isKeyInputUser(id: int):
	return db.execute("SELECT count() FROM keyusers WHERE id = ?", (id,)).fetchone() == (1,)

def addKeyInputUser(id: int):
	with db:
		db.execute("INSERT INTO keyusers VALUES(?)", (id,))

def removeKeyInputUser(id: int):
	with db:
		db.execute("DELETE FROM keyusers WHERE id = ?", (id,))

def isRawInputUser(id: int):
	return db.execute("SELECT count() FROM rawusers WHERE id = ?", (id,)).fetchone() == (1,)

def addRawInputUser(id: int):
	with db:
		db.execute("INSERT INTO rawusers VALUES(?)", (id,))

def removeRawInputUser(id: int):
	with db:
		db.execute("DELETE FROM rawusers WHERE id = ?", (id,))

@category("input")
@bot.command(ignore_extra=False)
async def key(_, ctx: Sc230Context, *, keys: str):
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
			> - Scroll right / Next channel / Continue scan upward
			< - Scroll left / Previous channel / Continue scan downward
			^ - Enter (presses scrollwheel)
			P - Power

		key chords:
			Keys can be sent while other keys are held by following the held key with a `+`.
			E.g. `F+P` will hold Func, press power, and finally release func.
	"""
	await ctx.send_keys(keys)

@category("input")
@bot.command(ignore_extra=False)
async def keyon(_, ctx: Sc230Context):
	"""
		enable treating all non-command messages as keycodes

		see `key` command's help for list of keycodes
	"""
	if isRawInputUser(ctx.author.id):
		raise CommandError("you are already in raw input mode")
	if isKeyInputUser(ctx.author.id):
		raise CommandError("you are already in key input mode")
	addKeyInputUser(ctx.author.id)

@category("input")
@bot.command(ignore_extra=False)
async def keyoff(_, ctx: Sc230Context):
	"""
		disable keycode messages mode
	"""
	if not isKeyInputUser(ctx.author.id):
		raise CommandError("you are not in key input mode")
	removeKeyInputUser(ctx.author.id)

@category("input")
@bot.command(ignore_extra=False)
async def raw(_, ctx: Sc230Context, *, input: str):
	"""
		admin only. allows transmitting raw protocol data

		see $info for protocol documentation
	"""
	enforce_is_admin(ctx.author)
	await ctx.send_raw(input.split("\n"))
	raise CommandHandled

@category("input")
@bot.command(ignore_extra=False)
async def rawon(_, ctx: Sc230Context):
	"""
		enable treating all non-command messages as raw protocol data
	"""
	enforce_is_admin(ctx.author)
	if isRawInputUser(ctx.author.id):
		raise CommandError("you are already in raw input mode")
	if isKeyInputUser(ctx.author.id):
		raise CommandError("you are already in key input mode")
	addRawInputUser(ctx.author.id)

@category("input")
@bot.command(ignore_extra=False)
async def rawoff(_, ctx: Sc230Context):
	"""
		disable raw protocol messages mode
	"""
	if not isRawInputUser(ctx.author.id):
		raise CommandError("you are not in raw input mode")
	removeRawInputUser(ctx.author.id)
