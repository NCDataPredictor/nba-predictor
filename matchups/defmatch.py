"""Emparejamientos defensivos (pedido de Nicolas, 22-nov): quién marca a quién, con datos de nba.com
(boxscorematchupsv3 por partido, bajados desde el Chrome del PC con matchups/fetch_matchups.js).

Solo es una herramienta de análisis para la web: NO toca la simulación ni las probabilidades del modelo.

    python matchups/defmatch.py build [fecha ...]   # docs/data/defmatch/<id>.json de los partidos (todas si no hay fecha)
    python matchups/defmatch.py backtest [desde]    # acierto del defensor principal previsto (solo datos previos)

Datos:
  matchups/nba/<temporada>.json        {cols, players{id:nombre}, games{gameId:{d,a,h,st,pos,p[[off,def,sec,poss,pts,...]]}}}
  matchups/nba/<temporada>_pairs.json  (opcional) totales por pareja de la temporada anterior (leagueseasonmatchups)

Predicción de emparejamientos de un partido (equipo A ataca, B defiende):
  K[O,D] = afinidad de puesto (perfil del defensor: a qué puestos marca y con qué dispersión)
           × (1 + STAR_B·(cuánto más de lo normal marca D al mejor anotador rival), si O es el mejor anotador)
           × (1 + H2H_G·(parte del tiempo de O que lo marcó D en cruces previos))
  y un ajuste proporcional (Sinkhorn): cada atacante reparte el 100% de su tiempo y cada defensor marca en
  proporción a sus minutos proyectados. Resultado = % del tiempo de O marcado por cada D.
"""
import glob
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "docs", "data", "defmatch")
SLOT = {"PG": 1.0, "SG": 2.0, "SF": 3.0, "PF": 4.0, "C": 5.0, "G": 1.6, "F": 3.6, "G-F": 2.5, "F-G": 2.5, "F-C": 4.5, "C-F": 4.5}
NBA_POS = {"G": 1.6, "F": 3.6, "C": 5.0}
# parámetros del ajuste (elegidos con `backtest`, ver README)
SD_FLOOR, STAR_B, H2H_G, SINK_IT = 0.75, 1.5, 3.0, 30


def _secs(v):
    """MATCHUP_MIN de leagueseasonmatchups viene como "mm:ss" (o minutos decimales)."""
    if isinstance(v, str) and ":" in v:
        m, x = v.split(":")
        return int(m) * 60 + float(x)
    return float(v or 0) * 60


def season_of(date):
    y, m = int(date[:4]), int(date[5:7])
    y0 = y if m >= 8 else y - 1
    return f"{y0}-{str(y0 + 1)[2:]}"


def prior_season(s):
    y0 = int(s[:4]) - 1
    return f"{y0}-{str(y0 + 1)[2:]}"


def _key(n):
    import unicodedata
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    for x in (".", "'", ",", " jr", " sr", " iii", " ii", " iv"):
        n = n.replace(x, "")
    return " ".join(n.split())


class Store:
    """Datos de emparejamientos de una temporada + nombres y puestos de los jugadores."""

    def __init__(self, season):
        self.season = season
        p = os.path.join(HERE, "nba", f"{season}.json")
        self.raw = json.load(open(p)) if os.path.exists(p) else {"games": {}, "players": {}}
        ids = json.load(open(os.path.join(ROOT, "docs", "data", "player_ids.json")))
        self.id2name = {str(v): k for k, v in ids.items()}
        self.name2id = {_key(k): str(v) for k, v in ids.items()}
        for i, n in self.raw.get("players", {}).items():
            self.name2id.setdefault(_key(n), str(i))
        self.pos = self._positions()
        self.games = []  # [(gameId, fecha, a, h, starters, pos, rows)] por fecha
        for gid, g in sorted(self.raw["games"].items(), key=lambda kv: (kv[1]["d"], kv[0])):
            rows = [dict(zip(self.raw["cols"], r)) for r in g["p"]]
            for r in rows:
                r["off"], r["def"] = str(r["off"]), str(r["def"])
            self.games.append(dict(id=gid, d=g["d"], a=g["a"], h=g["h"], st={str(x) for x in g.get("st", [])},
                                   aw={str(x) for x in g.get("aw", [])},
                                   pos={str(k): v for k, v in g.get("pos", {}).items()}, rows=rows))
        for g in self.games:  # puesto nba.com de titulares como respaldo
            for i, v in g["pos"].items():
                self.pos.setdefault(i, NBA_POS.get(v, 3.0))
        pp = os.path.join(HERE, "nba", f"{prior_season(season)}_pairs.json")
        self.prior = json.load(open(pp)) if os.path.exists(pp) else None
        self.prior_idx = {}
        if self.prior:
            c = self.prior["cols"]
            io, idf = c.index("OFF_PLAYER_ID"), c.index("DEF_PLAYER_ID")
            self.prior_idx = {(str(r[io]), str(r[idf])): dict(zip(c, r)) for r in self.prior["rows"]}

    def name(self, i):
        return self.id2name.get(str(i)) or self.raw.get("players", {}).get(str(i)) or \
            (self.prior or {}).get("players", {}).get(str(i)) or str(i)

    def _positions(self):
        """Puesto de cada jugador (1=PG ... 5=C) según los slates del modelo (games/*.json, el más reciente)."""
        pos = {}
        for f in sorted(glob.glob(os.path.join(ROOT, "games", "*.json"))):
            if f.endswith("_results.json"):
                continue
            try:
                d = json.load(open(f))
            except Exception:
                continue
            for p in d.get("players", []):
                i = self.name2id.get(_key(p["name"]))
                if i and p.get("pos") in SLOT:
                    pos[i] = SLOT[p["pos"]]
        return pos


