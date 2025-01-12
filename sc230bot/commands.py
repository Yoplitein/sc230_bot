from enum import Enum
import itertools

import discord

from . import config, serial, getLogger, UNLOCKED_EMOJI, LOCKED_EMOJI
from .serial import ProgramGuard, SerialError, SerialGuard, walk_ids
from .bot import Sc230Context, CommandError, CommandHandled, StatusGuard, enforce_is_admin, bot

logger = getLogger(__name__)

@bot.command(ignore_extra=False)
async def status(ctx: Sc230Context):
	"""
		print status lines (see also webcam)
	"""
	async with SerialGuard(ctx.message):
		body = (await serial.send_raw(b"STS"))
		(_, l1, l2, l3, l4, *_) = body.split(",")
		l1 = l1.strip()
		l2 = l2.strip()
		l3 = l3.strip()
		l4 = l4.strip()
		[l1, l2, l3, l4] = [serial.replace_special_chars(v) for v in [l1, l2, l3, l4]]
		await ctx.message.reply(f"```\n{l1}\n{l2}\n{l3}\n{l4}\n```")
		raise CommandHandled

class SearchOption(str, Enum):
	public = ("public", "", "public safety")
	news = ("news", ">")
	ham = ("ham", ">>", "amateur radio")
	marine = ("marine", ">>>")
	rail = ("rail", ">>>>")
	air = ("air", ">>>>>")
	cb = ("cb", ">>>>>>", "Citizens Band")
	gmrs = ("gmrs", ">>>>>>>", "Family Radio Service/General Mobile Radio Service")
	racing = ("racing", ">>>>>>>>", "NASCAR and stuff")
	special = ("special", ">>>>>>>>>", "idk man the manual doesn't say")
	weather = ("weather", "special", "National Weather Service broadcasts")

	def __new__(cls, label, keys, description=None):
		obj = str.__new__(cls, label)
		obj._value_ = label
		obj.keys = keys
		obj.description = description
		return obj

@bot.command(ignore_extra=False)
async def search(ctx: Sc230Context, *, option: SearchOption):
	"""
		scan preprogrammed frequency ranges
	"""
	async with SerialGuard(ctx.message):
		await serial.send_raw(b"EPG")
		if option == "weather":
			await serial.send_keys(b"M>>>>>^")
		else:
			await serial.send_keys(b"M>>^^")
			await serial.send_keys(option.keys)
		await serial.send_key(ord('^'))
search.help += f"\n\noption must be one of:" + \
	"\n  ".join(itertools.chain([""], (
		f"{o.value}{o.description and " - " or ""}{o.description or ""}" for o in SearchOption
	)))

@bot.command(ignore_extra=False)
async def freq(ctx: Sc230Context, *, freq: float):
	"""
		tune in to a specific frequency (to nearest 5kHz)
	"""
	freq = serial.format_frequency(freq)
	async with SerialGuard(ctx.message):
		match (await serial.send_raw(f"QSH,{freq},0,AUTO,0,2,0,1,0,0")).split(","):
			case ["QSH", "OK"]:
				pass
			case ["QSH", "ERR"]:
				raise CommandError("couldn't tune in, is the scanner busy?")

@bot.command(ignore_extra=False)
async def freqrange(ctx: Sc230Context, minfreq: float, maxfreq: float):
	"""
		scan through a range of frequencies

		overrides custom search group 0
	"""
	minfreq = serial.format_frequency(minfreq)
	maxfreq = serial.format_frequency(maxfreq)
	username = f"${serial.sanitize_string(ctx.author.display_name)}"
	async with SerialGuard(ctx.message):
		async with ProgramGuard():
			await serial.send_raw(f"CSG,{"1" * 9}0")
			await serial.send_raw(f"CSP,0,{username},{minfreq},{maxfreq},0,AUTO,0,2,0")
		await serial.send_keys(b"F+S.>E")

@bot.command(ignore_extra=False)
async def cs(ctx: Sc230Context, *, groups: str = "0123456789"):
	"""
		switch to custom search mode with the given group IDs enabled, defaults to all
	"""
	groups = groups.replace(" ", "")
	if not all(v in "0123456789" for v in groups):
		raise CommandError("search groups must be given as numbers 0-9")

	async with SerialGuard(ctx.message):
		async with ProgramGuard():
			state = list("1" * 10)
			for v in groups:
				v = int(v)
				state[v - 1] = "0"
			state = "".join(state)
			await serial.send_raw(f"CSG,{state}")
		await serial.send_keys("F+S.>E")

