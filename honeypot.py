"""Kick members who post in the honeypot, with a private rejoin notice."""
import asyncio
import discord

REJOIN_URL = "https://discord.gg/quantix"
_pending = set()


async def handle_message(message, log_event, guild_id=0):
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
        if (me is None or not me.guild_permissions.kick_members
                or member.id == guild.owner_id or member.top_role >= me.top_role):
            await log_event(guild, f"⚠️ Honeypot: cannot kick <@{member.id}>. Check Kick Members permission and role hierarchy; the server owner cannot be kicked.")
            return True

        # Send before removal, while we still share the server with the member.
        dm_sent = False
        try:
            await asyncio.wait_for(member.send(
                "You posted in **#honeypot** on **Quantix**. Posting in this channel "
                "triggers an automatic kick from the server.\n\n"
                "**Was it an accident?** You can rejoin here: " + REJOIN_URL +
                "\nPlease avoid posting in #honeypot after rejoining."
            ), timeout=8)
            dm_sent = True
        except (discord.HTTPException, asyncio.TimeoutError):
            pass

        try:
            await member.kick(reason=f"Honeypot: message {message.id} in #{message.channel.name}")
        except discord.HTTPException as exc:
            await log_event(guild, f"⚠️ Honeypot: kick failed for <@{member.id}> (Discord HTTP {exc.status}). DM {'sent' if dm_sent else 'unavailable'}.")
        else:
            await log_event(guild, f"🍯 Honeypot: kicked <@{member.id}> for posting in <#{message.channel.id}>. Rejoin DM {'sent' if dm_sent else 'could not be delivered (DMs blocked or unavailable)'}.")
        return True
    finally:
        _pending.discard(identity)
