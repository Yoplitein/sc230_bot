import asyncio
import glob
import logging
import os
import sys
import time

import discord
from discord.ext import commands

from . import config, serial, commands as _, getLogger
from .bot import Sc230Context, bot

async def main():
	serial.open()

	discord.utils.setup_logging(root = False)
	consoleHandler = logging.getLogger(discord.__name__).handlers[-1]
	for logger in getLogger.loggers:
		logger.addHandler(consoleHandler)
		logger.setLevel(int(os.getenv("LOG_LEVEL", logging.INFO)))

	infoCmds = commands.Cog()
	infoCmds.__cog_name__ = "info"
	for name in config.get("info_commands"):
		def pythonpls(name):
			async def cmd(_, ctx: Sc230Context):
				files = glob.glob(name + "*.txt")
				files.sort()
				for file in files:
					with open(file, "r") as f:
						contents = f.read().strip()
						await ctx.reply(contents)
			cmd = commands.Command(cmd, cog=infoCmds, name=name)
			cmd.params.clear() # misdetects `ctx` as a parameter, so fix that up
			infoCmds.__cog_commands__ += (cmd,)
		pythonpls(name)

	prefixes = config.get("command_prefix")
	if type(prefixes) is str:
		prefixes = [prefixes]
	bot.command_prefix = commands.when_mentioned_or(*prefixes)
	try:
		async with bot:
			await bot.add_cog(infoCmds)
			await bot.start(config.get("token"))
	finally:
		logger.info("exiting")

if __name__ == "__main__":
	try:
		if "--auto-restart" not in sys.argv or "--child" in sys.argv:
			asyncio.run(main())
		else:
			import subprocess

			args = sys.orig_argv + ["--child"]
			fails = 0
			while True:
				print("Supervisor (re-)starting bot", file=sys.stderr)
				result = subprocess.run(
					args,
					stdin=None,
					stdout=sys.stdout,
					stderr=sys.stderr,
				)
				if result.returncode == 0:
					fails = 0
				else:
					fails += 1
					if fails >= 5:
						print("Supervisor exiting due to restart loop")
						raise SystemExit(1)
					time.sleep(1)
	except KeyboardInterrupt:
		raise SystemExit(1)
