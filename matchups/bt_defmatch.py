"""Backtest: ¿el defensor previsto explica lo que el modelo falla en los puntos de los titulares?

Para cada titular de los partidos ya jugados (desde `desde`), con datos solo anteriores a la fecha:
  calidad prevista = Σ parte(D) × (pts/100 pos. que permite D, encogido a la media con K pos.) − media de liga
  error del modelo = puntos reales por minuto / puntos proyectados por minuto − 1   (titulares con 20+ min reales)
Se mide la correlación y si corregir la media con esa calidad baja el error (validación por mitades de fechas).
También la calidad del defensor REAL (cota superior: no se conoce antes del partido).

    python matchups/bt_defmatch.py [desde] [K]
"""
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import defmatch as dm  # noqa: E402


def d_quality(P, d, K, lg):
    s = P["dstats"].get(d)
    if not s:
        return 0.0
    return (s["pts"] + K * lg / 100) / (s["poss"] + K) * 100 - lg


def main(start="2026-11-01", K=300.0):
    st = dm.Store(dm.season_of(start))
    rows = []
    cache = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "docs", "data", "20*.json"))):
        r = json.load(open(f))
        g = r["game"]
        if g["date"] < start or "result" not in r:
            continue
        gp = os.path.join(ROOT, "games", f"{g['date']}_{g['away']}@{g['home']}.json")
        if not os.path.exists(gp):
            continue
        cfg = json.load(open(gp))
        P = cache.get(g["date"]) or cache.setdefault(g["date"], dm.profiles(st, g["date"]))
        tot = sum(s["pts"] for s in P["dstats"].values())
        pos = sum(s["poss"] for s in P["dstats"].values())
        lg = tot / pos * 100 if pos else 100
        real = dm._game_for(st, g["date"], g["away"], g["home"])
        act = dm.actual_shares(real)[0] if real else {}
        summ = {p["name"]: p for p in r["summary"]["players"]}
        for t_off, t_def in ((g["away"], g["home"]), (g["home"], g["away"])):
            offs, defs = [], []
            for p in cfg["players"]:
                i = st.name2id.get(dm._key(p["name"]))
                if not i:
                    continue
                if p["team"] == t_off and p.get("starter"):
                    offs.append((i, p["min"], p))
                if p["team"] == t_def and p["min"] >= 12 and p.get("p_play", 1) >= 0.5:
                    defs.append((i, p["min"], p))
            if not offs or not defs:
                continue
            star = max(offs, key=lambda x: P["ppg"].get(x[0], x[2]["cur"].get("pts", 0)))[0]
            pr = dm.predict(P, [(i, m) for i, m, _ in offs], [(i, m) for i, m, _ in defs], lambda i: st.pos.get(i), star_o=star)
            for i, m, p in offs:
                s = summ.get(p["name"])
                if not s or not s.get("actual") or s["actual"].get("min", 0) < 20 or s["pts"]["mean"] < 3:
                    continue
                q = sum(sh * d_quality(P, d, K, lg) for d, sh in pr[i].items())
                qa = sum(sh * d_quality(P, d, K, lg) for d, sh in act.get(i, {}).items()) if i in act else None
                exp_pm = s["pts"]["mean"] / max(s["min"], 1)
                err = s["actual"]["pts"] / s["actual"]["min"] / exp_pm - 1
                rows.append(dict(date=g["date"], name=p["name"], q=q, qa=qa, err=err, mean=s["pts"]["mean"],
                                 act=s["actual"]["pts"], min=s["min"], amin=s["actual"]["min"]))
    n = len(rows)
    if not n:
        print("sin datos")
        return

    def corr(xs, ys):
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        return sxy / (sx * sy) if sx and sy else 0.0, sxy / (sx * sx) if sx else 0.0

    c, b = corr([x["q"] for x in rows], [x["err"] for x in rows])
    ra = [x for x in rows if x["qa"] is not None]
    ca, ba = corr([x["qa"] for x in ra], [x["err"] for x in ra]) if ra else (None, None)
    print(f"titulares: {n} (desde {start}, K={K:g})")
    print(f"defensor previsto: corr {c:+.3f}, pendiente {b * 100:+.2f}% de puntos por cada +1 pt/100 pos. que permite")
    if ra:
        print(f"defensor real (cota): corr {ca:+.3f}, pendiente {ba * 100:+.2f}%  (n={len(ra)})")
    # validación: ajustar la pendiente con la primera mitad de fechas y aplicarla en la segunda
    ds = sorted({x["date"] for x in rows})
    cut = ds[len(ds) // 2]
    tr, te = [x for x in rows if x["date"] < cut], [x for x in rows if x["date"] >= cut]
    _, bt = corr([x["q"] for x in tr], [x["err"] for x in tr])
    mae0 = sum(abs(x["act"] - x["mean"] * x["amin"] / x["min"]) for x in te) / len(te)
    mae1 = sum(abs(x["act"] - x["mean"] * x["amin"] / x["min"] * (1 + bt * x["q"])) for x in te) / len(te)
    print(f"fuera de muestra ({len(te)} titulares desde {cut}): MAE pts (a minutos reales) {mae0:.3f} -> {mae1:.3f} con pendiente {bt * 100:+.2f}%")
    qs = sorted(x["q"] for x in rows)
    print(f"rango de la calidad prevista: p10 {qs[n // 10]:+.1f}, p90 {qs[9 * n // 10]:+.1f} pts/100 pos.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-11-01", float(sys.argv[2]) if len(sys.argv) > 2 else 300.0)
