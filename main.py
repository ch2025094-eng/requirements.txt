import discord
from discord.ext import commands
from discord import app_commands
import time
import sqlite3
from datetime import timedelta

# ========= 讀取環境變數 =========
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN 沒有設定，請檢查 .env 或部署平台環境變數")

print("✅ TOKEN 讀取成功")

# ========= Bot 設定 =========
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ========= 資料庫 =========
db = sqlite3.connect("bot.db")
cursor = db.cursor()

cursor.execute("CREATE TABLE IF NOT EXISTS blacklist (user_id INTEGER PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")
cursor.execute("""
CREATE TABLE IF NOT EXISTS config (
    guild_id INTEGER PRIMARY KEY,
    log_channel INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY,
    timeouts INTEGER,
    mutes INTEGER
)
""")
db.commit()

cursor.execute("SELECT * FROM stats WHERE id=1")
if not cursor.fetchone():
    cursor.execute("INSERT INTO stats VALUES (1,0,0)")
    db.commit()

# ========= 參數 =========
USER_LIMIT = 5
USER_MUTE_LIMIT = 8
USER_WINDOW = 3
MUTE_TIME = 120

user_msgs = {}

# ========= 工具 =========
def is_admin(m):
    return m.guild_permissions.administrator

def track_user(uid):
    now = time.time()
    user_msgs.setdefault(uid, []).append(now)
    user_msgs[uid] = [t for t in user_msgs[uid] if now - t <= USER_WINDOW]
    return len(user_msgs[uid])

async def send_log(guild, text):
    cursor.execute("SELECT log_channel FROM config WHERE guild_id=?", (guild.id,))
    row = cursor.fetchone()
    if row:
        ch = guild.get_channel(row[0])
        if ch:
            await ch.send(text)

async def get_or_create_muted_role(guild):
    role = discord.utils.get(guild.roles, name="Muted")
    if role:
        return role
    role = await guild.create_role(name="Muted")
    for channel in guild.channels:
        await channel.set_permissions(role, send_messages=False)
    return role

# ========= 事件 =========
@bot.event
async def on_ready():
    print(f"🤖 已登入 {bot.user}")
    await bot.tree.sync()
    print("✅ Slash 指令已同步")

@bot.event
async def on_message(msg):
    if not msg.guild or msg.author.bot:
        return

    uid = msg.author.id

    # 黑名單
    cursor.execute("SELECT 1 FROM blacklist WHERE user_id=?", (uid,))
    if cursor.fetchone():
        await msg.delete()
        return

    # 白名單
    cursor.execute("SELECT 1 FROM whitelist WHERE user_id=?", (uid,))
    if cursor.fetchone():
        return

    if not is_admin(msg.author):
        count = track_user(uid)

        if count >= USER_MUTE_LIMIT:
            role = await get_or_create_muted_role(msg.guild)
            await msg.author.add_roles(role, reason="嚴重刷頻")
            await msg.delete()
            cursor.execute("UPDATE stats SET mutes = mutes + 1 WHERE id=1")
            db.commit()
            await send_log(msg.guild, f"🔇 禁言：{msg.author}")
            return

        elif count >= USER_LIMIT:
            await msg.delete()
            try:
                await msg.author.timeout(
                    discord.utils.utcnow() + timedelta(seconds=MUTE_TIME),
                    reason="刷頻"
                )
            except:
                pass
            cursor.execute("UPDATE stats SET timeouts = timeouts + 1 WHERE id=1")
            db.commit()
            await send_log(msg.guild, f"⏳ Timeout：{msg.author}")
            return

    await bot.process_commands(msg)

# ========= 指令審計 =========
@bot.event
async def on_app_command_completion(interaction, command):
    if interaction.guild:
        await send_log(interaction.guild, f"📌 {interaction.user} 使用 /{command.name}")

# ======= 防新增怪頻道 =========
@bot.event
async def on_guild_channel_create(channel):

    # 如果名稱不包含 nuked 就略過
    if "nuked" not in channel.name.lower():
        return

    guild = channel.guild

    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):

        user = entry.user

        # 檢查是否白名單
        cursor.execute("SELECT user_id FROM whitelist WHERE user_id=?", (user.id,))
        if cursor.fetchone():
            return  # 白名單不處理

        # 刪除該頻道
        await channel.delete(reason="禁止建立 nuked 頻道")

        # 踢出違規者
        await user.kick(reason="建立 nuked 頻道")

        # 更新統計
        cursor.execute("UPDATE stats SET kicks = kicks + 1 WHERE id=1")
        db.commit()

        # 發送日誌
        log_channel = get_log_channel(guild)
        if log_channel:
            await log_channel.send(
                f"🚨 {user.mention} 嘗試建立 nuked 頻道，已刪除並踢出"
            )

        break

# ========= 管理員權限 =========
def admin():
    return app_commands.checks.has_permissions(administrator=True)

# ========= Slash 指令 =========
@bot.tree.command(name="加入黑名單", description="將用戶加入永久黑名單")
@admin()
async def add_black(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("INSERT OR IGNORE INTO blacklist VALUES (?)", (member.id,))
    db.commit()
    await interaction.response.send_message(f"🚫 已加入黑名單：{member}", ephemeral=True)

@bot.tree.command(name="移除黑名單", description="將用戶移出黑名單")
@admin()
async def remove_black(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message(f"❌ 已移除黑名單：{member}", ephemeral=True)

@bot.tree.command(name="加入白名單", description="將用戶加入永久白名單")
@admin()
async def add_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("INSERT OR IGNORE INTO whitelist VALUES (?)", (member.id,))
    db.commit()
    await interaction.response.send_message(f"✅ 已加入白名單：{member}", ephemeral=True)

@bot.tree.command(name="移除白名單", description="將用戶移出白名單")
@admin()
async def remove_white(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM whitelist WHERE user_id=?", (member.id,))
    db.commit()
    await interaction.response.send_message(f"❌ 已移除白名單：{member}", ephemeral=True)

@bot.tree.command(name="防炸狀態", description="查看防炸統計數據")
@admin()
async def status(interaction: discord.Interaction):
    cursor.execute("SELECT timeouts, mutes FROM stats WHERE id=1")
    row = cursor.fetchone()
    await interaction.response.send_message(
        f"📊 Timeout：{row[0]}\n🔇 禁言：{row[1]}",
        ephemeral=True
    )

@bot.tree.command(name="設置日誌頻道", description="設定防炸日誌輸出頻道")
@admin()
async def setlog(interaction: discord.Interaction, channel: discord.TextChannel):
    cursor.execute(
        "INSERT OR REPLACE INTO config VALUES (?,?)",
        (interaction.guild.id, channel.id)
    )
    db.commit()
    await interaction.response.send_message("📁 日誌頻道已設定", ephemeral=True)

# ========= 啟動 =========
bot.run(TOKEN)

