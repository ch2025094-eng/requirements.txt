import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

# ===== 讀取 TOKEN =====
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN 沒有設定")

print("✅ TOKEN 讀取成功")

# ===== Bot 設定 =====
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ===== 資料庫 =====
db = sqlite3.connect("bot.db", check_same_thread=False)
cursor = db.cursor()

# 黑名單
cursor.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at TEXT
)
""")

# 白名單
cursor.execute("""
CREATE TABLE IF NOT EXISTS whitelist (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at TEXT
)
""")

# 統計資料
cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY,
    kicks INTEGER DEFAULT 0,
    bans INTEGER DEFAULT 0,
    channel_restores INTEGER DEFAULT 0
)
""")

cursor.execute("INSERT OR IGNORE INTO stats (id) VALUES (1)")

# 日誌頻道設定
cursor.execute("""
CREATE TABLE IF NOT EXISTS config (
    guild_id INTEGER PRIMARY KEY,
    log_channel_id INTEGER
)
""")

db.commit()
# ===== 記憶體追蹤 =====
join_tracker = {}
message_tracker = {}

# ===== 管理員檢查 =====
def admin():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("❌ 你沒有權限")
        return False
    return app_commands.check(predicate)

# ===================== 事件 =====================

@bot.event
async def on_ready():
    print(f"🤖 已登入 {bot.user}")
    await bot.tree.sync()
    print("✅ Slash 指令已同步")

# ===== 成員加入 =====
@bot.event
async def on_member_join(member):

    # 白名單無敵
    cursor.execute("SELECT user_id FROM whitelist WHERE user_id=?", (member.id,))
    if cursor.fetchone():
        return

    # 黑名單自動踢
    cursor.execute("SELECT user_id FROM blacklist WHERE user_id=?", (member.id,))
    if cursor.fetchone():
        await member.kick(reason="黑名單使用者")
        cursor.execute("UPDATE stats SET kicks=kicks+1 WHERE id=1")
        db.commit()
        await send_log(member.guild, f"🚫 黑名單自動踢出：{member}")
        return

    # 防機器人炸群
    if member.bot:
        async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.bot_add):
            if entry.target.id == member.id:
                await member.kick(reason="防機器人炸群")
                cursor.execute("INSERT OR IGNORE INTO blacklist VALUES (?)", (entry.user.id,))
                db.commit()
                await send_log(member.guild, f"🤖 已踢出機器人：{member}")
                await send_log(member.guild, f"🚨 已將新增者加入黑名單：{entry.user}")
                return

    # 短時間大量加入
    now = datetime.utcnow()
    gid = member.guild.id

    if gid not in join_tracker:
        join_tracker[gid] = []

    join_tracker[gid].append(now)
    join_tracker[gid] = [t for t in join_tracker[gid] if now - t < timedelta(seconds=10)]

    if len(join_tracker[gid]) >= 5:
        for channel in member.guild.text_channels:
            await channel.set_permissions(member.guild.default_role, send_messages=False)
        await send_log(member.guild, "⚠ 偵測大量加入，已鎖定所有頻道")

# ===== 防洗頻 =====
@bot.event
async def on_message(message):

    if message.author.bot:
        return

    cursor.execute("SELECT user_id FROM whitelist WHERE user_id=?", (message.author.id,))
    if cursor.fetchone():
        await bot.process_commands(message)
        return

    now = datetime.utcnow()

    if message.author.id not in message_tracker:
        message_tracker[message.author.id] = []

    message_tracker[message.author.id].append(now)
    message_tracker[message.author.id] = [
        t for t in message_tracker[message.author.id]
        if now - t < timedelta(seconds=5)
    ]

    if len(message_tracker[message.author.id]) >= 4:
        await message.channel.send(f"🚨 {message.author.mention} 刷頻已列入黑名單")
        cursor.execute("INSERT OR IGNORE INTO blacklist VALUES (?)", (message.author.id,))
        db.commit()
        try:
            await message.author.kick(reason="刷頻")
        except:
            pass

    await bot.process_commands(message)

# ===== 防改頻道名稱 =====
@bot.event
async def on_guild_channel_update(before, after):

    if before.name != after.name:
        async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_update):
            if entry.target.id == after.id:
                await after.edit(name=before.name)
                try:
                    await entry.user.kick(reason="擅自修改頻道名稱")
                except:
                    pass
                await send_log(after.guild, f"🛑 阻止改名並踢出：{entry.user}")
                break

# ===== 防刪角色 =====
@bot.event
async def on_guild_role_delete(role):

    async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        if entry.target.id == role.id:
            await role.guild.create_role(name=role.name, permissions=role.permissions)
            try:
                await entry.user.kick(reason="刪除角色")
            except:
                pass
            await send_log(role.guild, f"🛑 角色已還原：{role.name}")
            await send_log(role.guild, f"🚨 已踢出操作者：{entry.user}")
            break

# ===== 防刪伺服器圖示 =====
@bot.event
async def on_guild_update(before, after):

    if before.icon != after.icon:
        await after.edit(icon=before.icon)
        await send_log(after, "🛑 伺服器圖示已還原")

