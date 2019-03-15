import logging
import typing

import mysql.connector as msql
import ts3

import common
import config
from bot import Bot

MESSAGE_REGEX = "!verify +(\\d+)"
USAGE = "!verify <TS-Datenbank-ID>"


def handle(bot: Bot, event: ts3.response.TS3Event, match: typing.Match):
    if event[0]["invokeruid"] not in config.WHITELIST["ADMIN"]:
        return

    msqlc = None
    cur = None
    try:
        # Grab cluid
        try:
            user = bot.ts3c.exec_("clientgetnamefromdbid", cldbid=match.group(1))
            cluid = user[0]["cluid"]
        except ts3.query.TS3QueryError:
            bot.send_message(event[0]["invokerid"], "User nicht gefunden!")
            return

        # Connect to MySQL
        msqlc = msql.connect(
            user=config.SQL_USER,
            password=config.SQL_PASS,
            host=config.SQL_HOST,
            port=config.SQL_PORT,
            database=config.SQL_DB,
        )
        cur = msqlc.cursor()

        # Grab user's latest API key
        cur.execute(
            "SELECT `apikey` FROM `users` WHERE `ignored` = FALSE AND `tsuid` = %s ORDER BY `timestamp` LIMIT 1",
            (cluid,),
        )
        row = cur.fetchone()

        if not row:
            bot.send_message(
                event[0]["invokerid"], "User hat scheinbar keinen API-Key hinterlegt!"
            )
            return

        # Grab account
        json = common.fetch_account(row[0])
        if not json:
            bot.send_message(
                event[0]["invokerid"], msg="Der API-Key scheint ungültig zu sein."
            )
            return
        world = json.get("world")

        # Grab server info from config
        server = None
        for s in config.SERVERS:
            if s["id"] == world:
                server = s
                break

        # Server wasn't found in config
        if not server:
            bot.send_message(
                event[0]["invokerid"],
                msg="Der Nutzer ist derzeit auf einem unbekannten Server: {}.".format(
                    world
                ),
            )
        else:
            bot.send_message(
                event[0]["invokerid"],
                msg="Der Nutzer sieht sauber aus, hinterlegter Account ({}) ist auf {}.".format(
                    json.get("name"), server["name"]
                ),
            )
    except msql.Error:
        logging.exception("MySQL error in !verify.")
    finally:
        if cur:
            cur.close()
        if msqlc:
            msqlc.close()