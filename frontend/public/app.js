const API = "/api";
const LABEL = { PENDENTE: "Pendente", PROCESSANDO: "Processando", CONCLUIDO: "Concluído", ERRO: "Erro" };
const ALIASES = { jpeg: "jpg" };
const MAX_MB = 20;

let formats = { png: ["jpg"], jpg: ["png"] };
const jobs = new Map();
const highlighted = new Set();
let selected = [];
let nextId = 1;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function sourceFormat(name) {
  const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  return ALIASES[ext] || ext;
}

function addFiles(fileList) {
  for (const file of fileList) {
    const source = sourceFormat(file.name);
    const targets = formats[source] || [];
    let error = "";
    if (!targets.length) {
      const ok = Object.keys(formats).map((f) => f.toUpperCase()).join(" ou ");
      error = `formato .${source || "?"} não suportado (use ${ok})`;
    } else if (file.size > MAX_MB * 1024 * 1024) {
      error = `maior que ${MAX_MB} MB`;
    }
    selected.push({ id: nextId++, file, source, target: targets[0] || "", error });
  }
  renderFiles();
}

function renderFiles() {
  const list = $("file-list");
  list.innerHTML = selected.map((s) => {
    const targets = formats[s.source] || [];
    const select = s.error ? "" : `
      <label class="file-target">
        <span class="visually-hidden">Converter ${esc(s.file.name)} para</span>
        <span aria-hidden="true">→</span>
        <select data-id="${s.id}">
          ${targets.map((t) => `<option value="${t}" ${t === s.target ? "selected" : ""}>${t.toUpperCase()}</option>`).join("")}
        </select>
      </label>`;
    return `
      <li class="file ${s.error ? "invalid" : ""}">
        <span class="file-name" title="${esc(s.file.name)}">${esc(s.file.name)}</span>
        ${select}
        <button type="button" class="remove" data-remove="${s.id}" aria-label="Remover ${esc(s.file.name)}">×</button>
        ${s.error ? `<span class="file-error">${esc(s.error)}</span>` : ""}
      </li>`;
  }).join("");
  const valid = selected.filter((s) => !s.error).length;
  const invalid = selected.length - valid;
  $("drop-hint").textContent = selected.length === 0
    ? "Cada arquivo vira um pedido. Até 20 MB cada."
    : `${valid} pronto(s) para enviar${invalid ? `, ${invalid} com problema (remova com ×)` : ""}.`;
  $("submit").textContent = valid > 1 ? `Enviar ${valid} arquivos` : "Enviar para conversão";
}

$("file").addEventListener("change", (ev) => {
  addFiles(ev.target.files);
  ev.target.value = "";               // permite escolher o mesmo arquivo de novo depois
});
$("file-list").addEventListener("click", (ev) => {
  const id = Number(ev.target.dataset.remove);
  if (!id) return;
  selected = selected.filter((s) => s.id !== id);
  renderFiles();
});
$("file-list").addEventListener("change", (ev) => {
  const item = selected.find((s) => s.id === Number(ev.target.dataset.id));
  if (item) item.target = ev.target.value;
});
const drop = $("drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, () => drop.classList.add("over")));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));

// ---------- envio -----------------------------------------------------------------
$("upload-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const receipt = $("receipt");
  const email = $("email").value.trim();
  const toSend = selected.filter((s) => !s.error);
  if (!toSend.length) {
    receipt.innerHTML = '<p class="bad">Escolha ao menos uma imagem PNG ou JPG válida.</p>';
    return;
  }
  if (!email || !$("email").checkValidity()) {
    receipt.innerHTML = '<p class="bad">Informe um e-mail válido.</p>';
    return;
  }

  $("submit").disabled = true;
  const accepted = [];
  const failed = [];
 
  await Promise.all(toSend.map(async (item) => {
    const data = new FormData();
    data.append("file", item.file);
    data.append("targetFormat", item.target);
    data.append("email", email);
    try {
      const t0 = performance.now();
      const res = await fetch(`${API}/jobs`, { method: "POST", body: data });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      accepted.push({ id: body.jobId, name: item.file.name, ms: Math.round(performance.now() - t0) });
      highlighted.add(body.jobId);
      upsert({ jobId: body.jobId, originalFilename: item.file.name, sourceFormat: item.source,
               targetFormat: item.target, status: "PENDENTE", progress: 0, attempts: 0,
               createdAt: new Date().toISOString() });
      selected = selected.filter((s) => s.id !== item.id);   // enviado: sai da lista
    } catch (err) {
      item.error = err.message;                              // fica na lista com o motivo
      failed.push(`${item.file.name}: ${err.message}`);
    }
  }));
  $("submit").disabled = false;
  renderFiles();

  receipt.innerHTML =
    (accepted.length ? `<p><span class="ok">${accepted.length === 1 ? "Pedido aceito" : `${accepted.length} pedidos aceitos`}</span>
       ${accepted.map((a) => `<br>${esc(a.name)}: <code>${esc(a.id)}</code> (${a.ms} ms)`).join("")}</p>` : "") +
    failed.map((f) => `<p class="bad">${esc(f)}</p>`).join("");
});

