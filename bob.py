import discord
from discord.ext import commands
from discord import app_commands
import psycopg2
import os

# -----------------------------
# CONFIG
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN = os.getenv("TOKEN")
LOG_CHANNEL_ID = 1476717008010870812

# -----------------------------
# DATABASE CONNECTION
# -----------------------------
conn = psycopg2.connect(DATABASE_URL, sslmode="require")
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS infractions (
    id SERIAL PRIMARY KEY
);
""")

cur.execute("""
ALTER TABLE infractions
    ADD COLUMN IF NOT EXISTS guild_id BIGINT,
    ADD COLUMN IF NOT EXISTS user_id BIGINT,
    ADD COLUMN IF NOT EXISTS moderator_id BIGINT,
    ADD COLUMN IF NOT EXISTS reason TEXT,
    ADD COLUMN IF NOT EXISTS timestamp TIMESTAMP DEFAULT NOW();
""")

cur.execute("""
ALTER TABLE infractions
    ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint,
    ALTER COLUMN guild_id TYPE BIGINT USING guild_id::bigint,
    ALTER COLUMN moderator_id TYPE BIGINT USING moderator_id::bigint;
""")

# -----------------------------
# BOT SETUP
# -----------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

GREEN_CHECK = "✅"
WIND_PHRASE = "It must’ve been the wind."

# -----------------------------
# HELPERS
# -----------------------------
def green_embed(title: str, description: str = None):
    return discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green()
    )

def red_embed(title: str, description: str = None):
    return discord.Embed(
        title=title,
        description=description,
        color=discord.Color.red()
    )

async def send_log(title: str, description: str = None):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(LOG_CHANNEL_ID)
        except:
            return
    await channel.send(embed=green_embed(title, description))

# -----------------------------
# /WARN
# -----------------------------
@bot.tree.command(name="warn", description="Warn a user and save it to the database")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):

    with conn.cursor() as c:
        c.execute(
            "INSERT INTO infractions (guild_id, user_id, moderator_id, reason) VALUES (%s, %s, %s, %s)",
            (interaction.guild.id, user.id, interaction.user.id, reason)
        )

    dm_embed = red_embed(
        "Roommates",
        f"You were warned in Roommates for **{reason}**"
    )

    try:
        await user.send(embed=dm_embed)
    except:
        pass

    embed = green_embed(
        f"{GREEN_CHECK} {user.name} has been warned."
    )
    await interaction.response.send_message(embed=embed)

    await send_log(
        "User Warned",
        f"**User:** {user} ({user.id})\n"
        f"**Moderator:** {interaction.user} ({interaction.user.id})\n"
        f"**Reason:** {reason}"
    )

# -----------------------------
# /UNWARN
# -----------------------------
@bot.tree.command(name="unwarn", description="Remove the latest or a specific warning")
@app_commands.checks.has_permissions(manage_messages=True)
async def unwarn(interaction: discord.Interaction, user: discord.Member, infraction_id: int = None):

    if infraction_id:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM infractions WHERE id = %s AND user_id = %s AND guild_id = %s RETURNING id",
                (infraction_id, user.id, interaction.guild.id)
            )
            deleted = c.fetchone()

        if deleted:
            embed = green_embed(
                f"{GREEN_CHECK} Warning removed.",
                f"Removed infraction ID **{infraction_id}** for **{user.name}**."
            )
            await send_log(
                "Warning Removed",
                f"**User:** {user} ({user.id})\n"
                f"**Moderator:** {interaction.user} ({interaction.user.id})\n"
                f"**Infraction ID:** {infraction_id}"
            )
        else:
            embed = green_embed(
                "⚠️ No matching infraction found.",
                "That infraction ID does not exist for this user."
            )

        return await interaction.response.send_message(embed=embed)

    with conn.cursor() as c:
        c.execute(
            "SELECT id FROM infractions WHERE user_id = %s AND guild_id = %s ORDER BY id DESC LIMIT 1",
            (user.id, interaction.guild.id)
        )
        row = c.fetchone()

    if not row:
        return await interaction.response.send_message(
            embed=green_embed("⚠️ No warnings found.", f"{user.name} has no warnings.")
        )

    latest_id = row[0]

    with conn.cursor() as c:
        c.execute("DELETE FROM infractions WHERE id = %s", (latest_id,))

    embed = green_embed(
        f"{GREEN_CHECK} Latest warning removed.",
        f"Removed infraction ID **{latest_id}** for **{user.name}**."
    )
    await interaction.response.send_message(embed=embed)

    await send_log(
        "Latest Warning Removed",
        f"**User:** {user} ({user.id})\n"
        f"**Moderator:** {interaction.user} ({interaction.user.id})\n"
        f"**Infraction ID:** {latest_id}"
    )

# -----------------------------
# /KICK
# -----------------------------
@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):

    try:
        await user.send(
            embed=red_embed(
                "Roommates",
                f"You were kicked in Roommates for **{reason}**"
            )
        )
    except:
        pass

    await user.kick(reason=reason)

    embed = green_embed(
        f"{GREEN_CHECK} {user.name} has been kicked.",
        WIND_PHRASE
    )
    await interaction.response.send_message(embed=embed)

    await send_log(
        "User Kicked",
        f"**User:** {user} ({user.id})\n"
        f"**Moderator:** {interaction.user} ({interaction.user.id})\n"
        f"**Reason:** {reason}"
    )

# -----------------------------
# /BAN
# -----------------------------
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):

    dm_embed = red_embed(
        "Roommates",
        f"You were banned in Roommates for **{reason}**"
    )

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Appeal Here",
        url="https://discord.gg/HDwzGxfKQ8"
    ))

    try:
        await user.send(embed=dm_embed, view=view)
    except:
        pass

    await user.ban(reason=reason)

    embed = green_embed(
        WIND_PHRASE
    )
    await interaction.response.send_message(embed=embed)

    await send_log(
        "User Banned",
        f"**User:** {user} ({user.id})\n"
        f"**Moderator:** {interaction.user} ({interaction.user.id})\n"
        f"**Reason:** {reason}"
    )

# -----------------------------
# /UNBAN
# -----------------------------
@bot.tree.command(name="unban", description="Unban a user from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str):

    try:
        user_id_int = int(user_id)
    except ValueError:
        return await interaction.response.send_message(
            embed=green_embed("Invalid ID", "User ID must be a number.")
        )

    try:
        user = await bot.fetch_user(user_id_int)
    except:
        return await interaction.response.send_message(
            embed=green_embed("User Not Found", "Discord could not find a user with that ID.")
        )

    try:
        await interaction.guild.unban(user)
    except discord.NotFound:
        return await interaction.response.send_message(
            embed=green_embed("Not Banned", "This user is not banned from the server.")
        )
    except discord.Forbidden:
        return await interaction.response.send_message(
            embed=green_embed("Missing Permissions", "I do not have permission to unban this user.")
        )

    embed = green_embed(
        f"{GREEN_CHECK} User Unbanned",
        f"{user} has been unbanned."
    )
    await interaction.response.send_message(embed=embed)

    await send_log(
        "User Unbanned",
        f"**User:** {user} ({user.id})\n"
        f"**Moderator:** {interaction.user} ({interaction.user.id})"
    )

# -----------------------------
# /INFRACTIONS
# -----------------------------
@bot.tree.command(name="infractions", description="View a user's warnings")
async def infractions(interaction: discord.Interaction, user: discord.Member):

    with conn.cursor() as c:
        c.execute(
            """
            SELECT id, reason, timestamp, moderator_id
            FROM infractions
            WHERE user_id = %s AND guild_id = %s
            ORDER BY timestamp DESC
            """,
            (user.id, interaction.guild.id)
        )
        rows = c.fetchall()

    if not rows:
        return await interaction.response.send_message(
            embed=green_embed("No warnings found.", f"{user.name} has no infractions.")
        )

    desc = ""
    for inf in rows:
        inf_id, reason, ts, mod_id = inf
        try:
            unix_ts = int(ts.timestamp())
            time_str = f"<t:{unix_ts}:R>"
        except:
            time_str = str(ts)

        desc += (
            f"**ID {inf_id}** — {reason}\n"
            f"**Moderator:** <@{mod_id}> • {time_str}\n\n"
        )

    embed = green_embed(f"Infractions for {user.name}", desc)
    await interaction.response.send_message(embed=embed)

# -----------------------------
# /CLEARINFRACTIONS
# -----------------------------
@bot.tree.command(name="clearinfractions", description="Clear all warnings for a user")
@app_commands.checks.has_permissions(manage_messages=True)
async def clearinfractions(interaction: discord.Interaction, user: discord.Member):

    with conn.cursor() as c:
        c.execute(
            "DELETE FROM infractions WHERE user_id = %s AND guild_id = %s",
            (user.id, interaction.guild.id)
        )

    embed = green_embed(
        f"{GREEN_CHECK} Infractions cleared.",
        f"All warnings for **{user.name}** have been removed."
    )
    await interaction.response.send_message(embed=embed)

    await send_log(
        "Infractions Cleared",
        f"**User:** {user} ({user.id})\n"
        f"**Moderator:** {interaction.user} ({interaction.user.id})"
    )

# -----------------------------
# LOGGING EVENTS
# -----------------------------
@bot.event
async def on_member_join(member: discord.Member):
    await send_log(
        "Member Joined",
        f"**User:** {member} ({member.id})\n"
        f"**Server:** {member.guild.name}"
    )

@bot.event
async def on_member_remove(member: discord.Member):
    await send_log(
        "Member Left",
        f"**User:** {member} ({member.id})\n"
        f"**Server:** {member.guild.name}"
    )

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    content = message.content or "*No content*"
    await send_log(
        "Message Deleted",
        f"**Author:** {message.author} ({message.author.id})\n"
        f"**Channel:** {message.channel.mention}\n"
        f"**Content:** {content}"
    )

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    before_content = before.content or "*No content*"
    after_content = after.content or "*No content*"
    await send_log(
        "Message Edited",
        f"**Author:** {before.author} ({before.author.id})\n"
        f"**Channel:** {before.channel.mention}\n"
        f"**Before:** {before_content}\n"
        f"**After:** {after_content}"
    )

@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    await send_log(
        "Channel Created",
        f"**Channel:** {channel.mention if isinstance(channel, discord.TextChannel) else channel.name}\n"
        f"**ID:** {channel.id}\n"
        f"**Server:** {channel.guild.name}"
    )

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    await send_log(
        "Channel Deleted",
        f"**Channel:** {channel.name}\n"
        f"**ID:** {channel.id}\n"
        f"**Server:** {channel.guild.name}"
    )

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # Timeouts (safe for your lib)
    before_timeout = getattr(before, "timed_out_until", None)
    after_timeout = getattr(after, "timed_out_until", None)

    if before_timeout != after_timeout:
        await send_log(
            "Timeout Updated",
            f"**User:** {after} ({after.id})\n"
            f"**Before:** {before_timeout}\n"
            f"**After:** {after_timeout}"
        )

    # Roles added/removed
    before_roles = set(before.roles)
    after_roles = set(after.roles)

    added_roles = after_roles - before_roles
    removed_roles = before_roles - after_roles

    for role in added_roles:
        if role.is_default():
            continue
        await send_log(
            "Role Given",
            f"**User:** {after} ({after.id})\n"
            f"**Role:** {role.name} ({role.id})"
        )

    for role in removed_roles:
        if role.is_default():
            continue
        await send_log(
            "Role Removed",
            f"**User:** {after} ({after.id})\n"
            f"**Role:** {role.name} ({role.id})"
        )

# -----------------------------
# READY
# -----------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

# -----------------------------
# RUN
# -----------------------------
bot.run(TOKEN)
