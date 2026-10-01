import os
import random
import secrets
import sqlite3
import asyncio
import re
from datetime import timedelta, datetime, timezone
from typing import Optional

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")
DEFAULT_GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
LICENSE_API_URL = os.getenv("LICENSE_API_URL", "").strip()
LICENSE_API_SECRET = os.getenv("LICENSE_API_SECRET", "").strip()
REVIEW_API_URL = os.getenv("REVIEW_API_URL", "").strip()
REVIEW_API_SECRET = os.getenv("REVIEW_API_SECRET", "").strip()
DEFAULT_VOUCH_CHANNEL_ID = int(os.getenv("VOUCH_CHANNEL_ID", "0") or 0)

# Quantix defaults (can still be changed later with /setup)
DEFAULT_WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0") or 0)
DEFAULT_LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or 0)
DEFAULT_AUX_LOG_CHANNEL_ID_1 = int(os.getenv("AUX_LOG_CHANNEL_ID_1", "0") or 0)
DEFAULT_AUX_LOG_CHANNEL_ID_2 = int(os.getenv("AUX_LOG_CHANNEL_ID_2", "0") or 0)
DEFAULT_PRO_ROLE_ID = int(os.getenv("PRO_ROLE_ID", "0") or 0)
DEFAULT_ELITE_ROLE_ID = int(os.getenv("ELITE_ROLE_ID", "0") or 0)
DEFAULT_SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID", "0") or 0)
DEFAULT_TICKET_PANEL_CHANNEL_ID = int(os.getenv("TICKET_PANEL_CHANNEL_ID", "0") or 0)
DEFAULT_TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0") or 0)
DEFAULT_VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "1555124964175380502") or 1555124964175380502)
DEFAULT_RULES_CHANNEL_ID = int(os.getenv("RULES_CHANNEL_ID", "0") or 0)

DB_PATH = os.getenv("DB_PATH", "quantix.db")
LICENSE_API_PORT = int(os.getenv("PORT", "8080") or 8080)


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            welcome_channel_id INTEGER DEFAULT 0,
            log_channel_id INTEGER DEFAULT 0,
            customer_role_id INTEGER DEFAULT 0,
            pro_role_id INTEGER DEFAULT 0,
            elite_role_id INTEGER DEFAULT 0,
            ticket_category_id INTEGER DEFAULT 0,
            support_role_id INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS verify_codes (
            code TEXT PRIMARY KEY,
            tier TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            used_by INTEGER DEFAULT 0,
            created_by INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS giveaways (
            message_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            prize TEXT NOT NULL,
            ended INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS vouches (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            review TEXT NOT NULL,
            rating INTEGER,
            synced INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS licenses (
            license_key TEXT PRIMARY KEY,
            tier TEXT NOT NULL,
            hwid TEXT NOT NULL,
            discord_user_id INTEGER NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked INTEGER DEFAULT 0,
            revoked_at TEXT,
            revoked_by INTEGER DEFAULT 0
        );
        """
    )
    # Lightweight migration for older Railway databases created before ticket types existed.
    columns = {row[1] for row in con.execute("PRAGMA table_info(tickets)").fetchall()}
    if "ticket_type" not in columns:
        con.execute("ALTER TABLE tickets ADD COLUMN ticket_type TEXT DEFAULT 'support'")
    con.commit()
    con.close()


def settings_for(guild_id: int):
    con = db()
    row = con.execute("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
    if not row:
        con.execute(
            """INSERT INTO guild_settings(
                guild_id, welcome_channel_id, log_channel_id, pro_role_id, elite_role_id,
                ticket_category_id, support_role_id
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                guild_id, DEFAULT_WELCOME_CHANNEL_ID, DEFAULT_LOG_CHANNEL_ID,
                DEFAULT_PRO_ROLE_ID, DEFAULT_ELITE_ROLE_ID, DEFAULT_TICKET_CATEGORY_ID,
                DEFAULT_SUPPORT_ROLE_ID,
            ),
        )
        con.commit()
        row = con.execute("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
    con.close()
    return row


intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
license_api_runner = None


def quantix_embed(title: str, description: str = "", *, success: Optional[bool] = None):
    if success is True:
        color = discord.Color.green()
    elif success is False:
        color = discord.Color.red()
    else:
        color = discord.Color.from_rgb(139, 92, 246)
    em = discord.Embed(title=title, description=description, color=color)
    em.set_footer(text="Quantix • Performance. Refined.")
    return em


async def log_event(guild: discord.Guild, text: str):
    s = settings_for(guild.id)
    cid = int(s["log_channel_id"] or 0)
    if cid:
        ch = guild.get_channel(cid)
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=quantix_embed("Quantix Log", text))
            except discord.HTTPException:
                pass


def parse_rating(text: str):
    """Extract an optional 1-5 rating from common vouch formats."""
    patterns = [
        r"(?<!\d)([1-5])\s*/\s*5(?!\d)",
        r"(?<!\d)([1-5])\s*(?:stars?|sterne?)(?!\w)",
    ]
    lowered = text.lower()
    for pattern in patterns:
        m = re.search(pattern, lowered, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))

    # Count explicit star emojis if the user only types stars as the rating.
    stars = text.count("⭐") + text.count("★")
    if 1 <= stars <= 5:
        return stars
    return None


def clean_review_text(text: str):
    cleaned = re.sub(r"(?<!\d)[1-5]\s*/\s*5(?!\d)", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?<!\d)[1-5]\s*(?:stars?|sterne?)(?!\w)", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("⭐", "").replace("★", "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|•\n\t")
    return cleaned or text.strip()


async def sync_vouch_to_website(*, guild: discord.Guild, channel_id: int, message_id: int, author: discord.abc.User, review: str, rating=None):
    """Send an already-approved Discord vouch to the Quantix website review API."""
    if not REVIEW_API_URL:
        return {"ok": False, "reason": "review_api_not_configured"}

    payload = {
        "source": "discord",
        "approved": True,
        "status": "approved",
        "review": review,
        "rating": rating,
        "discord_user_id": str(author.id),
        "discord_username": str(author),
        "discord_display_name": getattr(author, "display_name", str(author)),
        "discord_avatar_url": str(author.display_avatar.url),
        "discord_guild_id": str(guild.id),
        "discord_channel_id": str(channel_id),
        "discord_message_id": str(message_id),
    }
    headers = {"Content-Type": "application/json"}
    if REVIEW_API_SECRET:
        headers["Authorization"] = f"Bearer {REVIEW_API_SECRET}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(REVIEW_API_URL, json=payload, headers=headers, timeout=12) as r:
                if 200 <= r.status < 300:
                    try:
                        body = await r.json()
                    except Exception:
                        body = {}
                    return {"ok": True, "body": body}
                return {"ok": False, "reason": f"http_{r.status}"}
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}


