import logging.handlers
import os
import sys
import warnings
from pathlib import Path

import requests
import ts3
from ratelimit import limits

from ts3bot import constants
from ts3bot.config import Config


class RateLimitException(Exception):
    pass


@limits(calls=500, period=60)  # Rate limit is 600 per minute but let's play it safe
def fetch_account(key: str):
    try:
        response = requests.get(
            "https://api.guildwars2.com/v2/account?access_token=" + key
        )
        if 400 <= response.status_code < 500 and (
            "Invalid" in response.text or "invalid" in response.text
        ):  # Invalid API key
            return None
        elif response.status_code == 200:
            return response.json()
        elif response.status_code == 429:  # Rate limit
            raise RateLimitException()

        logging.error(response.text)
        raise requests.RequestException()  # API down
    except requests.RequestException:
        logging.exception("Failed to fetch API")
        raise


def assign_server_role(bot, server_id: int, invokerid: str, cldbid: str):
    # Grab server info from config
    server = find_world(server_id)

    if not server:
        bot.send_message(invokerid, "unknown_server")
        return

    bot.ts3c.exec_("servergroupaddclient", sgid=server["group_id"], cldbid=cldbid)


def remove_roles(ts3c, cldbid: str, use_whitelist=True):
    server_groups = ts3c.exec_("servergroupsbyclientid", cldbid=cldbid)
    removed_groups = []

    # Remove user from all non-whitelisted groups
    for server_group in server_groups:
        if (
            use_whitelist
            and server_group["name"] in Config.whitelist_cycle
            or server_group["name"] == "Guest"
        ):
            continue
        try:
            ts3c.exec_("servergroupdelclient", sgid=server_group["sgid"], cldbid=cldbid)
            logging.info(
                "Removed user dbid:%s from group %s", cldbid, server_group["name"]
            )
            removed_groups.append(server_group["name"])
        except ts3.TS3Error:
            # User most likely doesn't have the group
            logging.exception(
                "Failed to remove cldbid:%s from group %s for some reason.",
                cldbid,
                server_group["name"],
            )

    return removed_groups


def init_logger(name: str):
    if not Path("logs").exists():
        Path("logs").mkdir()

    logger = logging.getLogger()

    if os.environ.get("ENV", "") == "dev":
        level = logging.DEBUG
    else:
        level = logging.INFO

    logger.setLevel(level)
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
    stream.setLevel(level)
    logger.addHandler(stream)

    sentry_dsn = Config.get("sentry", "dsn")
    if sentry_dsn:
        import sentry_sdk

        def before_send(event, hint):
            if "exc_info" in hint:
                _, exc_value, _ = hint["exc_info"]
                if isinstance(exc_value, KeyboardInterrupt):
                    return None
            return event

        sentry_sdk.init(dsn=sentry_dsn, before_send=before_send, send_default_pii=True)


def world_name_from_id(wid: int):
    for srv in constants.SERVERS:
        if srv["id"] == wid:
            return srv["name"]
    return "Unknown ({})".format(wid)


def find_world(world_id: int):
    warnings.warn(
        "DEPRECATED: Use database query instead.", DeprecationWarning, stacklevel=2
    )
    return {"id": world_id, "name": "Unknown", "group_id": "100"}


class User:
    id: int
    db_id: int
    unique_id: str
    nickname: str
    country: str

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        return "<User nickname={0.nickname} db_id={0.db_id} unique_id={0.unique_id} country={0.country}".format(
            self
        )

    @property
    def locale(self):
        # TODO: Force locale
        if self.country in ["DE", "AT"]:
            return "de"
        return "en"
