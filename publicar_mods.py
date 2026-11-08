#!/usr/bin/env python3
r"""Publica (y mantiene actualizada) la lista de mods de Arma Reforger en un canal de Discord.

Uso:
    export DISCORD_WEBHOOK="https://discord.com/api/webhooks/ID/TOKEN"
    python publicar_mods.py             # publica, o edita los mensajes ya publicados
    python publicar_mods.py --refresh   # vuelve a leer la Workshop (descripciones / imágenes)
    python publicar_mods.py --dry       # no envía nada, solo muestra cómo quedaría
    python publicar_mods.py --force     # publica aunque no haya cambios
    (opcional) set MODS_CONFIG=C:\ruta\al\config.json del servidor
"""
import json, os, re, sys, time, html, hashlib
import requests

# ───────────────────────── Configuración ─────────────────────────
CONFIG_FILE = os.environ.get("MODS_CONFIG", "config.json")   # ruta al config.json REAL del servidor (o solo el bloque "mods")
CATS_FILE   = "categorias.json"      # categoría -> lista de "modId | nombre"
DESC_FILE   = "descripciones.json"   # opcional: {"modId": "descripción propia"}
STATE_FILE  = "state.json"           # IDs de los mensajes ya publicados
CACHE_FILE  = "cache.json"           # datos leídos de la Workshop
NOTIFIED_FILE = "pendientes_avisados.json"   # mods nuevos de los que ya se avisó
HASH_FILE   = "ultimo_hash.txt"      # huella de lo último publicado (para no publicar si no hubo cambios)
ESTILO      = "lista"                # "lista" (compacto) o "tarjetas" (un embed por mod, con imagen)

WORKSHOP = "https://reforger.armaplatform.com/workshop/"
HEADERS  = {"User-Agent": "Mozilla/5.0 (mod-list-bot)"}
COLORS   = [0x5865F2, 0x57F287, 0xFEE75C, 0xEB459E, 0xED4245, 0x3498DB, 0xE67E22, 0x1ABC9C, 0x9B59B6]
def env(name, default=""):
    """Lee una variable de entorno sin espacios ni saltos de línea (típico error al pegar claves)."""
    return "".join((os.environ.get(name) or default).split())


WEBHOOK  = env("DISCORD_WEBHOOK") or None
ADMIN_WEBHOOK = env("DISCORD_ADMIN_WEBHOOK") or None   # canal PRIVADO de administradores (opcional)
SIN_CAT  = "🆕 Sin categoría"

LIST_DESC_LEN, CARD_DESC_LEN = 130, 200
MAX_EMBEDS, MAX_CHARS, MAX_EMBED_DESC = 10, 5500, 3800   # límites de Discord con margen


# ───────────────────────── Utilidades ─────────────────────────
def load_json(path, default=None):
    """Lee un .json; tolera comas sobrantes y, si hay un error, indica la línea exacta."""
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        try:
            data = json.loads(_strip_trailing_commas(_strip_comments(txt)))
            print(f"Aviso: {path} tiene comas sobrantes o comentarios; se leyó igual. Conviene corregirlo.")
            return data
        except json.JSONDecodeError:
            lines = txt.splitlines()
            a, b = max(0, e.lineno - 3), min(len(lines), e.lineno + 1)
            ctx = "\n".join(f"  línea {k + 1}: {lines[k]}" for k in range(a, b))
            sys.exit(
                f"El archivo {path} tiene un error de formato: {e.msg} (línea {e.lineno}, columna {e.colno}).\n"
                f"{ctx}\n"
                f"Revisá esa línea y la anterior: casi siempre falta una coma al final de la línea anterior."
            )


