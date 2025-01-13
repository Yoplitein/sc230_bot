import inspect
from logging import getLogger
from typing import Callable
import collections

from discord.ext.commands import Cog, Command

LOCKED_EMOJI = "\N{LOCK}"
UNLOCKED_EMOJI = "\N{BLACK RIGHT-POINTING TRIANGLE}\uFE0F"

COMMAND_HANDLED_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
COMMAND_FAILED_EMOJI = "\N{CROSS MARK}"

logger = getLogger(__name__)

class Peekable:
	def __init__(self, iterable):
		self.iter = iter(iterable)
		self.queue = collections.deque()

	def next(self):
		if self.queue:
			return self.queue.popleft()
		else:
			return next(self.iter)

	def peek(self, n = 0):
		if n < len(self.queue):
			return self.queue[n]
		for _ in range(n - len(self.queue) + 1):
			try:
				v = next(self.iter)
				self.queue.append(v)
			except StopIteration:
				return None
		return self.queue[n]

class RestartProcess(SystemExit):
	pass

categories = {}
def get_category(name: str, help = None) -> Cog:
	if name in categories:
		return categories[name]

	category = Cog()
	category.__cog_name__ = name
	categories[name] = category
	if help:
		category.description = help
	else:
		category.description = None
	return category

def category(name: str):
	def inner(cmd: Command):
		from .bot import bot

		assert isinstance(cmd, Command), "category decorator must come before command decorator"
		assert len(inspect.getfullargspec(cmd.callback).args) >= 2, "categorized command must take cog parameter before ctx parameter"

		category = get_category(name)
		bot.remove_command(cmd.name)
		cmd.cog = category
		if "ctx" in cmd.params:
			cmd.params.pop("ctx")
		category.__cog_commands__ += (cmd,)
		return cmd
	return inner

get_category("info", "commands that print out general information")
get_category("input", "direct scanner interaction")
get_category("inspection", "prints out various datum")
get_category("locking", "lockout (or simply locking) prevents scanning a particular frequency, channel, group, or system")
get_category("programming", "commands to manipulate the device's database of candidate frequencies while in normal scan mode")
get_category("scanning", "commands to enter scanning modes")