def team_split(g):
    """{id: equipo} de los jugadores del partido (aw = ids del visitante, guardado por fetch_matchups.js)."""
    side = g.get("side")
    if side is None:
        ids = {r["off"] for r in g["rows"]} | {r["def"] for r in g["rows"]}
        side = g["side"] = {i: (g["a"] if i in g["aw"] else g["h"]) for i in ids}
    return side


# ---------------------------------------------------------------- perfiles con los partidos previos a una fecha
def profiles(store, before, extra_pairs=True):
    """Perfil de cada defensor y de cada atacante con los partidos anteriores a `before`."""
    dsum = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # tiempo, Σt·puesto, Σt·puesto², t sobre el mejor anotador
    pair = defaultdict(float)     # (off, def) -> segundos esta temporada
    pstat = defaultdict(lambda: defaultdict(float))
    otime = defaultdict(float)    # off -> segundos totales
    ostats = defaultdict(lambda: defaultdict(float))
    dstats = defaultdict(lambda: defaultdict(float))
    pts_g = defaultdict(lambda: [0.0, 0])
    for g in store.games:
        if g["d"] >= before:
            continue
        side = team_split(g)
        gp = defaultdict(float)
        for r in g["rows"]:
            gp[r["off"]] += r["pts"]
        for o, v in gp.items():
            pts_g[o][0] += v
            pts_g[o][1] += 1
        # mejor anotador de cada equipo en ese partido según su media previa (o lo de ese partido)
        best = {}
        for o in gp:
            t = side.get(o)
            m = pts_g[o][0] / max(pts_g[o][1], 1)
            if t and (t not in best or m > best[t][1]):
                best[t] = (o, m)
        for r in g["rows"]:
            o, d, t = r["off"], r["def"], r["sec"]
            po = store.pos.get(o)
            otime[o] += t
            pair[(o, d)] += t
            ps = pstat[(o, d)]
            for k in ("sec", "poss", "pts", "fgm", "fga"):
                ps[k] += r.get(k, 0)
            ps["g"] += 1
            for k in ("poss", "pts", "fgm", "fga", "fg3m", "fg3a", "ast", "tov"):
                ostats[o][k] += r.get(k, 0)
                dstats[d][k] += r.get(k, 0)
            ostats[o]["sec"] += t
            dstats[d]["sec"] += t
            if po is not None:
                s = dsum[d]
                s[0] += t
                s[1] += t * po
                s[2] += t * po * po
                if best.get(side.get(o), (None,))[0] == o:
                    s[3] += t
        gsec = defaultdict(lambda: defaultdict(float))
        for r in g["rows"]:
            gsec[r["off"]][r["def"]] += r["sec"]
        for o in gp:
            ostats[o]["games"] += 1
            tot = sum(gsec[o].values())
            if tot >= 180:  # marca de ese partido: parte del 1.er defensor y defensores con 1+ min
                ostats[o]["conc"] += max(gsec[o].values()) / tot
                ostats[o]["ndef"] += sum(1 for v in gsec[o].values() if v >= 60)
                ostats[o]["gm"] += 1
    prof = {}
    for d, (t, a, b, star) in dsum.items():
        if t <= 0:
            continue
        mu = a / t
        sd = math.sqrt(max(b / t - mu * mu, 0))
        prof[d] = dict(mu=mu, sd=sd, star=star / t, t=t)
    prior = defaultdict(float)
    if extra_pairs and store.prior:
        cols = store.prior["cols"]
        io, idf, imin = cols.index("OFF_PLAYER_ID"), cols.index("DEF_PLAYER_ID"), cols.index("MATCHUP_MIN")
        for r in store.prior["rows"]:
            prior[(str(r[io]), str(r[idf]))] += _secs(r[imin])
    ppg = {o: v[0] / v[1] for o, v in pts_g.items() if v[1]}
    return dict(prof=prof, pair=pair, pstat=pstat, prior=prior, otime=otime, ostats=ostats, dstats=dstats, ppg=ppg)


