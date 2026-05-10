import discord
from discord.ext import commands, tasks
import json
import os
import asyncio
from datetime import datetime
import logging

# ─── CONFIG (desde variables de entorno) ──────────────────────────────────────
TOKEN                    = os.environ["DISCORD_TOKEN"]          # Obligatorio
BACKUP_INTERVAL_MINUTES  = int(os.getenv("BACKUP_INTERVAL", "30"))
MAX_MESSAGES_PER_CHANNEL = int(os.getenv("MAX_MESSAGES", "500"))
NUKE_THRESHOLD           = int(os.getenv("NUKE_THRESHOLD", "3"))
NUKE_WINDOW_SECONDS      = int(os.getenv("NUKE_WINDOW", "10"))
BACKUP_FILE              = "server_backup.json"
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]   # Railway captura stdout/stderr
)
log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

deleted_channels_tracker: dict[int, list[datetime]] = {}
restore_in_progress: set[int] = set()


# ──────────────────────────────────────────────────────────────────────────────
#  BACKUP
# ──────────────────────────────────────────────────────────────────────────────

def load_backups() -> dict:
    if os.path.exists(BACKUP_FILE):
        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_backups(data: dict):
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def backup_guild(guild: discord.Guild) -> dict:
    log.info(f"📦 Backup iniciado: '{guild.name}' ({guild.id})")
    backup = {
        "guild_id": guild.id,
        "guild_name": guild.name,
        "timestamp": datetime.utcnow().isoformat(),
        "roles": [],
        "categories": [],
        "channels": []
    }

    for role in guild.roles:
        if role.name == "@everyone":
            continue
        backup["roles"].append({
            "id": role.id,
            "name": role.name,
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position": role.position
        })

    for cat in guild.categories:
        backup["categories"].append({
            "id": cat.id,
            "name": cat.name,
            "position": cat.position,
            "overwrites": serialize_overwrites(cat.overwrites)
        })

    for channel in guild.text_channels:
        channel_data = {
            "id": channel.id,
            "name": channel.name,
            "topic": channel.topic or "",
            "position": channel.position,
            "nsfw": channel.is_nsfw(),
            "slowmode_delay": channel.slowmode_delay,
            "category_id": channel.category_id,
            "overwrites": serialize_overwrites(channel.overwrites),
            "messages": []
        }
        try:
            async for msg in channel.history(limit=MAX_MESSAGES_PER_CHANNEL, oldest_first=True):
                channel_data["messages"].append({
                    "author": str(msg.author),
                    "author_id": msg.author.id,
                    "content": msg.content,
                    "timestamp": msg.created_at.isoformat(),
                    "attachments": [a.url for a in msg.attachments],
                    "embeds": [e.to_dict() for e in msg.embeds]
                })
        except discord.Forbidden:
            log.warning(f"  Sin permiso para leer #{channel.name}")
        backup["channels"].append(channel_data)
        await asyncio.sleep(0.2)

    for channel in guild.voice_channels:
        backup["channels"].append({
            "id": channel.id,
            "name": channel.name,
            "type": "voice",
            "position": channel.position,
            "bitrate": channel.bitrate,
            "user_limit": channel.user_limit,
            "category_id": channel.category_id,
            "overwrites": serialize_overwrites(channel.overwrites),
            "messages": []
        })

    log.info(f"✅ Backup listo: {len(backup['channels'])} canales, {sum(len(c.get('messages',[])) for c in backup['channels'])} mensajes")
    return backup


def serialize_overwrites(overwrites: dict) -> list:
    result = []
    for target, overwrite in overwrites.items():
        allow, deny = overwrite.pair()
        result.append({
            "type": "role" if isinstance(target, discord.Role) else "member",
            "id": target.id,
            "allow": allow.value,
            "deny": deny.value
        })
    return result


# ──────────────────────────────────────────────────────────────────────────────
#  RESTAURACIÓN
# ──────────────────────────────────────────────────────────────────────────────