@bot.command(ignore_extra=False)
async def csgroups(ctx: Sc230Context):
	"""
		print custom search groups
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		lines = []
		for id in range(10):
			id = (id + 1) % 10
			match (await serial.send_raw(f"CSP,{id}")).split(","):
				case ["CSP", name, minfreq, maxfreq, *_]:
					minfreq = serial.parse_frequency(minfreq)
					maxfreq = serial.parse_frequency(maxfreq)
					lines.append(f"* {id} - {name}")
					lines.append(f"  * {minfreq} to {maxfreq}")
				case resp:
					raise CommandError(f"unexpected response {resp=}")
		await ctx.message.reply("\n".join(lines))
		raise CommandHandled

@bot.command(ignore_extra=False)
async def csfreq(ctx: Sc230Context, group: int, minfreq: float, maxfreq: float):
	"""
		set custom search group min/max frequencies. note that on group 0 this may be overridden by `$freqrange`
	"""
	if group < 0 or group > 9:
		raise CommandError("search groups must be given as numbers 0-9")
	minfreq = serial.format_frequency(minfreq)
	maxfreq = serial.format_frequency(maxfreq)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CSP,{group},,{minfreq},{maxfreq},,,,,")

@bot.command(ignore_extra=False)
async def csname(ctx: Sc230Context, group: int, *, name: str):
	"""
		set custom search group name
	"""
	if group < 0 or group > 9:
		raise CommandError("search groups must be given as numbers 0-9")
	name = serial.sanitize_string(name)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CSP,{group},{name},,,,,,,")

csallBands = {
	"Petrol/CB/Ham": ("25", "54"),
	"Ham/Fed/Military": ("137", "174"),
	"More Ham/Fed": ("400", "512"),
	"Public": ("806", "956"),
	"GHz ham": ("1240", "1300")
}
@bot.command(ignore_extra=False)
async def csall(ctx: Sc230Context):
	"""
		programs set of search groups covering the hardware's frequency range
	"""
	async with SerialGuard(ctx.message, typing=True):
		async with ProgramGuard():
			enabled = ""
			for (group, (name, (minfreq, maxfreq))) in enumerate(csallBands.items()):
				group += 1
				enabled += str(group)
				minfreq = serial.format_frequency(minfreq)
				maxfreq = serial.format_frequency(maxfreq)
				await serial.send_raw(f"CSP,{group},{name},{minfreq},{maxfreq},,,,,")

			state = list("1" * 10)
			for v in enabled:
				v = int(v)
				state[v - 1] = "0"
			state = "".join(state)
			await serial.send_raw(f"CSG,{state}")
		await serial.send_keys("F+S.>E")

@bot.command(ignore_extra=False)
async def tree(ctx: Sc230Context):
	"""
		print all systems, their groups, and channels
	"""
	doneSystems = 0
	doneGroups = 0
	doneChannels = 0
	def format_status():
		return dict(content=(
			f"read {doneSystems} systems, " +
			f"{doneGroups} groups, " +
			f"{doneChannels} channels"
		))

	systems = []
	async with SerialGuard(ctx.message, typing=True), ProgramGuard(), StatusGuard(ctx.message, format_status, interval=2.5):
		systemHead = int((await serial.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serial.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			doneSystems += 1

			(_, _, systemName, _, _, locked, _, _, _, _, _, _, groupHead, groupTail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
			locked = locked != "0"
			[groupHead, groupTail] = map(int, [groupHead, groupTail])

			locked = LOCKED_EMOJI if locked else UNLOCKED_EMOJI
			embed = discord.Embed(title=f"{systemId} - {systemName} {locked}")
			systems.append(embed)

			noGroups = True
			for groupId in await walk_ids(groupHead, groupTail):
				noGroups = False
				doneGroups += 1

				(_, _, groupName, _, locked, _, _, _, chanHead, chanTail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
				locked = locked != "0"
				[chanHead, chanTail] = map(int, [chanHead, chanTail])

				locked = LOCKED_EMOJI if locked else UNLOCKED_EMOJI
				embed.add_field(name=f"{groupId} - {groupName} {locked}", value="", inline=False)

				channels = []
				for channelId in await walk_ids(chanHead, chanTail):
					doneChannels += 1

					(_, name, freq, _, _, _, _, locked, *_) = (await serial.send_raw(f"CIN,{channelId}")).split(",")
					formatted = serial.format_channel(channelId, name, freq, locked)
					channels.append(f"{formatted}")
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
		await ctx.message.reply("no systems")
	for batch in itertools.batched(systems, 10):
		await ctx.message.reply(embeds=batch)
	raise CommandHandled

@bot.command(ignore_extra=False)
async def locked(ctx: Sc230Context):
	"""
		fixme
	"""
	raise CommandError("fixme")
	async with SerialGuard(ctx.message), ProgramGuard():
		lines = []
		systemHead = int((await serial.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serial.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			# await serial.send_raw(f"QGL,{systemId},0000000000")
			# await serial.send_raw(f"SIN,{systemId},,,,0,,,,")
			(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
			[groupHead, groupTail] = map(int, [groupHead, groupTail])
			lines.append(f"## {systemId} - {systemName}")
			for groupId in await walk_ids(groupHead, groupTail):
				# await serial.send_raw(f"GIN,{groupId},,,0")
				(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
				[chanHead, chanTail] = map(int, [chanHead, chanTail])
				lines.append(f"### {groupId} - {groupName}")
				for channelId in await walk_ids(chanHead, chanTail):
					(_, name, freq, *_) = (await serial.send_raw(f"CIN,{channelId}")).split(",")
					lines.append(f"* {serial.format_channel(channelId, name, freq)}")
		await ctx.message.reply("\n".join(lines))
		raise CommandHandled

@bot.command(ignore_extra=False)
async def lockout(ctx: Sc230Context, *frequencies: float):
	"""
		lock out specific frequencies
	"""
	frequencies = list(map(serial.format_frequency, frequencies))
	logger.debug(f"{frequencies=}")
	async with SerialGuard(ctx.message), ProgramGuard():
		locked = 0
		errs = 0
		for freq in frequencies:
			match (await serial.send_raw(f"LOF,{freq}")).split(","):
				case ["LOF", "OK"]:
					locked += 1
				case ["LOF", "ERR"]:
					errs += 1
				case resp:
					raise SerialError(f"unexpected response {resp=}")
		if errs > 0:
			errs = f", {errs} out of band"
		else:
			errs = ""
		await ctx.message.reply(f"locked {locked} freqs{errs}")

@bot.command(ignore_extra=False)
async def unlockall(ctx: Sc230Context):
	"""
		unlock all locked out systems/groups/channels
	"""
	async with SerialGuard(ctx.message, typing=True), ProgramGuard():
		while True:
			match (await serial.send_raw(b"GLF")).split(","):
				case ["GLF", "-1"]:
					break
				case ["GLF", freq]:
					await serial.send_raw(f"ULF,{freq}")

		await serial.send_raw(f"QSL,0000000000")

		systemHead = int((await serial.send_raw(b"SIH")).split(",")[1])
		systemTail = int((await serial.send_raw(b"SIT")).split(",")[1])
		for systemId in await walk_ids(systemHead, systemTail):
			await serial.send_raw(f"QGL,{systemId},0000000000")
			await serial.send_raw(f"SIN,{systemId},,,,0,,,,")
			(_, _, systemName, _, _, _, _, _, _, _, _, _, groupHead, groupTail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
			[groupHead, groupTail] = map(int, [groupHead, groupTail])
			for groupId in await walk_ids(groupHead, groupTail):
				await serial.send_raw(f"GIN,{groupId},,,0")
				(_, _, groupName, _, _, _, _, _, chanHead, chanTail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
				[chanHead, chanTail] = map(int, [chanHead, chanTail])
				for channelId in await walk_ids(chanHead, chanTail):
					await serial.send_raw(f"CIN,{channelId},,,,,,,0,,,")

@bot.command(ignore_extra=False)
async def systems(ctx: Sc230Context):
	"""
		list systems and their IDs
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		head = int((await serial.send_raw(b"SIH")).split(",")[1])
		tail = int((await serial.send_raw(b"SIT")).split(",")[1])

		systems = []
		for id in await walk_ids(head, tail):
			match (await serial.send_raw(f"SIN,{id}")).split(","):
				case ["SIN", _, name, _, _, locked, *_]:
					locked = LOCKED_EMOJI if locked != "0" else UNLOCKED_EMOJI
					systems.append(f"* {id} - {name} {locked}")
				case resp:
					logger.warning(f"weird response for system {id}: {resp!r}")
		if systems:
			await ctx.message.reply("\n".join(systems))
		else:
			await ctx.message.reply("no systems found")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def systemadd(ctx: Sc230Context, *, name: str = ""):
	"""
		add a new system, optionally setting its name
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		[_, id] = (await serial.send_raw(b"CSY,CNV")).split(",")
		if id == "-1":
			raise CommandError("could not create system")
		if name:
			name = serial.sanitize_string(name)
			await serial.send_raw(f"SIN,{id},{name},,,,,,,")
		await ctx.message.reply(f"created new system with id {id}")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def systemdel(ctx: Sc230Context, *, id: int):
	"""
		delete a system
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"DSY,{id}")

