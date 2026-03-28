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
conn.autocommit = True
cur = conn.cursor()

# -----------------------------
# ENSURE TABLE + COLUMNS EXIST
# -----------------------------
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
# /UNBAN COMMAND
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

# -----------------------------
# BOT SETUP
# -----------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

GREEN_CHECK = "✅"
WIND_PHRASE = "It must’ve been the wind."

# -----------------------------
# HELPER: GREEN EMBED
# -----------------------------
def green_embed(title: str, description: str = None):
    return discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green()
    )

# -----------------------------
# /WARN COMMAND
# -----------------------------
@bot.tree.command(name="warn", description="Warn a user and save it to the database")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):

    with conn.cursor() as c:
        c.execute(
            "INSERT INTO infractions (guild_id, user_id, moderator_id, reason) VALUES (%s, %s, %s, %s)",
            (interaction.guild.id, user.id, interaction.user.id, reason)
        )

    # DM embed (Option B layout)
    dm_embed = green_embed(
        "Roommates",
        f"You were warned in Roommates for **{reason}**\n\n"
        f"Message from server: Roommates"
    )

    try:
        await user.send(embed=dm_embed)
    except:
        pass

    # Server embed (green, no reason)
    embed = green_embed(
        f"{GREEN_CHECK} {user.name} has been warned."
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
            embed = green_embed(
                f"{GREEN_CHECK} Warning removed.",
                f"Removed infraction ID **{infraction_id}** for **{user.name}**."
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

# -----------------------------
# /KICK COMMAND
# -----------------------------
@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):

    try:
        await user.send(
            embed=green_embed(
                "Roommates",
                f"You were kicked in Roommates for **{reason}**\n\n"
                f"Message from server: Roommates"
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

# -----------------------------
# /BAN COMMAND
# -----------------------------
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):

    # DM embed (Option B layout) + Appeal button
    dm_embed = green_embed(
        "Roommates",
        f"You were banned in Roommates for **{reason}**\n\n"
        f"Message from server: Roommates"
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

    # Server embed — ONLY “Must’ve been the wind.”
    embed = green_embed(
        WIND_PHRASE
    )

    await interaction.response.send_message(embed=embed)

# -----------------------------
# /INFRACTIONS COMMAND
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

    embed = green_embed(
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
