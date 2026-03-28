import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import psycopg2.extras
import os
from datetime import datetime

# -----------------------------
# DATABASE CONNECTION
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL, sslmode="require")
cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

cursor.execute("""
CREATE TABLE IF NOT EXISTS infractions (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# -----------------------------
# LOG INFRACTION
# -----------------------------
def log_infraction(user_id: str, moderator_id: str, action: str, reason: str):
    cursor.execute("""
        INSERT INTO infractions (user_id, moderator_id, action, reason)
        VALUES (%s, %s, %s, %s)
    """, (user_id, moderator_id, action, reason))
    conn.commit()

# -----------------------------
# WARN COMMAND
# -----------------------------
@bot.tree.command(name="warn", description="Warn a user and save it to the database")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user="User to warn", reason="Reason for the warning")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):

    log_infraction(str(user.id), str(interaction.user.id), "warn", reason)

    # DM the user
    try:
        await user.send(
            f"You were warned in Roommates for {reason}\n\n"
            f"Message from server: Roommates"
        )
    except:
        pass

    # Public embed
    embed = discord.Embed(
        description=f"✔️ **{user.mention} has been warned.**\n{reason}",
        color=discord.Color.green()
    )
    embed.set_footer(text="")
    embed.set_author(name="")

    await interaction.response.send_message(embed=embed)

# -----------------------------
# KICK COMMAND
# -----------------------------
@bot.tree.command(name="kick", description="Kick a user and log it")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user="User to kick", reason="Reason for the kick")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):

    # DM the user
    try:
        await user.send(
            f"You were kicked in Roommates for {reason}\n\n"
            f"Message from server: Roommates"
        )
    except:
        pass

    # Kick the user
    try:
        await user.kick(reason=reason)
    except:
        await interaction.response.send_message("Failed to kick user.", ephemeral=True)
        return

    log_infraction(str(user.id), str(interaction.user.id), "kick", reason)

    embed = discord.Embed(
        description="✔️ **Must have been the wind…**",
        color=discord.Color.green()
    )
    embed.set_footer(text="")
    embed.set_author(name="")

    await interaction.response.send_message(embed=embed)

# -----------------------------
# BAN COMMAND
# -----------------------------
@bot.tree.command(name="ban", description="Ban a user and log it")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user="User to ban", reason="Reason for the ban")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):

    # DM the user
    try:
        await user.send(
            f"You were banned in Roommates for {reason}\n\n"
            f"APPEAL HERE! https://discord.gg/JgNxTSuRWn\n\n"
            f"Message from server: Roommates"
        )
    except:
        pass

    # Ban the user
    try:
        await user.ban(reason=reason)
    except:
        await interaction.response.send_message("Failed to ban user.", ephemeral=True)
        return

    log_infraction(str(user.id), str(interaction.user.id), "ban", reason)

    embed = discord.Embed(
        description="✔️ **Must have been the wind…**",
        color=discord.Color.green()
    )
    embed.set_footer(text="")
    embed.set_author(name="")

    await interaction.response.send_message(embed=embed)

# -----------------------------
# INFRACTIONS COMMAND
# -----------------------------
@bot.tree.command(name="infractions", description="View a user's infractions")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user="User to check")
async def infractions(interaction: discord.Interaction, user: discord.Member):

    cursor.execute("""
        SELECT action, reason, timestamp, moderator_id
        FROM infractions
        WHERE user_id = %s
        ORDER BY timestamp DESC
    """, (str(user.id),))

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"{user.mention} has no infractions.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"📄 Infractions for {user.display_name}",
        color=discord.Color.green()
    )

    for action, reason, timestamp, mod_id in rows:
        embed.add_field(
            name=f"{action.upper()} — <@{mod_id}>",
            value=f"**Reason:** {reason}\n<t:{int(timestamp.timestamp())}:R>",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)
