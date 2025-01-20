import asyncio
import itertools
import time
from typing import Optional

import discord
import serial as pyserial

from .messages import Key, KeyCode, KeyState

from .. import config, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI

logger = getLogger(__name__)

allowedKeys = b"MFHSLC1234567890.E><^P"

class SerialError(Exception):
	def __init__(self, msg):
		self.msg = msg

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
	def enforce(Self):
		assert Self.lock.locked(), "expected serial lock to be locked but it is unlocked"
		assert serial is not None, "expected serial port to be open but it is `None`"

class ProgramGuard:
	level = 0

	@classmethod
	async def __aenter__(Self):
		Self.level += 1
		if Self.level > 1:
			return
		await send_message(messages.ExitProgramming())
		await send_message(messages.EnterProgramming())

	@classmethod
	async def __aexit__(Self, *_):
		Self.level -= 1
		if Self.level == 0:
			await send_message(messages.ExitProgramming())

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

def read_line(*, timeout: float = 5) -> bytes:
	SerialGuard.enforce()
	res = bytes()
	started = time.time()
	while True:
		read = serial.read(1)
		if len(read) == 0:
			time.sleep(0.1)
			if time.time() - started > timeout:
				err = SerialError("timed out trying to read line")
				logger.error(f"{err.msg} ({res=})")
				raise err
			continue
		res += read
		if read == b"\r":
			break
	logger.debug(f"serial read  {res!r}")

	res = res.strip()
	if res == b"ERR":
		raise SerialError("device received unknown command")
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
	serial.flush()

async def send_raw(line: str | bytes, *, timeout: float = 5):
	def inner():
		nonlocal line
		if type(line) is str:
			line = line.encode("ascii")
		write_line(line)
		return read_line(timeout=timeout).decode("ascii")
	return await asyncio.to_thread(inner)

async def send_key(keycode: KeyCode, state: KeyState = KeyState.press):
	assert type(keycode) is KeyCode
	await send_message(Key(keycode=keycode, state=state))

async def send_keys(keys: str):
	assert type(keys) is str

	parsed = []
	try:
		it = iter(keys)
		while True:
			keycode = KeyCode(next(it))

			holdCount = 0
			it, peek = itertools.tee(it)
			try:
				while next(peek) == "+":
					holdCount += 1
			except StopIteration:
				pass

			if holdCount == 0:
				parsed.append(keycode)
			else:
				parsed.append((keycode, holdCount))
				for _ in range(holdCount):
					next(it)
					pass
	except StopIteration:
		pass

	pressed = {}
	try:
		for keyspec in parsed:
			match keyspec:
				case (keycode, holdCount):
					curCount = pressed.get(keycode, 0)
					if curCount == 0:
						await send_key(keycode, KeyState.hold)
					pressed[keycode] = curCount + holdCount
				case keycode:
					await send_key(keycode)
					for keycode, holdCount in pressed.items():
						if holdCount == 1:
							await send_key(keycode, KeyState.release)
						if holdCount > 0:
							pressed[keycode] = holdCount - 1
	except:
		for keycode in pressed:
			try:
				await send_key(keycode, KeyState.release)
			except:
				pass
		raise

async def send_message[Msg](request: Msg, *, query = False, update = False, timeout: float = 5) -> Msg:
	Msg = type(request)
	response = await send_raw(request.write(query=query), timeout=timeout)

	[cmd, rest] = response.split(",", 1)
	if cmd != Msg.command_name():
		raise SerialError(f"exchanging message {Msg.command_name()!r} (`{Msg.__name__}`) but read back different type {cmd!r}")
	match rest:
		case "ERR":
			raise SerialError(f"encounterd format/value error while exchanging message {Msg.command_name()!r} (`{Msg.__name__}`)")
		case "NG":
			raise SerialError(f"{Msg.command_name()!r} (`{Msg.__name__}`) message is invalid in current mode")
		case "FER":
			raise SerialError(f"encounterd framing error while exchanging message {Msg.command_name()!r} (`{Msg.__name__}`)")
		case "ORER":
			raise SerialError(f"encounterd overrun error while exchanging message {Msg.command_name()!r} (`{Msg.__name__}`)")

	response = Msg.read(response, update=update)
	if query and hasattr(Msg, "id"):
		assert request.id is not None
		response.id = request.id
	return response

def format_channel(id, name, freq, locked = None):
	if type(freq) is not str or freq[-3:].lower() != "mhz":
		freq = parse_frequency(format_frequency(freq), pretty=True)
	if name.endswith("MHz"):
		name = ""
	else:
		name = f" ({name})"
	if locked != None:
		locked = LOCKED_EMOJI if locked else UNLOCKED_EMOJI
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

def parse_frequency(freq: str, *, pretty = False) -> float:
	"Parse frequency read from protocol"
	freq = int(freq)
	if freq < 0:
		return -1
	freq /= 1e4
	if pretty:
		return f"{freq}MHz"
	return freq

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
