import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta, UTC
import os
from collections import defaultdict

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 資料庫 =================

db = sqlite3.connect("bot.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS whitelist (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    added_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS config (
    guild_id INTEGER PRIMARY KEY,
    log_channel_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    anti_role_delete INTEGER DEFAULT 1,
    anti_guild_rename INTEGER DEFAULT 1,
    anti_channel_delete INTEGER DEFAULT 1
)
""")

db.commit()

# ================= 工具函數 =================

def is_whitelisted(user_id):
    cursor.execute("SELECT user_id FROM whitelist WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def add_blacklist(user_id, reason):
    cursor.execute(
        "INSERT OR REPLACE INTO blacklist VALUES (?,?,?)",
        (user_id, reason, datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"))
    )
    db.commit()

def get_log_channel(guild):
    cursor.execute("SELECT log_channel_id FROM config WHERE guild_id=?", (guild.id,))
    r = cursor.fetchone()
    return guild.get_channel(r[0]) if r else None

def get_settings(guild_id):
    cursor.execute("INSERT OR IGNORE INTO settings (guild_id) VALUES (?)", (guild_id,))
    db.commit()
    cursor.execute("SELECT anti_role_delete, anti_guild_rename, anti_channel_delete FROM settings WHERE guild_id=?", (guild_id,))
    return cursor.fetchone()

async def timeout(member, seconds):
    until = discord.utils.utcnow() + timedelta(seconds=seconds)
    await member.timeout(until)

# ================= 啟動 =================

@bot.event
async def on_ready():
    print(f"已登入 {bot.user}")
    await bot.tree.sync()
    print("Slash 指令同步完成")

# ================= 防刪角色 =================

@bot.event
async def on_guild_role_delete(role):
    anti_role_delete, _, _ = get_settings(role.guild.id)
    if not anti_role_delete:
        return

    async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        user = entry.user
        if is_whitelisted(user.id):
            return
        await role.guild.create_role(name=role.name)
        add_blacklist(user.id, "刪除角色")
        await timeout(user, 60)
        break

# ================= 防改伺服器名稱 =================

@bot.event
async def on_guild_update(before, after):
    _, anti_guild_rename, _ = get_settings(after.id)
    if not anti_guild_rename:
        return

    if before.name != after.name:
        await after.edit(name=before.name)

        async for entry in after.audit_logs(limit=1, action=discord.AuditLogAction.guild_update):
            user = entry.user
            if is_whitelisted(user.id):
                return
            add_blacklist(user.id, "修改伺服器名稱")
            await timeout(user, 60)
            break

# ================= 防刪頻道（含分類復原） =================

@bot.event
async def on_guild_channel_delete(channel):
    _, _, anti_channel_delete = get_settings(channel.guild.id)
    if not anti_channel_delete:
        return

    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        user = entry.user
        if is_whitelisted(user.id):
            return

        if isinstance(channel, discord.TextChannel):
            await channel.guild.create_text_channel(
                name=channel.name,
                category=channel.category
            )
        elif isinstance(channel, discord.VoiceChannel):
            await channel.guild.create_voice_channel(
                name=channel.name,
                category=channel.category
            )

        add_blacklist(user.id, "刪除頻道")
        await timeout(user, 60)
        break

# ================= 刷頻 & @everyone =================

message_tracker = defaultdict(list)
mention_tracker = defaultdict(list)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if is_whitelisted(message.author.id):
        return

    now = datetime.now().timestamp()

    # 6秒8則
    message_tracker[message.author.id].append(now)
    message_tracker[message.author.id] = [
        t for t in message_tracker[message.author.id]
        if now - t < 6
    ]

    if len(message_tracker[message.author.id]) >= 8:
        add_blacklist(message.author.id, "刷頻")
        await timeout(message.author, 60)
        return

    # 三秒內3次everyone
    if "@everyone" in message.content:
        mention_tracker[message.author.id].append(now)
        mention_tracker[message.author.id] = [
            t for t in mention_tracker[message.author.id]
            if now - t < 3
        ]

        if len(mention_tracker[message.author.id]) >= 3:
            add_blacklist(message.author.id, "短時間多次@everyone")
            await timeout(message.author, 60)
            return

        # 單則超過2次
        if message.content.count("@everyone") > 2:
            add_blacklist(message.author.id, "單則大量@everyone")
            await timeout(message.author, 60)
            return

    await bot.process_commands(message)

# ================= 指令 =================

@bot.tree.command(name="功能說明")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        "本機器人功能：\n"
        "防刪角色 / 防改伺服器名稱 / 防刪頻道（含分類復原）\n"
        "刷頻偵測（6秒8則）\n"
        "@everyone濫用偵測\n"
        "黑白名單系統\n"
        "所有防護皆可開關"
    )

@bot.tree.command(name="設定日誌頻道")
async def set_log(interaction: discord.Interaction, channel: discord.TextChannel):
    cursor.execute("INSERT OR REPLACE INTO config VALUES (?,?)",
                   (interaction.guild.id, channel.id))
    db.commit()
    await interaction.response.send_message("日誌頻道已設定")

@bot.tree.command(name="開關防刪角色")
async def toggle_role(interaction: discord.Interaction, state: bool):
    cursor.execute("UPDATE settings SET anti_role_delete=? WHERE guild_id=?",
                   (int(state), interaction.guild.id))
    db.commit()
    await interaction.response.send_message(f"防刪角色已設為 {state}")

@bot.tree.command(name="開關防改伺服器名稱")
async def toggle_rename(interaction: discord.Interaction, state: bool):
    cursor.execute("UPDATE settings SET anti_guild_rename=? WHERE guild_id=?",
                   (int(state), interaction.guild.id))
    db.commit()
    await interaction.response.send_message(f"防改伺服器名稱已設為 {state}")

@bot.tree.command(name="開關防刪頻道")
async def toggle_channel(interaction: discord.Interaction, state: bool):
    cursor.execute("UPDATE settings SET anti_channel_delete=? WHERE guild_id=?",
                   (int(state), interaction.guild.id))
    db.commit()
    await interaction.response.send_message(f"防刪頻道已設為 {state}")

# ================= 黑白名單管理 =================

@bot.tree.command(name="加入白名單", description="將成員加入白名單（不受防護系統影響）")
@app_commands.checks.has_permissions(administrator=True)
async def add_whitelist(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("INSERT OR IGNORE INTO whitelist VALUES (?)", (member.id,))
    db.commit()
    await interaction.response.send_message(f"{member.mention} 已加入白名單")

@bot.tree.command(name="移除白名單", description="將成員從白名單移除")
@app_commands.checks.has_permissions(administrator=True)
async def remove_whitelist(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM whitelist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message(f"{member.mention} 已移除白名單")

@bot.tree.command(name="查看白名單", description="查看目前白名單成員")
@app_commands.checks.has_permissions(administrator=True)
async def view_whitelist(interaction: discord.Interaction):
    cursor.execute("SELECT user_id FROM whitelist")
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("白名單目前是空的")
        return

    mentions = []
    for row in rows:
        member = interaction.guild.get_member(row[0])
        if member:
            mentions.append(member.mention)

    await interaction.response.send_message("📜 白名單成員：\n" + "\n".join(mentions))


@bot.tree.command(name="加入黑名單", description="手動將成員加入黑名單")
@app_commands.checks.has_permissions(administrator=True)
async def add_blacklist_cmd(interaction: discord.Interaction, member: discord.Member, 原因: str):
    add_blacklist(member.id, 原因)
    await timeout(member, 60)
    await interaction.response.send_message(f"{member.mention} 已加入黑名單\n原因：{原因}")


@bot.tree.command(name="移除黑名單", description="將成員從黑名單移除")
@app_commands.checks.has_permissions(administrator=True)
async def remove_blacklist(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message(f"{member.mention} 已移除黑名單")


@bot.tree.command(name="查看黑名單", description="查看目前黑名單成員")
@app_commands.checks.has_permissions(administrator=True)
async def view_blacklist(interaction: discord.Interaction):
    cursor.execute("SELECT user_id, reason, added_at FROM blacklist")
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("黑名單目前是空的")
        return

    msg = "🚫 黑名單列表：\n"
    for row in rows:
        member = interaction.guild.get_member(row[0])
        name = member.mention if member else f"ID:{row[0]}"
        msg += f"{name} | 原因：{row[1]} | 時間：{row[2]}\n"

    await interaction.response.send_message(msg)

bot.run(TOKEN)
