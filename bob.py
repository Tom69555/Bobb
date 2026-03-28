import discord
from discord.ext import commands
from discord import app_commands
import psycopg2
import os

# -----------------------------
# DATABASE CONNECTION
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL, sslmode="require")
conn.autocommit = True  # avoid lingering failed transactions
cur = conn.cursor()

# -----------------------------
# ENSURE TABLE + COLUMNS EXIST
# -----------------------------
# 1) Ensure table exists
cur.execute("""
CREATE TABLE IF NOT EXISTS infractions (
    id SERIAL PRIMARY KEY
);
""")

# 2) Ensure required columns exist (migration-safe)
cur.execute("""
ALTER TABLE infractions
    ADD COLUMN IF NOT EXISTS guild_id BIGINT,
    ADD COLUMN IF NOT EXISTS user_id BIGINT,
    ADD COLUMN IF NOT EXISTS moderator_id BIGINT,
    ADD COLUMN IF NOT EXISTS reason TEXT,
    ADD COLUMN IF NOT EXISTS timestamp TIMESTAMP DEFAULT NOW();
""")

# -----------------------------
# BOT SETUP
# -----------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

GREEN_CHECK = "✅"
WIND_PHRASE = "It must’ve been the wind."

# -----------------------------
# HELPER: WHITE EMBED
# -----------------------------
def white_embed(title: str, description: str):
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.from_rgb(255, 255, 255)
    )
    return embed

# -----------------------------
# /WARN COMMAND
# -----------------------------
@bot.tree.command(name="warn", description="Warn a user and save it to the database")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    # Save to DB
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO infractions (guild_id, user_id, moderator_id, reason) VALUES (%s, %s, %s, %s)",
            (interaction.guild.id, user.id, interaction.user.id, reason)
        )

    # DM user
    try:
        await user.send(f"You have been **warned** in **{interaction.guild.name}**.\nReason: {reason}")
    except:
        pass

    # Embed (white + green check)
    embed = white_embed(
        f"{GREEN_CHECK} {user.name} has been warned.",
        f"**Reason:** {reason}"
    )

    await interaction.response.send_message(embed=embed)

# -----------------------------
# /UNWARN COMMAND
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
            embed = white_embed(
                f"{GREEN_CHECK} Warning removed.",
                f"Removed infraction ID **{infraction_id}** for **{user.name}**."
            )
        else:
            embed = white_embed(
                "⚠️ No matching infraction found.",
                "That infraction ID does not exist for this user."
            )
        return await interaction.response.send_message(embed=embed)

    # Remove latest warning
    with conn.cursor() as c:
        c.execute(
            "SELECT id FROM infractions WHERE user_id = %s AND guild_id = %s ORDER BY id DESC LIMIT 1",
            (user.id, interaction.guild.id)
        )
        row = c.fetchone()

    if not row:
        return await interaction.response.send_message(
            embed=white_embed("⚠️ No warnings found.", f"{user.name} has no warnings.")
        )

    latest_id = row[0]

    with conn.cursor() as c:
        c.execute("DELETE FROM infractions WHERE id = %s", (latest_id,))

    embed = white_embed(
        f"{GREEN_CHECK} Latest warning removed.",
        f"Removed infraction ID **{latest_id}** for **{user.name}**."
    )
    await interaction.response.send_message(embed=embed)

# -----------------------------
# /KICK COMMAND
# -----------------------------
@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):
    # DM user
    try:
        await user.send(f"You were kicked from **{interaction.guild.name}**.\nReason: {reason}")
    except:
        pass

    await user.kick(reason=reason)

    embed = white_embed(
        f"{GREEN_CHECK} {user.name} has been kicked.",
        f"{WIND_PHRASE}\n\n**Reason:** {reason}"
    )

    await interaction.response.send_message(embed=embed)

# -----------------------------
# /BAN COMMAND
# -----------------------------
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):
    # DM user
    try:
        await user.send(f"You were banned from **{interaction.guild.name}**.\nReason: {reason}")
    except:
        pass

    await user.ban(reason=reason)

    embed = white_embed(
        f"{GREEN_CHECK} {user.name} has been banned.",
        f"{WIND_PHRASE}\n\n**Reason:** {reason}"
    )

    await interaction.response.send_message(embed=embed)

# -----------------------------
# /INFRACTIONS COMMAND
# -----------------------------
@bot.tree.command(name="infractions", description="View a user's warnings")
async def infractions(interaction: discord.Interaction, user: discord.Member):
    with conn.cursor() as c:
        c.execute(
            "SELECT id, reason, timestamp FROM infractions WHERE user_id = %s AND guild_id = %s ORDER BY id ASC",
            (user.id, interaction.guild.id)
        )
        rows = c.fetchall()

    if not rows:
        return await interaction.response.send_message(
            embed=white_embed("No warnings found.", f"{user.name} has no infractions.")
        )

    desc = ""
    for inf in rows:
        ts = inf[2]
        try:
            unix_ts = int(ts.timestamp())
            time_str = f"<t:{unix_ts}:R>"
        except:
            time_str = str(ts)
        desc += f"**ID {inf[0]}** — {inf[1]} *({time_str})*\n"

    embed = white_embed(f"Infractions for {user.name}", desc)
    await interaction.response.send_message(embed=embed)

# -----------------------------
# /CLEARINFRACTIONS COMMAND
# -----------------------------
@bot.tree.command(name="clearinfractions", description="Clear all warnings for a user")
@app_commands.checks.has_permissions(manage_messages=True)
async def clearinfractions(interaction: discord.Interaction, user: discord.Member):
    with conn.cursor() as c:
        c.execute(
            "DELETE FROM infractions WHERE user_id = %s AND guild_id = %s",
            (user.id, interaction.guild.id)
        )

    embed = white_embed(
        f"{GREEN_CHECK} Infractions cleared.",
        f"All warnings for **{user.name}** have been removed."
    )

    await interaction.response.send_message(embed=embed)

# -----------------------------
# SYNC COMMAND TREE
# -----------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

# -----------------------------
# RUN BOT
# -----------------------------
TOKEN = os.getenv("TOKEN")
bot.run(TOKEN)
