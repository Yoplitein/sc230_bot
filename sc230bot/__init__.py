import collections
import logging

LOCKED_EMOJI = "\N{LOCK}"
UNLOCKED_EMOJI = "\N{BLACK RIGHT-POINTING TRIANGLE}\uFE0F"

COMMAND_HANDLED_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
COMMAND_FAILED_EMOJI = "\N{CROSS MARK}"

def getLogger(name: str) -> logging.Logger:
	logger = logging.getLogger(__name__)
	getLogger.loggers.append(logger)
	return logger
getLogger.loggers = []
logger = logging.getLogger(__name__)

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
