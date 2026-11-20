"""
Proyección de minutos 1.7 (pedido de Nicolas, 20-nov): en vez de repartir 240 min entre todos los del roster según su
promedio de temporada, mirar la distribución de minutos de sus últimos partidos del equipo y entender los cambios.

Para cada jugador del partido (desde sus partidos de esta temporada antes de la fecha, context.season_games):
  - seq: minutos en los últimos LAST_N partidos de su equipo (0 = no jugó: DNP, lesión o aún no estaba).
  - Si no jugó ninguno de los últimos OUT_N partidos del equipo:
      * con >= RET_MIN min de media en la temporada = vuelve tras ausencia: minutos recientes × RET_F (manejo de cargas)
      * si no = fuera de la rotación: p_play baja a DNP_P (si juega, solo con el partido decidido).
  - Si no: minutos = mezcla de la media de sus últimos RECENT_N partidos jugados (peso W_RECENT) y la de temporada.
  - Cambio de rol: si la media de sus últimos 3 partidos jugados se aleja >= CHG_MIN min (y >= 20%) de la de los
    anteriores, se busca el porqué: compañero clave (>= KEY_MIN min) que volvió o que falta desde entonces, que él
    mismo volvió de una ausencia, o, si nada de eso cambió, decisión técnica / posible restricción de minutos.
    Si el porqué sigue vigente hoy (el compañero sigue fuera o sigue jugando), los minutos recientes pesan W_CHG.
El total del equipo vuelve a sumar 240 (el modelo ya reparte y limita a MAX_MIN). La nota del porqué queda en
p["min_note"] para la web.
"""
from collections import defaultdict

LAST_N, RECENT_N, OUT_N = 10, 5, 3
W_RECENT, W_CHG = 0.4, 0.6
RET_MIN, RET_F, DNP_P = 20.0, 0.9, 0.15
CHG_MIN, KEY_MIN = 6.0, 24.0
TEAM_MIN = 240.0


def _team_dates(t, pres, date):
    return sorted((d for (tt, d) in pres if tt == t and d < date), reverse=True)


def project(cfg, date=None, games=None, pres=None):
    from context import season_games
    from evaluate import norm
    date = date or cfg["game"]["date"]
    if games is None:
        games, pres = season_games(date)
    active = {norm(p["name"]) for p in cfg["players"] if p.get("p_play", 1.0) >= 0.5}
    for t in {p["team"] for p in cfg["players"]}:
        tds = _team_dates(t, pres, date)[:LAST_N]
        if len(tds) < 4:
            continue
        # compañeros clave: media >= KEY_MIN en los partidos que jugaron con el equipo
        mins_by = defaultdict(dict)
        for d in tds:
            for n in pres[(t, d)]:
                g = next((x for x in games[n] if x["date"] == d), None)
                if g:
                    mins_by[n][d] = g["line"].get("min") or 0
        allmin = defaultdict(list)
        for (tt, d), ns in pres.items():
            if tt == t and d < date:
                for n in ns:
                    g = next((x for x in games[n] if x["date"] == d), None)
                    if g:
                        allmin[n].append(g["line"].get("min") or 0)
        key = {n for n, m in allmin.items() if len(m) >= 3 and sum(m) / len(m) >= KEY_MIN}
        tp = [p for p in cfg["players"] if p["team"] == t]
        for p in tp:
            n = norm(p["name"])
            seq = [mins_by[n].get(d, 0) for d in tds]  # más reciente primero
            season = p["cur"]["min"]
            p["min_seq"] = seq
            if not any(seq):
                if allmin.get(n):  # jugó antes con el equipo pero no en los últimos LAST_N
                    if season >= RET_MIN:
                        p["min_note"] = f"vuelve tras {len(tds)}+ partidos fuera"
                        p["_min2"] = season * RET_F
                    else:
                        p["min_note"] = "fuera de la rotación"
                        p["_min2"] = season
                        p["p_play"] = min(p.get("p_play", 1.0), DNP_P)
                continue
            played = [m for m in seq if m > 0]
            if not any(seq[:OUT_N]):
                miss = next(i for i, m in enumerate(seq) if m > 0)
                if season >= RET_MIN or (sum(played) / len(played)) >= RET_MIN:
                    p["min_note"] = f"vuelve tras {miss} partidos fuera; posible manejo de cargas"
                    p["_min2"] = (sum(played[:RECENT_N]) / len(played[:RECENT_N])) * RET_F
                else:
                    p["min_note"] = f"sin jugar en los últimos {miss} partidos (fuera de la rotación)"
                    p["_min2"] = sum(played[:RECENT_N]) / len(played[:RECENT_N])
                    p["p_play"] = min(p.get("p_play", 1.0), DNP_P)
                continue
            rec = played[:RECENT_N]
            m_rec = sum(rec) / len(rec)
            w = W_RECENT * min(len(rec), RECENT_N) / RECENT_N
            proj = w * m_rec + (1 - w) * season
            # cambio de rol: últimos 3 jugados vs los anteriores
            idx = [i for i, m in enumerate(seq) if m > 0]
            if len(idx) >= 5:
                new, old = idx[:3], idx[3:]
                mn, mo = sum(seq[i] for i in new) / 3, sum(seq[i] for i in old) / len(old)
                if abs(mn - mo) >= CHG_MIN and abs(mn - mo) >= 0.2 * max(mo, 1):
                    dn, do = [tds[i] for i in new], [tds[i] for i in old]
                    why, holds = [], False
                    for k in sorted(key - {n}):
                        inn = sum(k in pres[(t, d)] for d in dn) / len(dn)
                        ino = sum(k in pres[(t, d)] for d in do) / len(do)
                        nm = next((x["name"] for x in games[k][:1]), k.title())
                        if inn - ino >= 0.6:
                            why.append(f"volvió {nm}")
                            holds |= k in active
                        elif ino - inn >= 0.6:
                            why.append(f"falta {nm}")
                            holds |= k not in active
                    if idx[3] - idx[2] > 1 and not why:
                        why.append("él volvió de una ausencia")
                        holds = True
                    if not why:
                        why.append("decisión técnica o restricción de minutos")
                        holds = True
                    p["min_note"] = f"{'sube' if mn > mo else 'baja'} de {mo:.0f} a {mn:.0f} min en sus últimos 3 partidos; coincide con: " + ", ".join(why)
                    if holds:
                        proj = W_CHG * mn + (1 - W_CHG) * proj
                    else:  # el motivo ya no está (p. ej. volvió el compañero): vuelve a su rol anterior
                        proj = 0.5 * mo + 0.5 * proj
                        p["min_note"] += " (hoy ya no aplica)"
            p["_min2"] = proj
        # re-escalar a 240 (el modelo reparte los minutos de quien no juegue entre los que sí)
        tot = sum(p.get("_min2", p["min"]) for p in tp)
        if tot <= 0:
            continue
        k = TEAM_MIN / tot
        for p in tp:
            p["min_old"] = p["min"]
            p["min"] = round(min(p.get("_min2", p["min"]) * k, 40.0), 1)
            p.pop("_min2", None)
    return cfg
