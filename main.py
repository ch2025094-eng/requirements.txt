import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

TOKEN = os.getenv("TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# 資料庫
# =========================
db = sqlite3.connect("data.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS whitelist (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    anti_role_delete INTEGER DEFAULT 0,
    anti_guild_rename INTEGER DEFAULT 0,
    anti_channel_delete INTEGER DEFAULT 0,
    anti_channel_create INTEGER DEFAULT 0
)
""")

db.commit()

# =========================
# 工具函數
# =========================
def ensure_guild_settings(guild_id):
    cursor.execute("INSERT OR IGNORE INTO settings (guild_id) VALUES (?)", (guild_id,))
    db.commit()

def is_whitelisted(user_id):
    cursor.execute("SELECT 1 FROM whitelist WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def is_blacklisted(user_id):
    cursor.execute("SELECT 1 FROM blacklist WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def add_blacklist(user_id):
    cursor.execute("INSERT OR IGNORE INTO blacklist (user_id) VALUES (?)", (user_id,))
    db.commit()

async def punish_user(member, reason):
    if is_whitelisted(member.id):
        return

    if is_blacklisted(member.id):
        await member.ban(reason=f"黑名單再次違規: {reason}")
        return

    add_blacklist(member.id)
    until = datetime.now(timezone.utc) + timedelta(seconds=60)
    await member.timeout(until, reason=reason)

# =========================
# 啟動
# =========================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"🤖 已登入 {bot.user}")

# =========================
# 反刷頻系統
# =========================
message_tracker = defaultdict(list)
mention_tracker = defaultdict(list)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    now = datetime.now().timestamp()

    # 6秒8則
    message_tracker[message.author.id].append(now)
    message_tracker[message.author.id] = [
        t for t in message_tracker[message.author.id]
        if now - t < 6
    ]

    if len(message_tracker[message.author.id]) >= 8:
        await punish_user(message.author, "刷頻")
        return

    # 3秒3次 everyone
    if "@everyone" in message.content:
        mention_tracker[message.author.id].append(now)
        mention_tracker[message.author.id] = [
            t for t in mention_tracker[message.author.id]
            if now - t < 3
        ]

        if len(mention_tracker[message.author.id]) >= 3:
            await punish_user(message.author, "短時間多次@everyone")
            return

        if message.content.count("@everyone") > 2:
            await punish_user(message.author, "單則大量@everyone")
            return

    await bot.process_commands(message)

# =========================
# 防刪角色
# =========================
@bot.event
async def on_guild_role_delete(role):
    ensure_guild_settings(role.guild.id)

    cursor.execute("SELECT anti_role_delete FROM settings WHERE guild_id=?", (role.guild.id,))
    if cursor.fetchone()[0] == 0:
        return

    async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        user = entry.user
        break

    if user.bot:
        return

    await punish_user(user, "未授權刪除角色")

# =========================
# 防改伺服器名稱
# =========================
@bot.event
async def on_guild_update(before, after):
    ensure_guild_settings(after.id)

    cursor.execute("SELECT anti_guild_rename FROM settings WHERE guild_id=?", (after.id,))
    if cursor.fetchone()[0] == 0:
        return

    if before.name != after.name:
        async for entry in after.audit_logs(limit=1, action=discord.AuditLogAction.guild_update):
            user = entry.user
            break

        if user.bot:
            return

        await after.edit(name=before.name)
        await punish_user(user, "未授權修改伺服器名稱")

# =========================
# 防刪頻道（含分類）
# =========================
@bot.event
async def on_guild_channel_delete(channel):
    ensure_guild_settings(channel.guild.id)

    cursor.execute("SELECT anti_channel_delete FROM settings WHERE guild_id=?", (channel.guild.id,))
    if cursor.fetchone()[0] == 0:
        return

    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        user = entry.user
        break

    if user.bot:
        return

    await punish_user(user, "未授權刪除頻道或分類")

# =========================
# 防新增頻道（含分類）
# =========================
@bot.event
async def on_guild_channel_create(channel):
    ensure_guild_settings(channel.guild.id)

    cursor.execute("SELECT anti_channel_create FROM settings WHERE guild_id=?", (channel.guild.id,))
    if cursor.fetchone()[0] == 0:
        return

    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
        user = entry.user
        break

    if user.bot:
        return

    await punish_user(user, "未授權新增頻道或分類")
    await channel.delete()

# =========================
# Slash 指令
# =========================

@bot.tree.command(name="加入黑名單")
async def add_black(interaction: discord.Interaction, member: discord.Member):
    add_blacklist(member.id)
    await interaction.response.send_message("已加入黑名單")

@bot.tree.command(name="移除黑名單")
async def remove_black(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message("已移除黑名單")

@bot.tree.command(name="查看黑名單")
async def view_black(interaction: discord.Interaction):
    cursor.execute("SELECT user_id FROM blacklist")
    data = cursor.fetchall()
    if not data:
        await interaction.response.send_message("黑名單為空")
        return
    msg = "\n".join([f"<@{u[0]}>" for u in data])
    await interaction.response.send_message(msg)

@bot.tree.command(name="加入白名單")
async def add_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (member.id,))
    db.commit()
    await interaction.response.send_message("已加入白名單")

@bot.tree.command(name="移除白名單")
async def remove_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM whitelist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message("已移除白名單")

@bot.tree.command(name="查看白名單")
async def view_white(interaction: discord.Interaction):
    cursor.execute("SELECT user_id FROM whitelist")
    data = cursor.fetchall()
    if not data:
        await interaction.response.send_message("白名單為空")
        return
    msg = "\n".join([f"<@{u[0]}>" for u in data])
    await interaction.response.send_message(msg)

# =========================
# 開關指令
# =========================

@bot.tree.command(name="防刪角色")
async def toggle_role(interaction: discord.Interaction, 狀態: bool):
    ensure_guild_settings(interaction.guild.id)
    cursor.execute("UPDATE settings SET anti_role_delete=? WHERE guild_id=?", (int(狀態), interaction.guild.id))
    db.commit()
    await interaction.response.send_message("設定完成")

@bot.tree.command(name="防改名稱")
async def toggle_rename(interaction: discord.Interaction, 狀態: bool):
    ensure_guild_settings(interaction.guild.id)
    cursor.execute("UPDATE settings SET anti_guild_rename=? WHERE guild_id=?", (int(狀態), interaction.guild.id))
    db.commit()
    await interaction.response.send_message("設定完成")

@bot.tree.command(name="防刪頻道")
async def toggle_channel_delete(interaction: discord.Interaction, 狀態: bool):
    ensure_guild_settings(interaction.guild.id)
    cursor.execute("UPDATE settings SET anti_channel_delete=? WHERE guild_id=?", (int(狀態), interaction.guild.id))
    db.commit()
    await interaction.response.send_message("設定完成")

@bot.tree.command(name="防新增頻道")
async def toggle_channel_create(interaction: discord.Interaction, 狀態: bool):
    ensure_guild_settings(interaction.guild.id)
    cursor.execute("UPDATE settings SET anti_channel_create=? WHERE guild_id=?", (int(狀態), interaction.guild.id))
    db.commit()
    await interaction.response.send_message("設定完成")

# =========================

bot.run(TOKEN)