async def store_vouch(message_id: int, guild_id: int, channel_id: int, user_id: int, review: str, rating, synced: bool):
    con = db()
    con.execute(
        "INSERT OR REPLACE INTO vouches(message_id,guild_id,channel_id,user_id,review,rating,synced) VALUES(?,?,?,?,?,?,?)",
        (message_id, guild_id, channel_id, user_id, review, rating, int(bool(synced))),
    )
    con.commit(); con.close()


LICENSE_DURATIONS = {
    "1d": ("1 Day", timedelta(days=1)),
    "1w": ("1 Week", timedelta(weeks=1)),
    "1m": ("1 Month", timedelta(days=30)),
    "1y": ("1 Year", timedelta(days=365)),
    "permanent": ("Permanent", None),
}


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except Exception:
        return None


def make_license_key(tier: str):
    prefix = "ELITE" if tier == "elite" else "PRO"
    return f"QTX-{prefix}-{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}"


def license_status(row):
    if not row:
        return "invalid"
    if int(row["revoked"] or 0):
        return "revoked"
    exp = parse_iso(row["expires_at"])
    if exp and utcnow() >= exp:
        return "expired"
    return "active"


def is_staff(member: discord.Member):
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    s = settings_for(member.guild.id)
    support_role_id = int(s["support_role_id"] or 0)
    return bool(support_role_id and any(r.id == support_role_id for r in member.roles))


