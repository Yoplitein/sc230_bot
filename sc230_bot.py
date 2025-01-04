import asyncio
import collections
import glob
import itertools
import json
import logging
import os
import sys
import traceback

import discord
import serial

logger = logging.getLogger(os.path.splitext(os.path.basename(__file__))[0])

class Client(discord.Client):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		self.rawInputUsers = set()
		self.keyInputUsers = set()

	async def on_ready(self):
		logger.info("ready")
		for channel in config["control_channels"]:
			channel = self.get_channel(channel)
			await channel.send("scanner control bot ready", delete_after=5.0)

		if "--auto-restart" in sys.argv:
			from inotify_simple import INotify, flags
			inotify = INotify()
			inotify.add_watch(__file__, flags.CLOSE_WRITE)
			def on_readable(*args, **kwargs):
				logger.info(f"{os.path.basename(__file__)} modified, restarting")
				os.execvp(sys.orig_argv[0], sys.orig_argv)
			self.loop.add_reader(inotify, on_readable)

	async def on_message(self, msg: discord.Message):
		if msg.author == self.user or msg.channel.id not in config["control_channels"]:
			return

		try:
			cmd, args = None, None
			if True:
				split = msg.content.split(maxsplit=1)
				cmd = split[0]
				args = split[1].split(" ") if len(split) > 1 else []
			match cmd:
				case "$help" | "$links":
					files = glob.glob(cmd.strip("$") + "*.txt")
					files.sort()
					for file in files:
						with open(file, "r") as f:
							contents = f.read().strip()
							await msg.reply(contents)
					return
				case "$status":
					async with SerialGuard(msg):
						body = (await serialClient.send_raw(b"STS"))
						(_, l1, l2, l3, l4, *_) = body.split(",")
						l1 = l1.strip()
						l2 = l2.strip()
						l3 = l3.strip()
						l4 = l4.strip()
						[l1, l2, l3, l4] = [replace_special_chars(v) for v in [l1, l2, l3, l4]]
						await msg.reply(f"```\n{l1}\n{l2}\n{l3}\n{l4}\n```")
						return
				case "$search":
					options = {
						"public": "",
						"news": ">",
						"ham": ">>",
						"marine": ">>>",
						"rail": ">>>>",
						"air": ">>>>>",
						"cb": ">>>>>>",
						"gmrs": ">>>>>>>",
						"racing": ">>>>>>>>",
						"special": ">>>>>>>>>",
						"weather": "special",
					}
					option = " ".join(args)
					if option not in options:
						raise CommandError(f"unknown search option {option!r}")

					async with SerialGuard(msg):
						await serialClient.send_raw(b"EPG")
						if option == "weather":
							await serialClient.send_keys(b"M>>>>>^")
						else:
							await serialClient.send_keys(b"M>>^^")
							await serialClient.send_keys(options[option])
						await serialClient.send_key(ord('^'))
				case "$freq":
					if len(args) != 1:
						raise CommandError("you must specify exactly one frequency")
					[freq] = args
					freq = serialClient.format_frequency(freq)
					async with SerialGuard(msg):
						match (await serialClient.send_raw(f"QSH,{freq},0,AUTO,0,2,0,1,0,0")).split(","):
							case ["QSH", "OK"]:
								pass
							case ["QSH", "ERR"]:
								raise CommandError("couldn't tune in, is the scanner busy?")
				case "$freqrange":
					[minfreq, maxfreq] = args
					minfreq = serialClient.format_frequency(minfreq)
					maxfreq = serialClient.format_frequency(maxfreq)
					username = f"${serialClient.sanitize_string(msg.author.display_name)}"
					async with SerialGuard(msg):
						async with ProgramGuard():
							await serialClient.send_raw(f"CSG,{"1" * 9}0")
							await serialClient.send_raw(f"CSP,0,{username},{minfreq},{maxfreq},0,AUTO,0,2,0")
						await serialClient.send_keys(b"F+S.>E")
				case "$cs":
					groups = "0123456789" if not args else "".join("".join(args).split())
					for v in groups:
						if v not in "0123456789":
							raise CommandError("search groups must be given as numbers 0-9")
					async with SerialGuard(msg):
						async with ProgramGuard():
							state = list("1" * 10)
							for v in groups:
								v = int(v)
								state[v - 1] = "0"
							state = "".join(state)
							await serialClient.send_raw(f"CSG,{state}")
						await serialClient.send_keys("F+S.>E")
				case "$csgroups":
					async with SerialGuard(msg), ProgramGuard():
						lines = []
						for id in range(10):
							id = (id + 1) % 10
							match (await serialClient.send_raw(f"CSP,{id}")).split(","):
								case ["CSP", name, minfreq, maxfreq, *_]:
									minfreq = serialClient.parse_frequency(minfreq)
									maxfreq = serialClient.parse_frequency(maxfreq)
									lines.append(f"* {id} - {name}")
									lines.append(f"  * {minfreq} to {maxfreq}")
								case resp:
									raise CommandError(f"unexpected response {resp=}")
						await msg.reply("\n".join(lines))
						return
				case "$csfreq":
					[group, minfreq, maxfreq] = args
					if group not in "0123456789":
						raise CommandError("search groups must be given as numbers 0-9")
					minfreq = serialClient.format_frequency(minfreq)
					maxfreq = serialClient.format_frequency(maxfreq)
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"CSP,{group},,{minfreq},{maxfreq},,,,,")
				case "$csname":
					group = args[0]
					name = " ".join(args[1:])
					if group not in "0123456789":
						raise CommandError("search groups must be given as numbers 0-9")
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"CSP,{group},{name},,,,,,,")
				case "$csall":
					bands = {
						"Petrol/CB/Ham": ("25", "54"),
						"Ham/Fed/Military": ("137", "174"),
						"More Ham/Fed": ("400", "512"),
						"Public": ("806", "956"),
						"GHz ham": ("1240", "1300")
					}
					async with SerialGuard(msg):
						async with ProgramGuard():
							enabled = ""
							for (group, (name, (minfreq, maxfreq))) in enumerate(bands.items()):
								group += 1
								enabled += str(group)
								minfreq = serialClient.format_frequency(minfreq)
								maxfreq = serialClient.format_frequency(maxfreq)
								await serialClient.send_raw(f"CSP,{group},{name},{minfreq},{maxfreq},,,,,")

							state = list("1" * 10)
							for v in enabled:
								v = int(v)
								state[v - 1] = "0"
							state = "".join(state)
							await serialClient.send_raw(f"CSG,{state}")
						await serialClient.send_keys("F+S.>E")
				case "$tree":
					async with SerialGuard(msg), ProgramGuard():
						systems = []
						systemHead = int((await serialClient.send_raw(b"SIH")).split(",")[1])
						systemTail = int((await serialClient.send_raw(b"SIT")).split(",")[1])
						for systemId in await walk_ids(systemHead, systemTail):
							(_, _, systemName, _, _, locked, _, _, _, _, _, _, groupHead, groupTail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
							locked = locked != "0"
							[groupHead, groupTail] = map(int, [groupHead, groupTail])

							locked = "\N{LOCK}" if locked else "\N{OPEN LOCK}"
							embed = discord.Embed(title=f"{systemId} - {systemName}{locked}")
							systems.append(embed)

							noGroups = True
							for groupId in await walk_ids(groupHead, groupTail):
								noGroups = False

								(_, _, groupName, _, locked, _, _, _, chanHead, chanTail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
								locked = locked != "0"
								[chanHead, chanTail] = map(int, [chanHead, chanTail])

								locked = "\N{LOCK}" if locked else "\N{OPEN LOCK}"
								embed.add_field(name=f"{groupId} - {groupName} {locked}", value="", inline=False)

								channels = []
								for channelId in await walk_ids(chanHead, chanTail):
									(_, name, freq, _, _, _, _, locked, *_) = (await serialClient.send_raw(f"CIN,{channelId}")).split(",")
									locked = locked != "0"
									locked = "\N{LOCK}" if locked else "\N{OPEN LOCK}"
									channels.append(f"{serialClient.format_channel(channelId, name, freq)} {locked}")
								if not channels:
									embed.add_field(name="", value="no channels")
									continue

								col1, col2 = [], []
								try:
									it = iter(channels)
									while True:
										col1.append(next(it))
										col2.append(next(it))
								except StopIteration:
									pass
								embed.add_field(name="", value="\n".join(col1))
								embed.add_field(name="", value="\n".join(col2))
							if noGroups:
								embed.add_field(name="", value="no groups")
						if not systems:
							await msg.reply("no systems")
						for batch in itertools.batched(systems, 10):
							await msg.reply(embeds=batch)
						return
				case "$locked":
					raise CommandError("fixme")
					async with SerialGuard(msg), ProgramGuard():
						lines = []
						systemHead = int((await serialClient.send_raw(b"SIH")).split(",")[1])
						systemTail = int((await serialClient.send_raw(b"SIT")).split(",")[1])
						for systemId in await walk_ids(systemHead, systemTail):
							# await serialClient.send_raw(f"QGL,{systemId},0000000000")
							# await serialClient.send_raw(f"SIN,{systemId},,,,0,,,,")
							(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
							[groupHead, groupTail] = map(int, [groupHead, groupTail])
							lines.append(f"## {systemId} - {systemName}")
							for groupId in await walk_ids(groupHead, groupTail):
								# await serialClient.send_raw(f"GIN,{groupId},,,0")
								(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
								[chanHead, chanTail] = map(int, [chanHead, chanTail])
								lines.append(f"### {groupId} - {groupName}")
								for channelId in await walk_ids(chanHead, chanTail):
									(_, name, freq, *_) = (await serialClient.send_raw(f"CIN,{channelId}")).split(",")
									lines.append(f"* {serialClient.format_channel(channelId, name, freq)}")
						await msg.reply("\n".join(lines))
						return
				case "$lockout":
					freqs = list(map(serialClient.format_frequency, " ".join(args).split()))
					logger.debug(f"{freqs=}")
					if not freqs:
						await msg.reply("you must specify at least one frequency")
						return
					async with SerialGuard(msg), ProgramGuard():
						locked = 0
						errs = 0
						for freq in freqs:
							match (await serialClient.send_raw(f"LOF,{freq}")).split(","):
								case ["LOF", "OK"]:
									locked += 1
								case ["LOF", "ERR"]:
									errs += 1
								case resp:
									raise SerialError(f"unexpected response {resp=}")
						if errs > 0:
							errs = f", {errs} out of band"
						await msg.reply(f"locked {locked} freqs{errs}")
				case "$unlockall":
					async with SerialGuard(msg), ProgramGuard():
						while True:
							match (await serialClient.send_raw(b"GLF")).split(","):
								case ["GLF", "-1"]:
									break
								case ["GLF", freq]:
									await serialClient.send_raw(f"ULF,{freq}")

						await serialClient.send_raw(f"QSL,0000000000")

						systemHead = int((await serialClient.send_raw(b"SIH")).split(",")[1])
						systemTail = int((await serialClient.send_raw(b"SIT")).split(",")[1])
						for systemId in await walk_ids(systemHead, systemTail):
							await serialClient.send_raw(f"QGL,{systemId},0000000000")
							await serialClient.send_raw(f"SIN,{systemId},,,,0,,,,")
							(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
							[groupHead, groupTail] = map(int, [groupHead, groupTail])
							for groupId in await walk_ids(groupHead, groupTail):
								await serialClient.send_raw(f"GIN,{groupId},,,0")
								(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
								[chanHead, chanTail] = map(int, [chanHead, chanTail])
								for channelId in await walk_ids(chanHead, chanTail):
									await serialClient.send_raw(f"CIN,{channelId},,,,,,,0,,,")
				case "$systems":
					async with SerialGuard(msg), ProgramGuard():
						head = int((await serialClient.send_raw(b"SIH")).split(",")[1])
						tail = int((await serialClient.send_raw(b"SIT")).split(",")[1])

						systems = []
						for id in await walk_ids(head, tail):
							match (await serialClient.send_raw(f"SIN,{id}")).split(","):
								case ["SIN", _, name, *_]:
									systems.append(f"* {id} - {name}")
								case resp:
									logger.warning(f"weird response for system {id}: {resp!r}")
						if systems:
							await msg.reply("\n".join(systems))
						else:
							await msg.reply("no systems found")
				case "$systemadd":
					async with SerialGuard(msg), ProgramGuard():
						[_, id] = (await serialClient.send_raw(b"CSY,CNV")).split(",")
						await msg.reply(f"created new system with id {id}")
						return
				case "$systemdel":
					id = int(args[0])
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"DSY,{id}")
				case "$systemname":
					[id, *newName] = args
					id = int(id)
					newName = serialClient.sanitize_string(" ".join(newName))
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"SIN,{id},{newName},,,,,,,")
				case "$systemlock":
					[id, locked] = args
					id = int(id)
					locked = parse_bool(locked)
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"SIN,{id},,,,{locked & 1},,,,")
				case "$groups":
					systemId = int(args[0])
					async with SerialGuard(msg), ProgramGuard():
						(_, _, systemName, _, _, _, _, _, _, _, _, _, head, tail, _) = (await serialClient.send_raw(f"SIN,{systemId}")).split(",")
						[head, tail] = map(int, [head, tail])

						groups = []
						for id in await walk_ids(head, tail):
							match (await serialClient.send_raw(f"GIN,{id}")).split(","):
								case ["GIN", _, name, *_]:
									groups.append(f"* {id} - {name}")
								case resp:
									logger.debug(f"weird response for group {id}: {resp!r}")
						if groups:
							groups = "\n".join(groups)
							await msg.reply(f"## {systemName}\n{groups}")
						else:
							await msg.reply("no groups found")
				case "$groupadd":
					systemId = int(args[0])
					async with SerialGuard(msg), ProgramGuard():
						[_, id] = (await serialClient.send_raw(f"AGC,{systemId}")).split(",")
						await msg.reply(f"created new group with id {id}")
						return
				case "$groupdel":
					id = int(args[0])
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"DGR,{id}")
				case "$groupname":
					[id, *newName] = args
					id = int(id)
					newName = serialClient.sanitize_string(" ".join(newName))
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"GIN,{id},{newName},,")
				case "$grouplock":
					[id, locked] = args
					id = int(id)
					locked = parse_bool(locked)
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"GIN,{id},,,{locked & 1}")
				case "$channels":
					groupId = int(args[0])
					async with SerialGuard(msg), ProgramGuard():
						(_, _, groupName, _, _, _, _, _, head, tail, _) = (await serialClient.send_raw(f"GIN,{groupId}")).split(",")
						[head, tail] = map(int, [head, tail])

						channels = []
						for id in await walk_ids(head, tail):
							match (await serialClient.send_raw(f"CIN,{id}")).split(","):
								case ["CIN", name, freq, *_]:
									channels.append(serialClient.format_channel(id, name, freq))
								case resp:
									logger.debug(f"weird response for channel {id}: {resp!r}")
						if channels:
							col1, col2 = [], []
							try:
								it = iter(channels)
								while True:
									col1.append(next(it))
									col2.append(next(it))
							except StopIteration:
								pass
							col1, col2 = "\n".join(col1), "\n".join(col2)
							embed = discord.Embed()
							embed.add_field(name="", value=col1)
							embed.add_field(name="", value=col2)
							await msg.reply(f"## {groupName}", embed=embed)
						else:
							await msg.reply("no channels found")
				case "$chanadd":
					groupId = int(args[0])
					frequencies = list(map(serialClient.format_frequency, " ".join(args[1:]).split()))
					if not frequencies:
						raise CommandError("you must specify at least one frequency")
					logger.debug(f"{frequencies=}")
					async with SerialGuard(msg), ProgramGuard():
						errors = []
						for freq in frequencies:
							[_, id] = (await serialClient.send_raw(f"ACC,{groupId}")).split(",")
							try:
								match (await serialClient.send_raw(f"CIN,{id},,{freq},,,,,,,,")).split(","):
									case ["CIN", "OK"]:
										pass
									case ["CIN", "ERR"]:
										raise CommandError("failed to update channel", freq=freq)
								match (await serialClient.send_raw(f"CIN,{id}")).split(","):
									case ["CIN", _, setFreq, *_] if setFreq == freq:
										pass
									case _:
										raise CommandError("out of band", freq=freq)
							except CommandError as err:
								freq = serialClient.parse_frequency(err.freq)
								errors.append(f"{freq}: {err.msg}")
								await serialClient.send_raw(f"DCH,{id}")
						numErrors = len(errors)
						if errors:
							errors = f"\n{"\n".join(errors)}"
						else:
							errors = ""
						await msg.reply(f"created {len(frequencies) - numErrors} new channels{errors}")
						return
				case "$chandel":
					id = int(args[0])
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"DCH,{id}")
				case "$channame":
					[id, *newName] = args
					id = int(id)
					newName = serialClient.sanitize_string(" ".join(newName))
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"CIN,{id},{newName},,,,,,,,,")
				case "$chanlock":
					[id, locked] = args
					id = int(id)
					locked = parse_bool(locked)
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"CIN,{id},,,,,,,{locked & 1},,,")
				case "$chanfreq":
					[id, freq] = args
					id = int(id)
					freq = serialClient.format_frequency(freq)
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(f"CIN,{id},,{freq},,,,,,,,")
				case "$key":
					await self.send_keys(msg, " ".join(args))
				case "$keyon":
					if msg.author.id in self.rawInputUsers:
						raise CommandError("you are already in raw input mode")
					if msg.author.id in self.keyInputUsers:
						raise CommandError("you are already in key input mode")
					self.keyInputUsers.add(msg.author.id)
				case "$keyoff":
					if msg.author.id not in self.keyInputUsers:
						raise CommandError("you are not in key input mode")
					self.keyInputUsers.remove(msg.author.id)
				case "$sweep":
					enforce_is_admin(msg.author)

					# should be enforced above but just to be safe
					assert msg.channel.id in config["control_channels"]

					queue = []
					async for msg in msg.channel.history(limit=None):
						if msg.pinned: continue
						queue.append(msg)
						if len(queue) >= 100:
							await msg.channel.delete_messages(queue)
							queue.clear()
					if len(queue) > 0:
						await msg.channel.delete_messages(queue)
					return
				case "$raw":
					enforce_is_admin(msg.author)
					await self.send_raw(msg, " ".join(args).split("\n"))
				case "$rawon":
					enforce_is_admin(msg.author)
					if msg.author.id in self.rawInputUsers:
						raise CommandError("you are already in raw input mode")
					if msg.author.id in self.keyInputUsers:
						raise CommandError("you are already in key input mode")
					self.rawInputUsers.add(msg.author.id)
				case "$rawoff":
					if msg.author.id not in self.rawInputUsers:
						raise CommandError("you are not in raw input mode")
					self.rawInputUsers.remove(msg.author.id)
				case "$hardclear":
					enforce_is_admin(msg.author)
					async with SerialGuard(msg), ProgramGuard():
						await serialClient.send_raw(b"CLR")
				case "$restart":
					enforce_is_admin(msg.author)
					[cmd, *args] = sys.argv
					await msg.reply("restarting")
					os.execvp(sys.orig_argv[0], sys.orig_argv)
				case _:
					if msg.content.startswith("$"):
						raise CommandError(f"unknown command {cmd}")
					if msg.author.id in self.rawInputUsers:
						await self.send_raw(msg, msg.content.split("\n"))
						return
					if msg.author.id in self.keyInputUsers:
						await self.send_keys(msg, msg.content)
						return
					return

			await msg.add_reaction("\N{WHITE HEAVY CHECK MARK}")
		except Exception as err:
			await msg.add_reaction("\N{CROSS MARK}")
			raise err

	async def on_error(self, event, msg = None, *args, **kwargs):
		(ty, err, _) = sys.exc_info()
		match err:
			case CommandError():
				await msg.reply(err.msg)
			case SerialError():
				rest = "" if not err.rest else f"\n{rest=}"
				await msg.reply(f":boom: serial error: {err.ty}:boom:{rest}")
			case _:
				traceback.print_exception(err)
				if msg:
					await msg.reply(f":boom: `{ty.__name__}: {err}` :boom:")

	async def send_raw(self, msg: discord.Message, lines: list[str]):
		enforce_is_admin(msg.author)
		async with SerialGuard(msg):
			responses = [f"```{await serialClient.send_raw(line)}```" for line in lines]
			await msg.reply("\n".join(responses))

	async def send_keys(self, msg: discord.Message, keys: str):
		async with SerialGuard(msg):
			keys = keys.replace("\n", "").replace(" ", "").upper()
			await serialClient.send_keys(keys.encode("ascii"))

			# TODO: parse exprs like `F+P`/`F++PP` (more plus holds down for more subsequent keys)
			""" unknown = set()
			for key in msg.content:
				key = ord(key)
				try:
					assert key < 128
					await serialClient.send_key(key)
				except Exception as e:
					unknown.add(key)
			if len(unknown) > 0:
				unknown = list(unknown)
				unknown.sort()
				await msg.reply(f"unknown keys: {", ".join(map(chr, unknown))}") """

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

