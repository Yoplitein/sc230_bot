import asyncio
import time
from typing import Optional

import discord
import serial as pyserial

from . import config, Peekable, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI

logger = getLogger(__name__)

allowedKeys = b"MFHSLC1234567890.E><^P"

class SerialError(Exception):
	def __init__(self, ty, msg, *rest):
		self.ty = ty
		self.msg = msg
		self.rest = rest

class SerialGuard:
	lock = asyncio.Lock()

	def __init__(self, msg: discord.Message, typing: bool = False):
		self.msg = msg
		self.typing = typing and msg.channel.typing() or None

	async def __aenter__(self):
		pendingMsg = None
		if self.lock.locked():
			pendingMsg = await self.msg.reply("waiting for other command(s) to finish")

		await self.lock.acquire()
		self.lockStart = time.time()
		if pendingMsg:
			asyncio.create_task(pendingMsg.delete())

		if self.typing:
			await self.typing.__aenter__()

		open()
		logger.debug(f"serial locked for author={self.msg.author.name!r} content={self.msg.content!r}")

	async def __aexit__(self, *_):
		close()
		if self.typing:
			await self.typing.__aexit__(*_)
		self.lock.release()
		lockDuration = time.time() - self.lockStart
		logger.debug(f"serial unlocked, held for {lockDuration:02f} seconds")

	@classmethod
	def enforce(self):
		assert self.lock.locked(), "expected serial lock to be locked but it is unlocked"
		assert serial is not None, "expected serial port to be open but it is `None`"

class ProgramGuard:
	level = 0

	@classmethod
	async def __aenter__(self):
		self.level += 1
		if self.level > 1:
			return
		await send_raw(b"EPG")
		await send_raw(b"PRG")

	@classmethod
	async def __aexit__(self, *_):
		self.level -= 1
		if self.level == 0:
			await send_raw(b"EPG")

class IdWalker:
	def __init__(self, seed: int):
		from collections import deque
		assert type(seed) is int
		self.queue = deque([seed])
		self.visited = set([seed])

	def __iter__(self):
		return self

	def __next__(self):
		if not self.queue:
			raise StopIteration
		return self.queue.popleft()

	def add(self, revIndex: int, fwdIndex: int):
		assert type(revIndex) is int
		assert type(fwdIndex) is int
		for v in [revIndex, fwdIndex]:
			if v < 0 or v in self.visited:
				continue
			self.visited.add(v)
			self.queue.append(v)

serial: Optional[pyserial.Serial] = None
def open():
	global serial
	assert SerialGuard.lock.locked()
	assert serial is None
	serial = pyserial.Serial(
		config.get("serial_port"),
		baudrate=config.get("serial_baud"),
		stopbits=1,
		bytesize=8,
		parity=pyserial.PARITY_NONE,
		xonxoff=False,
		rtscts=False,
		dsrdtr=False,
		timeout=0,
		write_timeout=0,
	)

def close():
	global serial
	SerialGuard.enforce()
	serial.close()
	serial = None

def read_line() -> bytes:
	SerialGuard.enforce()
	res = bytes()
	started = time.time()
	while True:
		read = serial.read(1)
		if len(read) == 0:
			time.sleep(0.1)
			if time.time() - started > 5:
				err = SerialError("timed out trying to read line")
				logger.error(f"{err.msg} ({res=})")
				raise err
			continue
		res += read
		if read == b"\r":
			break
	logger.debug(f"serial read  {res!r}")

	res = res.strip()
	match res.split(b","):
		case [b"ERR", *rest]:
			raise SerialError("command error", rest)
		case [b"NG", *rest]:
			raise SerialError("invalid state for command", rest)
		case [b"FER", *rest]:
			raise SerialError("framing error", rest)
		case [b"ORER", *rest]:
			raise SerialError("overrun error", rest)
		case _:
			return res

def write_line(line: bytes):
	SerialGuard.enforce()
	assert type(line) is bytes, "type error"
	line += b"\r"
	logger.debug(f"serial write {line!r}")
	while True:
		written = serial.write(line)
		line = line[written:]
		if len(line) == 0:
			break

async def send_raw(line: str | bytes):
	def inner():
		nonlocal line
		if type(line) is str:
			line = line.encode("ascii")
		write_line(line)
		return read_line().decode("ascii")
	return await asyncio.to_thread(inner)

async def send_key(key: int | str | bytes, mode = "P"):
	if type(key) in [str, bytes]:
		key = ord(key)
	assert key in allowedKeys, f"{chr(key)!r} is not a valid key"
	await send_raw(f"KEY,{chr(key)},{mode}")

async def send_keys(keys: str | bytes):
	if type(keys) is str:
		keys = keys.encode("ascii")

	parsed = []
	try:
		it = Peekable(keys)
		while True:
			key = bytes([it.next()])

			holdCount = 0
			peekIndex = 0
			while True:
				match it.peek(peekIndex):
					case c if c and chr(c) == "+":
						holdCount += 1
						peekIndex += 1
					case _:
						break
			if holdCount > 0:
				parsed.append((key, holdCount))
				for _ in range(holdCount):
					it.next()
					pass
			else:
				parsed.append(key)
	except StopIteration:
		pass

	pressed = {}
	try:
		for keyspec in parsed:
			match keyspec:
				case (char, count):
					curCount = pressed.get(char, 0)
					if curCount == 0:
						await send_key(char, "H")
					pressed[char] = curCount + count
				case char:
					await send_key(char)
					for key, count in pressed.items():
						if count == 1:
							await send_key(key, "R")
						if count > 0:
							pressed[key] = count - 1
	except:
		for key in pressed:
			try:
				await send_key(key, "R")
			except:
				pass
		raise

async def walk_ids(head: int, tail: int) -> list[int]:
	assert SerialGuard.lock.locked(), "trying to walk_ids without serial lock held"
	assert ProgramGuard.level > 0, "trying to walk_ids outside of program mode"

	if head == -1:
		return []

	ids = [head]
	while tail != "-1" and head != tail:
		match (await send_raw(f"FWD,{head}")).split(","):
			case ["FWD", "-1"]:
				assert False, "forward id is -1???"
			case ["FWD", next]:
				head = int(next)
		ids.append(head)
	return ids

def format_channel(id, name, freq, locked = None):
	freq = parse_frequency(freq)
	if name.endswith("MHz"):
		name = ""
	else:
		name = f" ({name})"
	if locked != None:
		locked = LOCKED_EMOJI if locked != "0" else UNLOCKED_EMOJI
		locked = " " + locked
	return f"{id} - {freq}{name}{locked}"

def format_frequency(freq: str | float) -> str:
	"Format frequency to be sent over protocol"
	match freq:
		case str():
			if freq[-3:].lower() == "mhz":
				freq = freq[:-3]
			freq = float(freq)
	freq = int(freq * 1e4)
	return f"{freq:08}"

def parse_frequency(freq: str) -> str:
	"Parse frequency read from protocol"
	freq = int(freq) / 1e4
	return f"{freq}MHz"

def sanitize_string(str: str) -> str:
	return str.translate({
		ord(","): "",
		ord("\r"): "",
		ord("\n"): " ",
	})[:16]

def replace_special_chars(str: str) -> str:
	# TODO: figure out how to reverse this in `sanitize_string`
	return str.translate({
		0x10: "\N{UPWARDS ARROW}",
		0x11: "\N{DOWNWARDS ARROW}",
	})
