#!/usr/bin/env python3
"""Clasifica un mod desde el formulario de GitHub (lista desplegable de categorías).
Lo ejecuta el workflow clasificar.yml con las variables MOD_ID, CATEGORIA y DESCRIPCION."""
import json, os, re, sys
import publicar_mods as P

def norm(s):
    return re.sub(r"[\W_]+", "", s, flags=re.UNICODE).lower()

def main():
    ids = [x[:16].upper() for x in re.split(r"[\s,;]+", os.environ.get("MOD_ID", "").strip()) if x]
    cat_in = os.environ.get("CATEGORIA", "").strip()
    desc = os.environ.get("DESCRIPCION", "").strip()
    if not ids:
        sys.exit("Falta el ID del mod (16 letras/números, está en el aviso de #admin-mods).")
    if len(ids) > 1 and desc:
        sys.exit("La descripción solo se puede usar con un mod por vez. Dejala vacía o cargá un mod por corrida.")

    cats = P.load_json(P.CATS_FILE, {})
    cat = next((c for c in cats if c == cat_in or norm(c) == norm(cat_in)), None)
    if not cat:
        sys.exit(f"La categoría '{cat_in}' no existe en categorias.json. Categorías: {', '.join(cats)}")

    mods = P.parse_mods(P.fetch_config_text())
    names = {m["modId"].upper(): m.get("name", "") for m in mods}
    cache = P.load_json(P.CACHE_FILE, {}) or {}
    descs = P.load_json(P.DESC_FILE, {}) or {}

    for mid in ids:
        if mid not in names:
            sys.exit(f"El mod {mid} no está en el config.json del servidor. Revisá el ID.")
        name = (cache.get(mid) or {}).get("name") or names[mid] or mid
        key = f"{mid} | {name}"
        for c in cats:                                        # sacarlo de cualquier otra categoría
            cats[c] = [x for x in cats[c] if x[:16].upper() != mid]
        cats[cat].insert(0, key)
        if desc:
            descs = {k: v for k, v in descs.items() if k[:16].upper() != mid}
            descs = {key: desc, **descs}
        print(f"✔ {key}  →  {cat}")

    with open(P.CATS_FILE, "w", encoding="utf-8") as f:
        json.dump(cats, f, ensure_ascii=False, indent=2)
    with open(P.DESC_FILE, "w", encoding="utf-8") as f:
        json.dump(descs, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
