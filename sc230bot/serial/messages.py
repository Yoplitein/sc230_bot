from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, fields, is_dataclass
import dataclasses
from enum import Enum, Flag
from logging import getLogger
from typing import Any, Literal, Optional, Self, Type, Union, override
import typing

from .. import serial

logger = getLogger(__name__)

class ProtocolMessage(ABC):
	@staticmethod
	@abstractmethod
	def command_name() -> str:
		...

	def write(self, *, query = False) -> str:
		assert is_dataclass(self)

		msg = self
		if hasattr(msg, "pre_write"):
			msg = type(self)(**asdict(self))
			msg.pre_write(query=query)

		if query:
			# certain commands have separate forms when retrieving data vs updating it
			# system/group/channel info commands pass an id when querying, else just the command name
			if hasattr(msg, "id"):
				assert msg.id is not None
				ty = next(v.type for v in fields(self) if v.name == "id")
				id = format_value(ty, msg.id)
				return f"{msg.command_name()},{id}"
			return msg.command_name()

		values = [msg.command_name()]
		for field in fields(msg):
			if not field.metadata["write"]:
				continue
			value = getattr(msg, field.name)
			value = format_value(field.type, value)
			values.append(value)
		return ",".join(values)

	@classmethod
	def read[M](Msg: Type[M], line: str, *, update = False) -> M:
		assert is_dataclass(Msg)

		values = line.strip().split(",")
		match values.pop(0):
			case v if v == Msg.command_name():
				pass
			case otherCommand:
				assert False, f"expected command name {Msg.command_name()} but got {otherCommand}"

		if update:
			match values.pop(0):
				case "OK" if len(values) == 0:
					pass
				case other:
					values.insert(0, other)
					assert False, f"expecting update ack but have remaining values: `{",".join(values)}`"
			return

		msgFields = {
			field.name: field
			for field in fields(Msg)
		}
		result = {}
		populatedFields = set()
		for fieldName, info in msgFields.items():
			if not info.metadata["read"]:
				continue
			populatedFields.add(fieldName)
			try:
				result[fieldName] = values.pop(0)
			except IndexError as cause:
				err = serial.SerialError(f"couldn't read {Msg.__name__!r} ({Msg.command_name()!r}) because no values left when trying to read field {fieldName!r}")
				err.add_note(f"parsing line {line!r} {update=}")
				raise err from cause
		result = Msg(**result)

		if hasattr(result, "pre_read"):
			result.pre_read()
		for fieldName in populatedFields:
			type = msgFields[fieldName].type
			value = getattr(result, fieldName)
			try:
				value = parse_value(type, value)
			except Exception as err:
				err.add_note(f"while trying to parse field {fieldName!r} of {Msg.__name__!r} ({Msg.command_name()!r})")
				raise err
			setattr(result, fieldName, value)
		if hasattr(result, "post_read"):
			result.post_read()
		return result

type MemoryId = int

def protocol_field(*, read=False, write=False, **fieldKwargs) -> dataclasses.Field:
	assert read or write, "protocol fields must be at least one of read or write, or both"
	kwargs = dict(
		default=None,
		kw_only=True,
		metadata=dict(read=read, write=write)
	)
	kwargs.update(fieldKwargs)
	f = field(**kwargs)
	return f

def format_value[T](expectedType: Type[T], value: T) -> str:
	if value is None:
		return ""

	if isinstance(expectedType, typing.TypeAliasType):
		expectedType = expectedType.__value__
	if getattr(expectedType, "__origin__", None) == Literal:
		allowed = expectedType.__args__
		for v in allowed:
			if value == v:
				return v
		raise ValueError(f"formatting field with literal type and got {value!r} when expecting one of {", ".join(map(lambda v: repr(str(v)), allowed))}")
	realType = type(value)
	if getattr(expectedType, "__origin__", None) == Union:
		variants = expectedType.__args__
		for variant in variants:
			if variant is realType:
				return format_value(variant, value)
		raise ValueError(f"formatting field with union type {expectedType} but none match for value {value!r}")
	assert realType is expectedType, f"expecting {expectedType} but got {realType} when formatting value {value!r}"

	if issubclass(expectedType, Flag):
		bits = len(bin(max(v.value for v in expectedType))[2:])
		return f"{value.value:0{bits}b}"
	if issubclass(expectedType, Enum):
		return str(value.value)

	if expectedType == str:
		return serial.sanitize_string(value)
	if expectedType == int:
		return str(value)
	if expectedType == float:
		return serial.format_frequency(value)
	if expectedType == bool:
		return f"{value & 1}"

	raise TypeError(f"don't know how to format {expectedType.__name__} for protocol")