STAR_BASE = 0.2  # parte "normal" del tiempo sobre el mejor anotador rival (1 de 5)


def predict(P, offense, defense, pos_of, star_o=None, sd_floor=None, star_b=None, h2h_g=None):
    """offense: [(id, minutos)] titulares que atacan; defense: [(id, minutos)] defensores posibles.
    Devuelve {off: {def: parte del tiempo}}."""
    sd_floor = SD_FLOOR if sd_floor is None else sd_floor
    star_b = STAR_B if star_b is None else star_b
    h2h_g = H2H_G if h2h_g is None else h2h_g
    if not offense or not defense:
        return {}
    K = []
    for o, _ in offense:
        po = pos_of(o)
        # cruces previos: parte del tiempo de O con cada defensor de esta defensa (esta temporada + anterior a la mitad)
        h = {d: P["pair"].get((o, d), 0) + 0.5 * P["prior"].get((o, d), 0) for d, _ in defense}
        ht = sum(h.values())
        row = []
        for d, m in defense:
            pf = P["prof"].get(d)
            pd_ = pos_of(d)
            mu = pf["mu"] if pf and pf["t"] > 600 else (pd_ if pd_ is not None else 3.0)
            if pf and pf["t"] <= 600 and pd_ is not None:  # poca muestra: mezcla con su puesto
                w = pf["t"] / 600
                mu = w * pf["mu"] + (1 - w) * pd_
            sd = max(pf["sd"] if pf and pf["t"] > 600 else 1.0, sd_floor)
            k = math.exp(-((po if po is not None else 3.0) - mu) ** 2 / (2 * sd * sd))
            if star_o == o and pf and pf["t"] > 600:
                k *= max(0.2, 1 + star_b * (pf["star"] - STAR_BASE))
            if ht > 120:
                k *= 1 + h2h_g * h[d] / ht
            row.append(max(k, 1e-6))
        K.append(row)
    n, m = len(offense), len(defense)
    col_t = [mm for _, mm in defense]
    s = sum(col_t) or 1
    col_t = [c / s * n for c in col_t]
    for _ in range(SINK_IT):
        for i in range(n):
            rs = sum(K[i])
            K[i] = [x / rs for x in K[i]]
        for j in range(m):
            cs = sum(K[i][j] for i in range(n))
            if cs > 0:
                f = col_t[j] / cs
                for i in range(n):
                    K[i][j] *= f
    for i in range(n):
        rs = sum(K[i])
        K[i] = [x / rs for x in K[i]]
    return {o: {d: K[i][j] for j, (d, _) in enumerate(defense)} for i, (o, _) in enumerate(offense)}


# ---------------------------------------------------------------- backtest
def actual_shares(g):
    """{off: {def: parte de su tiempo}} y minutos de cada defensor (tiempo marcando) en un partido."""
    tot = defaultdict(float)
    sh = defaultdict(lambda: defaultdict(float))
    dt = defaultdict(float)
    for r in g["rows"]:
        tot[r["off"]] += r["sec"]
        sh[r["off"]][r["def"]] += r["sec"]
        dt[r["def"]] += r["sec"]
    return {o: {d: v / tot[o] for d, v in ds.items()} for o, ds in sh.items() if tot[o] > 0}, dt


