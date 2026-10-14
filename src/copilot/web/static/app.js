// Service Desk Copilot UI. Plain JavaScript, no build step.
// It runs against the FastAPI app (/api/...) or, in the static preview, against
// data embedded in the page as window.PREVIEW_DATA.
"use strict";

const PREVIEW = typeof window.PREVIEW_DATA !== "undefined";
let DATA = null;
let SETTINGS = null;
const state = { priority: "all", query: "", alertKind: "all" };

// ---------------------------------------------------------------------------
// Backend: the API, or the embedded preview data with changes kept in memory
// ---------------------------------------------------------------------------

const backend = {
  async load() {
    if (PREVIEW) {
      const data = window.PREVIEW_DATA;
      data.audit = data.audit || [];
      return data;
    }
    const response = await fetch("/api/data");
    if (!response.ok) throw new Error("The copilot API did not answer.");
    return response.json();
  },
  async settings() {
    if (PREVIEW) return window.PREVIEW_SETTINGS;
    const response = await fetch("/api/settings");
    return response.json();
  },
  async override(ticket, name, value) {
    const field = ticket.fields[name];
    const from = field.value;
    if (!PREVIEW) {
      const response = await fetch(`/api/tickets/${ticket.id}/fields/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
      if (!response.ok) throw new Error("The change was not saved.");
      Object.assign(field, await response.json());
    } else {
      if (!("ai_value" in field)) field.ai_value = field.value;
      field.value = value;
      field.overridden = true;
    }
    DATA.audit.push(entry(ticket, name, from, value, "override"));
  },
  async revert(ticket, name) {
    const field = ticket.fields[name];
    const from = field.value;
    if (!PREVIEW) {
      const response = await fetch(`/api/tickets/${ticket.id}/fields/${name}`, { method: "DELETE" });
      if (!response.ok) throw new Error("The change was not undone.");
      Object.assign(field, await response.json());
    } else {
      field.value = field.ai_value;
      field.overridden = false;
    }
    DATA.audit.push(entry(ticket, name, from, field.value, "revert"));
  },
};

function entry(ticket, field, from, to, action) {
  return { ticket: ticket.id, field, from, to, by: "You", at: new Date().toISOString(), action };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtDate = (iso) =>
  new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const fmtDay = (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

function initials(name) {
  return String(name || "?").split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
}

function ago(iso, now) {
  const minutes = Math.max(0, Math.round((new Date(now) - new Date(iso)) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

const priorityOf = (t) => t.fields.priority?.value || null;
const pill = (p) => `<span class="pill p-${p || "none"}">${p || "Not set"}</span>`;
const nowOf = () => DATA.tickets.reduce((max, t) => (t.observed_at > max ? t.observed_at : max), "");

function confidenceColour(c) {
  if (c >= 0.8) return "var(--mint-ink)";
  if (c >= 0.6) return "var(--sky-ink)";
  return "var(--butter-ink)";
}

const ICONS = {
  dashboard: '<path d="M4 13h6V4H4zm10 7h6V11h-6zM4 20h6v-5H4zm10-11h6V4h-6z"/>',
  tickets: '<path d="M4 6h16v4a2 2 0 0 0 0 4v4H4v-4a2 2 0 0 0 0-4z"/><path d="M14 6v12" stroke-dasharray="2 2"/>',
  alerts: '<path d="M12 4a6 6 0 0 0-6 6v4l-2 3h16l-2-3v-4a6 6 0 0 0-6-6z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  evaluation: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4.9a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.5a7 7 0 0 0-2 1.2l-2.4-.9-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-.9a7 7 0 0 0 2 1.2L10 21h4l.5-2.5a7 7 0 0 0 2-1.2l2.4.9 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  review: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  chase: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  inbox: '<path d="M3 13l3-8h12l3 8v6H3z"/><path d="M3 13h5l1 3h6l1-3h5"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3600);
}

function allAlerts() {
  return DATA.tickets.flatMap((t) => t.alerts.map((a) => ({ ...a, ticket: t })))
    .sort((a, b) => (a.at < b.at ? 1 : -1));
}

// ---------------------------------------------------------------------------
// Charts (inline SVG, drawn to one scale)
// ---------------------------------------------------------------------------

function barChart(items) {
  const w = 460, h = 210, left = 34, bottom = 26, top = 12;
  const max = Math.max(...items.map((i) => i.value), 1);
  const step = Math.ceil(max / 4 / 5) * 5 || 1;
  const ceiling = step * 4;
  const slot = (w - left - 10) / items.length;
  const y = (v) => top + (h - top - bottom) * (1 - v / ceiling);
  let svg = "";
  for (let v = 0; v <= ceiling; v += step) {
    svg += `<line class="grid-line" x1="${left}" x2="${w - 6}" y1="${y(v)}" y2="${y(v)}"/>`;
    svg += `<text x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  }
  items.forEach((item, i) => {
    const bw = Math.min(46, slot * 0.6);
    const x = left + slot * i + (slot - bw) / 2;
    svg += `<rect x="${x}" y="${y(item.value)}" width="${bw}" height="${y(0) - y(item.value)}" rx="10" fill="${item.colour}"><title>${item.label}: ${item.value}</title></rect>`;
    svg += `<text x="${x + bw / 2}" y="${y(item.value) - 6}" text-anchor="middle" style="fill:var(--ink);font-weight:700">${item.value}</text>`;
    svg += `<text x="${x + bw / 2}" y="${h - 8}" text-anchor="middle">${item.label}</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Open tickets by priority">${svg}</svg>`;
}

function lineChart(series, labels) {
  const w = 560, h = 220, left = 34, bottom = 26, top = 14, right = 26;
  const max = Math.max(...series.flatMap((s) => s.values), 1);
  const step = Math.ceil(max / 4) || 1;
  const ceiling = step * 4;
  const x = (i) => left + ((w - left - right) * i) / Math.max(labels.length - 1, 1);
  const y = (v) => top + (h - top - bottom) * (1 - v / ceiling);
  let svg = "";
  for (let v = 0; v <= ceiling; v += step) {
    svg += `<line class="grid-line" x1="${left}" x2="${w - right}" y1="${y(v)}" y2="${y(v)}"/>`;
    svg += `<text x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  }
  const every = Math.ceil(labels.length / 7);
  labels.forEach((label, i) => {
    const last = i === labels.length - 1 && i % every >= every / 2;
    if (i % every === 0 || last) {
      svg += `<text x="${x(i)}" y="${h - 8}" text-anchor="middle">${label}</text>`;
    }
  });
  for (const s of series) {
    const points = s.values.map((v, i) => `${x(i)},${y(v)}`);
    const area = `M${x(0)},${y(0)} L${points.join(" L")} L${x(s.values.length - 1)},${y(0)} Z`;
    svg += `<path d="${area}" fill="${s.colour}" opacity="0.12"/>`;
    svg += `<path d="M${points.join(" L")}" fill="none" stroke="${s.colour}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>`;
    const last = s.values.length - 1;
    svg += `<circle cx="${x(last)}" cy="${y(s.values[last])}" r="5" fill="${s.colour}" stroke="var(--card)" stroke-width="2"/>`;
  }
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Emails per day">${svg}</svg>`;
}

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

function dashboard() {
  const tickets = DATA.tickets;
  const now = nowOf();
  const flagged = tickets.reduce((n, t) => n + t.flagged.length, 0);
  const chasing = tickets.filter((t) => t.alerts.some((a) => a.kind === "chase")).length;
  const breached = tickets.filter((t) => t.alerts.some((a) => a.kind === "silence")).length;
  const colours = { P1: "var(--rose-ink)", P2: "var(--peach-ink)", P3: "var(--butter-ink)", P4: "var(--mint-ink)", P5: "var(--sky-ink)" };
  const fills = { P1: "var(--rose)", P2: "var(--peach)", P3: "var(--butter)", P4: "var(--mint)", P5: "var(--sky)" };
  const byPriority = ["P1", "P2", "P3", "P4", "P5"].map((p) => ({
    label: p, value: tickets.filter((t) => priorityOf(t) === p).length, colour: fills[p], ink: colours[p],
  }));

  // Emails per day, inbound and outbound, over the last 14 days of data.
  const end = new Date(now);
  const days = [...Array(14)].map((_, i) => {
    const d = new Date(end);
    d.setDate(end.getDate() - 13 + i);
    return d.toISOString().slice(0, 10);
  });
  const count = (outbound) => days.map((d) =>
    tickets.reduce((n, t) => n + t.emails.filter((e) => e.outbound === outbound && e.date.slice(0, 10) === d).length, 0));
  const series = [
    { name: "From clients", values: count(false), colour: "var(--accent)" },
    { name: "Replies from the desk", values: count(true), colour: "var(--mint-ink)" },
  ];

  const groups = {};
  for (const t of tickets) {
    const g = t.fields.assignment_group?.value || "Not routed";
    groups[g] = (groups[g] || 0) + 1;
  }
  const groupRows = Object.entries(groups).sort((a, b) => b[1] - a[1]);
  const top = groupRows[0]?.[1] || 1;
  const recent = allAlerts().slice(0, 6);

  return `
  <div class="grid tiles">
    <a class="tile t-lavender" href="#tickets">${tileBody("inbox", tickets.length, "Open tickets")}</a>
    <a class="tile t-peach" href="#tickets">${tileBody("review", flagged, "Fields to check")}</a>
    <a class="tile t-rose" href="#alerts">${tileBody("chase", chasing, "Clients chasing")}</a>
    <a class="tile t-sky" href="#alerts">${tileBody("clock", breached, "Past response target")}</a>
  </div>
  <div class="grid two">
    <section class="card">
      <div class="card-head"><h2>Open tickets by priority</h2><span class="hint">From the AI's priority, after any overrides</span></div>
      ${barChart(byPriority)}
    </section>
    <section class="card">
      <div class="card-head"><h2>Emails per day</h2>
        <div class="legend">${series.map((s) => `<span><i style="background:${s.colour}"></i>${s.name}</span>`).join("")}</div>
      </div>
      ${lineChart(series, days.map((d) => fmtDay(d)))}
    </section>
  </div>
  <div class="grid two">
    <section class="card">
      <div class="card-head"><h2>Needs attention</h2><a class="hint" href="#alerts">All alerts</a></div>
      <div class="list">${recent.map(alertItem).join("") || '<p class="muted">Nothing needs attention.</p>'}</div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Tickets by team</h2><span class="hint">Assignment group</span></div>
      <div class="bars">${groupRows.map(([g, n]) => `
        <div class="bar-row"><span>${esc(g)}</span><span class="track"><span class="fill" style="width:${(100 * n) / top}%"></span></span><span class="n">${n}</span></div>`).join("")}
      </div>
    </section>
  </div>`;
}

function tileBody(name, value, label) {
  return `<span class="icon">${icon(name)}</span><span><span class="num">${value}</span><br><span class="lbl">${label}</span></span>`;
}

function alertItem(a) {
  const t = a.ticket;
  const kind = a.kind === "chase" ? '<span class="pill chase">Chasing</span>' : '<span class="pill silence">No response</span>';
  const who = a.notify === "team_lead" ? "team lead" : "assigned engineer";
  return `<a class="list-item" href="#ticket-${t.id}">
    ${kind}
    <span class="grow"><b>${esc(t.subject)}</b><small>${esc(t.fields.client_company?.value || "Unknown client")} · ${esc(a.reason)} Notifies the ${who}.</small></span>
    <span class="muted">${fmtDate(a.at)}</span></a>`;
}

function tickets() {
  const now = nowOf();
  const q = state.query.toLowerCase();
  const rows = DATA.tickets
    .filter((t) => state.priority === "all" || priorityOf(t) === state.priority)
    .filter((t) => !q || [t.subject, t.caller?.name, t.fields.client_company?.value, t.number].join(" ").toLowerCase().includes(q))
    .slice()
    .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  const chip = (p, label) => `<button class="chip" data-priority="${p}" aria-pressed="${state.priority === p}">${label}</button>`;
  return `
  <div class="chips">${chip("all", "All")}${["P1", "P2", "P3", "P4", "P5"].map((p) => chip(p, p)).join("")}</div>
  <section class="card">
    <div class="card-head"><h2>${rows.length} tickets</h2><span class="hint">Newest activity first</span></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Ticket</th><th>Subject</th><th>Caller</th><th>Priority</th><th>Assignment group</th><th>AI</th><th>Updated</th></tr></thead>
      <tbody>${rows.map((t) => `
        <tr data-ticket="${t.id}">
          <td class="ref">#${t.number}</td>
          <td><div class="subject">${esc(t.subject)}</div><small class="muted">${esc(t.fields.client_company?.value || "Unknown client")}</small></td>
          <td><div class="who"><span class="avatar">${initials(t.caller?.name)}</span><span>${esc(t.caller?.name || "Unknown")}<small>${esc(t.caller?.role || "")}</small></span></div></td>
          <td>${pill(priorityOf(t))}</td>
          <td>${esc(t.fields.assignment_group?.value || "")}</td>
          <td>${statusPills(t)}</td>
          <td class="muted">${ago(t.updated_at, now)} ago</td>
        </tr>`).join("")}
      </tbody></table></div>
  </section>`;
}

function statusPills(t) {
  const out = [];
  if (t.flagged.length) out.push(`<span class="pill review">${t.flagged.length} to check</span>`);
  if (t.alerts.some((a) => a.kind === "chase")) out.push('<span class="pill chase">Chasing</span>');
  if (t.alerts.some((a) => a.kind === "silence")) out.push('<span class="pill silence">Overdue</span>');
  if (Object.values(t.fields).some((f) => f.overridden)) out.push('<span class="pill edited">Edited</span>');
  return out.join(" ") || '<span class="pill ok">All set</span>';
}

function ticketView(id) {
  const t = DATA.tickets.find((x) => x.id === id);
  if (!t) return '<section class="card"><p>That ticket is not in this preview.</p></section>';
  const audit = DATA.audit.filter((a) => a.ticket === id).slice().reverse();
  return `
  <div class="crumbs"><a href="#tickets">Tickets</a><span>/</span><span>#${t.number}</span></div>
  <div class="ticket-head"><h1>${esc(t.subject)}</h1>${pill(priorityOf(t))}${statusPills(t)}</div>
  <div class="grid split">
    <div class="grid">
      <section class="card">
        <div class="card-head"><h2>Ticket fields</h2><span class="hint">Proposed by ${esc(DATA.source.system)}. Change anything.</span></div>
        <div class="fields">${Object.keys(DATA.field_labels).filter((n) => t.fields[n]).map((n) => fieldRow(t, n)).join("")}</div>
      </section>
      ${noteCard(t)}
      <section class="card">
        <div class="card-head"><h2>Activity</h2><span class="hint">Every AI change and every override is recorded</span></div>
        <div class="list">
          ${audit.map((a) => `<div class="list-item"><span class="pill ${a.action === "revert" ? "muted" : "edited"}">${a.action === "revert" ? "Undone" : "Changed"}</span>
            <span class="grow"><b>${esc(DATA.field_labels[a.field])}</b> from ${esc(a.from ?? "blank")} to ${esc(a.to ?? "blank")}<small>${esc(a.by)} · ${fmtDate(a.at)}</small></span></div>`).join("")}
          <div class="list-item"><span class="pill muted">AI</span><span class="grow">Fields proposed from ${t.emails.filter((e) => !e.outbound).length} client emails<small>${esc(DATA.source.system)} · ${fmtDate(t.updated_at)}</small></span></div>
        </div>
      </section>
    </div>
    <section class="card">
      <div class="card-head"><h2>Emails</h2><span class="hint">${t.emails.length} in this thread · ${esc(t.status)}</span></div>
      <div class="thread">${t.emails.map((e) => `
        <article class="email ${e.outbound ? "out" : ""}">
          <div class="email-head"><span><b>${esc(e.from_name)}</b> <span class="muted">&lt;${esc(e.from_address)}&gt;</span></span><span class="muted">${fmtDate(e.date)}</span></div>
          <pre>${esc(e.body)}</pre>
        </article>`).join("")}
      </div>
    </section>
  </div>`;
}

function fieldRow(t, name) {
  const f = t.fields[name];
  const editing = state.editing === `${t.id}:${name}`;
  const conf = Math.round(f.confidence * 100);
  let value = f.value === null || f.value === undefined || f.value === "" ? '<span class="muted">Not set</span>' :
    name === "priority" ? pill(f.value) : esc(f.value);
  if (name === "caller" && t.caller && t.caller.email === f.value && t.caller.name !== f.value) {
    value = `${esc(t.caller.name)} <small class="muted">${esc(t.caller.role)} · ${esc(f.value)}</small>`;
  }
  const choices = DATA.options[name];
  const editor = !editing ? "" : `
    <form class="editor" data-field="${name}">
      ${choices ? `<select id="edit-${name}" name="value">${choices.map((c) => `<option ${String(c) === String(f.value) ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`
        : `<input id="edit-${name}" name="value" value="${esc(f.value ?? "")}">`}
      <button class="btn primary" type="submit">Save</button>
      <button class="btn ghost" type="button" data-cancel>Cancel</button>
    </form>`;
  return `
  <div class="field ${f.needs_review && !f.overridden ? "needs-review" : ""}">
    <div class="name">${esc(DATA.field_labels[name])}</div>
    <div class="value">${value}</div>
    <div class="actions">
      ${f.overridden ? `<button class="btn ghost" data-revert="${name}">Undo</button>` : ""}
      ${editing ? "" : `<button class="btn" data-edit="${name}">Change</button>`}
    </div>
    <div class="meta">
      ${f.overridden ? `<span class="pill edited">Changed by you, AI said ${esc(f.ai_value ?? "blank")}</span>` : `
      <span class="meter" title="Confidence"><span style="width:${conf}%;background:${confidenceColour(f.confidence)}"></span></span>
      <span class="conf">${conf}% sure</span>
      ${f.needs_review ? '<span class="pill review">Check this</span>' : ""}`}
    </div>
    ${f.overridden ? "" : `<div class="reason">${esc(f.reason)}</div>`}
    ${editor}
  </div>`;
}

function noteCard(t) {
  const n = t.note;
  if (!n) return "";
  const list = (items) => (items.length ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : '<span class="muted">None stated</span>');
  return `
  <section class="card note">
    <div class="card-head"><h2>Note</h2><span class="pill muted">Draft from the rules baseline until Phase 4</span></div>
    <dl>
      <dt>Reported</dt><dd>${esc(n.reported) || '<span class="muted">Not stated</span>'}</dd>
      <dt>Affected</dt><dd>${esc(n.affected) || '<span class="muted">Not stated</span>'}</dd>
      <dt>Already tried</dt><dd>${list(n.tried)}</dd>
      <dt>Asking for</dt><dd>${list(n.asks)}</dd>
      <dt>Deadline or impact</dt><dd>${esc(n.deadline_or_impact) || '<span class="muted">Not stated</span>'}</dd>
      <dt>Open questions</dt><dd>${list(n.open_questions)}</dd>
    </dl>
  </section>`;
}

function alerts() {
  const items = allAlerts().filter((a) => state.alertKind === "all" || a.kind === state.alertKind);
  const chip = (k, label) => `<button class="chip" data-kind="${k}" aria-pressed="${state.alertKind === k}">${label}</button>`;
  return `
  <div class="chips">${chip("all", "All")}${chip("chase", "Chasing")}${chip("silence", "No response")}</div>
  <section class="card">
    <div class="card-head"><h2>${items.length} alerts</h2><span class="hint">Sent to the assigned engineer in Teams, then the team lead if still untouched (Phase 6)</span></div>
    <div class="list">${items.map(alertItem).join("") || '<p class="muted">No alerts.</p>'}</div>
  </section>`;
}

const METRIC_GROUPS = [
  ["Ticket fields", "fields."], ["Prompt injection threads", "adversarial."], ["Notes", "notes."],
  ["Check-ins", "checkins."], ["Chase detection", "chasing."], ["Alerts", "alerts."],
  ["PII leakage", "pii."], ["Cost", "cost."],
];

function fmtMetric(key, v) {
  if (v === null || v === undefined) return '<span class="muted">n/a</span>';
  if (key.startsWith("cost.usd")) return `$${v.toFixed(4)}`;
  if (key.startsWith("cost.") || key.endsWith("expected_updates") || key.endsWith("threads_sent_to_model")) return String(+v.toFixed(2));
  if (key.endsWith("per_thread")) return v.toFixed(2);
  return `${(v * 100).toFixed(1)}%`;
}

function evaluation() {
  const report = DATA.evaluation;
  if (!report) return '<section class="card"><p>Run <code>uv run python -m copilot.evaluation</code> to see scores here.</p></section>';
  const systems = Object.keys(report.systems);
  const metrics = (s) => report.systems[s].metrics;
  const base = metrics(systems[0]);
  const red = report.redaction || {};
  const label = (k) => k.split(".").slice(1).join(".").replace(/_/g, " ");
  return `
  <div class="grid tiles">
    <div class="tile t-lavender">${tileBody("evaluation", fmtMetric("x", base["fields.overall"]), "Field accuracy (baseline)")}</div>
    <div class="tile t-mint">${tileBody("review", fmtMetric("x", red["redaction.leakage_rate"]), "PII left after redaction")}</div>
    <div class="tile t-sky">${tileBody("inbox", report.threads, `Threads in the ${report.split} split`)}</div>
    <div class="tile t-peach">${tileBody("chase", fmtMetric("x", base["alerts.recall"]), "Alert recall (baseline)")}</div>
  </div>
  <section class="card">
    <div class="card-head"><h2>Harness scores</h2><span class="hint">${esc(report.split)} split · ${report.threads} threads · ${esc(report.generated_at)}</span></div>
    <div class="table-wrap"><table class="metric-table">
      <thead><tr><th>Metric</th>${systems.map((s) => `<th>${esc(s)}</th>`).join("")}</tr></thead>
      <tbody>${METRIC_GROUPS.map(([title, prefix]) => {
        const keys = Object.keys(base).filter((k) => k.startsWith(prefix));
        if (!keys.length) return "";
        return `<tr class="section"><td colspan="${systems.length + 1}">${title}</td></tr>` +
          keys.map((k) => `<tr><td>${esc(label(k))}</td>${systems.map((s) => `<td>${fmtMetric(k, metrics(s)[k])}</td>`).join("")}</tr>`).join("");
      }).join("")}
      ${Object.keys(red).length ? `<tr class="section"><td colspan="${systems.length + 1}">Redaction (redactor alone)</td></tr>` +
        Object.entries(red).map(([k, v]) => `<tr><td>${esc(label(k))}</td><td colspan="${systems.length}">${k.endsWith("seconds_per_thread") ? `${v.toFixed(3)} s` : fmtMetric(k, v)}</td></tr>`).join("") : ""}
      </tbody></table></div>
  </section>`;
}

function settingsPage() {
  const s = SETTINGS;
  const providers = [["anthropic", "Anthropic"], ["openai", "OpenAI"], ["azure", "Azure OpenAI"], ["gemini", "Google"], ["bedrock", "AWS Bedrock"], ["ollama", "Local (Ollama)"]];
  return `
  <section class="card">
    <div class="card-head"><h2>AI provider</h2>
      <span class="status-line">${s.model_configured ? '<span class="pill ok">Model ready</span>' : '<span class="pill review">No model configured</span>'}
      ${s.api_key_set ? '<span class="pill ok">API key saved</span>' : '<span class="pill muted">No API key</span>'}</span>
    </div>
    <form class="form" id="settings-form">
      <label for="set-provider">Provider<span>Any provider LiteLLM supports</span>
        <select id="set-provider">${providers.map(([v, l]) => `<option value="${v}" ${s.provider === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label for="set-model">Model<span>A cheap, fast model is the default</span><input id="set-model" value="${esc(s.model)}"></label>
      <label for="set-key">API key<span>Write only. The key is never shown again or sent to the browser.</span>
        <input id="set-key" type="password" autocomplete="off" placeholder="${s.api_key_set ? "A key is saved. Type to replace it." : "Paste your key"}"></label>
      <label for="set-base">Endpoint<span>For Azure OpenAI or a local Ollama server</span><input id="set-base" value="${esc(s.api_base || "")}" placeholder="Optional"></label>
    </form>
  </section>
  <section class="card">
    <div class="card-head"><h2>Limits and alerts</h2></div>
    <form class="form">
      <label for="set-cap">Spending cap (US dollars)<span>The copilot stops calling the model when this is reached</span><input id="set-cap" type="number" min="0" step="0.5" value="${s.spend_cap_usd}"></label>
      <label for="set-rate">Model calls per minute<input id="set-rate" type="number" min="1" value="${s.rate_limit_per_minute}"></label>
      <label for="set-review">Flag fields below this confidence<input id="set-review" type="number" min="0" max="1" step="0.05" value="0.6"></label>
      <label for="set-teams">Teams webhook<span>Where chasing and silence alerts go</span><input id="set-teams" placeholder="https://..." autocomplete="off"></label>
    </form>
  </section>
  <div><button class="btn primary" id="save-settings" type="button">Save settings</button></div>`;
}

// ---------------------------------------------------------------------------
// Shell and routing
// ---------------------------------------------------------------------------

const PAGES = {
  dashboard: { title: "Dashboard", sub: "What needs the desk's attention", render: dashboard },
  tickets: { title: "Tickets", sub: "Every open ticket with the AI's proposed fields", render: tickets },
  alerts: { title: "Alerts", sub: "Clients chasing and tickets past their response target", render: alerts },
  evaluation: { title: "Evaluation", sub: "How the AI scores against the labelled test data", render: evaluation },
  settings: { title: "Settings", sub: "Bring your own AI", render: settingsPage },
};

function route() {
  const hash = location.hash.slice(1) || "dashboard";
  if (hash.startsWith("ticket-")) return { page: "tickets", ticket: hash.slice(7) };
  return { page: PAGES[hash] ? hash : "dashboard" };
}

function render() {
  const r = route();
  const page = PAGES[r.page];
  document.querySelectorAll(".nav a").forEach((a) => {
    a.toggleAttribute("aria-current", a.dataset.page === r.page);
    if (a.dataset.page === r.page) a.setAttribute("aria-current", "page");
  });
  const alertsCount = allAlerts().length;
  document.getElementById("alert-count").textContent = alertsCount;
  const header = r.ticket ? "" : `
    <div class="top">
      <div><h1>${page.title}</h1><div class="sub">${page.sub}</div></div>
      <span class="spacer"></span>
      ${r.page === "tickets" ? `<label class="search" for="search">${icon("search")}<input id="search" type="search" placeholder="Search subject, caller or client" value="${esc(state.query)}"></label>` : ""}
    </div>`;
  const banner = `<div class="banner">${PREVIEW ? "Preview with synthetic Northgate data. " : ""}${esc(DATA.source.note)} Changes you make are kept until you reload.</div>`;
  document.getElementById("main").innerHTML = header + banner + (r.ticket ? ticketView(r.ticket) : page.render());
  document.title = r.ticket ? `Ticket ${r.ticket} · Service Desk Copilot` : `${page.title} · Service Desk Copilot`;
}

document.addEventListener("click", async (event) => {
  const el = event.target.closest("[data-ticket],[data-priority],[data-kind],[data-edit],[data-revert],[data-cancel],#save-settings");
  if (!el) return;
  const r = route();
  const ticket = r.ticket && DATA.tickets.find((t) => t.id === r.ticket);
  if (el.dataset.ticket) location.hash = `ticket-${el.dataset.ticket}`;
  else if (el.dataset.priority) { state.priority = el.dataset.priority; render(); }
  else if (el.dataset.kind) { state.alertKind = el.dataset.kind; render(); }
  else if (el.dataset.edit) { state.editing = `${ticket.id}:${el.dataset.edit}`; render(); document.getElementById(`edit-${el.dataset.edit}`)?.focus(); }
  else if ("cancel" in el.dataset) { state.editing = null; render(); }
  else if (el.dataset.revert) {
    try { await backend.revert(ticket, el.dataset.revert); toast("Change undone. The AI's value is back."); }
    catch (error) { toast(error.message); }
    render();
  } else if (el.id === "save-settings") {
    toast("Settings are read from .env in this version. Saving from this page arrives in Phase 6.");
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest(".editor");
  if (!form) return;
  event.preventDefault();
  const ticket = DATA.tickets.find((t) => t.id === route().ticket);
  const name = form.dataset.field;
  let value = form.elements.value.value.trim();
  if (["impact", "urgency"].includes(name)) value = Number(value);
  try {
    await backend.override(ticket, name, value === "" ? null : value);
    ticket.flagged = Object.entries(ticket.fields).filter(([, f]) => f.needs_review && !f.overridden).map(([n]) => n);
    toast(`${DATA.field_labels[name]} changed. The change is in the activity log.`);
  } catch (error) { toast(error.message); }
  state.editing = null;
  render();
});

document.addEventListener("input", (event) => {
  if (event.target.id !== "search") return;
  state.query = event.target.value;
  const caret = event.target.selectionStart;
  render();
  const input = document.getElementById("search");
  input.focus();
  input.setSelectionRange(caret, caret);
});

window.addEventListener("hashchange", () => { state.editing = null; render(); window.scrollTo(0, 0); });

(async function start() {
  try {
    [DATA, SETTINGS] = await Promise.all([backend.load(), backend.settings()]);
    for (const t of DATA.tickets) t.flagged = t.flagged || [];
    render();
  } catch (error) {
    document.getElementById("main").innerHTML = `<section class="card"><h2>Could not load tickets</h2><p>${esc(error.message)} Check the app is running with <code>uv run uvicorn copilot.web.app:app</code>.</p></section>`;
  }
})();
