/* TX-NOMINATE explorer: session maps and a roll call explorer, from data/*.json */
(() => {
  "use strict";
  const app = document.getElementById("app");
  const tip = document.getElementById("tip");
  const cache = {};
  let INDEX = null;

  const PARTY = { R: "Republican", D: "Democrat", O: "Other" };
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
      r.leg ||= INDEX.legislatures[0].leg;
      const meta = INDEX.legislatures.find(l => l.leg === r.leg);
      if (!meta) { location.hash = "#/"; return; }
      if (!meta.chambers[r.ch]) r.ch = Object.keys(meta.chambers)[0];
      const S = await json(`data/s/${r.leg}${r.ch}.json`);
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
    const ax = INDEX.axes;
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
        if (!opts.votes) return partyColor(m.p);
        if (m.vote === "Y") return partyColor(m.p);
        if (m.vote === "N") return "var(--panel)";
        return "var(--line)";
      })
      .attr("stroke", m => (opts.votes && !"YN".includes(m.vote) ? "none" : opts.votes && m.vote === "N" ? partyColor(m.p) : "var(--panel)"))
      .on("pointermove", (e, m) => showTip(e, `<b>${esc(m.n)}</b>${PARTY[m.p] || "Other"}${m.d ? `, District ${esc(m.d)}` : ""}${opts.votes ? `<br>${VOTE[m.vote] || "Not voting"}` : ""}<br><span class="muted">${esc(INDEX.axes.x.short)} ${num(m.x)} · ${esc(INDEX.axes.y.short)} ${num(m.y)}</span>`))
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

  // ---------- map page
  function renderMap(S, r) {
    const ax = INDEX.axes;
    const scaled = S.members.filter(m => m.x != null);
    const unscaled = S.members.filter(m => m.x == null);
    app.innerHTML = `
      ${controls(S, r, "map")}
      <h1>Texas ${CHAMBER[S.ch]}, ${ord(S.leg)} Legislature <span class="muted" style="font-weight:500">${esc(S.years)}</span></h1>
      <p class="lede">Each dot is a member, placed by every contested record vote of their career since 1995. Members who vote alike sit close together. Left to right is the main divide in Texas votes; up and down is the second-biggest. <a href="#/about">How to read this</a></p>
      <div class="mapwrap">
        <div class="card">
          <div class="plot" id="plot"></div>
          <div class="legend"><span><i class="dot" style="background:var(--dem)"></i>Democrat</span><span><i class="dot" style="background:var(--rep)"></i>Republican</span>${S.members.some(m => m.p === "O") ? '<span><i class="dot" style="background:var(--oth)"></i>Other</span>' : ""}</div>
        </div>
        <div>
          <input type="search" id="find" placeholder="Find a member" aria-label="Find a member" list="names">
          <datalist id="names">${S.members.map(m => `<option value="${esc(m.n)}">`).join("")}</datalist>
          <div class="card panel" id="panel" style="margin-top:12px"><p class="muted small" style="margin:0">Select a dot or search for a member to see their scores.</p></div>
        </div>
      </div>
      <h2>All members, ${esc(ax.x.neg.toLowerCase())} to ${esc(ax.x.pos.toLowerCase())}</h2>
      <div class="tablewrap tall"><table id="tbl"><thead><tr>
        <th class="num">#</th><th>Member</th><th>Party</th><th class="num">District</th>
        <th class="num" title="${esc(ax.x.name)}">${esc(ax.x.short)}</th><th class="num" title="${esc(ax.y.name)}">${esc(ax.y.short)}</th>
      </tr></thead><tbody></tbody></table></div>
      ${unscaled.length ? `<p class="muted small">Not placed (fewer than 20 contested votes): ${unscaled.map(m => esc(m.n)).join(", ")}.</p>` : ""}`;
    wireControls(S, "map");

    const P = plot(document.getElementById("plot"), S.members, { onClick: m => select(m.id) });
    const rows = [...scaled].sort((a, b) => a.x - b.x);
    document.querySelector("#tbl tbody").innerHTML = rows.map((m, i) => `
      <tr class="click" data-id="${m.id}"><td class="num">${i + 1}</td><td>${esc(m.n)}</td><td class="${partyClass(m.p)}">${m.p}</td>
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
      P.highlight(id);
      const rank = rows.findIndex(x => x.id === id);
      const sameParty = rows.filter(x => x.p === m.p);
      const prank = sameParty.findIndex(x => x.id === id);
      document.getElementById("panel").innerHTML = `
        <h3>${esc(m.n)}</h3>
        <div class="${partyClass(m.p)}">${PARTY[m.p] || "Other"}${m.d ? ` · District ${esc(m.d)}` : ""}</div>
        ${m.x == null ? `<p class="muted small" style="margin-top:10px">Cast fewer than 20 contested votes this session, so not placed on the map.</p>` : `
        <dl class="kv">
          <dt>${esc(ax.x.short)}</dt><dd>${num(m.x)} <span class="muted small">(${esc(ax.x.neg.toLowerCase())} −1 … +1 ${esc(ax.x.pos.toLowerCase())})</span></dd>
          <dt>${esc(ax.y.short)}</dt><dd>${num(m.y)}</dd>
          <dt>Rank</dt><dd>${rank + 1} of ${rows.length} from the ${esc(ax.x.neg.toLowerCase())} end; ${prank + 1} of ${sameParty.length} ${PARTY[m.p] ? PARTY[m.p] + "s" : "in party"}</dd>
          <dt>Votes</dt><dd>${m.v.toLocaleString()} contested votes this session (the position reflects their whole career)</dd>
        </dl>`}
        <button id="clr" type="button">Clear</button>`;
      document.getElementById("clr").addEventListener("click", () => {
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
  function describe(S, rc) {
    let t = (rc.t || "").replace(/\s+/g, " ").trim();
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
    const axisNote = tilt == null ? "" : tilt < 30 || tilt > 150 ? `split members along the main (${INDEX.axes.x.short.toLowerCase()}) divide` : tilt > 60 && tilt < 120 ? `split members along the second (${INDEX.axes.y.short.toLowerCase()}) divide` : "cut across both divides";
    app.innerHTML = `
      ${controls(S, r, "vote")}
      <p class="small"><a href="#/${S.leg}/${S.ch}/votes">← All ${CHAMBER[S.ch]} votes, ${ord(S.leg)} Legislature</a></p>
      <h1>${rc.b ? esc(rc.b) + ": " : ""}${esc(describe(S, rc))}</h1>
      <p class="muted">${fmtDate(rc.dt)} · ${esc(S.sessions[rc.s] || "")}${rc.r ? ` · Record ${rc.r}` : ""}${ok === true ? '<span class="badge">Passed</span>' : ok === false ? '<span class="badge">Failed</span>' : ""}</p>
      <div class="tally-big"><span>Yeas <b>${rc.y}</b></span><span>Nays <b>${rc.n}</b></span>${rc.p ? `<span>Present, not voting <b>${rc.p}</b></span>` : ""}<span class="muted">Absent <b>${by("A").length + by("E").length}</b></span></div>
      ${(cap || rc.m || rc.a || rc.t) ? `<div class="context">
        ${cap ? `<p><b>${esc(rc.b)}</b>: ${esc(cap)}</p>` : ""}
        ${rc.a ? `<p>${esc(rc.a)}</p>` : ""}${rc.m ? `<p>${esc(rc.m)}</p>` : ""}
        <p class="muted">Journal: “${esc(rc.t)}.”${S.days[rc.f] ? ` <a href="${esc(S.days[rc.f])}" target="_blank" rel="noopener">Read it in the ${CHAMBER[S.ch]} Journal</a>` : ""}</p>
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
          ${parties.map(p => `<tr><td class="${partyClass(p)}">${PARTY[p] || "Other"}</td>${["Y", "N", "P"].map(c => `<td class="num">${by(c).filter(m => m.p === p).length}</td>`).join("")}<td class="num">${[...by("A"), ...by("E")].filter(m => m.p === p).length}</td></tr>`).join("")}
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

  // ---------- about
  async function renderAbout() {
    const A = await json("data/about.json");
    app.innerHTML = `<div class="about">${A.html}</div>`;
    document.title = "About · TX-NOMINATE";
  }

  addEventListener("hashchange", render);
  render();
})();
