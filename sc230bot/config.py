import json

config = None
def load():
	global config
	if config is not None:
		return
	with open("config.json", "r") as f:
		config = f.read().strip()
		config = json.loads(config)
	for k in ["control_channels", "admin_ids"]:
		config[k] = list(map(int, config[k]))

def get[T](key: str, default: T = None) -> T:
	load()
	return config.get(key, default)
