import os
import asyncio
import logging
import sqlite3
import re
import threading
from html import unescape
from http.server import HTTPServer, BaseHTTPRequestHandler
import feedparser
from bs4 import BeautifulSoup
import nest_asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# Servidor de mantenimiento para Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot activo 24/7")

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()

nest_asyncio.apply()

# --- CONFIGURACIÓN ---
TOKEN = "8098653016:AAH4NrQqaC9gvGcuvMCJ5toZaqiA-CVp9bY"
FEED_URL = "https://contrataciondelestado.es/syndication/syndication_pge/licitaciones.atom"
CHECK_INTERVAL_SECONDS = 180
DB_PATH = "licitaciones_eventos.db"

PALABRAS_EVENTOS = [
    r"\bevento", r"\beventos", r"\borquesta", r"\bescenario", r"\bsonido",
    r"\biluminaci[oó]n", r"\bluces\b", r"\bfiestas?\b", r"\bfestejos?\b",
    r"\bconcierto", r"\bferias?\b", r"\bcarpas?\b", r"\bpirotecnia\b",
    r"\bcatering\b", r"\bpasacalles\b", r"\bcabalgata\b", r"\bespect[aá]culo",
    r"\banimaci[oó]n\b", r"\bmegafon[ií]a\b", r"\bpantalla\s+led\b",
    r"\bhalloween\b", r"\bnochevieja\b", r"\bnavide[ñn]o", r"\bnavidad\b",
    r"\bcarnaval\b", r"\breyes\b", r"\bmercado\b", r"\bl[uú]dico\b",
    r"\bcelebraci[oó]n\b", r"\btardeo\b", r"\bcampamento\b",
    r"\b79952000\b", r"\b79953000\b", r"\b79954000\b", r"\b92300000\b"
]


logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS enviadas (id TEXT PRIMARY KEY, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS suscriptores (chat_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

def guardar_licitacion(lic_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO enviadas (id) VALUES (?)", (lic_id,))
    conn.commit()
    conn.close()

def es_enviada(lic_id) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM enviadas WHERE id = ?", (lic_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def registrar_suscriptor(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO suscriptores (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def eliminar_suscriptor(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM suscriptores WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def obtener_suscriptores():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM suscriptores")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def limpiar_texto(html_text):
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    text = unescape(soup.get_text(separator=" "))
    return re.sub(r'\s+', ' ', text).strip()

def es_relacionado_con_eventos(texto):
    for patron in PALABRAS_EVENTOS:
        if re.search(patron, texto, re.IGNORECASE):
            return True
    return False

async def revisar_eventos_job(bot):
    suscriptores = obtener_suscriptores()
    if not suscriptores:
        return

    feed = feedparser.parse(FEED_URL)
    if not feed.entries:
        return

    nuevas_eventos = []
    for entry in reversed(feed.entries):
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id or es_enviada(entry_id):
            continue

        titulo = limpiar_texto(entry.get("title", ""))
        resumen = limpiar_texto(entry.get("summary", ""))
        contenido_completo = f"{titulo} {resumen}"

        if es_relacionado_con_eventos(contenido_completo):
            nuevas_eventos.append({
                "id": entry_id,
                "titulo": titulo,
                "resumen": resumen[:300] + ("..." if len(resumen) > 300 else ""),
                "link": entry.get("link", "https://contrataciondelestado.es")
            })
        else:
            guardar_licitacion(entry_id)

    for lic in nuevas_eventos:
        mensaje = (
            "🎉 <b>NUEVA LICITACIÓN DE EVENTOS</b>\n\n"
            f"📌 <b>Objeto:</b> {lic['titulo']}\n\n"
            f"📝 <b>Detalles:</b> {lic['resumen']}\n"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Ver expediente en el Estado", url=lic['link'])]
        ])

        for chat_id in suscriptores:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=mensaje,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                    disable_web_page_preview=True
                )
            except Exception:
                pass

        guardar_licitacion(lic["id"])

async def bucle_segundo_plano(app):
    await asyncio.sleep(3)
    while True:
        try:
            await revisar_eventos_job(app.bot)
        except Exception as e:
            logging.error(f"Error en revisión automática: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    registrar_suscriptor(chat_id)
    texto = (
        "<b>🎪 Bot de Licitaciones de Eventos Activado</b>\n\n"
        "Te avisaré automáticamente cada vez que un Ayuntamiento o entidad pública "
        "publique un contrato sobre <b>fiestas, sonido, escenarios, conciertos o eventos</b>.\n\n"
        "Comandos:\n"
        "• /buscar <i>[término]</i> - Buscar manualmente entre las recientes.\n"
        "• /stop - Pausar alertas automáticas."
    )
    await update.message.reply_text(texto, parse_mode="HTML")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    eliminar_suscriptor(chat_id)
    await update.message.reply_text("🔕 Alertas pausadas.")

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("⚠️ Ejemplo: <code>/buscar Sevilla</code>", parse_mode="HTML")
        return

    feed = feedparser.parse(FEED_URL)
    coincidencias = []

    for entry in feed.entries:
        titulo = limpiar_texto(entry.get("title", ""))
        resumen = limpiar_texto(entry.get("summary", ""))
        texto = f"{titulo} {resumen}"

        if re.search(re.escape(query), texto, re.IGNORECASE) and es_relacionado_con_eventos(texto):
            coincidencias.append((titulo, entry.get("link", "#")))

    if not coincidencias:
        await update.message.reply_text(f"❌ Sin contratos de eventos recientes para: <b>{query}</b>", parse_mode="HTML")
        return

    respuesta = f"<b>📋 Eventos encontrados para '{query}':</b>\n\n"
    for tit, lk in coincidencias[:5]:
        respuesta += f"• <b>{tit[:120]}...</b>\n🔗 <a href='{lk}'>Ver expediente</a>\n\n"

    await update.message.reply_text(respuesta, parse_mode="HTML", disable_web_page_preview=True)

async def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("buscar", buscar))

    asyncio.create_task(bucle_segundo_plano(app))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
