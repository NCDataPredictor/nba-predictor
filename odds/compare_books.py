"""
Betano frente al mercado de EE. UU. (cambio 5, aprobado por Nicolas el 20-nov): cada jornada se guarda la MISMA prop
en las dos fuentes para medir si Betano se queda atrás del mercado "afilado" (lo que hacen los comparadores +EV).

  odds/props/<fecha>_US.json   {jugador: {stat: [línea, over, under]}} de scoresandodds (cuotas americanas, mejor
                               cuota entre casas de EE. UU.); las de Betano ya quedan en slates/<fecha>.json
                               (prop_lines con "prop_odds_region": "CO") o en odds/props/<fecha>_CO.json.
  python odds/compare_books.py <fecha> [<fecha> ...]

Para cada prop con la misma línea en las dos: probabilidad justa de EE. UU. (sin margen) y EV de la cuota de Betano
con esa probabilidad. Si hay resultados (results/<fecha>.json), liquida las que tienen EV >= 0 en Betano.
"""
import json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
IX = {"pts": 1, "reb": 2, "ast": 3, "fg3m": 4}
COMBO = {"pra": ("pts", "reb", "ast"), "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast")}


def dec(a):
    return 1 + (a / 100 if a > 0 else 100 / -a)


def load(date, region):
    p = os.path.join(HERE, "odds", "props", f"{date}_{region}.json")
    if os.path.exists(p):
        return json.load(open(p))
    s = os.path.join(HERE, "slates", f"{date}.json")
    if os.path.exists(s):
        sl = json.load(open(s))
        if (sl.get("prop_odds_region") or "US") == region:
            return sl.get("prop_lines", {})
    return {}


def compare(date):
    from evaluate import norm
    us, co = load(date, "US"), load(date, "CO")
    usn = {norm(k): v for k, v in us.items()}
    act = {}
    rp = os.path.join(HERE, "results", f"{date}.json")
    if os.path.exists(rp):
        G = json.load(open(rp))["games"]
        act = {norm(n): v for x in (G.values() if isinstance(G, dict) else G) for n, v in x["players"].items()}
    rows = []
    for name, stats in co.items():
        u = usn.get(norm(name))
        if not u:
            continue
        for st, v in stats.items():
            w = u.get(st)
            if not w or None in (v[0], w[0]) or len(v) < 3 or len(w) < 3 or None in (v[1], v[2], w[1], w[2]) or v[0] != w[0]:
                continue
            io, iu = 1 / dec(w[1]), 1 / dec(w[2])
            fo = io / (io + iu)
            for side, fair, odd in (("over", fo, v[1]), ("under", 1 - fo, v[2])):
                ev = fair * dec(odd) - 1
                res = None
                a = act.get(norm(name))
                if a and all(k in IX for k in COMBO.get(st, (st,))):
                    real = sum(a[IX[k]] for k in COMBO.get(st, (st,)))
                    if real != v[0]:
                        res = (real > v[0]) == (side == "over")
                rows.append(dict(player=name, stat=st, line=v[0], side=side, betano=round(dec(odd), 2),
                                 fair=round(fair, 3), ev=round(ev, 3), won=res))
    return rows


if __name__ == "__main__":
    allr = []
    for d in sys.argv[1:]:
        r = compare(d)
        allr += r
        print(f"{d}: {len(r) // 2} props con la misma línea en Betano y EE. UU.")
    pos = sorted((x for x in allr if x["ev"] >= 0), key=lambda x: -x["ev"])
    print(f"Betano paga más que el precio justo de EE. UU. en {len(pos)} de {len(allr)} lados")
    for x in pos[:25]:
        print(f"  {x['player']} {x['side']} {x['line']} {x['stat']}: Betano {x['betano']} justo {x['fair']:.0%} EV {x['ev']:+.1%}"
              + ("" if x["won"] is None else (" ganó" if x["won"] else " perdió")))
    s = [x for x in pos if x["won"] is not None]
    if s:
        u = sum(x["betano"] - 1 if x["won"] else -1 for x in s)
        print(f"Liquidadas: {sum(x['won'] for x in s)}-{sum(not x['won'] for x in s)}, {u:+.2f} u")