async def restore_guild(guild: discord.Guild, backup: dict, log_channel=None):
    log.info(f"🔄 Restauración iniciada: '{guild.name}'")

    created_channels = []
    deleted_channels = []

    async def status(msg: str):
        log.info(msg)
        if log_channel:
            try:
                await log_channel.send(msg)
            except Exception:
                pass

    await status("🔄 **Restauración iniciada...**")

    # ── Roles ──────────────────────────────────────────────────────────────────
    await status("🎭 Restaurando roles...")
    role_map = {}
    for role_data in sorted(backup.get("roles", []), key=lambda r: r["position"]):
        existing = discord.utils.get(guild.roles, name=role_data["name"])
        if existing:
            role_map[role_data["id"]] = existing
            continue
        try:
            new_role = await guild.create_role(
                name=role_data["name"],
                color=discord.Color(role_data["color"]),
                hoist=role_data["hoist"],
                mentionable=role_data["mentionable"],
                permissions=discord.Permissions(role_data["permissions"])
            )
            role_map[role_data["id"]] = new_role
            await asyncio.sleep(0.3)
        except Exception as e:
            log.error(f"Error creando rol '{role_data['name']}': {e}")

    # ── Categorías ─────────────────────────────────────────────────────────────
    await status("📁 Restaurando categorías...")
    category_map = {}
    for cat_data in sorted(backup.get("categories", []), key=lambda c: c["position"]):
        existing = discord.utils.get(guild.categories, name=cat_data["name"])
        if existing:
            category_map[cat_data["id"]] = existing
            continue
        try:
            new_cat = await guild.create_category(
                name=cat_data["name"],
                overwrites=deserialize_overwrites(cat_data["overwrites"], guild, role_map),
                position=cat_data["position"]
            )
            category_map[cat_data["id"]] = new_cat
            await asyncio.sleep(0.3)
        except Exception as e:
            log.error(f"Error creando categoría '{cat_data['name']}': {e}")

    # ── Canales del backup ─────────────────────────────────────────────────────
    await status("💬 Restaurando canales y mensajes...")
    backup_channel_names_text  = {ch["name"] for ch in backup.get("channels", []) if ch.get("type") != "voice"}
    backup_channel_names_voice = {ch["name"] for ch in backup.get("channels", []) if ch.get("type") == "voice"}

    for ch_data in sorted(backup.get("channels", []), key=lambda c: c["position"]):
        category  = category_map.get(ch_data.get("category_id"))
        overwrites = deserialize_overwrites(ch_data.get("overwrites", []), guild, role_map)

        if ch_data.get("type") == "voice":
            existing_vc = discord.utils.get(guild.voice_channels, name=ch_data["name"])
            if not existing_vc:
                try:
                    await guild.create_voice_channel(
                        name=ch_data["name"],
                        category=category,
                        bitrate=ch_data.get("bitrate", 64000),
                        user_limit=ch_data.get("user_limit", 0),
                        overwrites=overwrites
                    )
                    created_channels.append(f"🔊 {ch_data['name']} (voz)")
                    log.info(f"  ✅ Canal de voz creado: {ch_data['name']}")
                    await asyncio.sleep(0.3)
                except Exception as e:
                    log.error(f"Error creando canal de voz '{ch_data['name']}': {e}")
            continue

        existing = discord.utils.get(guild.text_channels, name=ch_data["name"])
        if not existing:
            try:
                existing = await guild.create_text_channel(
                    name=ch_data["name"],
                    topic=ch_data.get("topic", ""),
                    nsfw=ch_data.get("nsfw", False),
                    slowmode_delay=ch_data.get("slowmode_delay", 0),
                    category=category,
                    overwrites=overwrites
                )
                created_channels.append(f"💬 {ch_data['name']}")
                log.info(f"  ✅ Canal de texto creado: #{ch_data['name']}")
                await asyncio.sleep(0.3)
            except Exception as e:
                log.error(f"Error creando canal '#{ch_data['name']}': {e}")
                continue

        if ch_data.get("messages"):
            await restore_messages(existing, ch_data["messages"])

    # ── Eliminar canales que NO están en el backup ─────────────────────────────
    await status("🧹 Eliminando canales extra (no presentes en el backup)...")

    for ch in list(guild.text_channels):
        if ch.name not in backup_channel_names_text:
            # No borrar el canal de log actual si existe
            if log_channel and ch.id == log_channel.id:
                continue
            try:
                log.warning(f"  🗑️  Borrando canal extra de texto: #{ch.name}")
                deleted_channels.append(f"💬 {ch.name}")
                await ch.delete(reason="Canal no presente en el backup — limpieza automática")
                await asyncio.sleep(0.5)
            except Exception as e:
                log.error(f"Error borrando canal '#{ch.name}': {e}")

    for vc in list(guild.voice_channels):
        if vc.name not in backup_channel_names_voice:
            try:
                log.warning(f"  🗑️  Borrando canal extra de voz: {vc.name}")
                deleted_channels.append(f"🔊 {vc.name}")
                await vc.delete(reason="Canal no presente en el backup — limpieza automática")
                await asyncio.sleep(0.5)
            except Exception as e:
                log.error(f"Error borrando canal de voz '{vc.name}': {e}")

    # ── Resumen / Log final ────────────────────────────────────────────────────
    ts = backup["timestamp"][:19].replace("T", " ")
    created_list = "\n".join(f"  • {c}" for c in created_channels) if created_channels else "  _ninguno_"
    deleted_list = "\n".join(f"  • {d}" for d in deleted_channels) if deleted_channels else "  _ninguno_"
    summary = (
        f"✅ **Restauración completada** — backup del `{ts} UTC`\n"
        f"📋 **Canales creados ({len(created_channels)}):**\n{created_list}\n"
        f"🗑️ **Canales extra eliminados ({len(deleted_channels)}):**\n{deleted_list}"
    )
    log.info(summary)
    if log_channel:
        try:
            # Discord tiene límite de 2000 caracteres por mensaje
            for chunk in [summary[i:i+1900] for i in range(0, len(summary), 1900)]:
                await log_channel.send(chunk)
        except Exception:
            pass

    restore_in_progress.discard(guild.id)


