# 🛡️ Discord Backup Bot

Bot de backup automático con detección de nukes y restauración automática.
Diseñado para desplegarse en **Railway** con uptime 24/7.

---

## 📁 Estructura del proyecto

```
discord_backup_bot/
├── bot.py            # Código principal del bot
├── requirements.txt  # Dependencias Python
├── Procfile          # Comando de arranque para Railway
├── railway.toml      # Configuración de Railway
├── .env.example      # Plantilla de variables de entorno
├── .gitignore        # Evita subir secretos y datos
└── README.md
```

---

## 🚀 Deploy en Railway (paso a paso)

### 1. Preparar el bot en Discord

1. Ve a https://discord.com/developers/applications
2. **New Application** → ponle nombre → ve a la pestaña **Bot**
3. Activa estos **Privileged Gateway Intents**:
   - ✅ `SERVER MEMBERS INTENT`
   - ✅ `MESSAGE CONTENT INTENT`
4. En **OAuth2 → URL Generator**, selecciona:
   - Scope: `bot`
   - Permisos: `Administrator`
5. Usa el enlace generado para invitar el bot a tu servidor
6. Copia el **Token** del bot (lo necesitas en el paso 3)

### 2. Subir el código a GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

> ⚠️ Nunca hagas commit del `.env` ni del `server_backup.json`.
> El `.gitignore` ya los excluye, pero verifica antes de hacer push.

### 3. Crear proyecto en Railway

1. Ve a https://railway.app y logueate con GitHub
2. **New Project → Deploy from GitHub repo** → selecciona tu repo
3. Railway detectará automáticamente el `Procfile`
4. Ve a **Variables** y agrega:

| Variable | Valor |
|---|---|
| `DISCORD_TOKEN` | Tu token del bot |
| `BACKUP_INTERVAL` | `30` (opcional) |
| `MAX_MESSAGES` | `500` (opcional) |
| `NUKE_THRESHOLD` | `3` (opcional) |
| `NUKE_WINDOW` | `10` (opcional) |

5. **Deploy** — el bot arrancará automáticamente ✅

---

## 📋 Comandos del bot

| Comando | Descripción | Permiso |
|---|---|---|
| `!backup` | Backup manual inmediato | Admin |
| `!restore` | Restaurar desde último backup | Admin |
| `!backupinfo` | Ver info del último backup | Admin |
| `!nuketest` | Simular nuke (para probar) | Admin |

---

## 🔍 Cómo funciona la detección de nuke

El bot escucha `on_guild_channel_delete`. Si detecta **3+ canales eliminados en menos de 10 segundos**:

1. Espera 3s a que termine el nuke
2. Carga el último backup guardado
3. Recrea roles → categorías → canales → mensajes (via webhooks)
4. Reporta el estado en un canal disponible

---

## 📦 Qué guarda el backup

- ✅ Roles (nombre, color, permisos, posición)
- ✅ Categorías con sus permisos
- ✅ Canales de texto y voz con permisos
- ✅ Últimos N mensajes por canal (configurable)
- ❌ Archivos adjuntos (solo guarda la URL, que puede expirar)

---

## ⚠️ Notas importantes

- El rol del bot debe estar **por encima** de todos los roles que recreará
- En Railway el plan Hobby (~$5/mes) da uptime 24/7; el plan gratuito tiene límite de horas
- `server_backup.json` se guarda en disco del contenedor de Railway. Si el servicio se reinicia, el archivo persiste mientras el volumen esté montado. Para mayor seguridad, considera agregar un volume en Railway o exportar a una base de datos externa.
- Los mensajes restaurados aparecen con el nombre del autor original pero enviados por el bot (via webhook)
