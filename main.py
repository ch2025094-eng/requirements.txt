import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime, UTC
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
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
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
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY,
    bans INTEGER DEFAULT 0,
    locks INTEGER DEFAULT 0
)
""")

cursor.execute("INSERT OR IGNORE INTO stats (id) VALUES (1)")
db.commit()

# ================= 工具 =================

def is_whitelisted(user_id):
    cursor.execute("SELECT user_id FROM whitelist WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def get_log_channel(guild):
    cursor.execute("SELECT log_channel_id FROM config WHERE guild_id=?", (guild.id,))
    result = cursor.fetchone()
    if result:
        return guild.get_channel(result[0])
    return None

async def punish(guild, user, reason):
    if is_whitelisted(user.id):
        return
    try:
        await user.ban(reason=reason)
        cursor.execute("UPDATE stats SET bans = bans + 1 WHERE id=1")
        db.commit()
    except:
        pass

    log = get_log_channel(guild)
    if log:
        await log.send(f"🚨 {user.mention} 已被封鎖 | 原因: {reason}")

async def lock_server(guild):
    for role in guild.roles:
        if role.permissions.administrator:
            try:
                await role.edit(permissions=discord.Permissions.none())
            except:
                pass

    cursor.execute("UPDATE stats SET locks = locks + 1 WHERE id=1")
    db.commit()

    log = get_log_channel(guild)
    if log:
        await log.send("🔒 偵測到爆量攻擊，伺服器已進入鎖定模式")

# ================= 啟動 =================

@bot.event
async def on_ready():
    print(f"🤖 已登入 {bot.user}")
    await bot.tree.sync()
    print("✅ Slash 指令已同步")

# ================= 攻擊偵測 =================

channel_tracker = defaultdict(list)

@bot.event
async def on_guild_channel_create(channel):

    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
        user = entry.user

        # 黑名單立即封鎖
        cursor.execute("SELECT user_id FROM blacklist WHERE user_id=?", (user.id,))
        if cursor.fetchone():
            await punish(channel.guild, user, "黑名單用戶建立頻道")
            return

        # 爆量偵測（3秒3頻道）
        now = datetime.now().timestamp()
        channel_tracker[user.id].append(now)
        channel_tracker[user.id] = [t for t in channel_tracker[user.id] if now - t < 3]

        if len(channel_tracker[user.id]) >= 3:
            await lock_server(channel.guild)
            await punish(channel.guild, user, "3秒內大量建立頻道")
            return

        # nuked 防護
        if "nuked" in channel.name.lower():
            await channel.delete(reason="建立 nuked 頻道")
            await punish(channel.guild, user, "建立 nuked 頻道")
            return

        break

@bot.event
async def on_guild_channel_update(before, after):
    if before.name == after.name:
        return

    if "nuked" in after.name.lower():
        await after.edit(name=before.name)

        async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_update):
            await punish(after.guild, entry.user, "改頻道名稱為 nuked")
            break

# ================= 指令 =================

@bot.tree.command(name="設定日誌頻道")
@app_commands.checks.has_permissions(administrator=True)
async def set_log(interaction: discord.Interaction, channel: discord.TextChannel):
    cursor.execute(
        "INSERT OR REPLACE INTO config VALUES (?,?)",
        (interaction.guild.id, channel.id)
    )
    db.commit()
    await interaction.response.send_message("✅ 日誌頻道已設定")

@bot.tree.command(name="加入白名單")
@app_commands.checks.has_permissions(administrator=True)
async def add_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute(
        "INSERT OR REPLACE INTO whitelist VALUES (?,?,?)",
        (member.id, interaction.user.id,
         datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"))
    )
    db.commit()
    await interaction.response.send_message(f"✅ {member.mention} 已加入白名單")

@bot.tree.command(name="移除白名單")
@app_commands.checks.has_permissions(administrator=True)
async def remove_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM whitelist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message("✅ 已移除白名單")

@bot.tree.command(name="查看白名單")
async def list_white(interaction: discord.Interaction):
    cursor.execute("SELECT user_id FROM whitelist")
    users = cursor.fetchall()
    text = "\n".join([f"<@{u[0]}>" for u in users]) if users else "無資料"
    await interaction.response.send_message(f"📜 白名單列表:\n{text}")

@bot.tree.command(name="加入黑名單")
@app_commands.checks.has_permissions(administrator=True)
async def add_black(interaction: discord.Interaction, member: discord.Member):
    cursor.execute(
        "INSERT OR REPLACE INTO blacklist VALUES (?,?,?)",
        (member.id, interaction.user.id,
         datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"))
    )
    db.commit()
    await interaction.response.send_message(f"⛔ {member.mention} 已加入黑名單")

@bot.tree.command(name="移除黑名單")
@app_commands.checks.has_permissions(administrator=True)
async def remove_black(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message("✅ 已移除黑名單")

@bot.tree.command(name="查看黑名單")
async def list_black(interaction: discord.Interaction):
    cursor.execute("SELECT user_id FROM blacklist")
    users = cursor.fetchall()
    text = "\n".join([f"<@{u[0]}>" for u in users]) if users else "無資料"
    await interaction.response.send_message(f"⛔ 黑名單列表:\n{text}")

@bot.tree.command(name="防炸狀態")
async def status(interaction: discord.Interaction):
    cursor.execute("SELECT bans, locks FROM stats WHERE id=1")
    bans, locks = cursor.fetchone()
    await interaction.response.send_message(
        f"🛡 封鎖次數: {bans}\n🔒 鎖服次數: {locks}"
    )

# ================= 啟動 =================

bot.run(TOKEN)
