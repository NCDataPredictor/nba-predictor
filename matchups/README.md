# Emparejamientos defensivos (22-nov-2026)

Pedido de Nicolas: ver quién marca a quién (y cuánta ayuda / marca doble recibe cada titular) para dar contexto a los props.
Solo análisis en la web; **no cambia la simulación ni los picks** (cualquier uso en el modelo se revisa antes con Nicolas).

1. Descarga (solo desde el Chrome del PC: nba.com/stats bloquea la nube). En una pestaña de https://www.nba.com/stats,
   pegar `fetch_matchups.js` y correr `await MU_RUN("2026-27")` (partidos finales del calendario; reanuda si se corta)
   y `MU_SAVE()` → `matchups/nba/2026-27.json`. Temporada anterior (opcional): `await MU_PRIOR("2025-26")` → `window.__MUP`
   → `matchups/nba/2025-26_pairs.json`.
2. `python matchups/defmatch.py build [fecha ...]` → `docs/data/defmatch/<id>.json` (la web los lee en Análisis).
3. `python matchups/defmatch.py backtest [desde]` → acierto del defensor principal previsto usando solo datos previos.

"Con ayuda" = tiros del atacante contestados por un segundo defensor (help FGA de nba.com), lo más cercano a una marca doble.

## Resultados (23-nov, 248 partidos 2026-27 + parejas 2025-26)
- Predicción del defensor principal de cada titular (solo datos previos, desde 1-nov, 1.580 titulares): acierta 51% (top-2: 71%) vs 38% de "el titular rival del mismo puesto". Parámetros: SD_FLOOR 0.75, STAR_B 3, H2H_G 3.
- `bt_defmatch.py`: la calidad del defensor previsto (pts/100 pos. que permite) NO explica el error del modelo en puntos (corr +0.006; ni siquiera el defensor real: +0.005; MAE fuera de muestra 5.177 → 5.181). Rebotes de PF/C vs rebotes del defensor previsto: corr −0.017. Por ahora no se propone cambiar el modelo.