class CommandError(Exception):
	def __init__(self, msg: str, **kwargs):
		self.msg = msg
		self.__dict__.update(kwargs)

def is_admin(user: discord.User):
	return user.id in config["admin_ids"]

def enforce_is_admin(user: discord.User):
	if not is_admin(user):
		raise CommandError("you do not have permission")

def replace_special_chars(str: str) -> str:
	return (str
		.replace("\x10", "\N{UPWARDS ARROW}")
		.replace("\x11", "\N{DOWNWARDS ARROW}")
	)

def parse_bool(str: str) -> bool:
	match str.lower():
		case "1" | "yes" | "on":
			return True
		case "0" | "no" | "off":
			return False
		case _:
			raise CommandError(f"ambiguous bool {str!r}")

async def walk_ids(head: int, tail: int) -> list[int]:
	assert SerialGuard.lock.locked(), "trying to walk_ids without serial lock held"
	assert ProgramGuard.level > 0, "trying to walk_ids outside of program mode"

	if head == -1:
		return []

	ids = [head]
	while tail != "-1" and head != tail:
		match (await serialClient.send_raw(f"FWD,{head}")).split(","):
			case ["FWD", "-1"]:
				assert False, "forward id is -1???"
			case ["FWD", next]:
				head = int(next)
		ids.append(head)
	return ids

