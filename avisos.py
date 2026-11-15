"""Avisos al celular cuando una revisión de bajas/cuotas cambia los picks de un partido (15-nov).

Flujo (lo usa tareas/revision_bajas_cuotas.txt):
  1. Al empezar la revisión, ANTES de tocar bajas o re-simular:
       python avisos.py snap 2026-11-15
     guarda en .avisos/snap_<fecha>.json los picks con valor (value_bets) y las bajas de cada partido de la jornada.
  2. Después de bajas.py y/o build_site.py:
       python avisos.py diff 2026-11-15 --label "Revisión 18:15 ET"
     compara con la foto y, por cada partido que aún no empieza y cuyos picks o bajas cambiaron, añade un aviso a
     docs/data/avisos.json (título, texto y enlace al partido). Imprime lo que añadió; si nada cambió no toca el archivo.
  3. git add docs/data/avisos.json junto con el resto del PR. Al llegar a main, la acción de GitHub
     .github/workflows/avisos.yml manda por OneSignal los avisos nuevos a quienes los activaron en la web.

Otros:
  python avisos.py send --before <sha>   # (lo corre la acción) envía los avisos que no estaban en <sha>
  python avisos.py test ["texto"]        # (acción manual) aviso de prueba a todos los suscritos
  python avisos.py list [fecha]

La web también muestra estos avisos en la campana del encabezado, así que sirven aunque el usuario no active el push.
Sin snapshot, diff compara contra --ref (por defecto origin/main).
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "docs", "data")
PATH = os.path.join(DATA, "avisos.json")
CONF = os.path.join(DATA, "push.json")
SNAP_DIR = os.path.join(HERE, ".avisos")
SITE = "https://ncerva1.github.io/nba-predictor/"
KEEP_DAYS = 7
ET = timezone(timedelta(hours=-5))  # hora del este (sin horario de verano en noviembre-marzo)


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _git_json(ref, rel, default):
    try:
        out = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=HERE, capture_output=True, check=True).stdout
        return json.loads(out.decode("utf-8"))
    except (subprocess.CalledProcessError, ValueError):
        return default


def bet_key(b):
    return "|".join(str(b.get(k) or "") for k in ("kind", "player", "team", "stat", "side")) or b.get("label", "")


def state(index, bajas, date):
    """{gid: {matchup, tip, picks: {key: label}, outs: {player: status}}} de la jornada."""
    st = {}
    for g in index.get("games", []):
        if g.get("date") != date:
            continue
        st[g["id"]] = dict(away=g["away"], home=g["home"], tip=g.get("tip_et"),
                           picks={bet_key(b): b["label"] for b in g.get("value_bets") or []},
                           lines={bet_key(b): b.get("line") for b in g.get("value_bets") or []}, outs={})
    for x in (bajas or {}).get("list", []):
        if x.get("date") == date and x.get("gid") in st:
            st[x["gid"]]["outs"][x["player"]] = x.get("status", "out")
    return st


def current(date):
    return state(_load(os.path.join(DATA, "index.json"), {}), _load(os.path.join(DATA, "bajas.json"), {}), date)


def planb_picks(gid, players):
    """Etiquetas de los picks plan B (index.json games[].wo[equipo].planb) de esos jugadores, si siguen en la simulación."""
    g = next((x for x in _load(os.path.join(DATA, "index.json"), {}).get("games", []) if x.get("id") == gid), None)
    out = []
    for W in ((g or {}).get("wo") or {}).values():
        for who, A in (W.get("planb") or {}).items():
            if who in players:
                out += [b["label"] for b in A.get("picks", [])]
    return out


def snap(date):
    os.makedirs(SNAP_DIR, exist_ok=True)
    p = os.path.join(SNAP_DIR, f"snap_{date}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(current(date), f, ensure_ascii=False, indent=1)
    print(f"Foto de {date} guardada en {os.path.relpath(p, HERE)}")


def tip_dt(date, tip):
    try:
        h, m = map(int, (tip or "").split(":"))
        y, mo, d = map(int, date.split("-"))
        return datetime(y, mo, d, h, m, tzinfo=ET)
    except ValueError:
        return None


def hhmm(tip):
    try:
        h, m = map(int, tip.split(":"))
        return f"{(h - 1) % 12 + 1}:{m:02d} {'pm' if h >= 12 else 'am'}"
    except (ValueError, AttributeError):
        return tip or ""


def short(name):
    p = name.split()
    return " ".join(p[1:]) if len(p) > 1 else name


def diff(date, label="", ref="origin/main", force=False):
    snap_p = os.path.join(SNAP_DIR, f"snap_{date}.json")
    before = _load(snap_p, None)
    if before is None:
        print(f"(sin foto en {os.path.relpath(snap_p, HERE)}: comparo contra {ref})")
        before = state(_git_json(ref, "docs/data/index.json", {}), _git_json(ref, "docs/data/bajas.json", {}), date)
    after = current(date)
    now = datetime.now(timezone.utc)
    new = []
    for gid, a in after.items():
        b = before.get(gid)
        if not b:
            continue
        t = tip_dt(date, a.get("tip"))
        if t and t <= now and not force:
            continue  # ya empezó: nadie puede tomar el pick
        added = [a["picks"][k] for k in a["picks"] if k not in b["picks"]]
        removed = [b["picks"][k] for k in b["picks"] if k not in a["picks"]]
        moved = [a["picks"][k] for k in a["picks"] if k in b["picks"] and b["picks"][k] != a["picks"][k]]
        outs = [p for p, s in a["outs"].items() if b["outs"].get(p) != s and s == "out"]
        doubts = [p for p, s in a["outs"].items() if b["outs"].get(p) != s and s != "out"]
        back = [p for p in b["outs"] if p not in a["outs"]]
        if not (added or removed or moved or outs or doubts or back):
            continue
        parts = []
        if outs:
            parts.append("Bajas: " + ", ".join(short(p) for p in outs) + " OUT.")
        if doubts:
            parts.append("En duda: " + ", ".join(short(p) for p in doubts) + ".")
        if back:
            parts.append("Vuelve" + ("n" if len(back) > 1 else "") + ": " + ", ".join(short(p) for p in back) + ".")
        if added:
            parts.append(("Pick nuevo: " if len(added) == 1 else "Picks nuevos: ") + "; ".join(added) + ".")
        if moved:
            parts.append("Línea movida: " + "; ".join(moved) + ".")
        if removed:
            parts.append(("Sale: " if len(removed) == 1 else "Salen: ") + "; ".join(removed) + ".")
        pb = planb_picks(gid, outs + doubts)  # picks plan B de quien pasó a OUT o a duda (si no se re-simuló aún)
        if pb:
            parts.append("Plan B: " + "; ".join(pb[:3]) + ".")
        if not (added or removed or moved or pb):
            parts.append("Los picks del modelo no cambian.")
        body = " ".join(parts)
        if len(body) > 230:
            body = body[:227].rstrip() + "…"
        title = f"{a['away']} @ {a['home']} ({hhmm(a.get('tip'))} ET): " + ("cambian los picks" if (added or removed or moved)
                                                                           else "picks plan B" if pb else "novedades de bajas")
        at = now.isoformat(timespec="minutes")
        aid = hashlib.sha1(f"{gid}|{at}|{body}".encode()).hexdigest()[:16]
        new.append(dict(id=aid, at=at, date=date, gid=gid, title=title, body=body, label=label, url=SITE + "#" + gid,
                        added=added, removed=removed, moved=moved, outs=outs, doubts=doubts, back=back,
                        planb=pb, picks_changed=bool(added or removed or moved or pb)))
    if not new:
        print("Sin cambios de picks ni bajas en partidos por empezar: no hay aviso.")
        return []
    data = _load(PATH, {"list": []})
    cut = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    data["list"] = [x for x in data.get("list", []) if x.get("date", "") >= cut] + new
    data["updated"] = now.isoformat(timespec="minutes")
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    for x in new:
        print(f"AVISO {x['title']}\n  {x['body']}")
    return new


# ---------- envío (acción de GitHub) ----------

def _post(payload, key):
    req = urllib.request.Request("https://api.onesignal.com/notifications", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Key {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def push(title, body, url, ident):
    app = (_load(CONF, {}) or {}).get("onesignal_app_id") or os.environ.get("ONESIGNAL_APP_ID")
    key = os.environ.get("ONESIGNAL_REST_API_KEY")
    if not app or not key:
        print("Falta onesignal_app_id en docs/data/push.json o el secreto ONESIGNAL_REST_API_KEY: no se envía.")
        return False
    uid = hashlib.md5(ident.encode()).hexdigest()
    payload = dict(app_id=app, target_channel="push", included_segments=["Total Subscriptions"],
                   headings={"en": title, "es": title}, contents={"en": body, "es": body}, web_url=url,
                   chrome_web_icon=SITE + "icons/icon-192.png", web_push_topic=ident.split("|")[0][:60],
                   idempotency_key=f"{uid[:8]}-{uid[8:12]}-4{uid[13:16]}-a{uid[17:20]}-{uid[20:32]}")
    r = _post(payload, key)
    print(f"Enviado: {title} → {r}")
    return True


def send(before):
    old = {x["id"] for x in _git_json(before, "docs/data/avisos.json", {}).get("list", [])} if before else set()
    now = datetime.now(timezone.utc)
    n = 0
    for x in _load(PATH, {}).get("list", []):
        if x["id"] in old:
            continue
        t = tip_dt(x["date"], next((g.get("tip_et") for g in _load(os.path.join(DATA, "index.json"), {}).get("games", [])
                                    if g["id"] == x["gid"]), None))
        if t and t <= now:
            print(f"Omitido (ya empezó): {x['title']}")
            continue
        push(x["title"], x["body"], x["url"], x["gid"] + "|" + x["id"])
        n += 1
    print(f"{n} aviso(s) nuevos procesados.")


def main(a):
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = a[0]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d
    if cmd == "snap":
        snap(a[1])
    elif cmd == "diff":
        diff(a[1], label=opt("--label", ""), ref=opt("--ref", "origin/main"), force="--force" in a)
    elif cmd == "send":
        send(opt("--before"))
    elif cmd == "test":
        msg = a[1] if len(a) > 1 else "Prueba: así te llegará un aviso cuando cambien los picks de un partido."
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        push("NBA Predictor", msg, SITE, "prueba|" + stamp)
    elif cmd == "list":
        for x in _load(PATH, {}).get("list", []):
            if len(a) < 2 or x["date"] == a[1]:
                print(f"{x['at']} {x['title']}\n  {x['body']}")
    else:
        sys.exit(f"Comando desconocido: {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
