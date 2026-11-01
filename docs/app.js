/* TX-NOMINATE explorer: session maps and a roll call explorer, from data/*.json */
(() => {
  "use strict";
  const app = document.getElementById("app");
  const tip = document.getElementById("tip");
  const cache = {};
  let INDEX = null;
  let AX = null;          // the axis labels for the legislature on screen (they read differently by era)
  let BLOCS = null;       // voting blocs by legislature and chamber (data/blocs.json)

  const PARTY = { R: "Republican", D: "Democrat", O: "Other or not recorded" };
  const VOTE = { Y: "Yea", N: "Nay", P: "Present, not voting", E: "Absent, excused", A: "Absent" };
  const CHAMBER = { H: "House", S: "Senate" };

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = s => new Date(s + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  const ord = n => n + (["th", "st", "nd", "rd"][(n % 100 - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th");
  const json = url => (cache[url] ||= fetch(url).then(r => { if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); }));
  const num = (x, d = 2) => (x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(d));

  // ---------- routing
  function route() {
    const p = location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean).map(decodeURIComponent);
    if (p[0] === "about") return { page: "about" };
    if (p[0] === "trends") return { page: "trends" };
    if (p[0] === "data") return { page: "data" };
    if (p[0] === "votes") return { page: "votes" };
    const leg = parseInt(p[0], 10) || null;
    const ch = (p[1] || "H").toUpperCase() === "S" ? "S" : "H";
    if (p[2] === "votes") return { page: "votes", leg, ch };
    if (p[2] === "vote" && p[3]) return { page: "vote", leg, ch, id: p[3] };
    return { page: "map", leg, ch };
  }

  async function render() {
    hideTip();
    const r = route();
    document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === (r.page === "vote" ? "votes" : r.page)));
    try {
      INDEX ||= await json("data/index.json");
      if (r.page === "about") return renderAbout();
      if (r.page === "trends") return renderTrends();
      if (r.page === "data") return renderData();
      r.leg ||= INDEX.legislatures[0].leg;
      const meta = INDEX.legislatures.find(l => l.leg === r.leg);
      if (!meta) { location.hash = "#/"; return; }
      if (!meta.chambers[r.ch]) r.ch = Object.keys(meta.chambers)[0];
      const S = await json(`data/s/${r.leg}${r.ch}.json`);
      BLOCS ||= await json("data/blocs.json").catch(() => ({}));
      AX = axesFor(r.leg);
      if (r.page === "map") renderMap(S, r);
      else if (r.page === "votes") renderVotes(S, r);
      else renderVote(S, r);
      document.title = `${CHAMBER[r.ch]}, ${ord(r.leg)} Legislature · TX-NOMINATE`;
    } catch (e) {
      app.innerHTML = `<p>Couldn't load that page. <a href="#/">Back to the map</a>.</p><p class="muted small">${esc(e.message)}</p>`;
    }
  }

  // ---------- shared pieces
  function controls(S, r, page) {
    const legs = INDEX.legislatures.map(l => `<option value="${l.leg}"${l.leg === S.leg ? " selected" : ""}>${ord(l.leg)} Legislature (${esc(l.years)})</option>`).join("");
    const meta = INDEX.legislatures.find(l => l.leg === S.leg);
    const chambers = ["H", "S"].filter(c => meta.chambers[c]).map(c =>
      `<a href="#/${S.leg}/${c}${page === "map" ? "" : "/votes"}" class="${c === S.ch ? "on" : ""}">${CHAMBER[c]}</a>`).join("");
    const tabs = `<a href="#/${S.leg}/${S.ch}" class="${page === "map" ? "on" : ""}">Map</a><a href="#/${S.leg}/${S.ch}/votes" class="${page !== "map" ? "on" : ""}">Votes</a>`;
    return `<div class="controls">
      <select id="leg" aria-label="Legislature">${legs}</select>
      <span class="seg" role="group" aria-label="Chamber">${chambers}</span>
      <span class="seg" role="group" aria-label="View">${tabs}</span>
    </div>`;
  }
  function wireControls(S, page) {
    const sel = document.getElementById("leg");
    sel.addEventListener("change", () => {
      const meta = INDEX.legislatures.find(l => l.leg === +sel.value);
      const ch = meta.chambers[S.ch] ? S.ch : Object.keys(meta.chambers)[0];
      location.hash = `#/${sel.value}/${ch}${page === "map" ? "" : "/votes"}`;
    });
  }
  const partyClass = p => `party-${p in PARTY ? p : "O"}`;
  const partyOf = m => (m.p === "O" ? m.pn || "Party not recorded" : PARTY[m.p]);
  // the shared axis labels, with the era's own wording where axes.json gives one
  function axesFor(leg) {
    const e = (INDEX.axes.eras || []).find(e => leg >= e.from && leg <= e.to) || {};
    return { x: { ...INDEX.axes.x, ...(e.x || {}) }, y: { ...INDEX.axes.y, ...(e.y || {}) }, era: e };
  }
  const partyColor = p => `var(--${p === "R" ? "rep" : p === "D" ? "dem" : "oth"})`;

  function showTip(evt, html) {
    tip.innerHTML = html;
    tip.hidden = false;
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
    if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  }
  function hideTip() { tip.hidden = true; }
  addEventListener("scroll", hideTip, { passive: true });

  // ---------- the plot
  // members: [{id, n, p, d, x, y}], opts: {votes, cut: {mid, spr}, highlight, onClick}
  function plot(el, members, opts = {}) {
    const W = 560, pad = 34, R = (W - 2 * pad) / 2, cx = W / 2, cy = W / 2;
    const ax = AX;
    const sx = v => cx + v * R, sy = v => cy - v * R;
    const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${W}`).attr("role", "img")
      .attr("aria-label", `Map of members: horizontal axis ${ax.x.name}, vertical axis ${ax.y.name}`);
    const unit = W / Math.max(el.clientWidth || W, 1);     // keep labels ~12.5px however wide the plot is drawn
    const uid = Math.random().toString(36).slice(2);
    svg.append("clipPath").attr("id", `c${uid}`).append("circle").attr("cx", cx).attr("cy", cy).attr("r", R * 1.02);
    svg.append("circle").attr("class", "frame").attr("cx", cx).attr("cy", cy).attr("r", R);
    const label = (txt, attrs) => { const t = svg.append("text").attr("class", "axlabel").style("font-size", `${12.5 * unit}px`).text(txt); Object.entries(attrs).forEach(([k, v]) => t.attr(k, v)); return t; };
    svg.append("line").attr("class", "axis").attr("x1", sx(-1)).attr("x2", sx(1)).attr("y1", cy).attr("y2", cy);
    svg.append("line").attr("class", "axis").attr("x1", cx).attr("x2", cx).attr("y1", sy(-1)).attr("y2", sy(1));
    label(`← ${ax.x.neg}`, { x: sx(-1), y: W - 8 });
    label(`${ax.x.pos} →`, { x: sx(1), y: W - 8, "text-anchor": "end" });
    label(`← ${ax.y.neg}`, { transform: `translate(${4 + 12 * unit},${sy(-1)}) rotate(-90)` });
    label(`${ax.y.pos} →`, { transform: `translate(${4 + 12 * unit},${sy(1)}) rotate(-90)`, "text-anchor": "end" });

    if (opts.cut) {
      // the cutting line: points equidistant (in weighted distance) from the yea and nay outcomes
      const [m1, m2] = opts.cut.mid, [d1, d2] = opts.cut.spr, [w1, w2] = INDEX.w;
      let n1 = w1 * w1 * d1, n2 = w2 * w2 * d2;
      const len = Math.hypot(n1, n2) || 1; n1 /= len; n2 /= len;
      const u1 = -n2, u2 = n1, L = 3;
      const g = svg.append("g").attr("clip-path", `url(#c${uid})`);
      // yea side: n·(x − m) < 0
      const poly = [[m1 + L * u1, m2 + L * u2], [m1 - L * u1, m2 - L * u2], [m1 - L * u1 - L * n1, m2 - L * u2 - L * n2], [m1 + L * u1 - L * n1, m2 + L * u2 - L * n2]];
      g.append("polygon").attr("class", "yeaside").attr("points", poly.map(([a, b]) => `${sx(a)},${sy(b)}`).join(" "));
      g.append("line").attr("class", "cut").attr("x1", sx(m1 - L * u1)).attr("y1", sy(m2 - L * u2)).attr("x2", sx(m1 + L * u1)).attr("y2", sy(m2 + L * u2));
    }

    const pts = members.filter(m => m.x != null);
    const r = el.clientWidth < 420 ? 6 : 5;
    const sel = svg.append("g").selectAll("circle").data(pts).join("circle")
      .attr("class", m => "pt" + (opts.highlight === m.id ? " hl" : ""))
      .attr("cx", m => sx(m.x)).attr("cy", m => sy(m.y ?? 0))
      .attr("r", m => (opts.votes && !"YN".includes(m.vote) ? r * 0.55 : r))
      .attr("fill", m => {
        if (opts.fill) return opts.fill(m);
        if (!opts.votes) return partyColor(m.p);
        if (m.vote === "Y") return partyColor(m.p);
        if (m.vote === "N") return "var(--panel)";
        return "var(--line)";
      })
      .attr("stroke", m => (opts.votes && !"YN".includes(m.vote) ? "none" : opts.votes && m.vote === "N" ? partyColor(m.p) : "var(--panel)"))
      .on("pointermove", (e, m) => showTip(e, `<b>${esc(m.n)}</b>${partyOf(m)}${m.d ? `, District ${esc(m.d)}` : ""}${opts.votes ? `<br>${VOTE[m.vote] || "Not voting"}` : ""}<br><span class="muted">${esc(AX.x.short)} ${num(m.x)} · ${esc(AX.y.short)} ${num(m.y)}</span>`))
      .on("pointerleave", hideTip)
      .on("click", (e, m) => opts.onClick && opts.onClick(m));
    if (opts.highlight != null) sel.filter(m => m.id === opts.highlight).raise();
    return {
      highlight(id) {
        sel.classed("hl", m => m.id === id).classed("dim", m => id != null && m.id !== id);
        if (id != null) sel.filter(m => m.id === id).raise();
      },
    };
  }

  // when only one chamber's votes survive (or have been read) for a legislature, say why
  function missingNote(S) {
    const meta = INDEX.legislatures.find(l => l.leg === S.leg);
    const other = S.ch === "H" ? "S" : "H";
    if (meta.chambers[other]) return "";
    const why = other === "H" && S.leg >= 63 && S.leg <= 67 ? "The House Journal printed these years' votes as tally sheets, and too few of them could be verified."
      : other === "H" && (S.leg === 9 || S.leg === 10) ? "The House Journals for these years survive only as newspaper reconstructions with almost no roll calls."
      : "No verified roll calls for it.";
    return `<p class="muted small">No ${CHAMBER[other]} map for this legislature. ${why} <a href="#/about">More</a></p>`;
  }

  // ---------- map page
  const BLOC_LETTER = "ABC";
  // where a bloc sits, in words, from its place among the others (they come sorted left to right)
  // (by the axis that sets the blocs apart: "left", "lower left", "upper"...; three blocs: by their order across)
  function blocWhere(B, i) {
    if (B.blocs.length === 3) return ["left on the map", "in the middle", "right on the map"][i];
    const [a, b] = B.blocs, dx = Math.abs((b.x ?? 0) - (a.x ?? 0)), dy = (b.y ?? 0) - (a.y ?? 0);
    const me = B.blocs[i], other = B.blocs[1 - i];
    const lr = me.x < other.x ? "left" : "right", ud = me.y < other.y ? "lower" : "upper";
    const where = Math.abs(dy) < dx / 2 ? lr : dx < Math.abs(dy) / 2 ? ud : `${ud} ${lr}`;
    return `${where} on the map`;
  }
  // a known division these blocs can be checked against (blocs.json "checks")
  function checkNote(S, B) {
    const c = (BLOCS.checks || []).find(x => x.key === `${S.leg}${S.ch}`);
    if (!c) return "";
    if (c.kind === "dirty_thirty")
      return `<p>Bloc ${BLOC_LETTER[c.bloc]} holds ${c.found === c.n ? "all" : c.found + " of the"} ${c.n} members of the "Dirty Thirty", the Democrats and Republicans who stood against Speaker Gus Mutscher during the Sharpstown scandal; the group had grown to about 35 by the end of the 1971 session.</p>`;
    if (c.kind === "wet_vote" && c.tallies.length === 2) {
      const i = c.tallies[0].Y > c.tallies[0].N ? 0 : 1;
      return `<p>The blocs split wet against dry: on a motion by Terrell of Bexar, a wet, to table sending a bill to the Committee on Liquor Traffic, Bloc ${BLOC_LETTER[i]} voted ${c.tallies[i].Y} to ${c.tallies[i].N} for it and Bloc ${BLOC_LETTER[1 - i]} ${c.tallies[1 - i].N} to ${c.tallies[1 - i].Y} against.</p>`;
    }
    return "";
  }
  function blocsSection(S, B) {
    const name = id => S.members.find(m => m.id === id)?.n || "";
    const parties = b => { const c = { D: 0, R: 0, O: 0 }; b.members.forEach(id => { const m = S.members.find(x => x.id === id); if (m) c[m.p]++; }); return c; };
    const opp = B.blocs.length === 2 ? B.opposed["0-1"] : null;
    const reps = S.members.filter(m => m.p === "R").length;
    // how far the blocs follow the parties: members in a bloc whose majority is their own party
    const counts = B.blocs.map(parties);
    const grouped = counts.reduce((a, c) => a + c.D + c.R, 0);
    const pure = counts.reduce((a, c) => a + Math.max(c.D, c.R), 0) / (grouped || 1);
    const how = reps < 5 ? "With one party holding nearly every seat, these blocs are the Legislature's real divisions: factions inside the Democratic Party."
      : pure >= 0.9 ? "The blocs largely follow the parties; the counts below say how far."
      : "The blocs cut across the parties: the counts below show how.";
    return `<h2>Voting blocs</h2>
      <p>Grouping members by how often they voted alike on this legislature's contested votes, not by where the map puts them, sorts them into ${B.blocs.length} blocs${opp != null ? `, whose majorities voted opposite ways on ${Math.round(100 * opp)}% of those votes` : ""}. ${how}</p>
      <div class="blocs">${B.blocs.map((b, i) => { const c = counts[i]; return `<div class="card">
        <h3><i class="dot" style="background:var(--bloc${i + 1})"></i> Bloc ${BLOC_LETTER[i]} <span class="muted" style="font-weight:500">${b.members.length} members, ${blocWhere(B, i)}</span></h3>
        <p class="small">Members agreed with each other on ${Math.round(100 * b.cohesion)}% of votes.${reps >= 5 ? ` ${c.D} Democrat${c.D === 1 ? "" : "s"}, ${c.R} Republican${c.R === 1 ? "" : "s"}${c.O ? `, ${c.O} other or not recorded` : ""}.` : ""}</p>
        <p class="small"><span class="muted">At its core:</span> ${b.core.map(id => esc(name(id))).join(", ")}</p></div>`; }).join("")}</div>
      ${checkNote(S, B)}
      <p class="small muted">Members who shared fewer than 20 contested votes with most others aren't grouped. <a href="#/trends">More on the method</a></p>`;
  }

  function renderMap(S, r) {
    const ax = AX;
    const scaled = S.members.filter(m => m.x != null);
    const unscaled = S.members.filter(m => m.x == null);
    const B = BLOCS && BLOCS.sessions && BLOCS.sessions[`${S.leg}${S.ch}`];
    const blocOf = {};
    if (B) B.blocs.forEach((b, i) => b.members.forEach(id => { blocOf[id] = i; }));
    const partyLegend = `<span><i class="dot" style="background:var(--dem)"></i>Democrat</span><span><i class="dot" style="background:var(--rep)"></i>Republican</span>${S.members.some(m => m.p === "O") ? '<span><i class="dot" style="background:var(--oth)"></i>Other or not recorded</span>' : ""}`;
    const blocLegend = B ? B.blocs.map((b, i) => `<span><i class="dot" style="background:var(--bloc${i + 1})"></i>Bloc ${BLOC_LETTER[i]}</span>`).join("") + '<span><i class="dot" style="background:var(--line)"></i>Not grouped</span>' : "";
    app.innerHTML = `
      ${controls(S, r, "map")}
      ${missingNote(S)}
      <h1>Texas ${CHAMBER[S.ch]}, ${ord(S.leg)} Legislature <span class="muted" style="font-weight:500">${esc(S.years)}</span></h1>
      <p class="lede">Each dot is a member, placed by every contested record vote of their career. Members who vote alike sit close together. The two axes are the two main ways Texas votes split, and what they stand for has changed since 1846, so what they mean in these years is explained below the map. <a href="#/about">How this works</a></p>
      <div class="mapwrap">
        <div class="card">
          ${B ? `<div class="colorby small"><span class="muted">Color by</span> <span class="seg" role="group" aria-label="Color the dots by"><a href="#" data-c="party" class="on">Party</a><a href="#" data-c="bloc">Voting bloc</a></span></div>` : ""}
          <div class="plot" id="plot"></div>
          <div class="legend" id="legend">${partyLegend}</div>
        </div>
        <div>
          <input type="search" id="find" placeholder="Find a member" aria-label="Find a member" list="names">
          <datalist id="names">${S.members.map(m => `<option value="${esc(m.n)}">`).join("")}</datalist>
          <div class="card panel" id="panel" style="margin-top:12px"><p class="muted small" style="margin:0">Select a dot or search for a member to see their scores.</p></div>
        </div>
      </div>
      <div class="eranote">
        <h2>Reading the axes${ax.era.label ? `, ${esc(ax.era.label)}` : ""}</h2>
        <p><b>Left to right: ${esc(ax.x.name)}.</b> ${esc(ax.x.desc)}</p>
        <p><b>Up and down: ${esc(ax.y.name)}.</b> ${esc(ax.y.desc)}</p>
      </div>
      ${B ? blocsSection(S, B) : ""}
      <h2>All members, ${esc(ax.x.neg.toLowerCase())} to ${esc(ax.x.pos.toLowerCase())}</h2>
      <div class="tablewrap tall"><table id="tbl"><thead><tr>
        <th class="num">#</th><th>Member</th><th>Party</th><th class="num">District</th>
        <th class="num" title="${esc(ax.x.name)}">${esc(ax.x.short)}</th><th class="num" title="${esc(ax.y.name)}">${esc(ax.y.short)}</th>
      </tr></thead><tbody></tbody></table></div>
      ${unscaled.length ? `<p class="muted small">Not placed (fewer than 20 contested votes): ${unscaled.map(m => esc(m.n)).join(", ")}.</p>` : ""}`;
    wireControls(S, "map");

    let P = plot(document.getElementById("plot"), S.members, { onClick: m => select(m.id) });
    let current = null;
    const wantBloc = new URLSearchParams(location.hash.split("?")[1] || "").get("color") === "bloc";
    document.querySelectorAll(".colorby a").forEach(a => a.addEventListener("click", e => {
      e.preventDefault();
      document.querySelectorAll(".colorby a").forEach(x => x.classList.toggle("on", x === a));
      const byBloc = a.dataset.c === "bloc";
      document.getElementById("plot").innerHTML = "";
      P = plot(document.getElementById("plot"), S.members, { onClick: m => select(m.id), fill: byBloc ? m => (m.id in blocOf ? `var(--bloc${blocOf[m.id] + 1})` : "var(--line)") : null });
      if (current != null) P.highlight(current);
      document.getElementById("legend").innerHTML = byBloc ? blocLegend : partyLegend;
    }));
    if (B && wantBloc) document.querySelector('.colorby a[data-c="bloc"]').click();
    const rows = [...scaled].sort((a, b) => a.x - b.x);
    document.querySelector("#tbl tbody").innerHTML = rows.map((m, i) => `
      <tr class="click" data-id="${m.id}"><td class="num">${i + 1}</td><td>${esc(m.n)}</td><td class="${partyClass(m.p)}">${m.p === "O" ? esc(m.pn || "–") : m.p}</td>
      <td class="num">${esc(m.d)}</td><td class="num">${num(m.x)}</td><td class="num">${num(m.y)}</td></tr>`).join("");
    document.querySelectorAll("#tbl tr.click").forEach(tr => tr.addEventListener("click", () => {
      select(+tr.dataset.id);
      document.getElementById("plot").scrollIntoView({ behavior: "smooth", block: "center" });
    }));
    const find = document.getElementById("find");
    find.addEventListener("change", () => {
      const q = find.value.trim().toLowerCase();
      const m = S.members.find(x => x.n.toLowerCase() === q) || S.members.find(x => x.n.toLowerCase().includes(q));
      if (m) select(m.id);
    });

    function select(id) {
      const m = S.members.find(x => x.id === id);
      current = id;
      P.highlight(id);
      const rank = rows.findIndex(x => x.id === id);
      const sameParty = rows.filter(x => x.p === m.p);
      const prank = sameParty.findIndex(x => x.id === id);
      document.getElementById("panel").innerHTML = `
        <h3>${esc(m.n)}</h3>
        <div class="${partyClass(m.p)}">${partyOf(m)}${m.d ? ` · District ${esc(m.d)}` : ""}</div>
        ${m.x == null ? `<p class="muted small" style="margin-top:10px">Cast fewer than 20 contested votes this session, so not placed on the map.</p>` : `
        <dl class="kv">
          <dt>${esc(ax.x.short)}</dt><dd>${num(m.x)} <span class="muted small">(${esc(ax.x.neg.toLowerCase())} −1 … +1 ${esc(ax.x.pos.toLowerCase())})</span></dd>
          <dt>${esc(ax.y.short)}</dt><dd>${num(m.y)}</dd>
          <dt>Rank</dt><dd>${rank + 1} of ${rows.length} from the ${esc(ax.x.neg.toLowerCase())} end${m.p === "O" ? "" : `; ${prank + 1} of ${sameParty.length} ${PARTY[m.p]}s`}</dd>
          <dt>Votes</dt><dd>${m.v.toLocaleString()} contested votes this session (the position reflects their whole career)</dd>
          ${m.id in blocOf ? `<dt>Bloc</dt><dd><i class="dot" style="background:var(--bloc${blocOf[m.id] + 1})"></i> Bloc ${BLOC_LETTER[blocOf[m.id]]} (${blocWhere(B, blocOf[m.id])})</dd>` : ""}
        </dl>`}
        <button id="clr" type="button">Clear</button>`;
      document.getElementById("clr").addEventListener("click", () => {
        current = null;
        P.highlight(null);
        document.getElementById("panel").innerHTML = '<p class="muted small" style="margin:0">Select a dot or search for a member to see their scores.</p>';
      });
    }
  }

  // ---------- votes list
  const contested = rc => rc.y + rc.n > 0 && Math.min(rc.y, rc.n) / (rc.y + rc.n) >= 0.025;
  // A short title for a roll call, from the journal's sentence and the context around it
  const BILL_RE = /^(?:C\.?\s?S\.?\s?)?[HS]\.?\s?(?:B|J\.?\s?R|C\.?\s?R|R)\.?\s*\d+\b\s*,?\s*/i;
  const who = s => s.replace(/^On behalf of [^,]+,\s*/i, "").replace(/^Representatives?\s+/, "Rep. ").replace(/^Senators?\s+/, "Sen. ").replace(/\s*[:.]\s*$/, "");
  function amendmentBy(a) {
    const m = (a || "").match(/(Representatives?|Senators?)\s+(.+?)\s+offered the following amendments?\s+to\s+(.+?)\s*:?\s*$/i);
    return m ? `Amendment by ${m[1].startsWith("S") ? "Sen." : "Rep."} ${m[2]}` : "";
  }
  // the journal's parliamentary phrasing, in plain English
  const MOTIONS = [
    [/moved to suspend the regular order of business to take up for consideration .*?on its (second|third) reading/i, m => `Motion to take up on ${m[1]} reading`],
    [/moved that .*?(Constitutional Rule|constitutional rule) requiring bills to be read on three several days be suspended/i, () => "Motion to suspend the three-readings rule"],
    [/moved to table (Amendment No\. \d+|the amendment|the motion[^.]*)/i, m => `Motion to table ${m[1].replace(/^the /, "the ")}`],
    [/moved to concur in (the )?(House|Senate) amendments?/i, m => `Motion to concur in ${m[2]} amendments`],
    [/moved to adopt the conference committee report/i, () => "Motion to adopt the conference committee report"],
    [/moved (the )?previous question/i, () => "Motion for the previous question"],
    [/moved to reconsider the vote by which/i, () => "Motion to reconsider"],
    [/moved to postpone/i, () => "Motion to postpone"],
    [/moved to (re-?refer|recommit)/i, () => "Motion to send back to committee"],
    [/moved to adjourn/i, () => "Motion to adjourn"],
  ];
  function shortMotion(m) {
    const name = (m.match(/^(?:On behalf of [^,]+,\s*)?(Representatives?|Senators?)\s+([A-Z][\w'’.-]*(?:,\s*[A-Z]\.)?(?:\s+[A-Z][\w'’.-]*)?)\s+moved/) || []);
    const by = name[2] ? ` (${name[1].startsWith("S") ? "Sen." : "Rep."} ${name[2]})` : "";
    for (const [re, fn] of MOTIONS) {
      const x = m.match(re);
      if (x) return fn(x) + by;
    }
    return who(m);
  }
  // the scanned journals' wording (before 1973): "Yeas and nays were demanded, and the motion to
  // table was lost by the following vote: Terrell of Bexar then moved to table ..." -> the motion,
  // with its mover where the journal names one ("Motion by Terrell of Bexar to table ...")
  function oldStyle(t) {
    const m = t.match(/^(.*?)[,;]?\s*(?:by|on) the following vote\W*(.*)$/i);
    if (!m) return null;
    let [, head, tail] = m;
    head = head.replace(/^.*?\b(?:were demanded|was ordered|was called)\W+(?:and\s+)?/i, "").replace(/^(?:question\W+|then\s+)/i, "")
      .replace(/^the\s+/i, "").replace(/\s+(?:was|were)\s+(?:lost|adopted|passed|tabled|refused|rejected|carried|sustained|overruled|not adopted|laid on the table)\b.*$/i, "")
      .replace(/\s+(?:prevailed|carried|failed)\b.*$/i, "").trim();
    const mover = (tail || "").match(/(?:^|\.\s+)((?:Senator\s+|Mr\.\s+)?[A-Z][\w'’.-]+(?:\s+of\s+[A-Z][\w'’.-]+)?)\s+(?:then\s+)?moved\s+(?:that\s+)?(?:to\s+)?(.{4,90}?)(?:[.:;]|$)/);
    if (mover) return `Motion by ${mover[1]}: ${mover[2].replace(/^to\s+/i, "")}`;
    const q = t.match(/\bSha[l1i]{2}\s+(.{3,110}?)\?/i);         // "Question—Shall the bill be passed?"
    if (q) return `Shall ${q[1]}?`;
    head = head.replace(/\s+pre\S{1,3}ailed\b.*$/i, "");
    return head ? head.charAt(0).toUpperCase() + head.slice(1, 120) : null;
  }
  function describe(S, rc) {
    let t = (rc.t || "").replace(/\s+/g, " ").trim();
    const old = S.leg < 63 ? oldStyle(t) : null;
    if (old) return old;
    let verified = false;
    if (/roll of those voting|verified vote|vote was verified/i.test(t)) { t = ""; verified = true; }
    if (rc.b && BILL_RE.test(t)) {              // "SB 40, as amended, was passed" -> "Passed, as amended"
      let rest = t.replace(BILL_RE, "");
      const amended = /^as amended,?\s*/i.test(rest);
      rest = rest.replace(/^as amended,?\s*/i, "").replace(/^was\s+/i, "").replace(/\band was\b/gi, "and");
      t = rest.charAt(0).toUpperCase() + rest.slice(1) + (amended ? ", as amended" : "");
    }
    const generic = !t || /^the motion( to [a-z ]+)? (prevailed|was lost)|^the (bill|resolution) was/i.test(t);
    if (generic && rc.m) {
      t = shortMotion(rc.m);                       // the outcome shows as a badge / check mark
    } else if (!t && rc.a) {
      t = amendmentBy(rc.a) || who(rc.a);
    }
    t = t || "Record vote";
    return verified ? `Verified vote: ${t}` : t;
  }
  const passed = rc => (/prevailed|was passed|were passed|was adopted|were adopted|finally passed|was ordered|concurred|was confirmed|were confirmed/i.test(rc.t) ? true : /was lost|failed|was not adopted|was refused|was rejected|did not/i.test(rc.t) ? false : null);

  function renderVotes(S, r) {
    const q = new URLSearchParams(location.hash.split("?")[1] || "");
    const sessions = Object.entries(S.sessions);
    app.innerHTML = `
      ${controls(S, r, "votes")}
      <h1>Record votes · Texas ${CHAMBER[S.ch]}, ${ord(S.leg)} Legislature</h1>
      <p class="lede">Every record vote in the journal${sessions.length > 1 ? ", across all sessions" : ""}. Contested votes (at least 2.5% on the losing side) are the ones used to place members on the map.</p>
      <div class="controls">
        <input type="search" id="q" placeholder="Search: bill number, topic, member" aria-label="Search votes" value="${esc(q.get("q") || "")}">
        ${sessions.length > 1 ? `<select id="sess" aria-label="Session"><option value="">All sessions</option>${sessions.map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>` : ""}
        <select id="sort" aria-label="Sort"><option value="order">In order</option><option value="close">Closest first</option></select>
        <label class="check"><input type="checkbox" id="all"> Include near-unanimous</label>
      </div>
      <p class="muted small" id="count"></p>
      <ul class="votes-list" id="list"></ul>
      <button class="more" id="more" type="button" hidden>Show more</button>`;
    wireControls(S, "votes");
    const els = ["q", "sess", "sort", "all"].map(id => document.getElementById(id));
    let shown = 150, filtered = [];
    const caps = S.caps;
    const apply = () => {
      const [qq, ss, so, al] = els;
      const text = (qq.value || "").trim().toLowerCase();
      const words = text.split(/\s+/).filter(Boolean);
      filtered = S.rc.filter(rc => (al.checked || contested(rc)) && (!ss || !ss.value || rc.s === ss.value) &&
        (!words.length || words.every(w => `${rc.b} ${rc.t} ${rc.m} ${rc.a} ${rc.c != null ? caps[rc.c] : ""}`.toLowerCase().includes(w))));
      if (so.value === "close") filtered = [...filtered].sort((a, b) => Math.abs(a.y - a.n) / (a.y + a.n) - Math.abs(b.y - b.n) / (b.y + b.n));
      shown = 150;
      draw();
    };
    const draw = () => {
      document.getElementById("count").textContent = `${filtered.length.toLocaleString()} vote${filtered.length === 1 ? "" : "s"}`;
      document.getElementById("list").innerHTML = filtered.slice(0, shown).map(rc => {
        const tot = rc.y + rc.n || 1, ok = passed(rc);
        return `<li><a href="#/${S.leg}/${S.ch}/vote/${encodeURIComponent(rc.id)}">
          <span class="date">${fmtDate(rc.dt)}</span>
          <span class="what">${rc.b ? `<b>${esc(rc.b)}</b> · ` : ""}${esc(describe(S, rc))}${rc.c != null ? `<br><span>${esc(trimCap(caps[rc.c]))}</span>` : ""}</span>
          <span class="tally">${rc.y}–${rc.n}${ok === true ? " ✓" : ok === false ? " ✗" : ""}<span class="bar" aria-hidden="true"><i style="width:${(100 * rc.y) / tot}%;background:var(--accent)"></i><i style="width:${(100 * rc.n) / tot}%;background:var(--muted)"></i></span></span>
        </a></li>`;
      }).join("");
      document.getElementById("more").hidden = shown >= filtered.length;
    };
    document.getElementById("more").addEventListener("click", () => { shown += 300; draw(); });
    els.forEach(e => e && e.addEventListener(e.type === "search" ? "input" : "change", apply));
    apply();
  }
  const trimCap = c => (c || "").replace(/^A bill to be entitled An Act\s+/i, "").replace(/^relating to\s+/i, "Relating to ").slice(0, 160) + ((c || "").length > 160 ? "…" : "");

  // ---------- one vote
  function renderVote(S, r) {
    const rc = S.rc.find(x => x.id === r.id);
    if (!rc) { app.innerHTML = `<p>No record vote ${esc(r.id)} here. <a href="#/${S.leg}/${S.ch}/votes">All votes</a></p>`; return; }
    const members = S.members.map((m, i) => ({ ...m, vote: rc.v[i] }));
    const by = code => members.filter(m => m.vote === code || (code === "E" && m.vote === "M"));
    const parties = [...new Set(members.filter(m => m.vote !== "-").map(m => m.p))].sort();
    const ok = passed(rc);
    const cap = rc.c != null ? S.caps[rc.c] : "";
    const scaled = rc.mid != null;
    const correct = scaled ? Math.round(100 * (1 - rc.err / (rc.y + rc.n))) : null;
    const tilt = scaled ? Math.abs(Math.atan2(INDEX.w[1] ** 2 * rc.spr[1], INDEX.w[0] ** 2 * rc.spr[0]) * 180 / Math.PI) : null;
    const axisNote = tilt == null ? "" : tilt < 30 || tilt > 150 ? `split members left to right (${AX.x.short.toLowerCase()})` : tilt > 60 && tilt < 120 ? `split members up and down (${AX.y.short.toLowerCase()})` : "cut across both divides";
    app.innerHTML = `
      ${controls(S, r, "vote")}
      <p class="small"><a href="#/${S.leg}/${S.ch}/votes">← All ${CHAMBER[S.ch]} votes, ${ord(S.leg)} Legislature</a></p>
      <h1>${rc.b ? esc(rc.b) + ": " : ""}${esc(describe(S, rc))}</h1>
      <p class="muted">${fmtDate(rc.dt)} · ${esc(S.sessions[rc.s] || "")}${rc.r ? ` · Record ${rc.r}` : ""}${ok === true ? '<span class="badge">Passed</span>' : ok === false ? '<span class="badge">Failed</span>' : ""}</p>
      <div class="tally-big"><span>Yeas <b>${rc.y}</b></span><span>Nays <b>${rc.n}</b></span>${rc.p ? `<span>Present, not voting <b>${rc.p}</b></span>` : ""}<span class="muted">Absent <b>${by("A").length + by("E").length}</b></span></div>
      ${(cap || rc.m || rc.a || rc.t) ? `<div class="context">
        ${cap ? `<p><b>${esc(rc.b)}</b>: ${esc(cap)}</p>` : ""}
        ${rc.a ? `<p>${esc(rc.a)}</p>` : ""}${rc.m ? `<p>${esc(rc.m)}</p>` : ""}
        <p class="muted">Journal: “${esc(rc.t.replace(/[\s:;.,]+$/, ""))}.”${S.days[rc.f] ? ` <a href="${esc(S.days[rc.f] + (rc.pg ? "#page=" + rc.pg : ""))}" target="_blank" rel="noopener">Read it in the ${CHAMBER[S.ch]} Journal</a>` : ""}</p>
      </div>` : ""}
      <div class="mapwrap" style="margin-top:20px">
        <div class="card">
          <div class="plot" id="plot"></div>
          <div class="legend">
            <span><i class="dot" style="background:var(--muted)"></i>Yea</span><span><i class="dot ring" style="border-color:var(--muted)"></i>Nay</span>
            <span><i class="dot" style="background:var(--line);width:6px;height:6px"></i>Didn't vote</span>
            ${scaled ? `<span><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="currentColor" stroke-dasharray="5 3" stroke-width="1.5"/></svg>Cutting line (shaded side expected to vote yea)</span>` : ""}
          </div>
        </div>
        <div class="card">
          <h3>By party</h3>
          <table><thead><tr><th>Party</th><th class="num">Yea</th><th class="num">Nay</th><th class="num">PNV</th><th class="num">Absent</th></tr></thead><tbody>
          ${parties.map(p => `<tr><td class="${partyClass(p)}">${PARTY[p]}</td>${["Y", "N", "P"].map(c => `<td class="num">${by(c).filter(m => m.p === p).length}</td>`).join("")}<td class="num">${[...by("A"), ...by("E")].filter(m => m.p === p).length}</td></tr>`).join("")}
          </tbody></table>
          <p class="small muted" style="margin-top:12px">${scaled
            ? `This vote ${axisNote}. The map predicts ${correct}% of the yea and nay votes correctly.`
            : "This vote was near-unanimous, so it isn't used to place members on the map."}</p>
        </div>
      </div>
      ${["Y", "N", "P", "E", "A"].map(code => {
        const list = by(code);
        if (!list.length) return "";
        return `<h3 style="margin-top:22px">${VOTE[code]} <span class="muted" style="font-weight:500">${list.length}</span></h3>
          <ul class="names">${list.sort((a, b) => a.n.split(" ").pop().localeCompare(b.n.split(" ").pop())).map(m => `<li><span class="${partyClass(m.p)}">●</span> ${esc(m.n)}</li>`).join("")}</ul>`;
      }).join("")}`;
    wireControls(S, "vote");
    plot(document.getElementById("plot"), members, { votes: true, cut: scaled ? { mid: rc.mid, spr: rc.spr } : null });
  }

  // ---------- charts (trends page)
  // a line chart over the years: series [{label, color, pts: [{x, y}]}], a null y breaking the line;
  // opts {title, y: [lo, hi], fmt, x: [from, to]}. A crosshair finds the year; one tooltip lists every series.
  function lineChart(el, series, opts) {
    const W = 520, H = 240, m = { t: 14, r: 96, b: 26, l: 42 };
    const x = d3.scaleLinear().domain(opts.x || [1846, 2026]).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain(opts.y).nice().range([H - m.b, m.t]);
    const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", opts.title);
    const unit = W / Math.max(el.clientWidth || W, 1), fs = `${12 * unit}px`;
    const yt = y.ticks(4), xt = x.ticks(6);
    svg.append("g").selectAll("line").data(yt).join("line").attr("class", "gl").attr("x1", m.l).attr("x2", W - m.r).attr("y1", d => y(d)).attr("y2", d => y(d));
    svg.append("g").selectAll("text").data(yt).join("text").attr("class", "tk").style("font-size", fs)
      .attr("x", m.l - 6).attr("y", d => y(d)).attr("dy", "0.32em").attr("text-anchor", "end").text(opts.tick || opts.fmt);
    svg.append("g").selectAll("text").data(xt).join("text").attr("class", "tk").style("font-size", fs)
      .attr("x", d => x(d)).attr("y", H - 6).attr("text-anchor", "middle").text(d => d);
    const line = d3.line().defined(d => d.y != null).x(d => x(d.x)).y(d => y(d.y));
    const ends = [];
    series.forEach(s => {
      svg.append("path").attr("class", "series").attr("stroke", s.color).attr("d", line(s.pts));
      // a year with no neighbours on either side shows as a dot
      s.pts.forEach((d, i) => {
        if (d.y != null && s.pts[i - 1]?.y == null && s.pts[i + 1]?.y == null)
          svg.append("circle").attr("class", "pt").attr("cx", x(d.x)).attr("cy", y(d.y)).attr("r", 4).attr("fill", s.color);
      });
      const last = [...s.pts].reverse().find(d => d.y != null);
      if (last) ends.push({ s, x: x(last.x), y: y(last.y) });
    });
    // direct labels at the line ends, unless two would collide (the legend and the tooltip carry them then)
    const apart = ends.every((a, i) => ends.every((b, j) => i === j || Math.abs(a.y - b.y) > 15 * unit));
    if (apart) ends.forEach(e => svg.append("text").attr("class", "endlabel").style("font-size", fs).attr("x", e.x + 7).attr("y", e.y).attr("dy", "0.32em").text(e.s.label));
    const years = [...new Set(series.flatMap(s => s.pts.filter(d => d.y != null).map(d => d.x)))].sort((a, b) => a - b);
    const hair = svg.append("line").attr("class", "xhair").attr("y1", m.t).attr("y2", H - m.b).style("opacity", 0);
    const at = (s, yr) => s.pts.filter(d => d.y != null && Math.abs(d.x - yr) <= 1).sort((a, b) => Math.abs(a.x - yr) - Math.abs(b.x - yr))[0];
    svg.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent")
      .on("pointermove", e => {
        if (!years.length) return;
        const xv = x.invert(d3.pointer(e)[0]);
        const yr = years.reduce((a, b) => (Math.abs(b - xv) < Math.abs(a - xv) ? b : a), years[0]);
        hair.attr("x1", x(yr)).attr("x2", x(yr)).style("opacity", 1);
        const rows = series.map(s => { const d = at(s, yr); return `<div class="trow"><i class="keyline" style="background:${s.color}"></i><b>${d ? opts.fmt(d.y) : "–"}</b> <span class="muted">${esc(s.label)}${d && d.note ? ` · ${esc(d.note)}` : ""}</span></div>`; });
        showTip(e, `<div class="muted small">${yr}</div>${rows.join("")}`);
      })
      .on("pointerleave", () => { hair.style("opacity", 0); hideTip(); });
  }

  // a scatter: pts [{x, y, color, tip}], both axes −1…+1; the nearest point to the pointer gets the tooltip
  function scatter(el, pts, opts) {
    const W = 300, m = { t: 10, r: 12, b: 44, l: 46 }, H = W;
    const x = d3.scaleLinear().domain([-1, 1]).range([m.l, W - m.r]), y = d3.scaleLinear().domain([-1, 1]).range([H - m.b, m.t]);
    const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", opts.title);
    const unit = W / Math.max(el.clientWidth || W, 1), fs = `${11.5 * unit}px`;
    [-1, 0, 1].forEach(v => {
      svg.append("line").attr("class", "gl").attr("x1", x(v)).attr("x2", x(v)).attr("y1", m.t).attr("y2", H - m.b);
      svg.append("line").attr("class", "gl").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(v)).attr("y2", y(v));
      svg.append("text").attr("class", "tk").style("font-size", fs).attr("x", x(v)).attr("y", H - m.b + 15).attr("text-anchor", "middle").text(num(v, 0));
      svg.append("text").attr("class", "tk").style("font-size", fs).attr("x", m.l - 5).attr("y", y(v)).attr("dy", "0.32em").attr("text-anchor", "end").text(num(v, 0));
    });
    svg.append("text").attr("class", "tk").style("font-size", fs).attr("x", (m.l + W - m.r) / 2).attr("y", H - 6).attr("text-anchor", "middle").text(opts.xLabel);
    svg.append("text").attr("class", "tk").style("font-size", fs).attr("transform", `translate(12,${(m.t + H - m.b) / 2}) rotate(-90)`).attr("text-anchor", "middle").text(opts.yLabel);
    const dots = svg.append("g").selectAll("circle").data(pts).join("circle").attr("class", "pt")
      .attr("cx", d => x(d.x)).attr("cy", d => y(d.y)).attr("r", 4).attr("fill", d => d.color);
    const del = d3.Delaunay.from(pts, d => x(d.x), d => y(d.y));
    svg.append("rect").attr("x", 0).attr("y", 0).attr("width", W).attr("height", H).attr("fill", "transparent")
      .on("pointermove", e => {
        const [px, py] = d3.pointer(e), i = del.find(px, py);
        const near = i != null && Math.hypot(x(pts[i].x) - px, y(pts[i].y) - py) < 24 * unit;
        dots.classed("hl", (d, j) => near && j === i);
        if (near) showTip(e, pts[i].tip); else hideTip();
      })
      .on("pointerleave", () => { dots.classed("hl", false); hideTip(); });
  }

  // ---------- trends
  async function renderTrends() {
    const [T, C, BL] = await Promise.all([json("data/trends.json"), json("data/congress.json"), json("data/blocs.json").catch(() => ({}))]);
    document.title = "Trends · TX-NOMINATE";
    const legs = [...new Set(T.texas.map(r => r.leg))].sort((a, b) => a - b);
    const tex = (ch, f) => legs.map(l => { const r = T.texas.find(x => x.leg === l && x.ch === ch); return { x: r ? r.year : null, y: r && f(r) != null ? f(r) : null, note: r ? `${ord(l)} Legislature` : "" }; }).filter(d => d.x != null);
    const us = ch => T.congress.filter(r => r.ch === ch && r.who === "all" && r.year >= 1845).map(r => ({ x: r.year, y: Math.abs(r.gap), note: `${ord(r.congress)} Congress` }));
    const pct = v => `${Math.round(100 * v)}%`, sd = v => v.toFixed(1);
    const facet = id => `<div class="card chartcard"><h3>${id === "H" ? "House" : "Senate"}</h3><div class="chart" id="c-${id}"></div></div>`;
    const P = C.pairs, era = [[1846, 1900, "1846–1899"], [1900, 1950, "1900–1949"], [1950, 1980, "1950–1979"], [1980, 2100, "1980 on"]];
    const corr = (a, b) => { const n = a.length, ma = d3.mean(a), mb = d3.mean(b); const sa = d3.deviation(a), sb = d3.deviation(b); return n > 4 && sa && sb ? d3.sum(a.map((v, i) => (v - ma) * (b[i] - mb))) / ((n - 1) * sa * sb) : null; };
    app.innerHTML = `
      <h1>Trends, 1846–2025</h1>
      <p class="lede">How divided by party the Legislature has been, measured the way Voteview measures Congress, and how the left–right of Austin lines up with the left–right of Washington. For most of 1876–1970 one party held nearly every seat, so these party measures only exist for Reconstruction and the last half century; for the one-party years, each legislature's map shows its <a href="#/">voting blocs</a> instead.</p>

      <h2>How far apart the parties sit</h2>
      <p>The distance between the average Democrat and the average Republican on the map's left–right axis, in units of how spread out members are within the parties. It is scale-free, so the Legislature can sit beside Congress, measured the same way on Voteview's DW-NOMINATE scores. Shown where each party seats at least ${T.min_seats} members.</p>
      <div class="keys"><span><i class="keyline" style="background:var(--accent)"></i>Texas Legislature</span><span><i class="keyline" style="background:var(--us)"></i>Congress (Voteview)</span></div>
      <div class="facets">${facet("H")}${facet("S")}</div>
      <details><summary>Table: the party gap by legislature</summary><div class="tablewrap tall"><table><thead><tr><th>Legislature</th><th class="num">Year</th><th>Chamber</th><th class="num">Democrats</th><th class="num">Republicans</th><th class="num">Gap (SDs)</th><th class="num">Overlap</th></tr></thead><tbody>
        ${T.texas.filter(r => r.gap != null).map(r => `<tr><td>${ord(r.leg)}</td><td class="num">${r.year}</td><td>${CHAMBER[r.ch]}</td><td class="num">${r.n.D || 0}</td><td class="num">${r.n.R || 0}</td><td class="num">${sd(Math.abs(r.gap))}</td><td class="num">${pct(r.overlap)}</td></tr>`).join("")}
      </tbody></table></div></details>

      <h2>Party-line votes</h2>
      <p>The share of contested record votes on which most Democrats voted one way and most Republicans the other (Congressional Quarterly's "party unity votes").</p>
      <div class="facets"><div class="card chartcard"><h3>House</h3><div class="chart" id="u-H"></div></div><div class="card chartcard"><h3>Senate</h3><div class="chart" id="u-S"></div></div></div>

      <h2>Voting with the party</h2>
      <p>On those party-line votes, how often the average member voted with their party's majority (members with at least 10 such votes).</p>
      <div class="keys"><span><i class="keyline" style="background:var(--dem)"></i>Democrats</span><span><i class="keyline" style="background:var(--rep)"></i>Republicans</span></div>
      <div class="facets"><div class="card chartcard"><h3>House</h3><div class="chart" id="l-H"></div></div><div class="card chartcard"><h3>Senate</h3><div class="chart" id="l-S"></div></div></div>
      <details><summary>Table: party-line votes and party loyalty by legislature</summary><div class="tablewrap tall"><table><thead><tr><th>Legislature</th><th class="num">Year</th><th>Chamber</th><th class="num">Contested votes</th><th class="num">Party-line</th><th class="num">Democrats with party</th><th class="num">Republicans with party</th></tr></thead><tbody>
        ${T.texas.filter(r => r.unity_votes != null).map(r => `<tr><td>${ord(r.leg)}</td><td class="num">${r.year}</td><td>${CHAMBER[r.ch]}</td><td class="num">${r.contested.toLocaleString()}</td><td class="num">${pct(r.unity_votes)}</td><td class="num">${r.unity_D != null ? pct(r.unity_D) : "–"}</td><td class="num">${r.unity_R != null ? pct(r.unity_R) : "–"}</td></tr>`).join("")}
      </tbody></table></div></details>

      <h2>From Austin to Washington</h2>
      <p>${P.length} members of the Texas Legislature also served in Congress. Each dot is one of them: across, their place on this site's left–right axis; up, their first-dimension DW-NOMINATE score in Congress, from Voteview, grouped by when they entered Congress. Since about 1950 a Texan's place in Austin predicts their place in Washington well. Before that it hardly does: in the one-party Legislature, the main divide ran through the Democrats, and it didn't match the national one.</p>
      <div class="keys"><span><i class="dot" style="background:var(--dem)"></i>Democrat in Congress</span><span><i class="dot" style="background:var(--rep)"></i>Republican</span>${P.some(p => p.party === "O") ? '<span><i class="dot" style="background:var(--oth)"></i>Other</span>' : ""}</div>
      <div class="facets four">${era.map(([a, b, label], i) => `<div class="card chartcard"><h3>${label}</h3><p class="muted small" id="r-${i}"></p><div class="chart" id="s-${i}"></div></div>`).join("")}</div>
      <p id="dim2note"></p>
      <details><summary>Table: the ${P.length} members</summary><div class="tablewrap tall"><table><thead><tr><th>Member</th><th class="num">In the Legislature</th><th class="num">TX-NOMINATE</th><th class="num">In Congress</th><th class="num">DW-NOMINATE</th><th>Party in Congress</th></tr></thead><tbody>
        ${P.map(p => `<tr><td>${esc(p.n)}</td><td class="num">${p.tx_years[0]}–${p.tx_years[1]}</td><td class="num">${num(p.tx[0])}</td><td class="num">${p.vv_years[0]}–${p.vv_years[1]}</td><td class="num">${num(p.vv[0])}</td><td class="${partyClass(p.party)}">${PARTY[p.party] || "Other"}</td></tr>`).join("")}
      </tbody></table></div></details>
      <p class="small muted">Members are matched to Voteview's Texas delegation by name and dates, and each match was checked by hand (fathers and sons with the same name are told apart by when they served). Voteview: Lewis, Poole, Rosenthal, Boche, Rudkin and Sonnet, <i>Voteview: Congressional Roll-Call Votes Database</i>, voteview.com.</p>
      ${blocsNote(BL)}`;

    ["H", "S"].forEach(ch => {
      const all = [...tex(ch, r => (r.gap != null ? Math.abs(r.gap) : null)), ...us(ch)].map(d => d.y).filter(v => v != null);
      lineChart(document.getElementById(`c-${ch}`), [
        { label: `U.S. ${CHAMBER[ch]}`, color: "var(--us)", pts: us(ch) },
        { label: `Texas ${CHAMBER[ch]}`, color: "var(--accent)", pts: tex(ch, r => (r.gap != null ? Math.abs(r.gap) : null)) },
      ], { title: `Gap between the parties, Texas ${CHAMBER[ch]} and U.S. ${CHAMBER[ch]}`, y: [0, d3.max(all)], fmt: sd, tick: v => v.toFixed(0) });
      lineChart(document.getElementById(`u-${ch}`), [{ label: `Texas ${CHAMBER[ch]}`, color: "var(--accent)", pts: tex(ch, r => r.unity_votes) }],
        { title: `Party-line votes, Texas ${CHAMBER[ch]}`, y: [0, 1], fmt: pct });
      lineChart(document.getElementById(`l-${ch}`), [
        { label: "Democrats", color: "var(--dem)", pts: tex(ch, r => r.unity_D) },
        { label: "Republicans", color: "var(--rep)", pts: tex(ch, r => r.unity_R) },
      ], { title: `Voting with the party, Texas ${CHAMBER[ch]}`, y: [0.5, 1], fmt: pct });
    });
    // the earliest members' Texas scores against Voteview's second dimension instead
    const early = P.filter(p => p.vv_years[0] < 1900), r2 = corr(early.map(p => p.tx[0]), early.map(p => p.vv[1]));
    if (r2 != null && r2 > 0.3) document.getElementById("dim2note").textContent = `For the ${early.length} who entered Congress before 1900, their Texas left–right lines up instead with Voteview's second dimension (correlation ${r2.toFixed(2)}), which in those decades picked up sectional and currency fights rather than the party divide.`;
    era.forEach(([a, b], i) => {
      const sel = P.filter(p => p.vv_years[0] >= a && p.vv_years[0] < b);
      const r = corr(sel.map(p => p.tx[0]), sel.map(p => p.vv[0]));
      document.getElementById(`r-${i}`).textContent = `${sel.length} members${r != null ? ` · correlation ${r.toFixed(2)}` : ""}`;
      scatter(document.getElementById(`s-${i}`), sel.map(p => ({
        x: p.tx[0], y: p.vv[0], color: partyColor(p.party),
        tip: `<b>${esc(p.n)}</b><br>Texas ${p.tx_years[0]}–${p.tx_years[1]}: <b>${num(p.tx[0])}</b><br>Congress ${p.vv_years[0]}–${p.vv_years[1]}: <b>${num(p.vv[0])}</b> <span class="muted">(${PARTY[p.party] || "Other"})</span>`,
      })), { title: `Members who served in both, ${era[i][2]}`, xLabel: "In Austin: left–right →", yLabel: "In Congress: DW-NOMINATE →" });
    });
  }

  // the method behind each map's voting blocs, and two checks of it against the record
  function blocsNote(BL) {
    const n = BL.sessions ? Object.keys(BL.sessions).length : 0;
    if (!n) return "";
    const dt = (BL.checks || []).find(c => c.kind === "dirty_thirty"), wet = (BL.checks || []).find(c => c.kind === "wet_vote");
    const b32 = BL.sessions["32H"];
    return `<h2>Voting blocs</h2>
      <p>For most of 1876–1970 one party held nearly every seat, so the map's parties say little about how the Legislature divided. Each legislature's map therefore also sorts its members into voting blocs, from the votes alone: two members' agreement is the share of the contested votes they both cast on which they voted alike, members are clustered by that agreement, and the number of blocs (two or three) is the one that separates them best. ${n} legislatures and chambers have blocs; open any map and choose <b>Color by: Voting bloc</b>.</p>
      ${dt ? `<p>Two checks against the record. In the 1971 House, the votes put ${dt.found === dt.n ? `all ${dt.n} members` : `${dt.found} of the ${dt.n} members`} of the "Dirty Thirty", the Democrats and Republicans who stood against Speaker Gus Mutscher during the Sharpstown scandal, in one bloc of ${dt.bloc_size}, the size the group had reached by the end of the session (<a href="#/62/H?color=bloc">see the 1971 House</a>).` : "<p>"}${wet && b32 && wet.tallies.length === 2 ? (() => {
        const [no, yes] = wet.tallies[0].N - wet.tallies[0].Y >= wet.tallies[1].N - wet.tallies[1].Y ? wet.tallies : [wet.tallies[1], wet.tallies[0]];
        return ` In the 1911 House, the blocs split dry against wet: on a motion by Terrell of Bexar, a wet, to table sending a bill to the Committee on Liquor Traffic, one bloc voted ${no.N} to ${no.Y} against it and the other ${yes.Y} to ${yes.N} for it (<a href="#/32/H?color=bloc">see the 1911 House</a>).`;
      })() : ""}</p>`;
  }

  // ---------- data
  async function renderData() {
    const D = await json("data/data.json");
    app.innerHTML = `<div class="about">${D.html}</div>`;
    document.title = "Data · TX-NOMINATE";
  }

  // ---------- about
  async function renderAbout() {
    const A = await json("data/about.json");
    app.innerHTML = `<div class="about">${A.html}</div>`;
    document.title = "About · TX-NOMINATE";
  }

  addEventListener("hashchange", render);
  render();
})();