def parse_value[T](expectedType: Type[T], value: str) -> T:
	if value == "":
		return None

	if isinstance(expectedType, typing.TypeAliasType):
		expectedType = expectedType.__value__
	if getattr(expectedType, "__origin__", None) == Literal:
		allowed = expectedType.__args__
		for v in allowed:
			if str(v) == value:
				return v
		raise ValueError(f"parsing field with literal type and got {value!r} when expecting one of {", ".join(map(lambda v: repr(str(v)), allowed))}")
	if getattr(expectedType, "__origin__", None) == Union:
		variants = expectedType.__args__
		for variant in variants:
			try:
				return parse_value(variant, value)
			except:
				pass
		raise ValueError(f"parsing field with union type {expectedType} but none match for value {value!r}")

	if issubclass(expectedType, Flag):
		try:
			value = int(value, 2)
		except Exception as err:
			err.add_note(f"when parsing {value!r}")
			raise err
		return expectedType(value)
	if issubclass(expectedType, Enum):
		valueTy = expectedType.mro()[1]
		assert valueTy is not Enum, f"enum type {expectedType.__name__} does not specify the type of its values"
		value = valueTy(value)
		return expectedType(value)

	if expectedType == str:
		return serial.replace_special_chars(value)
	if expectedType == int:
		return expectedType(value)
	if expectedType == float:
		return serial.parse_frequency(value)
	if expectedType == bool:
		match value.lower():
			case "0":
				return False
			case "1":
				return True
			case _:
				raise ValueError(f"given unrecognized value `{value!r}` for bool field")

	raise TypeError(f"don't know how to format {expectedType.__name__} for protocol")

class KeyCode(str, Enum):
	menu = 'M'
	function = 'F'
	hold = 'H'
	scan = 'S'
	lockout = 'L'
	car = 'C'
	key1 = '1'
	key2 = '2'
	key3 = '3'
	key4 = '4'
	key5 = '5'
	key6 = '6'
	key7 = '7'
	key8 = '8'
	key9 = '9'
	key0 = '0'
	no = '.'
	enter = 'E'
	right = '>'
	left = '<'
	enter2 = '^'
	power = 'P'

class KeyState(str, Enum):
	press = 'P'
	longPress = 'L'
	hold = 'H'
	release = 'R'

@dataclass
class Key(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)
	keycode: KeyCode = protocol_field(write=True)
	state: KeyState = protocol_field(write=True)

	def __post_init__(self):
		if self.keycode is not None and self.state is None:
			self.state = KeyState.press

	@override
	@staticmethod
	def command_name():
		return "KEY"

@dataclass
class MemoryUsage(ProtocolMessage):
	percentage: int = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "MEM"

@dataclass
class MemoryBlocks(ProtocolMessage):
	free: int = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "RMB"

@dataclass
class FactoryReset(ProtocolMessage):
	@override
	@staticmethod
	def command_name():
		return "CLR"

