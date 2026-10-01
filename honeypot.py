"""Softban honeypot posters, delete 24 hours of messages, then allow rejoining."""
import asyncio
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
import discord

REJOIN_URL = "https://discord.gg/quantix"
_pending = set()
_recovery_task = None
DELETE_MESSAGE_SECONDS = 24 * 60 * 60


def pending_unbans(db_path, *, save=None, remove=None):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS honeypot_unbans (guild_id INTEGER, user_id INTEGER, reason TEXT NOT NULL, PRIMARY KEY(guild_id,user_id))")
        if save:
            con.execute("INSERT OR REPLACE INTO honeypot_unbans VALUES(?,?,?)", save)
        if remove:
            con.execute("DELETE FROM honeypot_unbans WHERE guild_id=? AND user_id=?", remove)
        return con.execute("SELECT guild_id,user_id,reason FROM honeypot_unbans").fetchall()


async def recover_unbans(bot, log_event, db_path):
    for guild_id, user_id, reason in pending_unbans(db_path):
        identity = (guild_id, user_id)
        guild = bot.get_guild(guild_id)
        if guild is None or identity in _pending:
            continue
        _pending.add(identity)
        try:
            user = discord.Object(id=user_id)
            try:
                ban = await guild.fetch_ban(user)
            except discord.NotFound:
                pending_unbans(db_path, remove=identity)
                continue
            # Never undo a separate moderator ban.
            if ban.reason != reason:
                pending_unbans(db_path, remove=identity)
                await log_event(guild, f"⚠️ Honeypot recovery: left a different moderator ban in place for <@{user_id}>.")
                continue
            await guild.unban(user, reason="Honeypot recovery: allow rejoining")
            pending_unbans(db_path, remove=identity)
            await log_event(guild, f"🍯 Honeypot: delayed unban completed for <@{user_id}>; they can rejoin.")
        except discord.HTTPException as exc:
            logging.warning("Honeypot unban recovery will retry (HTTP %s)", exc.status)
        finally:
            _pending.discard(identity)


def start_recovery(bot, log_event, db_path):
    global _recovery_task
    if _recovery_task is not None and not _recovery_task.done():
        return

    async def run():
        while not bot.is_closed():
            try:
                await recover_unbans(bot, log_event, db_path)
            except Exception:
                logging.exception("Honeypot recovery failed; will retry")
            await asyncio.sleep(30)

    _recovery_task = asyncio.create_task(run(), name="honeypot-unban-recovery")


async def handle_message(message, log_event, guild_id=0, db_path="quantix.db"):
    guild = message.guild
    member = message.author
    if (guild is None or member.bot or message.webhook_id
            or (guild_id and guild.id != guild_id)
            or not isinstance(message.channel, discord.TextChannel)
            or message.channel.name.casefold() != "honeypot"
            or not isinstance(member, discord.Member)
            or message.is_system()):
        return False

    identity = (guild.id, member.id)
    if identity in _pending:
        return True
    _pending.add(identity)
    try:
        me = guild.me
        if (me is None or not me.guild_permissions.ban_members
                or member.id == guild.owner_id or member.top_role >= me.top_role):
            await log_event(guild, f"⚠️ Honeypot: cannot softban <@{member.id}>. Check Ban Members permission and role hierarchy; the server owner cannot be removed.")
            return True

        reason = f"Honeypot softban: message {message.id} in channel {message.channel.id}"
        # Persist before banning so a restart cannot lose the required unban.
        pending_unbans(db_path, save=(guild.id, member.id, reason))

        # Send before removal, while we still share the server with the member.
        dm_sent = False
        try:
            await asyncio.wait_for(member.send(
                "You posted in **#honeypot** on **Quantix**. Posting in this channel "
                "triggers removal from the server and deletion of your last **24 hours "
                "of messages across the server**. The temporary ban is immediately lifted.\n\n"
                "**Was it an accident?** You can rejoin here: " + REJOIN_URL +
                "\nPlease avoid posting in #honeypot after rejoining."
            ), timeout=8)
            dm_sent = True
        except (discord.HTTPException, asyncio.TimeoutError):
            pass

        try:
            await guild.ban(member, reason=reason, delete_message_seconds=DELETE_MESSAGE_SECONDS)
        except discord.HTTPException as exc:
            # Keep the recovery record: a failed response can be ambiguous.
            await log_event(guild, f"⚠️ Honeypot: softban request failed for <@{member.id}> (Discord HTTP {exc.status}); recovery will check the ban state. DM {'sent' if dm_sent else 'unavailable'}.")
        else:
            try:
                await guild.unban(member, reason="Honeypot: allow rejoining after 24h message cleanup")
            except discord.HTTPException as exc:
                await log_event(guild, f"⚠️ Honeypot: removed <@{member.id}> with 24h message cleanup, but unban failed (HTTP {exc.status}). Automatic retry is pending; they cannot rejoin yet.")
            else:
                pending_unbans(db_path, remove=identity)
                await log_event(guild, f"🍯 Honeypot: removed and unbanned <@{member.id}>; requested deletion of their last 24h of server messages. Rejoin DM {'sent' if dm_sent else 'could not be delivered (DMs blocked or unavailable)'}.")
        return True
    finally:
        _pending.discard(identity)
