import discord
from discord.ext import commands, tasks
from discord import app_commands
import psycopg2
import psycopg2.extras
import os
import asyncio
import aiohttp
from datetime import datetime

# ──────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────
DATABASE_URL   = os.getenv("DATABASE_URL")
TOKEN          = os.getenv("TOKEN")

DEBUG_CHANNEL_ID = 1487755467949211709   # internal debug logs
LOG_CHANNEL_ID   = 1476717008010870812   # moderation / server logs

AUTO_ROLE_1 = 1476717006794264598
AUTO_ROLE_2 = 1485452341200289975

GREEN_CHECK  = "✅"
WIND_PHRASE  = "It must've been the wind."

# ──────────────────────────────────────────────
#  CCU TRACKER CONFIG
# ──────────────────────────────────────────────
CCU_CHANNEL_ID     = 1489339616703156396
ROBLOX_UNIVERSE_ID = 7498491579   # game universe ID
CCU_UPDATE_INTERVAL = 60          # seconds

# ──────────────────────────────────────────────
#  LOGGING HELPERS
# ──────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(tag: str, message: str):
    line = f"[{_ts()}] [{tag}] {message}"
    print(line)
    return line

async def dlog(tag: str, message: str):
    """Console + Discord debug channel."""
    line = log(tag, message)
    try:
        ch = bot.get_channel(DEBUG_CHANNEL_ID)
        if ch:
            await safe_send(ch.send(f"```\n{line}\n```"))
    except Exception:
        pass

async def safe_send(coro, retries: int = 3, base_delay: float = 1.0):
    """
    Await a coroutine, automatically retrying on 429 rate-limit responses.
    Returns the result or None if all retries fail.
    """
    for attempt in range(retries):
        try:
            return await coro
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, "retry_after", base_delay * (attempt + 1))
                log("RATE-LIMIT", f"429 hit — retrying in {retry_after:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(retry_after)
            else:
                raise
    log("RATE-LIMIT", "All retries exhausted — request dropped.")
    return None

async def send_log(title: str, description: str = None, colour: discord.Colour = None):
    """Send a formatted embed to the moderation log channel."""
    if colour is None:
        colour = discord.Color.green()
    embed = discord.Embed(title=title, description=description, color=colour)
    embed.timestamp = datetime.utcnow()
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch is None:
            ch = await bot.fetch_channel(LOG_CHANNEL_ID)
        await safe_send(ch.send(embed=embed))
    except Exception as e:
        log("LOG", f"Failed to send log '{title}': {e}")

# ──────────────────────────────────────────────
#  EMBED HELPERS
# ──────────────────────────────────────────────
def green_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.green())

def red_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.red())

def orange_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.orange())

# ──────────────────────────────────────────────
#  DATABASE
# ──────────────────────────────────────────────
log("DB", "Connecting to PostgreSQL...")
conn = psycopg2.connect(DATABASE_URL, sslmode="require")
conn.autocommit = True
cur  = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
log("DB", "Connected to PostgreSQL successfully.")

# Create & migrate table
cur.execute("""
    CREATE TABLE IF NOT EXISTS infractions (
        id           SERIAL PRIMARY KEY,
        guild_id     BIGINT,
        user_id      BIGINT,
        moderator_id BIGINT,
        reason       TEXT,
        timestamp    TIMESTAMP DEFAULT NOW()
    )
""")

# Safe column-type migration (idempotent)
for col in ("user_id", "guild_id", "moderator_id"):
    try:
        cur.execute(f"""
            ALTER TABLE infractions
                ALTER COLUMN {col} TYPE BIGINT USING {col}::bigint
        """)
    except Exception:
        conn.rollback()

log("DB", "Database schema ready.")

# ── CCU peak helpers ────────────────────────────
cur.execute("""
    CREATE TABLE IF NOT EXISTS ccu_stats (
        key   TEXT PRIMARY KEY,
        value INTEGER NOT NULL DEFAULT 0
    )
""")
# Seed the peak row if it doesn't exist yet
cur.execute("""
    INSERT INTO ccu_stats (key, value)
    VALUES ('peak', 0)
    ON CONFLICT (key) DO NOTHING
""")
log("DB", "CCU stats table ready.")