@dataclass
class EnterProgramming(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "PRG"

@dataclass
class ExitProgramming(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "EPG"

class SearchStep(int, Enum):
	auto = 0, "auto"
	_500 = 500, "5k"
	_625 = 625, "6.25k"
	_750 = 750, "7.5k"
	_1000 = 1000, "10k"
	_1250 = 1250, "12.5k"
	_1500 = 1500, "15k"
	_2500 = 2500, "25k"
	_5000 = 5000, "50k"
	_10000 = 10000, "100k"

	def __new__(cls, value, label):
		obj = int.__new__(cls, value)
		obj._value_ = value
		obj.label = label
		return obj

	def __repr__(self):
		return f"<{self.__class__.__name__}: {self.label}>"

	def __str__(self):
		return self.label

class Modulation(str, Enum):
	auto = "AUTO"
	am = "AM"
	fm = "FM"
	nfm = "NFM"

@dataclass
class QuickSearch(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)
	frequency: float = protocol_field(write=True)
	step: SearchStep = protocol_field(write=True)
	modulation: Modulation = protocol_field(write=True)
	attenuation: bool = protocol_field(write=True)
	delay: int = protocol_field(write=True)
	dataSkip: bool = protocol_field(write=True)
	squelchCodeSearch: bool = protocol_field(write=True)
	pagerSkip: bool = protocol_field(write=True)
	repeaterFind: bool = protocol_field(write=True)

	@override
	@staticmethod
	def command_name():
		return "QSH"

class CSGroupId(int, Enum):
	group1 = 1
	group2 = 2
	group3 = 3
	group4 = 4
	group5 = 5
	group6 = 6
	group7 = 7
	group8 = 8
	group9 = 9
	group0 = 0

@dataclass
class CustomSearchGroup(ProtocolMessage):
	id: CSGroupId = protocol_field(write=True)
	name: str = protocol_field(read=True, write=True)
	min: float = protocol_field(read=True, write=True)
	max: float = protocol_field(read=True, write=True)
	step: SearchStep = protocol_field(read=True, write=True)
	modulation: Modulation = protocol_field(read=True, write=True)
	attenuation: bool = protocol_field(read=True, write=True)
	delay: int = protocol_field(read=True, write=True)
	dataSkip: bool = protocol_field(read=True, write=True)

	@override
	@staticmethod
	def command_name():
		return "CSP"

class CSGroupFlag(int, Flag):
	none = 0, None
	group1 = 1 << 9, CSGroupId.group1
	group2 = 1 << 8, CSGroupId.group2
	group3 = 1 << 7, CSGroupId.group3
	group4 = 1 << 6, CSGroupId.group4
	group5 = 1 << 5, CSGroupId.group5
	group6 = 1 << 4, CSGroupId.group6
	group7 = 1 << 3, CSGroupId.group7
	group8 = 1 << 2, CSGroupId.group8
	group9 = 1 << 1, CSGroupId.group9
	group0 = 1 << 0, CSGroupId.group0

	def __new__(cls, value, id):
		obj = int.__new__(cls, value)
		obj._value_ = value
		obj.id = id
		return obj

	@classmethod
	def from_digits(Self, digits: str):
		all = list(Self)
		value = Self.none
		for digit in digits:
			assert digit in "1234567890", "search groups must be given as numbers 0-9"
			digit = int(digit)
			value |= all[digit - 1]
		return value

@dataclass
class EnabledCSGroups(ProtocolMessage):
	enabled: Literal["OK"] | CSGroupFlag = protocol_field(read=True, write=True)

	@override
	@staticmethod
	def command_name():
		return "CSG"

	def pre_write(self, query):
		if self.enabled is not None:
			# protocol makes unfortunate choice here; with a zero indicating enabled
			self.enabled = ~self.enabled

	def post_read(self):
		if self.enabled != "OK":
			self.enabled = ~self.enabled

@dataclass
class AddGlobalLockout(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)
	frequency: float = protocol_field(write=True)

	@override
	@staticmethod
	def command_name():
		return "LOF"

@dataclass
class RemoveGlobalLockout(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)
	frequency: float = protocol_field(write=True)

	@override
	@staticmethod
	def command_name():
		return "ULF"

@dataclass
class GetGlobalLockouts(ProtocolMessage):
	frequency: float = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "GLF"

class UniqueQueue:
	def __init__(self, *ids):
		from collections import deque
		self.queue = deque()
		self.visited = set()
		self.add(*ids)

	def __iter__(self):
		return self

	def __next__(self):
		if not self.queue:
			raise StopIteration
		return self.queue.popleft()

	def add(self, *ids):
		for v in ids:
			if v < 0 or v in self.visited:
				continue
			self.visited.add(v)
			self.queue.append(v)

class QuickKey(str, Enum):
	none = "."
	key1 = "1"
	key2 = "2"
	key3 = "3"
	key4 = "4"
	key5 = "5"
	key6 = "6"
	key7 = "7"
	key8 = "8"
	key9 = "9"
	key0 = "0"

class QuickLockFlag(Flag):
	none = 0
	item1 = 1 << 9
	item2 = 1 << 8
	item3 = 1 << 7
	item4 = 1 << 6
	item5 = 1 << 5
	item6 = 1 << 4
	item7 = 1 << 3
	item8 = 1 << 2
	item9 = 1 << 1
	item0 = 1 << 0

@dataclass
class System(ProtocolMessage):
	id: MemoryId = protocol_field(write=True)
	type: Literal["CNV"] = protocol_field(read=True)
	name: str = protocol_field(read=True, write=True)
	quickKey: QuickKey = protocol_field(read=True, write=True)
	holdTime: int = protocol_field(read=True, write=True)
	lockout: bool = protocol_field(read=True, write=True)
	reserved: Literal[None] = protocol_field(read=True, write=True)
	delayTime: int = protocol_field(read=True, write=True)
	dataSkip: bool = protocol_field(read=True, write=True)
	emergencyAlert: bool = protocol_field(read=True, write=True)
	revIndex: MemoryId = protocol_field(read=True)
	fwdIndex: MemoryId = protocol_field(read=True)
	groupHead: MemoryId = protocol_field(read=True)
	groupTail: MemoryId = protocol_field(read=True)
	sequence: int = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "SIN"

	@classmethod
	async def get_all(Self):
		async with serial.ProgramGuard():
			head = (await serial.send_message(GetHeadSystem())).id
			if head == -1:
				return # no systems
			queue = UniqueQueue(head)
			for id in queue:
				system = await serial.send_message(Self(id=id), query=True)
				queue.add(system.revIndex, system.fwdIndex)
				yield system

@dataclass
class GetHeadSystem(ProtocolMessage):
	id: MemoryId = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "SIH"

@dataclass
class CreateSystem(ProtocolMessage):
	id: MemoryId = protocol_field(read=True)
	type: Literal["CNV"] = protocol_field(write=True, default="CNV")

	@override
	@staticmethod
	def command_name():
		return "CSY"

@dataclass
class DeleteSystem(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)
	id: MemoryId = protocol_field(write=True)

	@override
	@staticmethod
	def command_name():
		return "DSY"

@dataclass
class SystemQuickLockout(ProtocolMessage):
	quickSystems: Literal["OK"] | QuickLockFlag = protocol_field(read=True, write=True)

	@override
	@staticmethod
	def command_name():
		return "QSL"

@dataclass
class Group(ProtocolMessage):
	id: MemoryId = protocol_field(write=True)
	type: Literal["C", None] = protocol_field(read=True)
	name: str = protocol_field(read=True, write=True)
	quickKey: QuickKey = protocol_field(read=True, write=True)
	lockout: bool = protocol_field(read=True, write=True)
	revIndex: MemoryId = protocol_field(read=True)
	fwdIndex: MemoryId = protocol_field(read=True)
	sysIndex: MemoryId = protocol_field(read=True)
	chanHead: MemoryId = protocol_field(read=True)
	chanTail: MemoryId = protocol_field(read=True)
	sequence: int = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "GIN"

	@classmethod
	async def get_all(Self, systemId):
		async with serial.ProgramGuard():
			if type(systemId) is System:
				system = systemId
			else:
				system = await serial.send_message(System(id=systemId), query=True)
			queue = UniqueQueue(system.groupHead, system.groupTail)
			for id in queue:
				group = await serial.send_message(Self(id=id), query=True)
				queue.add(group.revIndex, group.fwdIndex)
				yield group

@dataclass
class CreateGroup(ProtocolMessage):
	systemId: MemoryId = protocol_field(write=True)
	groupId: MemoryId = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "AGC"

@dataclass
class DeleteGroup(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)
	id: MemoryId = protocol_field(write=True)

	@override
	@staticmethod
	def command_name():
		return "DGR"

@dataclass
class GroupQuickLockout(ProtocolMessage):
	systemId: MemoryId = protocol_field(write=True)
	quickGroups: Literal["OK"] | QuickLockFlag = protocol_field(read=True, write=True)

	@override
	@staticmethod
	def command_name():
		return "QGL"

class SquelchTone(int, Enum):
	none = (0, None)
	search = (127, None)

	ctcss670 = (64, "CTCSS 67.0Hz")
	ctcss693 = (65, "CTCSS 69.3Hz")
	ctcss719 = (66, "CTCSS 71.9Hz")
	ctcss744 = (67, "CTCSS 74.4Hz")
	ctcss770 = (68, "CTCSS 77.0Hz")
	ctcss797 = (69, "CTCSS 79.7Hz")
	ctcss825 = (70, "CTCSS 82.5Hz")
	ctcss854 = (71, "CTCSS 85.4Hz")
	ctcss885 = (72, "CTCSS 88.5Hz")
	ctcss915 = (73, "CTCSS 91.5Hz")
	ctcss948 = (74, "CTCSS 94.8Hz")
	ctcss974 = (75, "CTCSS 97.4Hz")
	ctcss1000 = (76, "CTCSS 100.0Hz")
	ctcss1035 = (77, "CTCSS 103.5Hz")
	ctcss1072 = (78, "CTCSS 107.2Hz")
	ctcss1109 = (79, "CTCSS 110.9Hz")
	ctcss1148 = (80, "CTCSS 114.8Hz")
	ctcss1188 = (81, "CTCSS 118.8Hz")
	ctcss1230 = (82, "CTCSS 123.0Hz")
	ctcss1273 = (83, "CTCSS 127.3Hz")
	ctcss1318 = (84, "CTCSS 131.8Hz")
	ctcss1365 = (85, "CTCSS 136.5Hz")
	ctcss1413 = (86, "CTCSS 141.3Hz")
	ctcss1462 = (87, "CTCSS 146.2Hz")
	ctcss1514 = (88, "CTCSS 151.4Hz")
	ctcss1567 = (89, "CTCSS 156.7Hz")
	ctcss1598 = (90, "CTCSS 159.8Hz")
	ctcss1622 = (91, "CTCSS 162.2Hz")
	ctcss1655 = (92, "CTCSS 165.5Hz")
	ctcss1679 = (93, "CTCSS 167.9Hz")
	ctcss1713 = (94, "CTCSS 171.3Hz")
	ctcss1738 = (95, "CTCSS 173.8Hz")
	ctcss1773 = (96, "CTCSS 177.3Hz")
	ctcss1799 = (97, "CTCSS 179.9Hz")
	ctcss1835 = (98, "CTCSS 183.5Hz")
	ctcss1862 = (99, "CTCSS 186.2Hz")
	ctcss1899 = (100, "CTCSS 189.9Hz")
	ctcss1928 = (101, "CTCSS 192.8Hz")
	ctcss1966 = (102, "CTCSS 196.6Hz")
	ctcss1995 = (103, "CTCSS 199.5Hz")
	ctcss2035 = (104, "CTCSS 203.5Hz")
	ctcss2065 = (105, "CTCSS 206.5Hz")
	ctcss2107 = (106, "CTCSS 210.7Hz")
	ctcss2181 = (107, "CTCSS 218.1Hz")
	ctcss2257 = (108, "CTCSS 225.7Hz")
	ctcss2291 = (109, "CTCSS 229.1Hz")
	ctcss2336 = (110, "CTCSS 233.6Hz")
	ctcss2418 = (111, "CTCSS 241.8Hz")
	ctcss2503 = (112, "CTCSS 250.3Hz")
	ctcss2541 = (113, "CTCSS 254.1Hz")

	dcs023 = (128, "DCS 023")
	dcs025 = (129, "DCS 025")
	dcs026 = (130, "DCS 026")
	dcs031 = (131, "DCS 031")
	dcs032 = (132, "DCS 032")
	dcs036 = (133, "DCS 036")
	dcs043 = (134, "DCS 043")
	dcs047 = (135, "DCS 047")
	dcs051 = (136, "DCS 051")
	dcs053 = (137, "DCS 053")
	dcs054 = (138, "DCS 054")
	dcs065 = (139, "DCS 065")
	dcs071 = (140, "DCS 071")
	dcs072 = (141, "DCS 072")
	dcs073 = (142, "DCS 073")
	dcs074 = (143, "DCS 074")
	dcs114 = (144, "DCS 114")
	dcs115 = (145, "DCS 115")
	dcs116 = (146, "DCS 116")
	dcs122 = (147, "DCS 122")
	dcs125 = (148, "DCS 125")
	dcs131 = (149, "DCS 131")
	dcs132 = (150, "DCS 132")
	dcs134 = (151, "DCS 134")
	dcs143 = (152, "DCS 143")
	dcs145 = (153, "DCS 145")
	dcs152 = (154, "DCS 152")
	dcs155 = (155, "DCS 155")
	dcs156 = (156, "DCS 156")
	dcs162 = (157, "DCS 162")
	dcs165 = (158, "DCS 165")
	dcs172 = (159, "DCS 172")
	dcs174 = (160, "DCS 174")
	dcs205 = (161, "DCS 205")
	dcs212 = (162, "DCS 212")
	dcs223 = (163, "DCS 223")
	dcs225 = (164, "DCS 225")
	dcs226 = (165, "DCS 226")
	dcs243 = (166, "DCS 243")
	dcs244 = (167, "DCS 244")
	dcs245 = (168, "DCS 245")
	dcs246 = (169, "DCS 246")
	dcs251 = (170, "DCS 251")
	dcs252 = (171, "DCS 252")
	dcs255 = (172, "DCS 255")
	dcs261 = (173, "DCS 261")
	dcs263 = (174, "DCS 263")
	dcs265 = (175, "DCS 265")
	dcs266 = (176, "DCS 266")
	dcs271 = (177, "DCS 271")
	dcs274 = (178, "DCS 274")
	dcs306 = (179, "DCS 306")
	dcs311 = (180, "DCS 311")
	dcs315 = (181, "DCS 315")
	dcs325 = (182, "DCS 325")
	dcs331 = (183, "DCS 331")
	dcs332 = (184, "DCS 332")
	dcs343 = (185, "DCS 343")
	dcs346 = (186, "DCS 346")
	dcs351 = (187, "DCS 351")
	dcs356 = (188, "DCS 356")
	dcs364 = (189, "DCS 364")
	dcs365 = (190, "DCS 365")
	dcs371 = (191, "DCS 371")
	dcs411 = (192, "DCS 411")
	dcs412 = (193, "DCS 412")
	dcs413 = (194, "DCS 413")
	dcs423 = (195, "DCS 423")
	dcs431 = (196, "DCS 431")
	dcs432 = (197, "DCS 432")
	dcs445 = (198, "DCS 445")
	dcs446 = (199, "DCS 446")
	dcs452 = (200, "DCS 452")
	dcs454 = (201, "DCS 454")
	dcs455 = (202, "DCS 455")
	dcs462 = (203, "DCS 462")
	dcs464 = (204, "DCS 464")
	dcs465 = (205, "DCS 465")
	dcs466 = (206, "DCS 466")
	dcs503 = (207, "DCS 503")
	dcs506 = (208, "DCS 506")
	dcs516 = (209, "DCS 516")
	dcs523 = (210, "DCS 523")
	dcs526 = (211, "DCS 526")
	dcs532 = (212, "DCS 532")
	dcs546 = (213, "DCS 546")
	dcs565 = (214, "DCS 565")
	dcs606 = (215, "DCS 606")
	dcs612 = (216, "DCS 612")
	dcs624 = (217, "DCS 624")
	dcs627 = (218, "DCS 627")
	dcs631 = (219, "DCS 631")
	dcs632 = (220, "DCS 632")
	dcs654 = (221, "DCS 654")
	dcs662 = (222, "DCS 662")
	dcs664 = (223, "DCS 664")
	dcs703 = (224, "DCS 703")
	dcs712 = (225, "DCS 712")
	dcs723 = (226, "DCS 723")
	dcs731 = (227, "DCS 731")
	dcs732 = (228, "DCS 732")
	dcs734 = (229, "DCS 734")
	dcs743 = (230, "DCS 743")
	dcs754 = (231, "DCS 754")

	def __new__(cls, value, label):
		obj = int.__new__(cls, value)
		obj._value_ = value
		obj.label = label
		return obj

	def __str__(self):
		return self.label or self.name

@dataclass
class Channel(ProtocolMessage):
	id: MemoryId = protocol_field(write=True)
	name: str = protocol_field(read=True, write=True)
	frequency: float = protocol_field(read=True, write=True)
	searchStep: SearchStep = protocol_field(read=True, write=True)
	modulation: Modulation = protocol_field(read=True, write=True)
	squelchTone: SquelchTone = protocol_field(read=True, write=True)
	squelchToneLockout: bool = protocol_field(read=True, write=True)
	lockout: bool = protocol_field(read=True, write=True)
	priority: bool = protocol_field(read=True, write=True)
	attenuation: bool = protocol_field(read=True, write=True)
	alert: bool = protocol_field(read=True, write=True)
	revIndex: MemoryId = protocol_field(read=True)
	fwdIndex: MemoryId = protocol_field(read=True)
	sysIndex: MemoryId = protocol_field(read=True)
	groupIndex: MemoryId = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "CIN"

	@classmethod
	async def get_all(Self, groupId):
		async with serial.ProgramGuard():
			if type(groupId) is Group:
				group = groupId
			else:
				group = await serial.send_message(System(id=groupId), query=True)
			queue = UniqueQueue(group.chanHead, group.chanTail)
			for id in queue:
				group = await serial.send_message(Self(id=id), query=True)
				queue.add(group.revIndex, group.fwdIndex)
				yield group

@dataclass
class CreateChannel(ProtocolMessage):
	groupId: MemoryId = protocol_field(write=True)
	channelId: MemoryId = protocol_field(read=True)

	@override
	@staticmethod
	def command_name():
		return "ACC"

@dataclass
class DeleteChannel(ProtocolMessage):
	ack: Literal["OK"] = protocol_field(read=True)
	id: MemoryId = protocol_field(write=True)

	@override
	@staticmethod
	def command_name():
		return "DCH"