class SerialError(Exception):
	def __init__(self, ty, msg, *rest):
		self.ty = ty
		self.msg = msg
		self.rest = rest

class SerialGuard:
	lock = asyncio.Lock()

	def __init__(self, msg: discord.Message):
		self.msg = msg
		self.pendingMsg = None

	async def __aenter__(self):
		if self.lock.locked():
			self.pendingMsg = await self.msg.reply("waiting for other command(s) to finish")
		await self.lock.acquire()
		logger.debug(f"serial locked for author={self.msg.author.name!r} content={self.msg.content!r}")

	async def __aexit__(self, *_):
		logger.debug("serial unlocked")
		self.lock.release()
		if self.pendingMsg:
			await self.pendingMsg.delete()

class ProgramGuard:
	level = 0

	@classmethod
	async def __aenter__(self, ):
		self.level += 1
		if self.level > 1:
			return
		await serialClient.send_raw(b"EPG")
		await serialClient.send_raw(b"PRG")

	@classmethod
	async def __aexit__(self, *_):
		self.level -= 1
		if self.level == 0:
			await serialClient.send_raw(b"EPG")

class SerialProtocol:
	allowedKeys = b"MFHSLC1234567890.E><^P"

	def __init__(self):
		self.serial = serial.Serial(
			config["serial_port"],
			baudrate=config["serial_baud"],
			stopbits=1,
			bytesize=8,
			parity=serial.PARITY_NONE,
			xonxoff=False,
			rtscts=False,
			dsrdtr=False,
			timeout=0,
			write_timeout=0,
		)

	def read_line(self) -> bytes:
		res = bytes()
		while True:
			read = self.serial.read(1)
			if len(read) == 0:
				import time
				time.sleep(0.1)
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

	def write_line(self, line: bytes):
		assert type(line) is bytes, "type error"
		line += b"\r"
		logger.debug(f"serial write {line!r}")
		while True:
			written = self.serial.write(line)
			line = line[written:]
			if len(line) == 0:
				break

	async def send_raw(self, line: str | bytes):
		def inner():
			nonlocal line
			if type(line) is str:
				line = line.encode("ascii")
			self.write_line(line)
			return self.read_line().decode("ascii")
		return await asyncio.to_thread(inner)

	async def send_key(self, key: int | str | bytes, mode = "P"):
		if type(key) in [str, bytes]:
			key = ord(key)
		assert key in self.allowedKeys, f"{chr(key)!r} is not a valid key"
		await self.send_raw(f"KEY,{chr(key)},{mode}")

	async def send_keys(self, keys: str | bytes):
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
							await self.send_key(char, "H")
						pressed[char] = curCount + count
					case char:
						await self.send_key(char)
						for key, count in pressed.items():
							if count == 1:
								await self.send_key(key, "R")
							if count > 0:
								pressed[key] = count - 1
		except:
			for key in pressed:
				try:
					await self.send_key(key, "R")
				except:
					pass
			raise

	@staticmethod
	def format_channel(id, name, freq):
		freq = serialClient.parse_frequency(freq)
		if name.endswith("MHz"):
			name = ""
		else:
			name = f" ({name})"
		return f"{id} - {freq}{name}"

	@staticmethod
	def format_frequency(freq: str) -> str:
		"Format frequency to be sent over protocol"
		freq = int(float(freq) * 1e4)
		return f"{freq:08}"

	@staticmethod
	def parse_frequency(freq: str) -> str:
		"Parse frequency read from protocol"
		freq = int(freq) / 1e4
		return f"{freq}MHz"

	@staticmethod
	def sanitize_string(str: str) -> str:
		return str.replace(",", "")[:16]

config = None
serialClient: SerialProtocol = None
discordClient: discord.Client = None
def main():
	global config, serialClient, discordClient

	with open("config.json", "r") as f:
		config = f.read().strip()
		config = json.loads(config)
	for k in ["control_channels", "admin_ids"]:
		config[k] = list(map(int, config[k]))

	serialClient = SerialProtocol()

	discord.utils.setup_logging(root = False)
	consoleHandler = logging.getLogger(discord.__name__).handlers[-1]
	logger.addHandler(consoleHandler)
	logger.setLevel(int(os.getenv("LOG_LEVEL", logging.INFO)))

	intents = discord.Intents.default()
	intents.message_content = True
	discordClient = Client(intents = intents)
	discordClient.run(config["token"])

if __name__ == "__main__":
	main()