async def restore_messages(channel: discord.TextChannel, messages: list):
    try:
        webhook = await channel.create_webhook(name="BackupBot Restore")
    except Exception as e:
        log.error(f"No se pudo crear webhook en #{channel.name}: {e}")
        return

    for msg in messages:
        if not msg.get("content") and not msg.get("attachments"):
            continue
        try:
            content = f"**[{msg['author']}** | {msg['timestamp'][:10]}**]** {msg['content']}"
            await webhook.send(
                content=content[:2000],
                username=msg["author"][:80],
                allowed_mentions=discord.AllowedMentions.none()
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            log.error(f"Error enviando mensaje: {e}")

    try:
        await webhook.delete()
    except Exception:
        pass


def deserialize_overwrites(overwrites_data: list, guild: discord.Guild, role_map: dict) -> dict:
    result = {}
    for ow in overwrites_data:
        target = None
        if ow["type"] == "role":
            target = role_map.get(ow["id"]) or discord.utils.get(guild.roles, id=ow["id"])
        else:
            target = guild.get_member(ow["id"])
        if target:
            allow = discord.Permissions(ow["allow"])
            deny = discord.Permissions(ow["deny"])
            result[target] = discord.PermissionOverwrite.from_pair(allow, deny)
    return result


# ──────────────────────────────────────────────────────────────────────────────
#  DETECCIÓN DE NUKE
# ──────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    now = datetime.utcnow()

    if guild.id not in deleted_channels_tracker:
        deleted_channels_tracker[guild.id] = []

    tracker = deleted_channels_tracker[guild.id]
    tracker.append(now)
    tracker[:] = [t for t in tracker if (now - t).total_seconds() <= NUKE_WINDOW_SECONDS]

    log.warning(f"⚠️  Canal eliminado: #{channel.name} ({len(tracker)} en ventana de {NUKE_WINDOW_SECONDS}s)")

    if len(tracker) >= NUKE_THRESHOLD and guild.id not in restore_in_progress:
        restore_in_progress.add(guild.id)
        tracker.clear()
        log.critical(f"🚨 NUKE DETECTADO en '{guild.name}' — restaurando en 3s...")
        await asyncio.sleep(3)
        await auto_restore(guild)


async def auto_restore(guild: discord.Guild):
    backups = load_backups()
    guild_backup = backups.get(str(guild.id))
    if not guild_backup:
        log.error(f"❌ No hay backup para '{guild.name}'")
        restore_in_progress.discard(guild.id)
        return

    log_channel = None
    for ch in guild.text_channels:
        try:
            await ch.send("🚨 **NUKE DETECTADO** — Iniciando restauración automática...")
            log_channel = ch
            break
        except Exception:
            continue

    if not log_channel:
        try:
            log_channel = await guild.create_text_channel("backup-log")
        except Exception:
            pass

    await restore_guild(guild, guild_backup, log_channel)


# ──────────────────────────────────────────────────────────────────────────────
#  BACKUP AUTOMÁTICO
# ──────────────────────────────────────────────────────────────────────────────

@tasks.loop(minutes=BACKUP_INTERVAL_MINUTES)
async def auto_backup():
    backups = load_backups()
    for guild in bot.guilds:
        try:
            backup = await backup_guild(guild)
            backups[str(guild.id)] = backup
            save_backups(backups)
        except Exception as e:
            log.error(f"Error en backup automático de '{guild.name}': {e}")

@auto_backup.before_loop
async def before_auto_backup():
    await bot.wait_until_ready()


# ──────────────────────────────────────────────────────────────────────────────
#  COMANDOS
# ──────────────────────────────────────────────────────────────────────────────

@bot.command(name="backup")
@commands.has_permissions(administrator=True)
async def cmd_backup(ctx):
    msg = await ctx.send("⏳ Creando backup...")
    backups = load_backups()
    backup = await backup_guild(ctx.guild)
    backups[str(ctx.guild.id)] = backup
    save_backups(backups)
    ts = backup["timestamp"][:19].replace("T", " ")
    await msg.edit(content=f"✅ Backup completado!\n📅 `{ts} UTC` | 💬 `{len(backup['channels'])}` canales | 🎭 `{len(backup['roles'])}` roles")


@bot.command(name="restore")
@commands.has_permissions(administrator=True)
async def cmd_restore(ctx):
    backups = load_backups()
    guild_backup = backups.get(str(ctx.guild.id))
    if not guild_backup:
        await ctx.send("❌ No hay backup disponible para este servidor.")
        return
    ts = guild_backup["timestamp"][:19].replace("T", " ")
    ch_count = len(guild_backup.get("channels", []))
    role_count = len(guild_backup.get("roles", []))
    await ctx.send(
        f"🔄 **Restaurando último backup...**\n"
        f"📅 Fecha: `{ts} UTC`\n"
        f"💬 Canales en backup: `{ch_count}` | 🎭 Roles: `{role_count}`\n"
        f"⚠️ Los canales que **no estén en el backup** serán eliminados."
    )
    restore_in_progress.add(ctx.guild.id)
    await restore_guild(ctx.guild, guild_backup, ctx.channel)


@bot.command(name="backupinfo")
@commands.has_permissions(administrator=True)
async def cmd_backupinfo(ctx):
    backups = load_backups()
    g = backups.get(str(ctx.guild.id))
    if not g:
        await ctx.send("❌ No hay backup disponible.")
        return
    ts = g["timestamp"][:19].replace("T", " ")
    total_msgs = sum(len(c.get("messages", [])) for c in g["channels"])
    embed = discord.Embed(title="📦 Info del Backup", color=0x5865F2)
    embed.add_field(name="📅 Fecha (UTC)", value=f"`{ts}`", inline=False)
    embed.add_field(name="💬 Canales", value=str(len(g["channels"])), inline=True)
    embed.add_field(name="📁 Categorías", value=str(len(g["categories"])), inline=True)
    embed.add_field(name="🎭 Roles", value=str(len(g["roles"])), inline=True)
    embed.add_field(name="✉️ Mensajes", value=str(total_msgs), inline=True)
    embed.add_field(name="⏰ Auto-backup", value=f"Cada {BACKUP_INTERVAL_MINUTES} min", inline=True)
    await ctx.send(embed=embed)


@bot.command(name="nuketest")
@commands.has_permissions(administrator=True)
async def cmd_nuketest(ctx):
    await ctx.send("⚠️ Simulando detección de nuke...")
    await auto_restore(ctx.guild)


@bot.event
async def on_ready():
    log.info(f"✅ Bot online: {bot.user} ({bot.user.id})")
    log.info(f"📡 Servidores conectados: {[g.name for g in bot.guilds]}")
    auto_backup.start()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Necesitas permisos de **Administrador**.")
    elif not isinstance(error, commands.CommandNotFound):
        log.error(f"Error en comando: {error}")


if __name__ == "__main__":
    bot.run(TOKEN)
