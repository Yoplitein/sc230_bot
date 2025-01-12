import asyncio
import collections
import glob
import logging
import os
import subprocess
import sys
import time

import discord
from discord.ext import commands

from . import config, RestartProcess, getCategory, category, categories, logger
from .bot import Sc230Context, CommandHandled, bot

async def main():
	from . import commands as _
	from .commands import channel as _
	from .commands import custom_search as _
	from .commands import group as _
	from .commands import input as _
	from .commands import system as _

	discord.utils.setup_logging(root=False)
	consoleHandler = logging.getLogger(discord.__name__).handlers[-1]
	logger.addHandler(consoleHandler)
	logger.setLevel(int(os.getenv("LOG_LEVEL", logging.INFO)))
	logger.info("logging initialized")

	for name in config.get("info_commands"):
		def pythonpls(name):
			global category
			@category("info")
			@commands.command(name=name)
			async def cmd(_, ctx: Sc230Context):
				files = glob.glob(name + "*.txt")
				files.sort()
				for file in files:
					with open(file, "r") as f:
						contents = f.read().strip()
						await ctx.reply(contents)
				raise CommandHandled
		pythonpls(name)

	prefixes = config.get("command_prefix")
	if type(prefixes) is str:
		prefixes = [prefixes]
	bot.command_prefix = commands.when_mentioned_or(*prefixes)
	try:
		async with bot:
			for category in categories.values():
				logger.debug(f"adding category {category.__cog_name__}")
				await bot.add_cog(category)
			await bot.start(config.get("token"))
	finally:
		logger.info("exiting")

def supervisorMain():
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
				print("Supervisor waiting for changes due to restart loop", file=sys.stderr)
				waitForChanges()
				fails = 0
				continue
			time.sleep(2.5)

def waitForChanges():
	inotify = None
	try:
		from pathlib import Path
		from inotify_simple import INotify, flags

		root = Path(__file__).parent
		assert root.is_dir()
		dirs = []
		queue = collections.deque([root])
		while queue:
			head = queue.popleft()
			dirs.append(head)
			for dir in head.iterdir():
				if dir.name == "__pycache__":
					continue
				if dir.is_dir():
					queue.append(dir)
		pwd = os.path.abspath(".")
		dirs = [dir.relative_to(pwd) for dir in dirs]

		inotify = INotify()
		for dir in dirs:
			print(f"watching directory `{dir}`", file=sys.stderr)
			inotify.add_watch(dir, flags.CLOSE_WRITE)
		file = inotify.read()[0].name
		print(f"`{file}` modified, restarting")
	finally:
		if inotify:
			inotify.close()

def restart():
	if "--auto-restart" in sys.argv:
		logger.info("restart requested; deferring to supervisor")
	else:
		logger.info("restarting process without supervisor")
		import gc
		gc.collect()
		args = sys.orig_argv
		os.execvp(args[0], args)

if __name__ == "__main__":
	try:
		if "--auto-restart" not in sys.argv or "--child" in sys.argv:
			try:
				asyncio.run(main())
			except RestartProcess:
				restart()
		else:
			supervisorMain()
	except KeyboardInterrupt:
		raise SystemExit(1)