# ===================== Slash 指令 =====================

from datetime import datetime
import discord

@bot.tree.command(name="加入黑名單", description="將成員加入黑名單")
@admin()
async def add_black(interaction: discord.Interaction, member: discord.Member):

    cursor.execute("SELECT user_id FROM blacklist WHERE user_id = ?", (member.id,))
    if cursor.fetchone():
        await interaction.response.send_message("❌ 該成員已在黑名單中", ephemeral=True)
        return

    cursor.execute(
        "INSERT INTO blacklist (user_id, added_by, added_at) VALUES (?, ?, ?)",
        (member.id, interaction.user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()

    await interaction.response.send_message(f"✅ 已將 {member.mention} 加入黑名單")

@bot.tree.command(name="移除黑名單", description="將指定成員從黑名單移除")
@admin()
async def remove_black(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message("✅ 已移除黑名單")

@bot.tree.command(name="加入白名單", description="將指定成員加入白名單（不受防炸影響）")
@admin()
async def add_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("INSERT OR IGNORE INTO whitelist VALUES (?)", (member.id,))
    db.commit()
    await interaction.response.send_message("🟢 已加入白名單")

@bot.tree.command(name="移除白名單", description="將指定成員從白名單移除")
@admin()
async def remove_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM whitelist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message("🔴 已移除白名單")

@bot.tree.command(name="查看黑名單", description="查看黑名單完整資訊")
@admin()
async def view_black(interaction: discord.Interaction):

    cursor.execute("SELECT * FROM blacklist")
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("黑名單是空的", ephemeral=True)
        return

    embeds = []
    embed = discord.Embed(
        title="🚫 黑名單列表",
        color=discord.Color.red()
    )

    count = 0

    for user_id, added_by, added_at in rows:
        member = interaction.guild.get_member(user_id)
        admin_user = interaction.guild.get_member(added_by)

        name = member.mention if member else f"未知使用者 ({user_id})"
        admin_name = admin_user.mention if admin_user else f"未知管理員 ({added_by})"

        embed.add_field(
            name=f"👤 {name}",
            value=f"🆔 `{user_id}`\n"
                  f"👮 加入者：{admin_name}\n"
                  f"🕒 時間：{added_at}",
            inline=False
        )

        count += 1

        if count % 25 == 0:
            embeds.append(embed)
            embed = discord.Embed(
                title="🚫 黑名單列表（續）",
                color=discord.Color.red()
            )

    embeds.append(embed)

    await interaction.response.send_message(embed=embeds[0])

    for e in embeds[1:]:
        await interaction.followup.send(embed=e)

@bot.tree.command(name="查看白名單", description="查看白名單完整資訊")
@admin()
async def view_white(interaction: discord.Interaction):

    cursor.execute("SELECT * FROM whitelist")
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("白名單是空的", ephemeral=True)
        return

    embeds = []
    embed = discord.Embed(
        title="✅ 白名單列表",
        color=discord.Color.green()
    )

    count = 0

    for user_id, added_by, added_at in rows:
        member = interaction.guild.get_member(user_id)
        admin_user = interaction.guild.get_member(added_by)

        name = member.mention if member else f"未知使用者 ({user_id})"
        admin_name = admin_user.mention if admin_user else f"未知管理員 ({added_by})"

        embed.add_field(
            name=f"👤 {name}",
            value=f"🆔 `{user_id}`\n"
                  f"👮 加入者：{admin_name}\n"
                  f"🕒 時間：{added_at}",
            inline=False
        )

        count += 1

        if count % 25 == 0:
            embeds.append(embed)
            embed = discord.Embed(
                title="✅ 白名單列表（續）",
                color=discord.Color.green()
            )

    embeds.append(embed)

    await interaction.response.send_message(embed=embeds[0])

    for e in embeds[1:]:
        await interaction.followup.send(embed=e)

@bot.tree.command(name="設定日誌頻道", description="設定防炸事件的日誌輸出頻道")
@app_commands.checks.has_permissions(administrator=True)
async def status(interaction: discord.Interaction):

    cursor.execute("SELECT kicks, bans, channel_restores FROM stats WHERE id=1")
    kicks, bans, restores = cursor.fetchone()

    embed = discord.Embed(
        title="🛡 防炸統計",
        color=discord.Color.blue()
    )

    embed.add_field(name="踢出次數", value=kicks)
    embed.add_field(name="封鎖次數", value=bans)
    embed.add_field(name="還原頻道", value=restores)

    await interaction.response.send_message(embed=embed)
@bot.tree.command(name="防炸狀態", description="查看目前自動踢出的統計數量")
@admin()
async def status(interaction: discord.Interaction):
    cursor.execute("SELECT kicks FROM stats WHERE id=1")
    row = cursor.fetchone()
    await interaction.response.send_message(f"🚨 目前自動踢出：{row[0]} 人")

# ===== 啟動 =====
bot.run(TOKEN)