async def license_validate_request(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    key = str(body.get("key", "")).strip().upper()
    hwid = str(body.get("hwid", "")).strip()
    if not key or not hwid:
        return web.json_response({"ok": False, "error": "key_and_hwid_required"}, status=400)

    con = db()
    row = con.execute("SELECT * FROM licenses WHERE license_key=?", (key,)).fetchone()
    con.close()
    status = license_status(row)
    if status != "active":
        return web.json_response({"ok": False, "status": status}, status=403)
    if hwid != row["hwid"]:
        return web.json_response({"ok": False, "status": "hwid_mismatch"}, status=403)

    return web.json_response({
        "ok": True,
        "status": "active",
        "tier": row["tier"],
        "expires_at": row["expires_at"],
        "permanent": row["expires_at"] is None,
        "discord_user_id": str(row["discord_user_id"]),
    })


async def license_health_request(request: web.Request):
    return web.json_response({"ok": True, "service": "quantix-license-api"})


async def start_license_api():
    app = web.Application()
    app.router.add_get("/health", license_health_request)
    app.router.add_post("/api/license/validate", license_validate_request)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", LICENSE_API_PORT)
    await site.start()
    print(f"License API listening on port {LICENSE_API_PORT}")
    return runner


TICKET_TYPES = {
    "purchase": {
        "label": "Purchase Questions",
        "emoji": "🛒",
        "description": "Questions before or after buying Quantix",
        "channel_prefix": "purchase",
        "title": "Purchase Questions",
        "intro": "Tell us what you want to know about purchasing, plans, pricing, or your order.",
    },
    "verification": {
        "label": "Buyer Verification",
        "emoji": "✅",
        "description": "Get help verifying your Pro or Elite purchase",
        "channel_prefix": "verify",
        "title": "Buyer Verification",
        "intro": "Send the information needed to verify your Quantix purchase. Never post passwords, recovery codes, or full payment credentials.",
    },
    "technical": {
        "label": "Technical Support",
        "emoji": "🛠️",
        "description": "Utility, license, setup, or tweaking support",
        "channel_prefix": "support",
        "title": "Technical Support",
        "intro": "Describe the issue, what you already tried, and include screenshots or error text when useful.",
    },
    "media": {
        "label": "Media and Partnerships",
        "emoji": "🤝",
        "description": "Creators, affiliates, collaborations, and partnerships",
        "channel_prefix": "media",
        "title": "Media & Partnerships",
        "intro": "Tell us about your channel, audience, collaboration idea, or partnership request.",
    },
}


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=data["label"],
                value=key,
                description=data["description"],
                emoji=data["emoji"],
            )
            for key, data in TICKET_TYPES.items()
        ]
        super().__init__(
            placeholder="Choose a ticket type...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="quantix:ticket_type",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)

        # Acknowledge immediately so Discord never shows "application did not respond"
        # while Railway is creating the private channel.
        await interaction.response.defer(ephemeral=True, thinking=True)

        ticket_type = self.values[0]
        config = TICKET_TYPES.get(ticket_type, TICKET_TYPES["technical"])

        con = db()
        existing = con.execute(
            "SELECT channel_id, ticket_type FROM tickets WHERE guild_id=? AND user_id=? AND status='open'",
            (guild.id, interaction.user.id),
        ).fetchone()
        con.close()
        if existing and guild.get_channel(existing["channel_id"]):
            return await interaction.followup.send(
                f"You already have an open ticket: <#{existing['channel_id']}>", ephemeral=True
            )

        s = settings_for(guild.id)
        category = guild.get_channel(int(s["ticket_category_id"] or 0))
        support_role = guild.get_role(int(s["support_role_id"] or 0))
        me = guild.me or guild.get_member(bot.user.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            ),
        }
        if me:
            overwrites[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
            )
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            )

        safe_user = re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower().replace(" ", "-")) or str(interaction.user.id)
        name = f"{config['channel_prefix']}-{safe_user}"[:90]
        try:
            channel = await guild.create_text_channel(
                name,
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                topic=f"Quantix {config['title']} ticket • User ID: {interaction.user.id}",
                reason=f"Quantix ticket opened by {interaction.user}",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "I couldn't create the ticket channel. Please give the bot **Manage Channels** permission and try again.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                "Discord couldn't create the ticket right now. Please try again in a moment.", ephemeral=True
            )

        con = db()
        con.execute(
            "INSERT OR REPLACE INTO tickets(channel_id,guild_id,user_id,status,ticket_type) VALUES(?,?,?,'open',?)",
            (channel.id, guild.id, interaction.user.id, ticket_type),
        )
        con.commit(); con.close()

        em = quantix_embed(
            f"{config['emoji']} {config['title']}",
            f"{config['intro']}\n\n"
            f"**Opened by:** {interaction.user.mention}\n"
            f"**Department:** {config['label']}\n\n"
            "A staff member will be with you as soon as possible. Use **/closeticket** when the issue is solved."
        )
        # Ping the support role exactly once when a new ticket is created.
        # This happens only here, never on bot restarts / persistent-view registration.
        ping_parts = [interaction.user.mention]
        if support_role:
            ping_parts.append(support_role.mention)
        await channel.send(
            " ".join(ping_parts),
            embed=em,
            allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
        )
        await interaction.followup.send(f"Ticket created: {channel.mention} ✅", ephemeral=True)
        await log_event(
            guild,
            f"{config['emoji']} **{config['label']}** ticket opened by {interaction.user.mention}: {channel.mention}"
        )


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())




class RulesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="I agree — verify me",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="quantix:rules_verify",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)

        # Acknowledge instantly so Discord never shows "application did not respond".
        await interaction.response.defer(ephemeral=True)

        role = interaction.guild.get_role(DEFAULT_VERIFIED_ROLE_ID)
        if role is None:
            return await interaction.followup.send(
                "⚠️ The Verified role could not be found. Please contact staff.", ephemeral=True
            )

        if role in interaction.user.roles:
            return await interaction.followup.send("✅ You are already verified.", ephemeral=True)

        try:
            await interaction.user.add_roles(role, reason="Accepted Quantix community rules")
        except discord.Forbidden:
            return await interaction.followup.send(
                "⚠️ I cannot assign the Verified role. Move the bot role above the Verified role and give it **Manage Roles** permission.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                "⚠️ Discord could not assign the role right now. Please try again.", ephemeral=True
            )

        await interaction.followup.send(f"✅ Verified! You now have {role.mention}.", ephemeral=True)
        await log_event(interaction.guild, f"✅ {interaction.user.mention} accepted the rules and received {role.mention}")


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.users = set()

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.success, emoji="🎉", custom_id="quantix:giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.users.add(interaction.user.id)
        await interaction.response.send_message("You're entered! 🎉", ephemeral=True)


