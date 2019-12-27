#!/usr/bin/python3
# -*- coding: utf-8 -*-
import datetime
import json
import logging
import re
from importlib import import_module

import mysql.connector as msql
import requests
import ts3

import commands
import common
import config
from constants import STRINGS


class Bot:
    def __init__(self):
        self.commands = []
        # Register commands
        for _ in commands.__all__:
            mod = import_module("commands.{}".format(_))
            mod.REGEX = re.compile(mod.MESSAGE_REGEX)
            logging.info("Registered command.%s", _)
            self.commands.append(mod)

        # Connect to TS3
        self.ts3c = ts3.query.TS3ServerConnection(
            "{}://{}:{}@{}".format(
                config.TS3_PROTOCOL,
                config.CLIENT_USER,
                config.CLIENT_PASS,
                config.QUERY_HOST,
            )
        )

        # Select server and change nick
        self.ts3c.exec_("use", sid=config.SERVER_ID)

        current_nick = self.ts3c.exec_("whoami")
        if current_nick[0]["client_nickname"] != config.CLIENT_NICK:
            self.ts3c.exec_("clientupdate", client_nickname=config.CLIENT_NICK)

        # Subscribe to events
        self.ts3c.exec_("servernotifyregister", event="channel", id=config.CHANNEL_ID)
        self.ts3c.exec_("servernotifyregister", event="textprivate")
        self.ts3c.exec_("servernotifyregister", event="server")

        # Move to target channel
        self.own_id = self.ts3c.exec_("clientfind", pattern=config.CLIENT_NICK)[0][
            "clid"
        ]
        self.ts3c.exec_("clientmove", clid=self.own_id, cid=config.CHANNEL_ID)

    def loop(self):
        while True:
            self.ts3c.send_keepalive()
            try:
                event = self.ts3c.wait_for_event(timeout=60)
                # type: ts3.response.TS3Event
            except ts3.query.TS3TimeoutError:
                pass  # Ignore wait timeout
            else:
                # Ignore own events
                if (
                    "invokername" in event[0]
                    and event[0]["invokername"] == config.CLIENT_NICK
                    or "clid" in event[0]
                    and event[0]["clid"] == self.own_id
                ):
                    continue

                self.handle_event(event)

    def handle_event(self, event):
        if event.event == "notifycliententerview":  # User connected/entered view
            self.verify_user(
                event[0]["client_unique_identifier"],
                event[0]["client_database_id"],
                event[0]["clid"],
            )
        elif event.event == "notifyclientmoved":
            if event[0]["ctid"] == str(config.CHANNEL_ID):
                logging.info("User id:%s joined channel", event[0]["clid"])
                self.send_message(event[0]["clid"], STRINGS["welcome"])
            else:
                logging.info("User id:%s left channel", event[0]["clid"])
        elif event.event == "notifytextmessage":
            message = event[0]["msg"].strip()
            logging.info(
                "%s (%s): %s", event[0]["invokername"], event[0]["invokeruid"], message
            )

            valid_command = False
            for command in self.commands:
                match = command.REGEX.match(message)
                if match:
                    valid_command = True
                    try:
                        command.handle(self, event, match)
                    except ts3.query.TS3QueryError:
                        logging.exception(
                            "Unexpected TS3QueryError in command handler."
                        )
                    break

            if not valid_command:
                self.send_message(event[0]["invokerid"], STRINGS["invalid_input"])

    def send_message(self, recipient: str, msg: str):
        try:
            logging.info("Response: %s", msg)
            self.ts3c.exec_("sendtextmessage", targetmode=1, target=recipient, msg=msg)
        except ts3.query.TS3Error:
            logging.exception(
                "Seems like the user I tried to message vanished into thin air"
            )

    def verify_user(
        self, client_unique_id: str, client_database_id: str, client_id: str
    ):
        def revoked(response="roles_revoked_invalid_key"):
            removed_roles = common.remove_roles(self.ts3c, client_database_id)
            logging.info(
                "Revoked user's (cldbid:%s) roles (%s) due to invalid API key/world.",
                client_database_id,
                removed_roles,
            )
            self.send_message(client_id, STRINGS[response])

        # Skip user if they only have the Guest role
        server_groups = self.ts3c.exec_(
            "servergroupsbyclientid", cldbid=client_database_id
        )
        if "Guest" in [_["name"] for _ in server_groups]:
            return

        msqlc, cur = None, None
        try:
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
                "SELECT `apikey`, `last_check` FROM `users` WHERE `ignored` = FALSE "
                "AND `tsuid` = %s ORDER BY `timestamp` DESC LIMIT 1",
                (client_unique_id,),
            )

            row = cur.fetchone()
            # User does not exist in DB
            if not row:
                revoked()
                return

            # User was checked today, don't check again
            if (datetime.datetime.today() - row[1]).days < 1:
                return

            logging.debug(
                "Checking cldbid:%s, cluid:%s", client_database_id, client_unique_id
            )

            account = common.fetch_account(row[0])
            # Account could not be fetched, invalid API key
            if not account:
                revoked()
                return

            world = common.find_world(account.get("world", -1))
            # World is not listed in config
            if not world:
                logging.info(
                    "User cldbid:%s is currently on %s.",
                    client_database_id,
                    common.world_name_from_id(world),
                )
                revoked("roles_revoked_invalid_world")
                return
            else:
                cur.execute(
                    "UPDATE `users` SET `last_check` = CURRENT_TIMESTAMP, "
                    "`guilds` = %s WHERE `apikey` = %s AND `ignored` = FALSE",
                    (json.dumps(account.get("guilds", [])), row[0]),
                )
                msqlc.commit()
        except msql.Error:
            logging.exception("MySQL error")
        except (requests.RequestException, common.RateLimitException):
            logging.exception("Error during API call")
        finally:
            if cur:
                cur.close()
            if msqlc:
                msqlc.close()


if __name__ == "__main__":
    common.init_logger("bot")
    Bot().loop()
