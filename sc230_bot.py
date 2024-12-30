import asyncio
import json
import sys
import traceback

import discord
import serial

class Client(discord.Client):
	async def on_ready(self):
		print("ready")
		""" channel = self.get_channel(config["voice_channel"])
		vc = await channel.connect()
		src = discord.FFmpegPCMAudio("anoisesrc=c=brown", before_options="-loglevel trace -f lavfi", options="-loglevel trace")
		vc.play(src) """

	async def on_message(self, msg: discord.Message):
		if msg.channel.id != config["voice_channel"] or msg.author == self.user:
			return

		try:
			[cmd, *args] = msg.content.split()
			match cmd:
				case "$help":
					with open("help.txt", "r") as f:
						help = f.read().strip()
						await msg.reply(help)
				case "$raw":
					if msg.author.id != config["admin_id"]:
						await msg.reply("illegal")
					content = " ".join(args)
					resp = await serialClient.send_raw(content.encode("ascii"))
					await msg.reply(f"```{resp.decode("ascii").strip()}```")
				case "$status":
					body = (await serialClient.send_raw(b"STS")).decode("ascii")
					(_, l1, l2, l3, l4, *_) = body.split(",")
					l1 = l1.strip()
					l2 = l2.strip()
					l3 = l3.strip()
					l4 = l4.strip()
					await msg.reply(f"```\n{l1}\n{l2}\n{l3}\n{l4}\n```")
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
					option = msg.content.split(" ", 1)[1] or [""]
					if option not in options:
						await msg.reply(f"unknown search option {option!r}")
						return

					await serialClient.send_raw(b"EPG")
					if option == "weather":
						await serialClient.send_keys(b"M>>>>>^")
					else:
						await serialClient.send_keys(b"M>>^^")
						await serialClient.send_keys(options[option].encode("ASCII"))
					await serialClient.send_key(ord('^'))
				case "$freqrange":
					[minfreq, maxfreq] = args
					await serialClient.send_raw(b"EPG")
					await serialClient.send_keys(f"M>>^>>^^>^...{minfreq}E...{maxfreq}EMM<^".encode("ascii"))
				case "$freq":
					status = await msg.reply(f"0/{len(args)} frequencies processed")
					notes = []
					for (i, freq) in enumerate(args):
						try:
							await serialClient.send_raw(b"EPG")
							await serialClient.send_raw(b"QSH,0,0,AUTO,0,2,0,1,0,0")
							await serialClient.send_keys(f"{freq}E".encode("ascii"))

							[_, sline, *_] = (await serialClient.send_raw(b"STS")).decode("ascii").split(",")
							print("s1", sline)
							if "out of band" in sline.lower():
								notes.append(f"{freq}: out of band")
								continue

							await serialClient.send_key(ord('E'))

							[_, sline, *_] = (await serialClient.send_raw(b"STS")).decode("ascii").split(",")
							print("s2", sline)
							if "frequency exists" in sline.lower():
								notes.append(f"{freq}: already exists")
								continue

							await serialClient.send_raw(b"EPG")
						finally:
							await status.edit(content=f"{i + 1}/{len(args)} frequencies processed ({len(notes)} notes)")
					await serialClient.send_key(ord('H'))
					if len(notes) > 0:
						await status.edit(content=f"```\n{"\n".join(notes)}\n```")
						return
					else:
						await status.delete()
				case "$memclear":
					await serialClient.send_raw(b"EPG")
					await serialClient.send_keys(b"MEE>>^^>>>^^")
					await serialClient.send_raw(b"EPG")
				case "$hardclear":
					await serialClient.send_raw(b"EPG")
					await serialClient.send_raw(b"PRG")
					await serialClient.send_raw(b"CLR")
					await serialClient.send_raw(b"EPG")
				case _:
					if msg.content.startswith("$"):
						await msg.reply(f"unknown command `{cmd}`")
						return

					await serialClient.send_keys(msg.content.encode("ascii"))

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

					return

			await msg.add_reaction("\N{WHITE HEAVY CHECK MARK}")
		except Exception as err:
			await msg.add_reaction("\N{CROSS MARK}")
			raise err

	async def on_error(self, event, msg, *args, **kwargs):
		(ty, err, _) = sys.exc_info()
		await msg.reply(f":boom: `{ty.__name__}: {err}` :boom:")
		traceback.print_exception(err)

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

	def read_line(self):
		pass

	def write_line(self, line: bytes):
		assert type(line) is bytes, "type error"
		line += b"\r"
		# print(f"write {line!r}")
		while True:
			written = self.serial.write(line)
			line = line[written:]
			if len(line) == 0:
				break

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
		# print(f"read {res!r}")
		res = res.strip()
		return res

	async def send_raw(self, line: bytes):
		return await asyncio.to_thread(self.write_line, line)

	async def send_key(self, key: int, mode = "P"):
		assert key in self.allowedKeys, f"{chr(key)!r} is not a valid key"
		await self.send_raw(f"KEY,{chr(key)},{mode}".encode("ascii"))

	async def send_keys(self, keys: bytes):
		for key in keys:
			await self.send_key(key)

config = None
discordClient = None
serialClient = None
def main():
	global config, discordClient, serialClient
	with open("config.json", "r") as f:
		config = f.read().strip()
		config = json.loads(config)
	for k in ["voice_channel", "admin_id"]:
		config[k] = int(config[k])

	serialClient = SerialProtocol()

	""" loop = asyncio.new_event_loop()
	async def wef():
		res = await serialClient.send_raw(b"STS")
		print(f"STS => {res!r}")
	loop.run_until_complete(wef()) """

	intents = discord.Intents.default()
	intents.message_content = True

	discordClient = Client(intents = intents)
	discordClient.run(config["token"])

if __name__ == "__main__":
	main()