@bot.event
async def on_ready():
    global license_api_runner
    bot.add_view(TicketView())
    bot.add_view(RulesView())
    if license_api_runner is None:
        try:
            license_api_runner = await start_license_api()
        except OSError as exc:
            print(f"License API start failed: {exc}")
    try:
        if DEFAULT_GUILD_ID:
            guild_obj = discord.Object(id=DEFAULT_GUILD_ID)
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
        else:
            synced = await bot.tree.sync()
        print(f"Logged in as {bot.user} | synced {len(synced)} commands")
    except Exception as e:
        print(f"Command sync failed: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # Automatic Discord -> website vouch sync for one configured review channel.
    if DEFAULT_VOUCH_CHANNEL_ID and message.channel.id == DEFAULT_VOUCH_CHANNEL_ID:
        content = (message.content or "").strip()
        if content:
            rating = parse_rating(content)
            review = clean_review_text(content)
            result = await sync_vouch_to_website(
                guild=message.guild, channel_id=message.channel.id, message_id=message.id,
                author=message.author, review=review, rating=rating
            )
            await store_vouch(message.id, message.guild.id, message.channel.id, message.author.id, review, rating, result.get("ok"))
            try:
                await message.add_reaction("✅" if result.get("ok") else "⚠️")
            except discord.HTTPException:
                pass
            if result.get("ok"):
                await log_event(message.guild, f"⭐ Website vouch synced from {message.author.mention} • rating: {rating or 'none'}")
            else:
                await log_event(message.guild, f"⚠️ Vouch saved but website sync failed ({result.get('reason', 'unknown')}) • {message.author.mention}")

    await bot.process_commands(message)


@bot.event
async def on_member_join(member: discord.Member):
    s = settings_for(member.guild.id)
    cid = int(s["welcome_channel_id"] or 0)
    if not cid:
        return
    channel = member.guild.get_channel(cid)
    if isinstance(channel, discord.TextChannel):
        em = quantix_embed(
            "Welcome to Quantix 💜",
            f"Hey {member.mention}, welcome to **{member.guild.name}**!\n\nCheck the rules, grab your roles and open a ticket if you need help."
        )
        em.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=em)


@bot.tree.command(name="ping", description="Check the Quantix bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 **{round(bot.latency * 1000)}ms**", ephemeral=True)


@bot.tree.command(name="setup", description="Configure Quantix bot channels and roles")
@app_commands.checks.has_permissions(administrator=True)
async def setup(
    interaction: discord.Interaction,
    welcome_channel: Optional[discord.TextChannel] = None,
    log_channel: Optional[discord.TextChannel] = None,
    customer_role: Optional[discord.Role] = None,
    pro_role: Optional[discord.Role] = None,
    elite_role: Optional[discord.Role] = None,
    ticket_category: Optional[discord.CategoryChannel] = None,
    support_role: Optional[discord.Role] = None,
):
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message("Server only.", ephemeral=True)
    current = settings_for(guild.id)
    vals = {
        "welcome_channel_id": welcome_channel.id if welcome_channel else current["welcome_channel_id"],
        "log_channel_id": log_channel.id if log_channel else current["log_channel_id"],
        "customer_role_id": customer_role.id if customer_role else current["customer_role_id"],
        "pro_role_id": pro_role.id if pro_role else current["pro_role_id"],
        "elite_role_id": elite_role.id if elite_role else current["elite_role_id"],
        "ticket_category_id": ticket_category.id if ticket_category else current["ticket_category_id"],
        "support_role_id": support_role.id if support_role else current["support_role_id"],
    }
    con = db()
    con.execute(
        """UPDATE guild_settings SET welcome_channel_id=?,log_channel_id=?,customer_role_id=?,pro_role_id=?,elite_role_id=?,ticket_category_id=?,support_role_id=? WHERE guild_id=?""",
        (*vals.values(), guild.id),
    )
    con.commit(); con.close()
    await interaction.response.send_message(embed=quantix_embed("Setup saved ✅", "Quantix bot configuration has been updated.", success=True), ephemeral=True)




@bot.tree.command(name="rulespanel", description="Post the Quantix rules verification panel")
@app_commands.checks.has_permissions(administrator=True)
async def rulespanel(interaction: discord.Interaction):
    em = quantix_embed(
        "Community rules",
        "1.  **Be respectful.**\n"
        "No harassment, hate speech, threats, or targeted abuse.\n"
        "2.  **No scams or impersonation.**\n"
        "Staff will never ask for passwords or recovery codes.\n"
        "3.  **No piracy, malware, or credential tools.**\n"
        "Do not post stolen, cracked, or malicious content.\n"
        "4.  **Keep private content private.**\n"
        "Buyer downloads and support details stay in their intended channels.\n"
        "5.  **No spam or unsolicited advertising.**\n"
        "Use the proper channels and follow staff guidance.\n"
        "6.  **Use tweaks responsibly.**\n"
        "Back up your system and follow rollback instructions.\n"
        "7.  **Follow Discord’s Terms and Community Guidelines.**\n\n"
        "Press below to confirm you agree and receive the **Verified** role."
    )
    em.set_footer(text="Quantix Bot managed panel: rules")

    guild = interaction.guild
    target = guild.get_channel(DEFAULT_RULES_CHANNEL_ID) if guild and DEFAULT_RULES_CHANNEL_ID else None
    if isinstance(target, discord.TextChannel) and interaction.channel_id != target.id:
        await target.send(embed=em, view=RulesView())
        await interaction.response.send_message(f"Rules panel posted in {target.mention} ✅", ephemeral=True)
    else:
        await interaction.response.send_message(embed=em, view=RulesView())