@bot.command(ignore_extra=False)
async def systemname(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a system's name. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"SIN,{id},{newName},,,,,,,")

@bot.command(ignore_extra=False)
async def systemlock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a system's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"SIN,{id},,,,{locked & 1},,,,")

@bot.command(ignore_extra=False)
async def groups(ctx: Sc230Context, systemId: int):
	"""
		list groups and IDs for the given system
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		(_, _, systemName, _, _, _, _, _, _, _, _, _, head, tail, _) = (await serial.send_raw(f"SIN,{systemId}")).split(",")
		[head, tail] = map(int, [head, tail])

		groups = []
		for id in await walk_ids(head, tail):
			match (await serial.send_raw(f"GIN,{id}")).split(","):
				case ["GIN", _, name, _, locked, *_]:
					locked = LOCKED_EMOJI if locked != "0" else UNLOCKED_EMOJI
					groups.append(f"* {id} - {name} {locked}")
				case resp:
					logger.debug(f"weird response for group {id}: {resp!r}")
		if groups:
			groups = "\n".join(groups)
			await ctx.message.reply(f"## {systemName}\n{groups}")
		else:
			await ctx.message.reply("no groups found")

		raise CommandHandled

@bot.command(ignore_extra=False)
async def groupadd(ctx: Sc230Context, systemId: int, *, name: str = ""):
	"""
		add a new group to a system, optionally setting its name
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		if (await serial.send_raw(f"SIN,{systemId}")).split(",", 1)[1] == "ERR":
			raise CommandError(f"system {systemId} does not exist")
		[_, id] = (await serial.send_raw(f"AGC,{systemId}")).split(",")
		if id == "-1":
			raise CommandError("could not create group")
		if name:
			await serial.send_raw(f"GIN,{id},{name},,")
		await ctx.message.reply(f"created new group with id {id}")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def groupdel(ctx: Sc230Context, id: int):
	"""
		delete a group
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"DGR,{id}")

@bot.command(ignore_extra=False)
async def groupname(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a group's name. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"GIN,{id},{newName},,")

@bot.command(ignore_extra=False)
async def grouplock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a group's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"GIN,{id},,,{locked & 1}")

@bot.command(ignore_extra=False)
async def channels(ctx: Sc230Context, groupId: int):
	"""
		list channels in a given group
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		(_, _, groupName, _, _, _, _, _, head, tail, _) = (await serial.send_raw(f"GIN,{groupId}")).split(",")
		[head, tail] = map(int, [head, tail])

		channels = []
		for id in await walk_ids(head, tail):
			match (await serial.send_raw(f"CIN,{id}")).split(","):
				case ["CIN", name, freq, _, _, _, _, locked, *_]:
					channels.append(serial.format_channel(id, name, freq, locked))
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
			await ctx.message.reply(f"## {groupName}", embed=embed)
		else:
			await ctx.message.reply("no channels found")

		raise CommandHandled

@bot.command(ignore_extra=False)
async def chanadd(ctx: Sc230Context, groupId: int, *, frequencySpecs: str):
	"""
		add new channels (frequencies) to a group. each frequency on one line, optionally followed by a name
	"""
	frequencies = []
	for line in frequencySpecs.split("\n"):
		line = line.split(maxsplit=1)
		freq = serial.format_frequency(line[0])
		name = line[1:]
		if name:
			name = serial.sanitize_string(name[0])
			frequencies.append((freq, name))
		else:
			frequencies.append(freq)
	if not frequencies:
		raise CommandError("you must specify at least one frequency")

	processedFreqs = 0
	def format_status():
		return dict(content=f"{processedFreqs}/{len(frequencies)} processed")

	async with SerialGuard(ctx.message, typing=True), ProgramGuard(), StatusGuard(ctx.message, format_status):
		errors = []
		for freq in frequencies:
			name = ""
			match freq:
				case str(f):
					pass
				case (f, n):
					freq = f
					name = n
				case _:
					assert False, "unexpected case"

			if (await serial.send_raw(f"GIN,{groupId}")).split(",", 1)[1] == "ERR":
				raise CommandError(f"group {groupId} does not exist")
			[_, id] = (await serial.send_raw(f"ACC,{groupId}")).split(",")
			if id == "-1":
				raise CommandError("could not create channel")
			try:
				match (await serial.send_raw(f"CIN,{id},{name},{freq},,,,,,,,")).split(","):
					case ["CIN", "OK"]:
						pass
					case ["CIN", "ERR"]:
						raise CommandError("failed to update channel", freq=freq)
				match (await serial.send_raw(f"CIN,{id}")).split(","):
					case ["CIN", _, setFreq, *_] if setFreq == freq:
						pass
					case _:
						raise CommandError("out of band", freq=freq)
			except CommandError as err:
				freq = serial.parse_frequency(err.freq)
				errors.append(f"{freq}: {err.msg}")
				await serial.send_raw(f"DCH,{id}")
			processedFreqs += 1

		numErrors = len(errors)
		if errors:
			errors = f"\n{"\n".join(errors)}"
		else:
			errors = ""
		await ctx.message.reply(f"created {len(frequencies) - numErrors} new channels out of {len(frequencies)} given{errors}")
		raise CommandHandled

@bot.command(ignore_extra=False)
async def chandel(ctx: Sc230Context, ids: list[int]):
	"""
		delete a channel
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		for id in ids:
			await serial.send_raw(f"DCH,{id}")

@bot.command(ignore_extra=False)
async def channame(ctx: Sc230Context, id: int, *, newName: str):
	"""
		set a channel's name. limit 16 characters
	"""
	newName = serial.sanitize_string(newName)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},{newName},,,,,,,,,")

@bot.command(ignore_extra=False)
async def chanlock(ctx: Sc230Context, id: int, locked: bool):
	"""
		set a channel's lockout status
	"""
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},,,,,,,{locked & 1},,,")

@bot.command(ignore_extra=False)
async def chanfreq(ctx: Sc230Context, id: int, freq: float):
	"""
		set a channel's frequency
	"""
	freq = serial.format_frequency(freq)
	async with SerialGuard(ctx.message), ProgramGuard():
		await serial.send_raw(f"CIN,{id},,{freq},,,,,,,,")

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
async def sweep(ctx: Sc230Context):
	"""
		admin only. delete all messages in the channel \N{BROOM}\N{DASH SYMBOL}
	"""
	enforce_is_admin(ctx.author)

	# should be enforced by `on_message` but just to be safe
	assert ctx.channel.id in config.get("control_channels")

	messagesSwept = 0
	def format_status():
		return dict(content=f"{messagesSwept} messages swept", delete_after=5)
	statusGuard = StatusGuard(ctx.message, format_status)

	async with statusGuard, ctx.channel.typing():
		queue = []
		async for foundMsg in ctx.channel.history(limit=None):
			if foundMsg.pinned or (statusGuard.statusMsg and foundMsg.id == statusGuard.statusMsg.id):
				continue
			queue.append(foundMsg)
			if len(queue) >= 100:
				await ctx.channel.delete_messages(queue)
				messagesSwept += len(queue)
				queue.clear()
		if len(queue) > 0:
			await ctx.channel.delete_messages(queue)
			messagesSwept += len(queue)
		await ctx.message.reply("done", delete_after=5)
		raise CommandHandled

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

@bot.command(ignore_extra=False)
async def factoryreset(ctx: Sc230Context):
	"""
		admin only. restores all settings to factory defaults
	"""
	enforce_is_admin(ctx.author)
	async with SerialGuard(ctx.message, typing=True), ProgramGuard():
		await serial.send_raw(b"CLR")

@bot.command(ignore_extra=False)
async def restart(ctx: Sc230Context):
	"""
		admin only. restarts bot
	"""
	from . import RestartProcess
	enforce_is_admin(ctx.author)
	raise RestartProcess
