// Emparejamientos defensivos de nba.com (boxscorematchupsv3), para correr en la consola / javascript_tool
// de Chrome en una pestaña de https://www.nba.com/stats (las peticiones a stats.nba.com solo funcionan
// desde el navegador). Deja el resultado en window.__MU y lo descarga como matchups_<temporada>.json.
//   await MU_RUN("2026-27", ["0022600001", ...])   // ids opcionales; sin ids usa el calendario (partidos finales)
// Formato de cada fila de p (una por pareja atacante-defensor):
//   [off, def, seg, poss, pts, fgm, fga, fg3m, fg3a, ast, tov, blk, help_blk, help_fgm, help_fga, ftm, fta, switches, pct_off_time]
window.MU_COLS = ["off", "def", "sec", "poss", "pts", "fgm", "fga", "fg3m", "fg3a", "ast", "tov", "blk",
  "help_blk", "help_fgm", "help_fga", "ftm", "fta", "switches", "pct_off"];
window.MU_SCHED = async function (season) {
  const j = await (await fetch("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json")).json();
  const out = [];
  for (const d of j.leagueSchedule.gameDates) for (const g of d.games)
    if (/^002/.test(g.gameId) && g.gameStatus === 3)
      out.push({ id: g.gameId, d: (g.gameDateEst || "").slice(0, 10), a: g.awayTeam.teamTricode, h: g.homeTeam.teamTricode });
  return out;
};
window.MU_ONE = async function (gid) {
  const u = `https://stats.nba.com/stats/boxscorematchupsv3?GameID=${gid}&LeagueID=00&endPeriod=0&endRange=28800&rangeType=0&startPeriod=0&startRange=0`;
  const r = await fetch(u, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(gid + " HTTP " + r.status);
  const b = (await r.json()).boxScoreMatchups;
  const names = {}, pos = {}, st = [], aw = new Set(), p = [], n = x => +x || 0;
  for (const side of ["awayTeam", "homeTeam"]) for (const o of b[side].players || []) {
    names[o.personId] = `${o.firstName} ${o.familyName}`.trim();
    if (side === "awayTeam") aw.add(o.personId);
    if (o.position) { st.push(o.personId); pos[o.personId] = o.position; }
    for (const m of o.matchups || []) {
      names[m.personId] = `${m.firstName} ${m.familyName}`.trim();
      if (side === "homeTeam") aw.add(m.personId);
      const s = m.statistics;
      // matchupMinutesSort viene en segundos (79 = "1:19")
      const sec = typeof s.matchupMinutesSort === "number" ? Math.round(s.matchupMinutesSort)
        : (([mm, ss]) => n(mm) * 60 + n(ss))(String(s.matchupMinutes || "0:0").split(":"));
      const poss = n(s.partialPossessions);
      if (poss < 0.5) continue;  // help* vienen siempre en 0 en v3 (verificado 23-nov)
      p.push([o.personId, m.personId, sec, +poss.toFixed(1), n(s.playerPoints), n(s.matchupFieldGoalsMade), n(s.matchupFieldGoalsAttempted),
        n(s.matchupThreePointersMade), n(s.matchupThreePointersAttempted), n(s.matchupAssists), n(s.matchupTurnovers), n(s.matchupBlocks),
        n(s.helpBlocks), n(s.helpFieldGoalsMade), n(s.helpFieldGoalsAttempted), n(s.matchupFreeThrowsMade), n(s.matchupFreeThrowsAttempted),
        n(s.switchesOn), +n(s.percentageOffensiveTotalTime).toFixed(3)]);
    }
  }
  return { a: b.awayTeam.teamTricode, h: b.homeTeam.teamTricode, st, pos, aw: [...aw], p, names };
};
window.MU_RUN = async function (season, ids, waitMs = 700) {
  const sched = await MU_SCHED(season);
  const want = ids && ids.length ? sched.filter(g => ids.includes(g.id)) : sched;
  const out = window.__MU = window.__MU || { season, cols: MU_COLS, players: {}, games: {}, errors: {} };
  for (const g of want) {
    if (out.games[g.id]) continue;
    try {
      const r = await MU_ONE(g.id);
      Object.assign(out.players, r.names);
      out.games[g.id] = { d: g.d, a: r.a, h: r.h, st: r.st, pos: r.pos, aw: r.aw, p: r.p };
      delete out.errors[g.id];
    } catch (e) { out.errors[g.id] = String(e); }
    await new Promise(z => setTimeout(z, waitMs));
  }
  out.fetched = new Date().toISOString();
  return { games: Object.keys(out.games).length, errors: Object.keys(out.errors).length };
};
window.MU_SAVE = function (name) {
  const blob = new Blob([JSON.stringify(window.__MU)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = name || `matchups_${window.__MU.season}.json`; document.body.appendChild(a); a.click(); a.remove();
};
// Temporada anterior: totales por pareja atacante-defensor (leagueseasonmatchups), una petición por equipo atacante.
// Guarda window.__MUP = {season, cols, rows} con las parejas de al menos minPoss posesiones.
window.MU_PRIOR = async function (season, minPoss = 5, waitMs = 900) {
  const teams = [...new Set((await MU_SCHED()).flatMap(g => [g.a, g.h]))];
  const ids = (await (await fetch("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json")).json())
    .leagueSchedule.gameDates.flatMap(d => d.games).flatMap(g => [g.homeTeam, g.awayTeam]);
  const tid = {}; ids.forEach(t => { if (t.teamTricode) tid[t.teamTricode] = t.teamId; });
  const out = window.__MUP = window.__MUP || { season, cols: null, rows: [], done: [], players: {} };
  for (const t of teams) {
    if (out.done.includes(t)) continue;
    const u = `https://stats.nba.com/stats/leagueseasonmatchups?DefPlayerID=&DefTeamID=&LeagueID=00&OffPlayerID=&OffTeamID=${tid[t]}&PORound=0&PerMode=Totals&Season=${season}&SeasonType=Regular%20Season`;
    try {
      const j = await (await fetch(u, { headers: { Accept: "application/json" } })).json();
      const rs = j.resultSets[0], H = rs.headers, ix = k => H.indexOf(k);
      const keep = ["OFF_PLAYER_ID", "DEF_PLAYER_ID", "GP", "MATCHUP_MIN", "PARTIAL_POSS", "PLAYER_PTS", "MATCHUP_FGM", "MATCHUP_FGA",
        "MATCHUP_FG3M", "MATCHUP_FG3A", "MATCHUP_AST", "MATCHUP_TOV", "MATCHUP_BLK", "HELP_BLK", "HELP_FGM", "HELP_FGA", "MATCHUP_FTM", "MATCHUP_FTA", "SFL"];
      out.cols = keep;
      for (const r of rs.rowSet) {
        if ((+r[ix("PARTIAL_POSS")] || 0) < minPoss) continue;
        out.players[r[ix("OFF_PLAYER_ID")]] = r[ix("OFF_PLAYER_NAME")]; out.players[r[ix("DEF_PLAYER_ID")]] = r[ix("DEF_PLAYER_NAME")];
        out.rows.push(keep.map(k => { const v = r[ix(k)]; return typeof v === "number" ? +v.toFixed(2) : v; }));
      }
      out.done.push(t);
    } catch (e) { console.log(t, e); }
    await new Promise(z => setTimeout(z, waitMs));
  }
  return { teams: out.done.length, rows: out.rows.length };
};