// ---------- lista de pedidos ------------------------------------------------------
function upsert(job) {
  jobs.set(job.jobId, { ...(jobs.get(job.jobId) || {}), ...job });
  render();
}

function linkBlock(j) {
  const fmt = (j.targetFormat || "").toUpperCase();

  return `
    <div class="result">
      <a href="${esc(j.publicUrl)}" class="thumb" title="Baixar ${fmt}">
        <img src="${esc(j.publicUrl)}" alt="Prévia de ${esc(j.originalFilename)} convertido" loading="lazy">
      </a>
      <div class="result-body">
        <span class="result-label">Link público</span>
        <span class="url">${esc(j.publicUrl)}</span>
        <div class="result-actions">
          <a class="btn" href="${esc(j.publicUrl)}" download>Baixar ${fmt}</a>
          <button type="button" class="copy" data-copy="${esc(j.publicUrl)}">Copiar link</button>
        </div>
      </div>
    </div>`;
}

function sideText(j) {
  if (j.status === "CONCLUIDO") {
    const secs = j.durationSeconds != null ? ` em ${j.durationSeconds.toFixed(1)} s` : "";
    return `<div>por ${esc(j.workerId || "?")}${secs}</div>`;
  }
  if (j.status === "PROCESSANDO") return `<div>${j.progress}% em ${esc(j.workerId || "?")}</div>`;
  if (j.status === "PENDENTE" && j.error) return "<div>aguardando nova tentativa</div>";
  return "";
}

function render() {
  const list = [...jobs.values()].sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  $("job-list").innerHTML = list.map((j) => {
    const attempt = j.attempts > 1 ? ` (tentativa ${j.attempts})` : "";
    const error = j.error && (j.status === "ERRO" || j.status === "PENDENTE")
      ? `<p class="job-error">${esc(j.error)}</p>` : "";
    const conv = j.sourceFormat && j.targetFormat
      ? `${j.sourceFormat.toUpperCase()} → ${j.targetFormat.toUpperCase()}` : "";
    return `
      <li class="job ${highlighted.has(j.jobId) ? "highlight" : ""}" data-status="${esc(j.status)}" id="job-${esc(j.jobId)}">
        <div>
          <div class="job-name">${esc(j.originalFilename || "arquivo")} <span class="job-conv">${conv}</span></div>
          <div class="job-id">${esc(j.jobId)}</div>
        </div>
        <div class="job-side">
          <div class="job-status">${LABEL[j.status] || esc(j.status)}${attempt}</div>
          ${sideText(j)}
        </div>
        <div class="track" style="--p:${Number(j.progress) || 0}" aria-hidden="true">
          <span class="stop s1"></span><span class="seg seg1"><i></i></span>
          <span class="stop s2"></span><span class="seg seg2"><i></i></span>
          <span class="stop s3"></span>
        </div>
        <div class="track-labels" aria-hidden="true">
          <span>Na fila</span><span>Convertendo</span><span>${j.status === "ERRO" ? "Erro" : "Concluído"}</span>
        </div>
        ${j.status === "CONCLUIDO" && j.publicUrl ? linkBlock(j) : ""}
        ${error}
      </li>`;
  }).join("");
}