def backtest(store, start="2026-11-01", **kw):
    hit = base_hit = n = 0
    ll = 0.0
    top2 = 0
    cache = {}
    for g in store.games:
        if g["d"] < start:
            continue
        P = cache.get(g["d"]) or cache.setdefault(g["d"], profiles(store, g["d"]))
        side = team_split(g)
        act, dt = actual_shares(g)
        for t_off, t_def in ((g["a"], g["h"]), (g["h"], g["a"])):
            offs = [o for o in g["st"] if side.get(o) == t_off and o in act]
            defs = [(d, dt[d] / 60) for d in dt if side.get(d) == t_def and dt[d] >= 300]
            if len(offs) < 4 or len(defs) < 5:
                continue
            star = max(offs, key=lambda o: P["ppg"].get(o, 0))
            pr = predict(P, [(o, 30) for o in offs], defs, lambda i: store.pos.get(i), star_o=star, **kw)
            dstart = [d for d in g["st"] if side.get(d) == t_def]
            for o in offs:
                a = act[o]
                true = max(a, key=a.get)
                p = pr[o]
                rank = sorted(p, key=p.get, reverse=True)
                hit += rank[0] == true
                top2 += true in rank[:2]
                po = store.pos.get(o, 3.0)
                bl = min(dstart, key=lambda d: abs(store.pos.get(d, 3.0) - po)) if dstart else None
                base_hit += bl == true
                ll += -sum(w * math.log(max(p.get(d, 1e-6), 1e-6)) for d, w in a.items())
                n += 1
    return dict(n=n, top1=hit / n if n else None, top2=top2 / n if n else None, base_top1=base_hit / n if n else None,
                xent=ll / n if n else None)


# ---------------------------------------------------------------- vista para la web
def _rate(x, y, k=100):
    return round(k * x / y, 1) if y else None


def _game_for(store, date, a, h):
    for g in store.games:
        if g["d"] == date and g["a"] == a and g["h"] == h:
            return g
    return None


def player_line(P, store, i, as_def=False):
    s = (P["dstats"] if as_def else P["ostats"]).get(i)
    if not s or s["poss"] < 20:
        return None
    out = dict(poss=round(s["poss"]), pts100=_rate(s["pts"], s["poss"]),
               fg=f"{int(s['fgm'])}/{int(s['fga'])}", fgp=_rate(s["fgm"], s["fga"]) if s["fga"] else None)
    if not as_def:
        g = s.get("gm", 0)
        if g:
            out.update(conc=round(s["conc"] / g, 3), ndef=round(s["ndef"] / g, 1))
    return out


def league_marks(P):
    """Referencia de liga (atacantes con 5+ partidos): parte media del 1.er defensor y defensores distintos por partido.
    nba.com publica la "ayuda" (help FGA) siempre en 0 en boxscorematchupsv3, así que no hay dato directo de dobles marcas."""
    xs = [(s["conc"] / s["gm"], s["ndef"] / s["gm"]) for s in P["ostats"].values() if s.get("gm", 0) >= 5]
    if not xs:
        return None
    c = sorted(x[0] for x in xs)
    n = sorted(x[1] for x in xs)
    return dict(conc=round(sum(c) / len(c), 3), conc_p20=round(c[int(len(c) * 0.2)], 3),
                ndef=round(sum(n) / len(n), 1), ndef_p80=round(n[int(len(n) * 0.8)], 1))


def view_for(store, cfg, P=None):
    """Vista de emparejamientos de un partido del modelo (games/*.json)."""
    g = cfg["game"]
    date, a, h = g["date"], g["away"], g["home"]
    P = P or profiles(store, date)
    out = dict(date=date, season=store.season, n_games=sum(1 for x in store.games if x["d"] < date),
               teams={}, league=league_marks(P))
    pos_of = lambda i: store.pos.get(i)
    names = {}
    for t_off, t_def in ((a, h), (h, a)):
        offs, defs = [], []
        for p in cfg["players"]:
            i = store.name2id.get(_key(p["name"]))
            if not i:
                continue
            names[i] = p["name"]
            if p["team"] == t_off and p.get("starter"):
                offs.append((i, p["min"], p))
            if p["team"] == t_def and p["min"] >= 12 and p.get("p_play", 1) >= 0.5:
                defs.append((i, p["min"], p))
        if not offs or not defs:
            continue
        star = max(offs, key=lambda x: P["ppg"].get(x[0], x[2]["cur"].get("pts", 0)))[0]
        pr = predict(P, [(i, m) for i, m, _ in offs], [(i, m) for i, m, _ in defs], pos_of, star_o=star)
        rows = []
        for i, m, p in offs:
            ranked = sorted(pr[i].items(), key=lambda kv: kv[1], reverse=True)
            cand = []
            for d, sh in ranked[:3]:
                if sh < 0.08 and cand:
                    break
                pairs = P["pair"].get((i, d), 0)
                vs = pair_stats(P, store, i, d)
                cand.append(dict(name=names.get(d, store.name(d)), share=round(sh, 3),
                                 starter=next((q.get("starter", False) for dd, _, q in defs if dd == d), False),
                                 d=player_line(P, store, d, as_def=True), vs=vs, h2h_min=round(pairs / 60, 1)))
            rows.append(dict(name=p["name"], slot=p.get("slot") or p.get("pos"), star=i == star,
                             o=player_line(P, store, i), defs=cand, last=last_games(store, i, date, t_def)))
        out["teams"][t_off] = dict(vs=t_def, rows=rows)
    real = _game_for(store, date, a, h)
    if real:
        out["actual"] = actual_view(store, real, names)
    return out