@bot.tree.command(name="ticketpanel", description="Post the Quantix support ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def ticketpanel(interaction: discord.Interaction):
    em = quantix_embed(
        "Private support tickets",
        "Select the department that best matches your request. The bot will create a private channel visible only to you and the appropriate staff team.\n\n"
        "🛒 **Purchase Questions**\n"
        "✅ **Buyer Verification**\n"
        "🛠️ **Technical Support**\n"
        "🤝 **Media and Partnerships**\n\n"
        "One open ticket per person is allowed. Never include passwords, recovery codes, bot tokens, or full payment credentials."
    )
    guild = interaction.guild
    target = guild.get_channel(DEFAULT_TICKET_PANEL_CHANNEL_ID) if guild and DEFAULT_TICKET_PANEL_CHANNEL_ID else None
    if isinstance(target, discord.TextChannel) and interaction.channel_id != target.id:
        await target.send(embed=em, view=TicketView())
        await interaction.response.send_message(f"Ticket panel posted in {target.mention} ✅", ephemeral=True)
    else:
        await interaction.response.send_message(embed=em, view=TicketView())


@bot.tree.command(name="closeticket", description="Close the current support ticket")
async def closeticket(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel
    if not guild or not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message("Server text channels only.", ephemeral=True)
    con = db()
    row = con.execute("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (channel.id,)).fetchone()
    if not row:
        con.close()
        return await interaction.response.send_message("This is not an open Quantix ticket.", ephemeral=True)
    s = settings_for(guild.id)
    support_role = guild.get_role(int(s["support_role_id"] or 0))
    can_close = interaction.user.id == row["user_id"] or interaction.user.guild_permissions.manage_channels or (support_role and support_role in interaction.user.roles)
    if not can_close:
        con.close()
        return await interaction.response.send_message("You can't close this ticket.", ephemeral=True)
    con.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (channel.id,))
    con.commit(); con.close()
    await interaction.response.send_message("Closing ticket in 3 seconds…")
    ticket_type = row["ticket_type"] if "ticket_type" in row.keys() else "support"
    type_cfg = TICKET_TYPES.get(ticket_type, TICKET_TYPES["technical"])
    await log_event(guild, f"🔒 **{type_cfg['label']}** ticket closed by {interaction.user.mention}: #{channel.name}")
    await asyncio.sleep(3)
    await channel.delete(reason=f"Ticket closed by {interaction.user}")


@bot.tree.command(name="announce", description="Post a professional Quantix announcement")
@app_commands.checks.has_permissions(manage_guild=True)
async def announce(interaction: discord.Interaction, title: str, message: str, channel: Optional[discord.TextChannel] = None):
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await interaction.response.send_message("Choose a text channel.", ephemeral=True)
    await target.send(embed=quantix_embed(title, message))
    await interaction.response.send_message("Announcement posted ✅", ephemeral=True)


key_group = app_commands.Group(name="key", description="Manage Quantix utility licenses")
keys_group = app_commands.Group(name="keys", description="List Quantix utility licenses")


@key_group.command(name="create", description="Create a Pro/Elite license and DM it to a customer")
@app_commands.choices(
    tier=[
        app_commands.Choice(name="Pro", value="pro"),
        app_commands.Choice(name="Elite", value="elite"),
    ],
    duration=[
        app_commands.Choice(name="1 Day", value="1d"),
        app_commands.Choice(name="1 Week", value="1w"),
        app_commands.Choice(name="1 Month", value="1m"),
        app_commands.Choice(name="1 Year", value="1y"),
        app_commands.Choice(name="Permanent", value="permanent"),
    ],
)
async def key_create(
    interaction: discord.Interaction,
    customer: discord.Member,
    tier: app_commands.Choice[str],
    hwid: str,
    duration: app_commands.Choice[str],
):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    hwid = hwid.strip()
    if len(hwid) < 4 or len(hwid) > 300:
        return await interaction.response.send_message("❌ Please enter a valid HWID.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    label, delta = LICENSE_DURATIONS[duration.value]
    created = utcnow()
    expires = created + delta if delta else None
    key = make_license_key(tier.value)
    con = db()
    con.execute(
        "INSERT INTO licenses(license_key,tier,hwid,discord_user_id,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
        (key, tier.value, hwid, customer.id, interaction.user.id, iso(created), iso(expires)),
    )
    con.commit(); con.close()

    dm = quantix_embed(
        f"🔑 Quantix {tier.name} License",
        "Your Quantix license has been created. Keep this key private.",
        success=True,
    )
    dm.add_field(name="License Key", value=f"`{key}`", inline=False)
    dm.add_field(name="Duration", value=label, inline=True)
    dm.add_field(name="HWID", value=f"`{hwid}`", inline=False)
    dm.add_field(name="Expires", value=(f"<t:{int(expires.timestamp())}:F>" if expires else "Permanent"), inline=False)
    dm.set_footer(text="Quantix • Do not share your license key")

    dm_ok = True
    try:
        await customer.send(embed=dm)
    except discord.HTTPException:
        dm_ok = False

    await interaction.followup.send(
        f"✅ **{tier.name}** key created for {customer.mention} • **{label}**\n"
        + ("📩 Sent by DM." if dm_ok else "⚠️ Key created, but the customer's DMs are closed."),
        ephemeral=True,
    )
    await log_event(interaction.guild, f"🔑 {tier.name} key created for {customer.mention} • {label} • by {interaction.user.mention}")


@key_group.command(name="info", description="Show license status without exposing the full key publicly")
async def key_info(interaction: discord.Interaction, license_key: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    key = license_key.strip().upper()
    con = db(); row = con.execute("SELECT * FROM licenses WHERE license_key=?", (key,)).fetchone(); con.close()
    if not row:
        return await interaction.response.send_message("❌ License not found.", ephemeral=True)
    status = license_status(row)
    exp = parse_iso(row["expires_at"])
    masked = key[:9] + "••••••" + key[-4:]
    em = quantix_embed("Quantix License", success=(status == "active"))
    em.add_field(name="Key", value=f"`{masked}`", inline=False)
    em.add_field(name="Tier", value=row["tier"].title(), inline=True)
    em.add_field(name="Status", value=status.title(), inline=True)
    em.add_field(name="Customer", value=f"<@{row['discord_user_id']}>", inline=True)
    em.add_field(name="HWID", value=f"`{row['hwid']}`", inline=False)
    em.add_field(name="Expires", value=(f"<t:{int(exp.timestamp())}:F>" if exp else "Permanent"), inline=False)
    await interaction.response.send_message(embed=em, ephemeral=True)


@key_group.command(name="revoke", description="Revoke a Quantix license immediately")
async def key_revoke(interaction: discord.Interaction, license_key: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    key = license_key.strip().upper()
    con = db(); row = con.execute("SELECT * FROM licenses WHERE license_key=?", (key,)).fetchone()
    if not row:
        con.close(); return await interaction.response.send_message("❌ License not found.", ephemeral=True)
    con.execute("UPDATE licenses SET revoked=1, revoked_at=?, revoked_by=? WHERE license_key=?", (iso(utcnow()), interaction.user.id, key))
    con.commit(); con.close()
    await interaction.response.send_message("✅ License revoked.", ephemeral=True)
    await log_event(interaction.guild, f"🚫 License revoked for <@{row['discord_user_id']}> by {interaction.user.mention}")


@key_group.command(name="reset-hwid", description="Replace the HWID bound to an existing license")
async def key_reset_hwid(interaction: discord.Interaction, license_key: str, new_hwid: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    key = license_key.strip().upper(); new_hwid = new_hwid.strip()
    if len(new_hwid) < 4 or len(new_hwid) > 300:
        return await interaction.response.send_message("❌ Please enter a valid HWID.", ephemeral=True)
    con = db(); row = con.execute("SELECT * FROM licenses WHERE license_key=?", (key,)).fetchone()
    if not row:
        con.close(); return await interaction.response.send_message("❌ License not found.", ephemeral=True)
    con.execute("UPDATE licenses SET hwid=? WHERE license_key=?", (new_hwid, key)); con.commit(); con.close()
    await interaction.response.send_message("✅ HWID updated.", ephemeral=True)
    await log_event(interaction.guild, f"🖥️ HWID changed for <@{row['discord_user_id']}> by {interaction.user.mention}")


@key_group.command(name="extend", description="Extend or make a license permanent")
@app_commands.choices(duration=[
    app_commands.Choice(name="+1 Day", value="1d"),
    app_commands.Choice(name="+1 Week", value="1w"),
    app_commands.Choice(name="+1 Month", value="1m"),
    app_commands.Choice(name="+1 Year", value="1y"),
    app_commands.Choice(name="Make Permanent", value="permanent"),
])
async def key_extend(interaction: discord.Interaction, license_key: str, duration: app_commands.Choice[str]):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    key = license_key.strip().upper()
    con = db(); row = con.execute("SELECT * FROM licenses WHERE license_key=?", (key,)).fetchone()
    if not row:
        con.close(); return await interaction.response.send_message("❌ License not found.", ephemeral=True)
    label, delta = LICENSE_DURATIONS[duration.value]
    if delta is None:
        new_exp = None
    else:
        current_exp = parse_iso(row["expires_at"])
        base = current_exp if current_exp and current_exp > utcnow() else utcnow()
        new_exp = base + delta
    con.execute("UPDATE licenses SET expires_at=?, revoked=0, revoked_at=NULL, revoked_by=0 WHERE license_key=?", (iso(new_exp), key))
    con.commit(); con.close()
    await interaction.response.send_message(f"✅ License updated: **{label}**.", ephemeral=True)
    await log_event(interaction.guild, f"⏱️ License extended for <@{row['discord_user_id']}> • {label} • by {interaction.user.mention}")


@keys_group.command(name="user", description="Show all licenses belonging to a Discord customer")
async def keys_user(interaction: discord.Interaction, customer: discord.Member):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    con = db(); rows = con.execute("SELECT * FROM licenses WHERE discord_user_id=? ORDER BY created_at DESC", (customer.id,)).fetchall(); con.close()
    if not rows:
        return await interaction.response.send_message(f"No licenses found for {customer.mention}.", ephemeral=True)
    lines = []
    for row in rows[:20]:
        exp = parse_iso(row["expires_at"])
        masked = row["license_key"][:9] + "••••••" + row["license_key"][-4:]
        expiry = "Permanent" if not exp else f"<t:{int(exp.timestamp())}:d>"
        lines.append(f"• `{masked}` • **{row['tier'].title()}** • {license_status(row).title()} • {expiry}")
    await interaction.response.send_message(embed=quantix_embed(f"Licenses • {customer}", "\n".join(lines)), ephemeral=True)


bot.tree.add_command(key_group)
bot.tree.add_command(keys_group)


@bot.tree.command(name="createcode", description="Create a one-use customer verification code")
@app_commands.choices(tier=[
    app_commands.Choice(name="Customer", value="customer"),
    app_commands.Choice(name="Pro", value="pro"),
    app_commands.Choice(name="Elite", value="elite"),
])
@app_commands.checks.has_permissions(administrator=True)
async def createcode(interaction: discord.Interaction, tier: app_commands.Choice[str]):
    code = f"QX-{secrets.token_hex(4).upper()}"
    con = db()
    con.execute("INSERT INTO verify_codes(code,tier,created_by) VALUES(?,?,?)", (code, tier.value, interaction.user.id))
    con.commit(); con.close()
    await interaction.response.send_message(f"🔑 **{tier.name} code:** `{code}`\nOne-time use.", ephemeral=True)


async def verify_with_api(code: str, discord_id: int):
    if not LICENSE_API_URL:
        return None
    headers = {"Authorization": f"Bearer {LICENSE_API_SECRET}"} if LICENSE_API_SECRET else {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(LICENSE_API_URL, json={"key": code, "discord_id": str(discord_id)}, headers=headers, timeout=10) as r:
                if r.status != 200:
                    return {"ok": False}
                return await r.json()
    except Exception:
        return {"ok": False}


@bot.tree.command(name="verify", description="Verify your Quantix purchase/license")
async def verify(interaction: discord.Interaction, code: str):
    guild = interaction.guild
    if not guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    api_result = await verify_with_api(code.strip(), interaction.user.id)
    tier = None
    if api_result is not None:
        if api_result.get("ok"):
            tier = str(api_result.get("tier", "customer")).lower()
        else:
            return await interaction.followup.send("❌ That license could not be verified.")
    else:
        con = db()
        row = con.execute("SELECT * FROM verify_codes WHERE code=?", (code.strip().upper(),)).fetchone()
        if not row or row["used"]:
            con.close()
            return await interaction.followup.send("❌ Invalid or already-used verification code.")
        tier = row["tier"]
        con.execute("UPDATE verify_codes SET used=1, used_by=? WHERE code=?", (interaction.user.id, row["code"]))
        con.commit(); con.close()

    s = settings_for(guild.id)
    role_id = {
        "elite": int(s["elite_role_id"] or 0),
        "pro": int(s["pro_role_id"] or 0),
        "customer": int(s["customer_role_id"] or 0),
    }.get(tier, int(s["customer_role_id"] or 0))
    role = guild.get_role(role_id)
    if not role:
        return await interaction.followup.send("⚠️ License verified, but the matching role is not configured. Ask staff to run `/setup`.")
    try:
        await interaction.user.add_roles(role, reason="Quantix purchase verification")
    except discord.Forbidden:
        return await interaction.followup.send("⚠️ Verified, but I cannot assign the role. Move the bot role above the customer roles.")
    await interaction.followup.send(f"✅ Verified as **{tier.title()}**. You now have {role.mention}.")
    await log_event(guild, f"✅ {interaction.user.mention} verified as **{tier.title()}**")


@bot.tree.command(name="vouch", description="Post a Quantix review and sync it to the website")
@app_commands.describe(review="Your review", rating="Optional rating from 1 to 5")
async def vouch(interaction: discord.Interaction, review: str, rating: Optional[app_commands.Range[int, 1, 5]] = None):
    guild = interaction.guild
    if not guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)

    target = guild.get_channel(DEFAULT_VOUCH_CHANNEL_ID) if DEFAULT_VOUCH_CHANNEL_ID else interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await interaction.response.send_message("The vouch channel is not configured yet.", ephemeral=True)

    stars = ("⭐" * int(rating)) if rating else "No rating"
    em = quantix_embed("Quantix Vouch 💜", review, success=True)
    em.add_field(name="Rating", value=stars, inline=True)
    em.add_field(name="Customer", value=interaction.user.mention, inline=True)
    em.set_thumbnail(url=interaction.user.display_avatar.url)
    posted = await target.send(embed=em)

    result = await sync_vouch_to_website(
        guild=guild, channel_id=target.id, message_id=posted.id, author=interaction.user,
        review=review.strip(), rating=int(rating) if rating is not None else None
    )
    await store_vouch(posted.id, guild.id, target.id, interaction.user.id, review.strip(), int(rating) if rating is not None else None, result.get("ok"))

    if result.get("ok"):
        await interaction.response.send_message(f"✅ Vouch posted in {target.mention} and published on the website.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"✅ Vouch posted in {target.mention}. Website sync is not configured yet, so it was saved for later.",
            ephemeral=True,
        )


@bot.tree.command(name="clear", description="Delete recent messages")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Text channel only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 Deleted **{len(deleted)}** messages.")


@bot.tree.command(name="kick", description="Kick a member")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=f"{reason} | By {interaction.user}")
    await interaction.response.send_message(f"👢 Kicked **{member}** — {reason}")
    if interaction.guild: await log_event(interaction.guild, f"👢 {member} kicked by {interaction.user.mention}: {reason}")


@bot.tree.command(name="ban", description="Ban a member")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=f"{reason} | By {interaction.user}")
    await interaction.response.send_message(f"🔨 Banned **{member}** — {reason}")
    if interaction.guild: await log_event(interaction.guild, f"🔨 {member} banned by {interaction.user.mention}: {reason}")


@bot.tree.command(name="timeout", description="Timeout a member")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
    await member.timeout(timedelta(minutes=minutes), reason=f"{reason} | By {interaction.user}")
    await interaction.response.send_message(f"⏳ Timed out **{member}** for **{minutes} min** — {reason}")


@bot.tree.command(name="userinfo", description="Show information about a member")
async def userinfo(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    if not isinstance(member, discord.Member):
        return await interaction.response.send_message("Server only.", ephemeral=True)
    em = quantix_embed(f"User Info • {member}")
    em.set_thumbnail(url=member.display_avatar.url)
    em.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, style="R"))
    em.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown")
    em.add_field(name="Top role", value=member.top_role.mention)
    await interaction.response.send_message(embed=em)


@bot.tree.command(name="serverinfo", description="Show Quantix server information")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    if not g:
        return await interaction.response.send_message("Server only.", ephemeral=True)
    em = quantix_embed(f"{g.name} • Server Info")
    if g.icon: em.set_thumbnail(url=g.icon.url)
    em.add_field(name="Members", value=str(g.member_count))
    em.add_field(name="Channels", value=str(len(g.channels)))
    em.add_field(name="Roles", value=str(len(g.roles)))
    em.add_field(name="Created", value=discord.utils.format_dt(g.created_at, style="R"))
    await interaction.response.send_message(embed=em)


@bot.tree.command(name="giveaway", description="Start a Quantix giveaway")
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway(interaction: discord.Interaction, prize: str, minutes: app_commands.Range[int, 1, 10080], channel: Optional[discord.TextChannel] = None):
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await interaction.response.send_message("Choose a text channel.", ephemeral=True)
    view = GiveawayView()
    ends = discord.utils.utcnow() + timedelta(minutes=minutes)
    em = quantix_embed("🎉 Quantix Giveaway", f"**Prize:** {prize}\n**Ends:** {discord.utils.format_dt(ends, style='R')}\n\nClick below to enter!")
    msg = await target.send(embed=em, view=view)
    con = db(); con.execute("INSERT OR REPLACE INTO giveaways(message_id,channel_id,prize,ended) VALUES(?,?,?,0)", (msg.id,target.id,prize)); con.commit(); con.close()
    await interaction.response.send_message("Giveaway started ✅", ephemeral=True)

    await asyncio.sleep(minutes * 60)
    if view.users:
        winner_id = random.choice(list(view.users))
        await target.send(f"🎉 Congratulations <@{winner_id}>! You won **{prize}**!")
    else:
        await target.send(f"Giveaway for **{prize}** ended with no entries.")
    con = db(); con.execute("UPDATE giveaways SET ended=1 WHERE message_id=?", (msg.id,)); con.commit(); con.close()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ You don't have permission to use that command."
    else:
        msg = f"❌ Something went wrong: `{str(error)[:180]}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    init_db()
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN. Copy .env.example to .env and add your token.")
    bot.run(TOKEN)