document.addEventListener("click", async (ev) => {
  const url = ev.target.dataset?.copy;
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    const label = ev.target.textContent;
    ev.target.textContent = "Copiado";
    setTimeout(() => { ev.target.textContent = label; }, 1500);
  } catch {
    window.prompt("Copie o link:", url);
  }
});

function connectEvents() {
  const es = new EventSource(`${API}/events`);
  es.onmessage = (ev) => {
    const job = JSON.parse(ev.data);
    upsert(job);
    if (job.jobId === lookedUp) showLookup(job);
  };
  // Em caso de queda, o próprio EventSource reconecta; ao voltar, sincronizamos a lista.
  es.onopen = () => refreshList();
}

async function refreshList() {
  try {
    const res = await fetch(`${API}/jobs?limit=40`);
    if (!res.ok) return;
    for (const job of await res.json()) upsert(job);
  } catch { /* API reiniciando: tenta de novo no próximo ciclo */ }
}

let lookedUp = null;

function showLookup(job) {
  const extra = job.status === "CONCLUIDO" && job.publicUrl ? linkBlock(job)
    : job.status === "ERRO" ? `<p class="bad">${esc(job.error || "")}</p>`
    : job.status === "PROCESSANDO" ? `<p>${job.progress}% concluído</p>` : "";
  $("lookup-result").innerHTML = `
    <p><strong>${esc(job.originalFilename)}</strong>: <span class="st st-${esc(job.status)}">${LABEL[job.status]}</span></p>
    ${extra}`;
}

$("lookup-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const id = $("lookup-id").value.trim();
  if (!id) return;
  const res = await fetch(`${API}/jobs/${encodeURIComponent(id)}`);
  if (!res.ok) {
    lookedUp = null;
    $("lookup-result").innerHTML = '<p class="bad">Nenhum pedido com esse código.</p>';
    return;
  }
  const job = await res.json();
  lookedUp = job.jobId;
  showLookup(job);
  upsert(job);
  highlighted.add(job.jobId);
  render();
  document.getElementById(`job-${job.jobId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
});

async function refreshMetrics() {
  try {
    const res = await fetch(`${API}/metrics`);
    if (!res.ok) return;
    const m = await res.json();
    const q = m.queues || {};
    const n = (v) => (v == null ? "?" : v);
    const s = m.jobsByStatus || {};
    const rows = [
      ["Na fila de conversão", n(q["conversion.requests"])],
      ["Esperando nova tentativa", n(q["conversion.retry"])],
      ["Falhas definitivas (DLQ)", n(q["conversion.dlq"]), q["conversion.dlq"] > 0],
      ["E-mails a enviar", n(q["notifications"])],
      ["Em processamento", s.PROCESSANDO || 0],
      ["Concluídos", s.CONCLUIDO || 0],
      ["Com erro", s.ERRO || 0, (s.ERRO || 0) > 0],
      ["Tempo médio de conversão", m.avgConversionSeconds != null ? `${m.avgConversionSeconds.toFixed(1)} s` : "–"],
      ["Taxa de erro", `${(m.errorRate * 100).toFixed(1)}%`],
    ];
    $("metrics").innerHTML = rows.map(([k, v, alert]) =>
      `<dt>${k}</dt><dd class="${alert ? "alert" : ""}">${esc(v)}</dd>`).join("");

    const per = Object.entries(m.completedPerWorker || {});
    const max = Math.max(1, ...per.map(([, v]) => v));
    $("per-worker").innerHTML = per.length
      ? per.map(([w, v]) => `<li><span class="wid" title="${esc(w)}">${esc(w)}</span>
          <span class="bar" style="width:${(v / max) * 100}%"></span><span class="n">${v}</span></li>`).join("")
      : '<li class="empty">Nenhuma conversão concluída ainda.</li>';
  } catch { /* ignora: próxima rodada tenta de novo */ }
}

(async function init() {
  try {
    formats = await (await fetch(`${API}/formats`)).json();
  } catch { /* mantém o padrão */ }
  renderFiles();
  await refreshList();
  connectEvents();
  refreshMetrics();
  setInterval(refreshList, 15000);   // rede de segurança; o normal é chegar pelo stream
  setInterval(refreshMetrics, 3000);
})();