def pair_stats(P, store, o, d):
    """Lo que hizo O con D marcándolo (esta temporada antes de la fecha + temporada anterior)."""
    cur = P["pstat"].get((o, d)) or {}
    out = {}
    if cur.get("poss", 0) >= 3:
        out["cur"] = dict(g=int(cur["g"]), min=round(cur["sec"] / 60, 1), poss=round(cur["poss"]), pts=int(cur["pts"]),
                          fg=f"{int(cur['fgm'])}/{int(cur['fga'])}")
    r = store.prior_idx.get((o, d))
    if r:
        out["prior"] = dict(g=int(r["GP"]), min=round(_secs(r["MATCHUP_MIN"]) / 60, 1), poss=round(float(r["PARTIAL_POSS"])),
                            pts=int(r["PLAYER_PTS"]), fg=f"{int(r['MATCHUP_FGM'])}/{int(r['MATCHUP_FGA'])}")
    return out or None


def last_games(store, o, before, vs_team=None, n=3):
    """Últimos partidos del atacante: defensor principal, % del tiempo, puntos, tiros y tiros con ayuda."""
    out = []
    for g in reversed(store.games):
        if g["d"] >= before or len(out) >= n:
            continue
        rows = [r for r in g["rows"] if r["off"] == o]
        if not rows:
            continue
        tot = sum(r["sec"] for r in rows) or 1
        top = sorted(rows, key=lambda r: r["sec"], reverse=True)
        side = team_split(g)
        opp = side.get(top[0]["def"])
        out.append(dict(date=g["d"], opp=opp, top=[dict(name=store.name(r["def"]), share=round(r["sec"] / tot, 2)) for r in top[:2]],
                        pts=int(sum(r["pts"] for r in rows)), fg=f"{int(sum(r['fgm'] for r in rows))}/{int(sum(r['fga'] for r in rows))}",
                        ndef=sum(1 for r in rows if r["sec"] >= 60)))
    return out


def actual_view(store, g, names=None):
    """Emparejamientos reales de un partido jugado: para cada titular, sus defensores principales."""
    names = names or {}
    side = team_split(g)
    out = {}
    for t in (g["a"], g["h"]):
        rows = []
        for o in [x for x in g["st"] if side.get(x) == t]:
            rs = sorted([r for r in g["rows"] if r["off"] == o], key=lambda r: r["sec"], reverse=True)
            tot = sum(r["sec"] for r in rs) or 1
            rows.append(dict(name=names.get(o, store.name(o)), min=round(tot / 60, 1), ndef=sum(1 for r in rs if r["sec"] >= 60),
                             pts=int(sum(r["pts"] for r in rs)), fg=f"{int(sum(r['fgm'] for r in rs))}/{int(sum(r['fga'] for r in rs))}",
                             defs=[dict(name=names.get(r["def"], store.name(r["def"])), share=round(r["sec"] / tot, 3), min=round(r["sec"] / 60, 1),
                                        poss=r["poss"], pts=int(r["pts"]), fg=f"{int(r['fgm'])}/{int(r['fga'])}",
                                        ) for r in rs[:4] if r["sec"] >= 30]))
        out[t] = rows
    return out


def build(dates=None):
    os.makedirs(OUT, exist_ok=True)
    stores = {}
    n = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "games", "*.json"))):
        b = os.path.basename(f)
        if b.endswith("_results.json"):
            continue
        date = b[:10]
        if dates and date not in dates:
            continue
        s = season_of(date)
        st = stores.get(s) or stores.setdefault(s, Store(s))
        if not st.games:
            continue
        cfg = json.load(open(f))
        v = view_for(st, cfg)
        if not v["teams"] and "actual" not in v:
            continue
        gid = f"{date}-{cfg['game']['away']}-at-{cfg['game']['home']}"
        json.dump(v, open(os.path.join(OUT, f"{gid}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
        n += 1
    print(f"defmatch: {n} partidos")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "backtest":
        st = Store(sys.argv[3] if len(sys.argv) > 3 else "2026-27")
        print(backtest(st, sys.argv[2] if len(sys.argv) > 2 else "2026-11-01"))
    else:
        build(set(sys.argv[2:]) or None)
