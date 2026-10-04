// Interface do conversor. Fala SOMENTE com a API (/api/...), via HTTP.
const API = "/api";
const LABEL = { PENDENTE: "Pendente", PROCESSANDO: "Processando", CONCLUIDO: "Concluído", ERRO: "Erro" };

const jobs = new Map();      // jobId -> dados do job
let highlighted = new Set(); // pedidos enviados nesta aba

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- lista de pedidos ----------------------------------------------
function upsert(job) {
  jobs.set(job.jobId, { ...(jobs.get(job.jobId) || {}), ...job });
  render();
}

function sideText(j) {
  if (j.status === "CONCLUIDO") {
    const secs = j.durationSeconds != null ? ` em ${j.durationSeconds.toFixed(1)} s` : "";
    return `<a href="${esc(j.publicUrl)}" target="_blank" rel="noopener">Abrir ${esc(j.targetFormat.toUpperCase())}</a>
            <div>por ${esc(j.workerId || "?")}${secs}</div>`;
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
    return `
      <li class="job ${highlighted.has(j.jobId) ? "highlight" : ""}" data-status="${esc(j.status)}">
        <div>
          <div class="job-name">${esc(j.originalFilename || "arquivo")}</div>
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
        ${error}
      </li>`;
  }).join("");
}

// ---------- tempo real: Server-Sent Events ----------------------------------
// UM stream para todos os pedidos (não um por pedido): navegadores limitam a
// ~6 conexões simultâneas por servidor em HTTP/1.1.
function connectEvents() {
  const es = new EventSource(`${API}/events`);
  es.onmessage = (ev) => upsert(JSON.parse(ev.data));
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

// ---------- envio -------------------------------------------------------------
const form = $("upload-form");
const fileInput = $("file");
const drop = $("drop");

fileInput.addEventListener("change", () => {
  const n = fileInput.files.length;
  $("drop-text").textContent = n === 0
    ? "Arraste arquivos PNG ou clique para escolher"
    : n === 1 ? fileInput.files[0].name : `${n} arquivos selecionados`;
  drop.classList.toggle("filled", n > 0);
});
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, () => drop.classList.add("over")));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const receipt = $("receipt");
  const email = $("email").value.trim();
  if (!fileInput.files.length) { receipt.innerHTML = '<span class="bad">Escolha ao menos um arquivo PNG.</span>'; return; }
  if (!$("email").checkValidity() || !email) { receipt.innerHTML = '<span class="bad">Informe um e-mail válido.</span>'; return; }

  $("submit").disabled = true;
  const accepted = [];
  const failed = [];
  // Um pedido (e um jobId) por arquivo. Cada POST volta em milissegundos,
  // porque a API só grava no MinIO e publica na fila.
  await Promise.all([...fileInput.files].map(async (file) => {
    const data = new FormData();
    data.append("file", file);
    data.append("targetFormat", $("target").value);
    data.append("email", email);
    try {
      const t0 = performance.now();
      const res = await fetch(`${API}/jobs`, { method: "POST", body: data });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      accepted.push({ id: body.jobId, ms: Math.round(performance.now() - t0) });
      highlighted.add(body.jobId);
      upsert({ jobId: body.jobId, originalFilename: file.name, targetFormat: $("target").value,
               status: "PENDENTE", progress: 0, attempts: 0, createdAt: new Date().toISOString() });
    } catch (err) {
      failed.push(`${file.name}: ${err.message}`);
    }
  }));
  $("submit").disabled = false;

  receipt.innerHTML =
    (accepted.length ? `<p><span class="ok">${accepted.length === 1 ? "Pedido aceito" : `${accepted.length} pedidos aceitos`}</span>
       ${accepted.map((a) => `<br><code>${esc(a.id)}</code> (${a.ms} ms)`).join("")}</p>` : "") +
    failed.map((f) => `<p class="bad">${esc(f)}</p>`).join("");

  if (accepted.length) {
    form.reset();
    fileInput.dispatchEvent(new Event("change"));
    $("email").value = email;           // mantém o e-mail para o próximo envio
  }
});

$("lookup-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const id = $("lookup-id").value.trim();
  if (!id) return;
  const res = await fetch(`${API}/jobs/${encodeURIComponent(id)}`);
  const receipt = $("receipt");
  if (!res.ok) { receipt.innerHTML = `<p class="bad">Nenhum pedido com o código ${esc(id)}.</p>`; return; }
  const job = await res.json();
  highlighted.add(job.jobId);
  upsert(job);
  receipt.innerHTML = `<p>Pedido encontrado: <strong>${LABEL[job.status]}</strong>.</p>`;
});

// ---------- estado do sistema (observabilidade) ----------------------------------
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

// ---------- início ------------------------------------------------------------------
(async function init() {
  try {
    const formats = await (await fetch(`${API}/formats`)).json();
    const targets = [...new Set(Object.values(formats).flat())];
    $("target").innerHTML = targets.map((t) => `<option value="${t}">${t.toUpperCase()}</option>`).join("");
  } catch {
    $("target").innerHTML = '<option value="jpg">JPG</option>';
  }
  await refreshList();
  connectEvents();
  refreshMetrics();
  setInterval(refreshList, 15000);   // rede de segurança; o normal é chegar pelo stream
  setInterval(refreshMetrics, 3000);
})();