def db_get_peak() -> int:
    cur.execute("SELECT value FROM ccu_stats WHERE key = 'peak'")
    row = cur.fetchone()
    return row[0] if row else 0

def db_set_peak(value: int):
    cur.execute("""
        INSERT INTO ccu_stats (key, value)
        VALUES ('peak', %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (value,))

# ──────────────────────────────────────────────
#  BOT SETUP
# ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.guilds          = True
intents.messages        = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ──────────────────────────────────────────────
#  READY
# ──────────────────────────────────────────────
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        await dlog("STARTUP", f"Bot online: {bot.user} — synced {len(synced)} command(s)")
    except Exception as e:
        log("STARTUP", f"Failed to sync commands: {e}")

    if not update_ccu.is_running():
        update_ccu.start()
        log("CCU", f"CCU tracker started for universe {ROBLOX_UNIVERSE_ID}")

# ──────────────────────────────────────────────
#  AUTO-ROLES + JOIN LOGGING
# ──────────────────────────────────────────────
@bot.event
async def on_member_join(member: discord.Member):
    await dlog("MEMBER", f"{member} ({member.id}) joined '{member.guild.name}'")

    role1 = member.guild.get_role(AUTO_ROLE_1)
    role2 = member.guild.get_role(AUTO_ROLE_2)
    try:
        roles_to_add = [r for r in (role1, role2) if r]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Auto-role on join")
            await dlog("AUTO-ROLE", f"Assigned {len(roles_to_add)} auto-role(s) to {member}")
    except Exception as e:
        await dlog("AUTO-ROLE", f"Failed to assign auto-roles to {member}: {e}")

    await send_log(
        "📥 Member Joined",
        f"**User:** {member} (`{member.id}`)\n"
        f"**Account Created:** <t:{int(member.created_at.timestamp())}:R>\n"
        f"**Server:** {member.guild.name}",
        colour=discord.Color.green(),
    )

# ──────────────────────────────────────────────
#  SLASH COMMANDS
# ──────────────────────────────────────────────

# /WARN
@bot.tree.command(name="warn", description="Warn a user and save it to the database")
@app_commands.describe(user="User to warn", reason="Reason for the warning")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    await interaction.response.defer(ephemeral=True)

    cur.execute(
        "INSERT INTO infractions (guild_id, user_id, moderator_id, reason) VALUES (%s, %s, %s, %s) RETURNING id",
        (interaction.guild.id, user.id, interaction.user.id, reason),
    )
    infraction_id = cur.fetchone()[0]

    try:
        await safe_send(user.send(embed=red_embed("⚠️ You have been warned", f"**Server:** {interaction.guild.name}\n**Reason:** {reason}")))
    except Exception:
        pass

    await asyncio.sleep(0.5)  # small buffer between DM and followup

    await interaction.followup.send(
        embed=green_embed(f"{GREEN_CHECK} {user.display_name} has been warned.", f"Infraction ID: `{infraction_id}`")
    )
    await send_log(
        "⚠️ User Warned",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)\n"
        f"**Reason:** {reason}\n"
        f"**Infraction ID:** `{infraction_id}`",
        colour=discord.Color.yellow(),
    )
    await dlog("WARN", f"{interaction.user} warned {user} (ID {infraction_id}): {reason}")

# /UNWARN
@bot.tree.command(name="unwarn", description="Remove the latest or a specific warning")
@app_commands.describe(user="User to unwarn", infraction_id="Specific infraction ID (optional)")
@app_commands.checks.has_permissions(manage_messages=True)
async def unwarn(interaction: discord.Interaction, user: discord.Member, infraction_id: int = None):
    await interaction.response.defer(ephemeral=True)

    if infraction_id:
        cur.execute(
            "DELETE FROM infractions WHERE id = %s AND user_id = %s AND guild_id = %s RETURNING id",
            (infraction_id, user.id, interaction.guild.id),
        )
        deleted = cur.fetchone()

        if deleted:
            await interaction.followup.send(
                embed=green_embed(f"{GREEN_CHECK} Warning removed.", f"Removed infraction ID `{infraction_id}` for **{user.display_name}**.")
            )
            await send_log(
                "🗑️ Warning Removed",
                f"**User:** {user} (`{user.id}`)\n"
                f"**Moderator:** {interaction.user} (`{interaction.user.id}`)\n"
                f"**Infraction ID:** `{infraction_id}`",
                colour=discord.Color.blue(),
            )
            await dlog("UNWARN", f"{interaction.user} removed infraction {infraction_id} from {user}")
        else:
            await interaction.followup.send(
                embed=orange_embed("⚠️ Not found.", f"Infraction ID `{infraction_id}` does not exist for **{user.display_name}**.")
            )
        return

    # Remove latest warning
    cur.execute(
        "SELECT id FROM infractions WHERE user_id = %s AND guild_id = %s ORDER BY id DESC LIMIT 1",
        (user.id, interaction.guild.id),
    )
    row = cur.fetchone()

    if not row:
        await interaction.followup.send(
            embed=orange_embed("⚠️ No warnings found.", f"**{user.display_name}** has no infractions.")
        )
        return

    latest_id = row[0]
    cur.execute("DELETE FROM infractions WHERE id = %s", (latest_id,))

    await interaction.followup.send(
        embed=green_embed(f"{GREEN_CHECK} Latest warning removed.", f"Removed infraction ID `{latest_id}` for **{user.display_name}**.")
    )
    await send_log(
        "🗑️ Latest Warning Removed",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)\n"
        f"**Infraction ID:** `{latest_id}`",
        colour=discord.Color.blue(),
    )
    await dlog("UNWARN", f"{interaction.user} removed latest infraction {latest_id} from {user}")

# /KICK
@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.describe(user="User to kick", reason="Reason for the kick")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):
    await interaction.response.defer(ephemeral=True)

    if user.top_role >= interaction.guild.me.top_role:
        await interaction.followup.send(
            embed=red_embed("❌ Cannot kick", "That user's role is equal to or higher than mine.")
        )
        return

    try:
        await safe_send(user.send(embed=red_embed("🦵 You have been kicked", f"**Server:** {interaction.guild.name}\n**Reason:** {reason}")))
    except Exception:
        pass

    try:
        await user.kick(reason=reason)
    except discord.Forbidden:
        await interaction.followup.send(embed=red_embed("❌ Missing Permissions", "I don't have permission to kick that user."))
        return
    except Exception as e:
        await interaction.followup.send(embed=red_embed("❌ Error", f"Failed to kick user: {e}"))
        return

    await interaction.followup.send(
        embed=green_embed(f"{GREEN_CHECK} {user.display_name} has been kicked.", WIND_PHRASE)
    )
    await send_log(
        "🦵 User Kicked",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)\n"
        f"**Reason:** {reason}",
        colour=discord.Color.orange(),
    )
    await dlog("KICK", f"{interaction.user} kicked {user}: {reason}")

# /BAN
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.describe(user="User to ban", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):
    await interaction.response.defer(ephemeral=True)

    if user.top_role >= interaction.guild.me.top_role:
        await interaction.followup.send(
            embed=red_embed("❌ Cannot ban", "That user's role is equal to or higher than mine.")
        )
        return

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Appeal Here", url="https://discord.gg/HDwzGxfKQ8"))

    try:
        await safe_send(user.send(
            embed=red_embed("🔨 You have been banned", f"**Server:** {interaction.guild.name}\n**Reason:** {reason}"),
            view=view,
        ))
    except Exception:
        pass

    try:
        await user.ban(reason=reason, delete_message_days=0)
    except discord.Forbidden:
        await interaction.followup.send(embed=red_embed("❌ Missing Permissions", "I don't have permission to ban that user."))
        return
    except Exception as e:
        await interaction.followup.send(embed=red_embed("❌ Error", f"Failed to ban user: {e}"))
        return

    await interaction.followup.send(embed=green_embed(WIND_PHRASE))
    await send_log(
        "🔨 User Banned",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)\n"
        f"**Reason:** {reason}",
        colour=discord.Color.red(),
    )
    await dlog("BAN", f"{interaction.user} banned {user}: {reason}")

# /UNBAN
@bot.tree.command(name="unban", description="Unban a user from the server")
@app_commands.describe(user_id="The Discord ID of the user to unban")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str):
    await interaction.response.defer(ephemeral=True)

    try:
        uid = int(user_id)
    except ValueError:
        await interaction.followup.send(embed=red_embed("❌ Invalid ID", "User ID must be a number."))
        return

    try:
        user = await bot.fetch_user(uid)
    except discord.NotFound:
        await interaction.followup.send(embed=red_embed("❌ User Not Found", "No Discord user found with that ID."))
        return
    except Exception as e:
        await interaction.followup.send(embed=red_embed("❌ Error", f"Could not fetch user: {e}"))
        return

    try:
        await interaction.guild.unban(user, reason=f"Unbanned by {interaction.user}")
    except discord.NotFound:
        await interaction.followup.send(embed=orange_embed("⚠️ Not Banned", "This user is not banned from the server."))
        return
    except discord.Forbidden:
        await interaction.followup.send(embed=red_embed("❌ Missing Permissions", "I don't have permission to unban this user."))
        return

    await interaction.followup.send(
        embed=green_embed(f"{GREEN_CHECK} User Unbanned", f"**{user}** has been unbanned.")
    )
    await send_log(
        "✅ User Unbanned",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)",
        colour=discord.Color.green(),
    )
    await dlog("UNBAN", f"{interaction.user} unbanned {user} ({uid})")

# /INFRACTIONS
@bot.tree.command(name="infractions", description="View a user's warnings")
@app_commands.describe(user="User to look up")
async def infractions(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    cur.execute(
        """
        SELECT id, reason, timestamp, moderator_id
        FROM infractions
        WHERE user_id = %s AND guild_id = %s
        ORDER BY timestamp DESC
        """,
        (user.id, interaction.guild.id),
    )
    rows = cur.fetchall()

    if not rows:
        await interaction.followup.send(
            embed=green_embed("No infractions found.", f"**{user.display_name}** has a clean record.")
        )
        return

    desc = ""
    for row in rows:
        inf_id, reason, ts, mod_id = row[0], row[1], row[2], row[3]
        try:
            time_str = f"<t:{int(ts.timestamp())}:R>"
        except Exception:
            time_str = str(ts)
        desc += (
            f"**ID `{inf_id}`** — {reason}\n"
            f"**Moderator:** <@{mod_id}> • {time_str}\n\n"
        )

    embed = green_embed(f"Infractions for {user.display_name} ({len(rows)} total)", desc)
    embed.set_thumbnail(url=user.display_avatar.url)
    await interaction.followup.send(embed=embed)

# /CLEARINFRACTIONS
@bot.tree.command(name="clearinfractions", description="Clear all warnings for a user")
@app_commands.describe(user="User whose infractions to clear")
@app_commands.checks.has_permissions(manage_messages=True)
async def clearinfractions(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    cur.execute(
        "DELETE FROM infractions WHERE user_id = %s AND guild_id = %s",
        (user.id, interaction.guild.id),
    )

    await interaction.followup.send(
        embed=green_embed(f"{GREEN_CHECK} Infractions cleared.", f"All warnings for **{user.display_name}** have been removed.")
    )
    await send_log(
        "🗑️ Infractions Cleared",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)",
        colour=discord.Color.blue(),
    )
    await dlog("CLEAR-INF", f"{interaction.user} cleared all infractions for {user}")

# /TIMEOUT
@bot.tree.command(name="timeout", description="Timeout a user")
@app_commands.describe(user="User to timeout", minutes="Duration in minutes", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout_cmd(interaction: discord.Interaction, user: discord.Member, minutes: int, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)

    if minutes < 1 or minutes > 40320:
        await interaction.followup.send(embed=red_embed("❌ Invalid duration", "Duration must be between 1 and 40320 minutes (28 days)."))
        return

    from datetime import timedelta, timezone
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    try:
        await user.timeout(until, reason=reason)
    except discord.Forbidden:
        await interaction.followup.send(embed=red_embed("❌ Missing Permissions", "I can't timeout that user."))
        return
    except Exception as e:
        await interaction.followup.send(embed=red_embed("❌ Error", str(e)))
        return

    await interaction.followup.send(
        embed=green_embed(f"{GREEN_CHECK} {user.display_name} timed out.", f"Duration: **{minutes} minute(s)**\nReason: {reason}")
    )
    await send_log(
        "🔇 User Timed Out",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)\n"
        f"**Duration:** {minutes} minute(s)\n"
        f"**Until:** <t:{int(until.timestamp())}:R>\n"
        f"**Reason:** {reason}",
        colour=discord.Color.orange(),
    )
    await dlog("TIMEOUT", f"{interaction.user} timed out {user} for {minutes}m: {reason}")

# /UNTIMEOUT
@bot.tree.command(name="untimeout", description="Remove a timeout from a user")
@app_commands.describe(user="User to untimeout")
@app_commands.checks.has_permissions(moderate_members=True)
async def untimeout_cmd(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    try:
        await user.timeout(None)
    except discord.Forbidden:
        await interaction.followup.send(embed=red_embed("❌ Missing Permissions", "I can't remove that user's timeout."))
        return
    except Exception as e:
        await interaction.followup.send(embed=red_embed("❌ Error", str(e)))
        return

    await interaction.followup.send(
        embed=green_embed(f"{GREEN_CHECK} Timeout removed for {user.display_name}.")
    )
    await send_log(
        "🔊 Timeout Removed",
        f"**User:** {user} (`{user.id}`)\n"
        f"**Moderator:** {interaction.user} (`{interaction.user.id}`)",
        colour=discord.Color.green(),
    )
    await dlog("UNTIMEOUT", f"{interaction.user} removed timeout from {user}")

# ──────────────────────────────────────────────
#  SERVER / MOD LOG EVENTS
# ──────────────────────────────────────────────
@bot.event
async def on_member_remove(member: discord.Member):
    await dlog("MEMBER", f"{member} ({member.id}) left '{member.guild.name}'")
    await send_log(
        "📤 Member Left",
        f"**User:** {member} (`{member.id}`)\n"
        f"**Server:** {member.guild.name}",
        colour=discord.Color.red(),
    )

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    content = message.content or "*No content (embed or attachment)*"
    await send_log(
        "🗑️ Message Deleted",
        f"**Author:** {message.author} (`{message.author.id}`)\n"
        f"**Channel:** {message.channel.mention}\n"
        f"**Content:** {content[:1000]}",
        colour=discord.Color.red(),
    )

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    before_content = before.content or "*No content*"
    after_content  = after.content  or "*No content*"
    await send_log(
        "✏️ Message Edited",
        f"**Author:** {before.author} (`{before.author.id}`)\n"
        f"**Channel:** {before.channel.mention}\n"
        f"**Before:** {before_content[:500]}\n"
        f"**After:** {after_content[:500]}",
        colour=discord.Color.yellow(),
    )

@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    mention = channel.mention if isinstance(channel, discord.TextChannel) else channel.name
    await send_log(
        "📢 Channel Created",
        f"**Channel:** {mention} (`{channel.id}`)\n"
        f"**Type:** {type(channel).__name__}\n"
        f"**Server:** {channel.guild.name}",
        colour=discord.Color.green(),
    )
    await dlog("CHANNEL", f"Channel created: #{channel.name} ({channel.id}) in '{channel.guild.name}'")

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    await send_log(
        "🗑️ Channel Deleted",
        f"**Channel:** #{channel.name} (`{channel.id}`)\n"
        f"**Type:** {type(channel).__name__}\n"
        f"**Server:** {channel.guild.name}",
        colour=discord.Color.red(),
    )
    await dlog("CHANNEL", f"Channel deleted: #{channel.name} ({channel.id}) in '{channel.guild.name}'")

@bot.event
async def on_guild_role_create(role: discord.Role):
    await send_log(
        "🔖 Role Created",
        f"**Role:** {role.name} (`{role.id}`)\n"
        f"**Server:** {role.guild.name}",
        colour=discord.Color.green(),
    )

@bot.event
async def on_guild_role_delete(role: discord.Role):
    await send_log(
        "🗑️ Role Deleted",
        f"**Role:** {role.name} (`{role.id}`)\n"
        f"**Server:** {role.guild.name}",
        colour=discord.Color.red(),
    )

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    await dlog("BAN-EVENT", f"{user} ({user.id}) was banned from '{guild.name}'")

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    await dlog("UNBAN-EVENT", f"{user} ({user.id}) was unbanned from '{guild.name}'")

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # Timeout changes
    before_to = getattr(before, "timed_out_until", None)
    after_to  = getattr(after,  "timed_out_until", None)

    if before_to != after_to:
        if after_to:
            await send_log(
                "🔇 Member Timed Out",
                f"**User:** {after} (`{after.id}`)\n"
                f"**Until:** <t:{int(after_to.timestamp())}:R>",
                colour=discord.Color.orange(),
            )
        else:
            await send_log(
                "🔊 Timeout Lifted",
                f"**User:** {after} (`{after.id}`)",
                colour=discord.Color.green(),
            )

    # Role changes
    added   = set(after.roles)  - set(before.roles)
    removed = set(before.roles) - set(after.roles)

    for role in added:
        if role.is_default():
            continue
        await send_log(
            "➕ Role Given",
            f"**User:** {after} (`{after.id}`)\n"
            f"**Role:** {role.name} (`{role.id}`)",
            colour=discord.Color.green(),
        )

    for role in removed:
        if role.is_default():
            continue
        await send_log(
            "➖ Role Removed",
            f"**User:** {after} (`{after.id}`)\n"
            f"**Role:** {role.name} (`{role.id}`)",
            colour=discord.Color.orange(),
        )

    # Nickname changes
    if before.nick != after.nick:
        await send_log(
            "📝 Nickname Changed",
            f"**User:** {after} (`{after.id}`)\n"
            f"**Before:** {before.nick or '*None*'}\n"
            f"**After:** {after.nick or '*None*'}",
            colour=discord.Color.blurple(),
        )

# ──────────────────────────────────────────────
#  CCU TRACKER
# ──────────────────────────────────────────────
async def fetch_roblox_game_data(universe_id: int):
    url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"

    try:
        async with aiohttp.ClientSession() as session:

            async with session.get(url) as resp:
                if resp.status != 200:
                    log("CCU", f"API error {resp.status}")
                    return None

                data = await resp.json()
                game = data["data"][0]

                ccu = game["playing"]
                name = game["name"]
                visits = game.get("visits", 0)
                place_id = game["rootPlaceId"]

            # ICON
            async with session.get(
                f"https://thumbnails.roblox.com/v1/games/icons?universeIds={universe_id}&size=512x512&format=Png"
            ) as r:
                icon = None
                if r.status == 200:
                    t = await r.json()
                    icon = t["data"][0]["imageUrl"]

            # VOTES
            async with session.get(
                f"https://games.roblox.com/v1/games/votes?universeIds={universe_id}"
            ) as r:
                likes = 0
                ratio = 0

                if r.status == 200:
                    v = await r.json()
                    up = v["data"][0]["upVotes"]
                    down = v["data"][0]["downVotes"]
                    likes = up
                    ratio = (up / (up + down) * 100) if (up + down) else 0

            return {
                "ccu": ccu,
                "name": name,
                "visits": visits,
                "place_id": place_id,
                "icon": icon,
                "likes": likes,
                "ratio": ratio
            }

    except Exception as e:
        log("CCU", f"Error: {e}")
        return None

# Stores the message ID of the persistent CCU embed so we can edit it each tick
ccu_message_id: int | None = None

def build_ccu_embed(data, peak):
    embed = discord.Embed(
        title=f"🎮 {data['name']} — Live Stats",
        color=discord.Color.from_rgb(255, 255, 255),
    )

    embed.add_field(name="🟢 CCU", value=f"**{data['ccu']}**", inline=True)
    embed.add_field(name="🏆 Peak", value=f"**{peak}**", inline=True)
    embed.add_field(name="👍 Likes", value=f"**{data['likes']} ({data['ratio']:.1f}%)**", inline=True)
    embed.add_field(name="👁 Visits", value=f"**{data['visits']:,}**", inline=True)

    if data["icon"]:
        embed.set_thumbnail(url=data["icon"])

    embed.timestamp = datetime.utcnow()
    return embed

@tasks.loop(seconds=CCU_UPDATE_INTERVAL)
@tasks.loop(seconds=CCU_UPDATE_INTERVAL)
async def update_ccu():
    global ccu_message_id
    await bot.wait_until_ready()

    channel = bot.get_channel(CCU_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(CCU_CHANNEL_ID)
        except Exception as e:
            log("CCU", f"Could not fetch CCU channel: {e}")
            return

    # fetch game data
    data = await fetch_roblox_game_data(ROBLOX_UNIVERSE_ID)
    if not data:
        log("CCU", "Failed to fetch game data")
        return

    # get peak
    try:
        current_peak = db_get_peak()
    except Exception as e:
        log("CCU", f"DB error: {e}")
        return

    new_peak = data["ccu"] > current_peak

    if new_peak:
        try:
            db_set_peak(data["ccu"])
            current_peak = data["ccu"]
        except Exception as e:
            log("CCU", f"Failed to update peak: {e}")

    # button
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="▶️ Join Game",
            url=f"https://www.roblox.com/games/{data['place_id']}"
        )
    )

    # embed
    embed = build_ccu_embed(data, current_peak)

    # edit existing message
    if ccu_message_id:
        try:
            msg = await channel.fetch_message(ccu_message_id)
            await msg.edit(embed=embed, view=view)
        except discord.NotFound:
            ccu_message_id = None
        except Exception as e:
            log("CCU", f"Edit failed: {e}")
            return

    # send first message
    if not ccu_message_id:
        try:
            msg = await safe_send(channel.send(embed=embed, view=view))
            if msg:
                ccu_message_id = msg.id
        except Exception as e:
            log("CCU", f"Send failed: {e}")
            return

    # peak ping
    if new_peak:
        try:
            ghost = await channel.send("@here")
            await ghost.delete()
        except Exception as e:
            log("CCU", f"Ghost ping failed: {e}")

data = await fetch_roblox_game_data(ROBLOX_UNIVERSE_ID)

if not data:
    log("CCU", "Failed to fetch game data")
    return

    try:
        current_peak = db_get_peak()
    except Exception as e:
        log("CCU", f"Failed to read peak from DB: {e}")
        return

    new_peak = ccu > current_peak
    if new_peak:
        try:
            db_set_peak(ccu)
            current_peak = ccu
            log("CCU", f"New peak saved to DB: {ccu}")
        except Exception as e:
            log("CCU", f"Failed to write new peak to DB: {e}")
            return
view = discord.ui.View()
view.add_item(
    discord.ui.Button(
        label="▶️ Join Game",
        url=f"https://www.roblox.com/games/{data['place_id']}"
    )
)
embed = build_ccu_embed(data, current_peak)

    # Try to edit the existing embed message
    if ccu_message_id:
        try:
            msg = await channel.fetch_message(ccu_message_id)
await msg.edit(embed=embed, view=view)
            log("CCU", f"Embed updated — CCU: {ccu}, Peak: {current_peak}")
        except discord.NotFound:
            ccu_message_id = None  # message was deleted, send a new one
        except Exception as e:
            log("CCU", f"Failed to edit CCU embed: {e}")
            return

    # Send a fresh embed if we don't have one yet (first run or message was deleted)
    if not ccu_message_id:
        try:
            msg = await safe_send(channel.send(embed=embed))
            if msg:
                ccu_message_id = msg.id
                log("CCU", f"CCU embed posted (message ID: {msg.id})")
        except Exception as e:
            log("CCU", f"Failed to send CCU embed: {e}")
            return

    # Ghost ping @here on new peak
    if new_peak:
        try:
            ghost = await channel.send("@here")
            await ghost.delete()
            await dlog("CCU", f"New peak CCU: {ccu} — ghost ping sent, DB updated")
        except Exception as e:
            log("CCU", f"Failed to send ghost ping: {e}")

# ──────────────────────────────────────────────
#  ERROR HANDLER
# ──────────────────────────────────────────────
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = "An unexpected error occurred."

    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "I'm missing permissions to do that."
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"You're on cooldown. Try again in {error.retry_after:.1f}s."

    await dlog("CMD-ERROR", f"/{interaction.command.name if interaction.command else '?'} by {interaction.user}: {error}")

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=red_embed("❌ Error", msg), ephemeral=True)
        else:
            await interaction.response.send_message(embed=red_embed("❌ Error", msg), ephemeral=True)
    except Exception:
        pass

# ──────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────
log("STARTUP", "Starting bot...")
bot.run(TOKEN)