def fetch_config_text():
    """Si hay PANEL_API_KEY lee el config.json desde el panel del hosting (API de Pterodactyl);
    si no, lo lee del archivo local CONFIG_FILE."""
    key = env("PANEL_API_KEY")
    if not key:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return f.read()
    base = env("PANEL_URL").rstrip("/")
    server = env("PANEL_SERVER")
    if not base or not server:
        sys.exit("Falta PANEL_URL o PANEL_SERVER (ver publicar_auto.bat)")
    r = requests.get(
        f"{base}/api/client/servers/{server}/files/contents",
        params={"file": env("PANEL_FILE", "/config.json")},
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code in (401, 403, 404):
        causa = {
            401: "El panel rechazó la API key (401): no la reconoce, venció o fue borrada/regenerada.",
            403: "La API key no tiene permiso para leer archivos de este servidor (403).",
            404: "El panel no encontró el servidor o el archivo (404). Revisá PANEL_SERVER y PANEL_FILE.",
        }[r.status_code]
        detalle = re.sub(r"\s+", " ", r.text or "")[:200].replace(key, "***")   # mensaje propio del panel, sin la clave
        sys.exit(f"{causa}\nRespuesta del panel: {detalle or '(vacía)'}")
    r.raise_for_status()
    return r.text


def _strip_comments(txt):
    """Quita comentarios // y /* */ (sin tocar el texto entre comillas)."""
    out, i, n, in_str = [], 0, len(txt), False
    while i < n:
        c = txt[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(txt[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if txt.startswith("//", i):
            j = txt.find("\n", i); i = n if j == -1 else j; continue
        if txt.startswith("/*", i):
            j = txt.find("*/", i + 2); i = n if j == -1 else j + 2; continue
        out.append(c); i += 1
    return "".join(out)


def _strip_trailing_commas(txt):
    """Quita comas sobrantes antes de } o ] (sin tocar el texto entre comillas)."""
    out, i, n, in_str = [], 0, len(txt), False
    while i < n:
        c = txt[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(txt[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True
        elif c == ",":
            k = i + 1
            while k < n and txt[k] in " \t\r\n":
                k += 1
            if k < n and txt[k] in "}]":
                i += 1; continue
        out.append(c); i += 1
    return "".join(out)


def _mask(line):
    """Muestra solo la estructura de una línea (llaves, comas, comillas), ocultando el contenido."""
    return re.sub(r'[^{}\[\],:"/\s]+', "x", line)


def parse_mods(txt):
    """Acepta: lista de mods, config completa del servidor, o el fragmento '"mods": [ ... ],'.
    Tolera comentarios y comas sobrantes. Si no puede leerlo, indica la línea SIN mostrar su contenido."""
    txt = txt.lstrip("\ufeff").strip().rstrip(",")
    if not txt.startswith(("{", "[")):
        txt = "{" + txt + "}"
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        try:
            data = json.loads(_strip_trailing_commas(_strip_comments(txt)))
            print("Aviso: el config.json tiene comentarios o comas sobrantes; se leyó igual.")
        except json.JSONDecodeError as e:
            lines = txt.splitlines()
            a, b = max(0, e.lineno - 3), min(len(lines), e.lineno + 1)
            ctx = "\n".join(f"  línea {k + 1}: {_mask(lines[k])}" for k in range(a, b))
            sys.exit(
                f"El config.json del panel no es un JSON válido: {e.msg} (línea {e.lineno}, columna {e.colno}).\n"
                f"Estructura de las líneas cercanas (contenido oculto por seguridad):\n{ctx}\n"
                f"Abrí ese archivo en el panel y revisá esa línea y la anterior."
            )
    if isinstance(data, dict):
        data = data.get("mods") or data.get("game", {}).get("mods", [])
    return data


def esc(s):
    """Escapa markdown de Discord (por los guiones bajos de nombres tipo COC_Banderas)."""
    return re.sub(r"([\\*_~`|\[\]>])", r"\\\1", s)


def short(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def meta(page, prop):
    for tag in re.findall(r"<meta[^>]+>", page):
        if f'property="{prop}"' in tag or f'name="{prop}"' in tag:
            m = re.search(r'content="([^"]*)"', tag)
            if m:
                return html.unescape(m.group(1))


def scrape(mod_id):
    r = requests.get(WORKSHOP + mod_id, headers=HEADERS, timeout=20)
    if r.status_code == 404:
        raise LookupError("404 en la Workshop: revisá que el modId sea correcto")
    r.raise_for_status()
    p = r.text
    return {
        "name": (meta(p, "og:title") or mod_id).replace(" - Arma Reforger", ""),
        "desc": meta(p, "og:description") or "",
        "image": meta(p, "og:image"),
    }


# ───────────────────────── Armado de embeds ─────────────────────────
def group_mods(mods, cats):
    """Reparte los mods según categorias.json. Devuelve (grupos, mods sin categoría)."""
    by_id = {m["modId"].upper(): m for m in mods}
    grouped = {cat: [] for cat in cats}
    assigned = set()
    for cat, entries in cats.items():
        for e in entries:
            mid = e[:16].upper()
            if mid in by_id:
                grouped[cat].append(by_id[mid]); assigned.add(mid)
            else:
                print(f"⚠ {e!r} está en categorias.json pero NO en la config del servidor")
    missing = [m for mid, m in by_id.items() if mid not in assigned]
    return grouped, missing


# Palabras clave para sugerir categoría (la clave es un trozo del nombre de la categoría).
# Un "$" al final = palabra completa. Es solo una SUGERENCIA: puede equivocarse.
KEYWORDS = {
    "Servidor": ["optimiz", "performance", "fps", "admin", "cleaner", "leaderboard", "killfeed",
                 "monitor", "discord", "limiter", "lod$", "lods$", "rendimiento"],
    "Game Master": ["game master", "gamemaster", "zeus", "curator"],
    "Comunidad": ["coc$", "jtf$"],
    "Mundo": ["ambien", "weather", "clima", "lighthouse", "intel$", "sabotage", "door", "puerta",
              "tree", "mission", "misi", "ambush", "convoy", "vegetation", "vegetaci"],
    "Combate": ["blood", "sangre", "explosi", "shrapnel", "suppress", "supresi", "muzzle", "ballistic",
                "balística", "grenade", "granada", "effect", "efecto", "bullet", "jam$", "ied$", "cqb", "bleed"],
    "Audio": ["sound", "sonido", "audio", "voice", "voz", "voces", "radio", "comms", "earplug"],
    "Vehículos": ["vehicle", "vehículo", "tank", "tanque", "truck", "btr", "apc$", "lav$", "boat",
                  "rhib", "motorcycle", "moto$", "driving", "brake", "jeep", "humvee"],
    "Aeronaves": ["heli", "aircraft", "plane$", "pilot", "flight", "vuelo", "jet$", "landing", "runway",
                  "aterriza", "avión", "avion", "parachute", "paraca", "cockpit", "rotor"],
    "Logística": ["logistic", "supply", "resupply", "cargo", "crate", "rappel", "rapel", "fastrope",
                  "fast rope", "rope$", "cuerda", "fire support", "airdrop"],
    "Movimiento": ["animation", "animaci", "ladder", "escalera", "climb", "sit$", "sentarse", "wirecut",
                   "breath", "respiraci", "stance", "vault"],
    "ACE": ["ace$", "medical", "médico", "medic$", "wound", "bandage", "captive", "prisioner", "surrender",
            "carrying", "tourniquet"],
    "Facciones": ["faction", "facci", "soldier", "soldado", "militia", "civilian", "civil", "ussr", "rangers",
                  "police", "policía", "dog$", "dogs$", "perro", "character", "personaje"],
    "Equipo": ["gear", "loadout", "uniform", "vest", "chaleco", "helmet", "casco", "glove", "guante",
               "beret", "boina", "beard", "barba", "glasses", "lentes", "balaclava", "night vision",
               "nvg", "backpack", "mochila", "clothing", "arsenal"],
    "Armas": ["weapon", "arma$", "armas$", "rifle", "pistol", "smg$", "submetralleta", "optic", "óptica",
              "scope", "sight", "ak-47", "ak47", "ak74", "mortar", "mortero", "launcher", "lanzador",
              "attachment", "suppressor", "silenciador", "magazine", "cargador"],
    "Librerías": ["framework", "library", "librería", "dependency", "dependencia", "core$"],
}


def _kw_pattern(kw):
    return r"\b" + re.escape(kw[:-1]) + r"\b" if kw.endswith("$") else r"\b" + re.escape(kw)


def suggest_category(text, cats):
    """Devuelve la categoría (clave de categorias.json) con más palabras clave en el texto, o None."""
    text = text.lower()
    best, best_score = None, 0
    for sub, kws in KEYWORDS.items():
        cat = next((c for c in cats if sub.lower() in c.lower()), None)
        if not cat:
            continue
        score = sum(1 for kw in kws if re.search(_kw_pattern(kw), text))
        if score > best_score:
            best, best_score = cat, score
    return best


def place_missing(grouped, missing, cats, cache, descs):
    """Ubica cada mod sin categoría en la categoría sugerida (o en 'Sin categoría'). Devuelve la lista de pendientes."""
    pending = []
    for m in missing:
        x = mod_info(m, cache, descs)
        cat = suggest_category(f"{x['name']} {x['desc']}", cats)
        grouped.setdefault(cat or SIN_CAT, []).append(m)
        pending.append({**x, "suggested": cat})
        print(f"⚠ Sin categoría: {x['id']} | {x['name']}  →  sugerida: {cat or 'ninguna'}")
    for k in [k for k, v in grouped.items() if not v]:
        del grouped[k]
    return pending


def _chunks(blocks, limit=1800):
    out, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > limit:
            out.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + b
    if cur:
        out.append(cur)
    return out


def notify_pending(pending, dry=False):
    """Avisa UNA sola vez, por el canal privado de admins, de cada mod nuevo sin clasificar."""
    notified = set(load_json(NOTIFIED_FILE, []) or [])
    current = {x["id"] for x in pending}
    keep = notified & current                      # los ya clasificados salen de la lista de avisados
    new = [x for x in pending if x["id"] not in notified]
    if new:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        base = f"https://github.com/{repo}/edit/main/" if repo else ""
        blocks = []
        for x in new:
            key = json.dumps(f"{x['id']} | {x['name']}", ensure_ascii=False)
            desc = json.dumps(short(x["desc"], 100), ensure_ascii=False)
            blocks.append(
                f"**{x['name']}** · `{x['id']}`\n"
                f"Categoría sugerida: **{x['suggested'] or 'ninguna (quedó en 🆕 Sin categoría)'}**\n"
                f"Descripción de la Workshop (en inglés): {desc}"
            )
        head = (f"## 🆕 {len(new)} mod(s) nuevo(s) sin clasificar\n"
                "Ya aparecen en la lista pública, en la categoría sugerida. Para confirmarla o cambiarla, "
                "abrí el formulario, pegá el ID del mod y elegí la categoría en la lista desplegable."
                + (f"\n👉 <https://github.com/{repo}/actions/workflows/clasificar.yml> (botón **Run workflow**)" if repo else ""))
        msgs = _chunks([head] + blocks)
        if dry or not ADMIN_WEBHOOK:
            print("Aviso a admins (no enviado: " + ("modo --dry" if dry else "falta DISCORD_ADMIN_WEBHOOK") + "):")
            for m in msgs:
                print(m.replace("\n", "\n   "))
        else:
            ok = True
            for m in msgs:
                r = send("POST", ADMIN_WEBHOOK, {"content": m, "allowed_mentions": {"parse": []}})
                ok = ok and r.ok
            if ok:
                keep |= {x["id"] for x in new}
                print(f"Aviso enviado a los administradores ({len(new)} mod/s).")
            else:
                print("⚠ No pude enviar el aviso a los administradores; se reintenta en la próxima corrida.")
    if not dry:
        with open(NOTIFIED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(keep), f)


def test_notification():
    """--test-aviso: manda un mensaje de prueba al canal de administradores."""
    if not ADMIN_WEBHOOK:
        sys.exit("Falta el Secret DISCORD_ADMIN_WEBHOOK")
    r = send("POST", ADMIN_WEBHOOK, {"content": "✅ Prueba: el aviso de mods sin clasificar funciona en este canal."})
    sys.exit("No pude enviar la prueba (" + str(r.status_code) + ")") if not r.ok else print("Mensaje de prueba enviado.")


def mod_info(m, cache, descs):
    mid = m["modId"].upper()
    info = cache.get(mid, {})
    return {
        "id": mid,
        "name": m.get("name") or info.get("name") or mid,
        "url": WORKSHOP + mid,
        "desc": descs.get(mid) or info.get("desc") or "Sin descripción.",
        "image": info.get("image"),
    }


def build_embeds(grouped, cache, descs):
    embeds = []
    for i, (cat, mods) in enumerate(grouped.items()):
        color = COLORS[i % len(COLORS)]
        title = f"{cat} ({len(mods)})"
        infos = [mod_info(m, cache, descs) for m in mods]

        if ESTILO == "tarjetas":
            embeds.append({"title": title, "color": color})
            for x in infos:
                e = {"title": x["name"][:250], "url": x["url"],
                     "description": short(x["desc"], CARD_DESC_LEN), "color": color}
                if x["image"]:
                    e["thumbnail"] = {"url": x["image"]}
                embeds.append(e)
        else:
            lines = [f"• **[{esc(x['name'])}]({x['url']})** — {esc(short(x['desc'], LIST_DESC_LEN))}"
                     for x in infos]
            chunk, size, part = [], 0, 0

            def flush():
                nonlocal chunk, size, part
                if chunk:
                    t = title if part == 0 else f"{cat} (cont.)"
                    embeds.append({"title": t, "description": "\n".join(chunk), "color": color})
                    chunk, size, part = [], 0, part + 1

            for line in lines:
                if chunk and size + len(line) + 1 > MAX_EMBED_DESC:
                    flush()
                chunk.append(line); size += len(line) + 1
            flush()
    return embeds


def make_batches(embeds):
    out, cur, size = [], [], 0
    for e in embeds:
        s = len(json.dumps(e, ensure_ascii=False))
        if cur and (len(cur) >= MAX_EMBEDS or size + s > MAX_CHARS):
            out.append(cur); cur, size = [], 0
        cur.append(e); size += s
    if cur:
        out.append(cur)
    return out


# ───────────────────────── Discord ─────────────────────────
def send(method, url, payload=None):
    while True:
        r = requests.request(method, url, json=payload, timeout=30)
        if r.status_code == 429:                       # rate limit: esperar y reintentar
            time.sleep(float(r.json().get("retry_after", 1)) + 0.3)
            continue
        time.sleep(0.7)
        return r


def read_hash():
    """Devuelve (huella, hora del último cambio publicado) guardadas en HASH_FILE."""
    if not os.path.exists(HASH_FILE):
        return None, None
    with open(HASH_FILE) as f:
        parts = f.read().split()
    sig = parts[0] if parts else None
    ts = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return sig, ts


def make_header(n, last_change=None):
    h = f"## 📦 Mods del servidor — {n} en total\nÚltima revisión: <t:{int(time.time())}:R>"
    if last_change:
        h += f" · Último cambio: <t:{last_change}:f>"
    return h


# ───────────────────────── Main ─────────────────────────
def main():
    dry, refresh = "--dry" in sys.argv, "--refresh" in sys.argv
    if "--test-aviso" in sys.argv:
        test_notification(); return
    if not dry and not WEBHOOK:
        sys.exit("Falta la variable de entorno DISCORD_WEBHOOK")

    mods = parse_mods(fetch_config_text())
    if not mods:
        sys.exit('No encontré mods en la config del servidor: no se publica nada.')
    cats = load_json(CATS_FILE, {})
    descs = {k[:16].upper(): v for k, v in (load_json(DESC_FILE, {}) or {}).items()}
    cache = load_json(CACHE_FILE, {})

    # 1) Leer la Workshop (solo mods nuevos, salvo --refresh)
    pending = [m for m in mods if refresh or m["modId"].upper() not in cache]
    if pending:
        print(f"Leyendo {len(pending)} mod(s) de la Workshop…")
    for m in pending:
        mid = m["modId"].upper()
        try:
            cache[mid] = scrape(mid)
            print(f"  ✓ {mid} {cache[mid]['name']}")
            time.sleep(1)
        except Exception as ex:
            print(f"  ⚠ {mid} ({m.get('name', '')}): {ex}")
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)

    # 2) Armar mensajes
    grouped, missing = group_mods(mods, cats)
    pending = place_missing(grouped, missing, cats, cache, descs)
    notify_pending(pending, dry)
    batches = make_batches(build_embeds(grouped, cache, descs))
    header = make_header(len(mods), int(time.time()))

    if dry:
        for i, b in enumerate(batches, 1):
            print(f"Mensaje {i}: {len(b)} embed(s), {len(json.dumps(b, ensure_ascii=False))} chars")
        print("\nEjemplo del primer embed:\n" + json.dumps(batches[0][0], ensure_ascii=False, indent=1)[:1500])
        return

    # 3) Publicar / editar (solo si algo cambió)
    state = load_json(STATE_FILE, [])
    new_state = []
    sig = hashlib.sha256(json.dumps([len(mods), batches], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    old_sig, old_ts = read_hash()
    if state and "--force" not in sys.argv and old_sig == sig:
        # Sin cambios: solo se refresca la hora de "Última revisión" (los embeds no se tocan)
        r = send("PATCH", f"{WEBHOOK}/messages/{state[0]}", {"content": make_header(len(mods), old_ts)})
        if r.status_code != 404:                      # 404 = borraron el mensaje: se republica abajo
            msg = "se actualizó la hora de revisión" if r.ok else f"no pude actualizar la hora de revisión ({r.status_code})"
            print(f"{time.strftime('%Y-%m-%d %H:%M')} Sin cambios: {msg}.")
            return

    def save():
        with open(STATE_FILE, "w") as f:
            json.dump(new_state + state[len(new_state):], f)

    for i, batch in enumerate(batches):
        payload = {"content": header if i == 0 else "", "embeds": batch}
        r = send("PATCH", f"{WEBHOOK}/messages/{state[i]}", payload) if i < len(state) else None
        if r is None or r.status_code == 404:          # no existe (o lo borraron): crear
            r = send("POST", f"{WEBHOOK}?wait=true", payload)
        if not r.ok:
            save()
            sys.exit(f"Discord respondió {r.status_code}: {r.text[:300]}")
        new_state.append(r.json()["id"])
        save()

    for old in state[len(batches):]:                   # borrar mensajes que sobran
        send("DELETE", f"{WEBHOOK}/messages/{old}")
    with open(STATE_FILE, "w") as f:
        json.dump(new_state, f)

    with open(HASH_FILE, "w") as f:
        f.write(f"{sig}\n{int(time.time())}\n")
    print(f"{time.strftime('%Y-%m-%d %H:%M')} ✔ {len(mods)} mods publicados en {len(batches)} mensaje(s)")


if __name__ == "__main__":
    main()
