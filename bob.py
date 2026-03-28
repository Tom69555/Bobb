import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import psycopg2.extras
import os
from datetime import datetime

# -----------------------------
# BOT SETUP
# -----------------------------
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# DATABASE CONNECTION FUNCTION
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# -----------------------------
# CREATE TABLE
# -----------------------------
with get_conn() as conn:
    with conn.cursor() as cursor:
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
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO infractions (user_id, moderator_id, action, reason)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (user_id, moderator_id, action, reason))
            case_id = cursor.fetchone()[0]
            conn.commit()
            return case_id

# -----------------------------
# SAFETY CHECK
# -----------------------------
async def can_punish(interaction, user):
    if user == interaction.user:
        await interaction.response.send_message("You can't punish yourself.", ephemeral=True)
        return False

    if user.top_role >= interaction.user.top_role:
        await interaction.response.send_message("You can't punish this user.", ephemeral=True)
        return False

    return True

# -----------------------------
# WARN COMMAND
# -----------------------------
@bot.tree.command(name="warn", description="Warn a user")
@app_commands.checks.has_permissions(ban_members=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):

    if not await can_punish(interaction, user):
        return

    case_id = log_infraction(str(user.id), str(interaction.user.id), "warn", reason)

    try:
        await user.send(f"You were warned in Roommates for: {reason}")
    except discord.Forbidden:
        pass

    embed = discord.Embed(
        title="⚠️ User Warned",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.set_footer(text=f"Case ID: #{case_id}")

    await interaction.response.send_message(embed=embed)

# -----------------------------
# KICK COMMAND
# -----------------------------
@bot.tree.command(name="kick", description="Kick a user")
@app_commands.checks.has_permissions(ban_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):

    if not await can_punish(interaction, user):
        return

    try:
        await user.send(f"You were kicked in Roommates for: {reason}")
    except discord.Forbidden:
        pass

    try:
        await user.kick(reason=reason)
    except:
        await interaction.response.send_message("Failed to kick user.", ephemeral=True)
        return

    case_id = log_infraction(str(user.id), str(interaction.user.id), "kick", reason)

    embed = discord.Embed(
        title="👢 User Kicked",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.set_footer(text=f"Case ID: #{case_id}")

    await interaction.response.send_message(embed=embed)

# -----------------------------
# BAN COMMAND
# -----------------------------
@bot.tree.command(name="ban", description="Ban a user")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):

    if not await can_punish(interaction, user):
        return

    try:
        await user.send(
            f"You were banned in Roommates for: {reason}\n\n"
            f"Appeal: https://discord.gg/JgNxTSuRWn"
        )
    except discord.Forbidden:
        pass

    try:
        await user.ban(reason=reason)
    except:
        await interaction.response.send_message("Failed to ban user.", ephemeral=True)
        return

    case_id = log_infraction(str(user.id), str(interaction.user.id), "ban", reason)

    embed = discord.Embed(
        title="🔨 User Banned",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.set_footer(text=f"Case ID: #{case_id}")

    await interaction.response.send_message(embed=embed)

# -----------------------------
# INFRACTIONS COMMAND
# -----------------------------
@bot.tree.command(name="infractions", description="View infractions")
@app_commands.checks.has_permissions(ban_members=True)
async def infractions(interaction: discord.Interaction, user: discord.Member):

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, action, reason, timestamp, moderator_id
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

    for row in rows:
        embed.add_field(
            name=f"#{row['id']} • {row['action'].upper()} — <@{row['moderator_id']}>",
            value=f"**Reason:** {row['reason']}\n<t:{int(row['timestamp'].timestamp())}:R>",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

# -----------------------------
# READY EVENT
# -----------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

# -----------------------------
# RUN BOT
# -----------------------------
bot.run(os.getenv("TOKEN"))
