# -*- coding: utf-8 -*-

import logging.handlers
import sys
from pathlib import Path

import requests
import ts3
from ratelimit import limits

import config


class RateLimitException(Exception):
    pass


@limits(calls=500, period=60 * 60)  # Rate limit is 600/600 but let's play it safe
def fetch_account(key: str):
    try:
        response = requests.get(
            "https://api.guildwars2.com/v2/account?access_token=" + key
        )
        if (
            response.status_code in [400, 403] and "invalid key" in response.text
        ):  # Invalid API key
            return None
        elif response.status_code == 200:
            return response.json()
        elif response.status_code == 429:  # Rate limit
            raise RateLimitException()
        raise requests.RequestException()  # API down
    except requests.RequestException:
        logging.exception("Failed to fetch API")
        raise


def assign_server_role(bot, server_id: int, invokerid: str, cldbid: str):
    # Grab server info from config
    server = None
    for s in config.SERVERS:
        if s["id"] == server_id:
            server = s
            break

    if not server:
        bot.send_message(
            invokerid,
            "Der aktuell hinterlegte Server konnt nicht zugeordnet werden. "
            "Bitte wenden Sie sich an einen Admin.",
        )
        return

    bot.ts3c.exec_("servergroupaddclient", sgid=server["group_id"], cldbid=cldbid)


def remove_roles(ts3c, cldbid: str, use_whitelist=True):
    server_groups = ts3c.exec_("servergroupsbyclientid", cldbid=cldbid)

    # Remove user from all non-whitelisted groups
    for server_group in server_groups:
        if use_whitelist and server_group["name"] in config.WHITELIST["CYCLE"] or server_group["name"] == "Guest":
            continue
        try:
            ts3c.exec_("servergroupdelclient", sgid=server_group["sgid"], cldbid=cldbid)
            logging.info(
                "Removed user dbid:{} from group {}".format(
                    cldbid, server_group["name"]
                )
            )
        except ts3.TS3Error:
            # User most likely doesn't have the group
            logging.exception(
                "Failed to remove user_db:{} from group {} for some reason.".format(
                    cldbid, server_group["name"]
                )
            )

    return server_groups


def init_logger(name: str):
    if not Path("logs").exists():
        Path("logs").mkdir()

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    hldr = logging.handlers.TimedRotatingFileHandler(
        "logs/{}.log".format(name), when="W0", encoding="utf-8", backupCount=16
    )
    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    hldr.setFormatter(fmt)
    logger.addHandler(hldr)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.setLevel(logging.DEBUG)
    logger.addHandler(stream)
