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

# Servidor de mantenimiento para Render (mantiene el bot activo 24/7)
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

# 1. Filtro de Tipo de Evento
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

# 2. Filtro de Ubicación (Alicante y Murcia)
UBICACIONES_FILTRO = [
    r"alicante", r"alacant", r"murcia", r"diputaci[oó]n", r"regi[oó]n de murcia",
    r"orihuela", r"torrevieja", r"elche", r"\belx\b", r"benidorm", r"alcoy", r"alcoi",
    r"elda", r"san vicente", r"d[eé]nia", r"denia", r"villena", r"petrer", r"santa pola",
    r"villajoyosa", r"vila joiosa", r"j[aá]vea", r"x[aà]bia", r"calpe", r"calp",
    r"crevillent", r"campello", r"altea", r"\bibi\b", r"mutxamel", r"muchamiel",
    r"novelda", r"aspe", r"sant joan", r"pilar de la horadada", r"almorad[ií]",
    r"callosa", r"guardamar", r"rojales", r"san fulgencio", r"albatera", r"benij[oó]far",
    r"redov[aá]n", r"\bcox\b", r"bigastro", r"catral", r"dolores", r"rafal",
    r"benej[uú]zar", r"los montesinos", r"san isidro", r"algorfa", r"formentera",
    r"granja de rocamora", r"jacarilla", r"daya nueva", r"daya vieja", r"castalla",
    r"onil", r"mon[oó]var", r"sax", r"biar", r"pego", r"pedreguer", r"benissa",
    r"cartagena", r"lorca", r"molina de segura", r"alcantarilla", r"torre-pacheco",
    r"torre pacheco", r"[aá]guilas", r"cieza", r"yecla", r"san javier", r"totana",
    r"mazarr[oó]n", r"caravaca", r"jumilla", r"san pedro del pinatar", r"alhama",
    r"las torres de cotillas", r"la uni[oó]n", r"archena", r"mula", r"los alc[aá]zares",
    r"ceheg[ií]n", r"fuente [aá]lamo", r"santomera", r"puerto lumbreras", r"abar[aá]n",
    r"bullas", r"beniel", r"calasparra", r"fortuna", r"alguazas", r"moratalla",
    r"lorqu[ií]", r"blanca", r"librilla", r"pliego", r"campos del rio", r"oj[oó]s"
]

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
chats_suscritos = set()

def inicializar_bd():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licitaciones (
            id TEXT PRIMARY KEY,
            titulo TEXT,
            link TEXT,
            fecha TEXT
        )
    """)
    conn.commit()
    conn.close()

def guardar_licitacion(lic_id, titulo, link, fecha):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO licitaciones VALUES (?, ?, ?, ?)", (lic_id, titulo, link, fecha))
        conn.commit()
        guardado = True
    except sqlite3.IntegrityError:
        guardado = False
    conn.close()
    return guardado

def limpiar_html(texto_html):
    if not texto_html:
        return ""
    soup = BeautifulSoup(texto_html, "html.parser")
    texto = soup.get_text(separator=" ")
    return unescape(texto).strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chats_suscritos.add(chat_id)
    await update.message.reply_text(
        "¡Hola! Bot de Licitaciones configurado para **Alicante y Murcia** 🏛️✨\n\n"
        "Te avisaré automáticamente de licitaciones en estado **PUBLICADA** sobre:\n"
        "• Eventos, Fiestas, Halloween, Navidad, Orquestas, Luces, Escenarios, etc.\n\n"
        "Comandos disponibles:\n"
        "• /buscar [palabra] - Búsqueda manual puntual"
    )

async def buscar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Por favor, escribe algo para buscar. Ejemplo: `/buscar orihuela`")
        return
    
    await update.message.reply_text(f"🔍 Buscando licitaciones recientes sobre: *{query}*...")
    feed = feedparser.parse(FEED_URL)
    encontradas = 0
    
    for entry in feed.entries:
        titulo = limpiar_html(entry.get("title", ""))
        summary = limpiar_html(entry.get("summary", ""))
        link = entry.get("link", "")
        texto_completo = f"{titulo} {summary}"
        
        if query.lower() in texto_completo.lower():
            encontradas += 1
            texto_msg = f"🔎 **Resultado encontrado:**\n\n📌 **{titulo}**\n\n🔗 [Ver Licitación]({link})"
            await update.message.reply_text(texto_msg, parse_mode="Markdown", disable_web_page_preview=True)
            if encontradas >= 5:
                break
                
    if encontradas == 0:
        await update.message.reply_text("No se han encontrado licitaciones recientes con ese término.")

async def revisar_feed(context: ContextTypes.DEFAULT_TYPE):
    feed = feedparser.parse(FEED_URL)
    for entry in feed.entries:
        lic_id = entry.get("id", entry.get("link"))
        titulo = limpiar_html(entry.get("title", ""))
        summary = limpiar_html(entry.get("summary", ""))
        link = entry.get("link", "")
        fecha = entry.get("published", "")
        
        texto_completo = f"{titulo} {summary}".lower()
        
        # 1. Comprobar TIPO DE EVENTO
        coincide_evento = any(re.search(p, texto_completo, re.IGNORECASE) for p in PALABRAS_EVENTOS)
        
        # 2. Comprobar UBICACIÓN (Alicante / Murcia)
        coincide_ubicacion = any(re.search(u, texto_completo, re.IGNORECASE) for u in UBICACIONES_FILTRO)
        
        # 3. Comprobar ESTADO (Solo PUBLICADA o EN PLAZO)
        es_publicada = "publicada" in texto_completo or "en plazo" in texto_completo
        
        # Solo guarda y notifica si se cumplen las TRES condiciones a la vez
        if coincide_evento and coincide_ubicacion and es_publicada:
            if guardar_licitacion(lic_id, titulo, link, fecha):
                mensaje = (
                    f"🎉 **¡Nueva Licitación Publicada!**\n\n"
                    f"📌 **{titulo}**\n\n"
                    f"📍 *Alicante / Murcia*\n"
                    f"🔗 [Ver detalles en la plataforma]({link})"
                )
                for chat_id in chats_suscritos:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=mensaje,
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                    except Exception as e:
                        logging.error(f"Error enviando mensaje a {chat_id}: {e}")

async def post_init(application: Application):
    job_queue = application.job_queue
    job_queue.run_repeating(revisar_feed, interval=CHECK_INTERVAL_SECONDS, first=10)

def main():
    inicializar_bd()
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("buscar", buscar_manual))
    
    logging.info("Bot iniciado y funcionando correctamente...")
    application.run_polling()

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    asyncio.run(main())
