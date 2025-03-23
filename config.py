import configparser
import os

config = configparser.ConfigParser()
config_file = os.path.join(os.path.dirname(__file__), "config.ini")
config.read(config_file)

TELEGRAM_API_TOKEN = config["TELEGRAM"].get("TELEGRAM_API_TOKEN")
DATABASE_URL = config["DATABASE"].get("DATABASE_URL")
