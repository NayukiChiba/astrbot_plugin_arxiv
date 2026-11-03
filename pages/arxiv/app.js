/* ArXiv 控制台前端逻辑。
 *
 * 通过 AstrBot 插件页面 Bridge SDK（window.AstrBotPluginPage）与插件
 * 后端 Web API 通信，端点映射为 /api/plug/astrbot_plugin_arxiv/<endpoint>。
 */

const bridge = window.AstrBotPluginPage;

const state = {
  overview: null,
  fetchedPapers: [],
  selectedIndex: -1,
  preview: null,
  previewTab: "plain",
};

// ── 工具 ──────────────────────────────────────────────

function $(id) {
  return document.getElementById(id);
}

function toast(message, ms = 2600) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.add("hidden"), ms);
}

function fmtSize(bytes) {
  if (!bytes) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// ── 概览 ──────────────────────────────────────────────

// 合并默认配置、平台类型配置与实例条目，得到某平台实例的生效设置。
// 与后端规则一致：aiocqhttp 强制纯文本，合并转发仅 aiocqhttp 生效。
function effectivePlatformConfig(platformName, platformId) {
  const pcfg = state.overview?.config?.platform_config || {};
  const key = platformName.startsWith("qqofficial") ? "qqofficial" : platformName;
  const eff = { ...(pcfg.default || {}), ...(pcfg[key] || {}) };
  for (const entry of pcfg.instances || []) {
    if ((entry.session || "").split(":")[0] === platformId) Object.assign(eff, entry);
  }
  if (platformName === "aiocqhttp") eff.text_format = "plain";
  else eff.use_forward = false;
  return eff;
}

async function loadOverview() {
  const res = await bridge.apiGet("overview");
  state.overview = res;
  const cfg = res.config;
  const storage = res.storage;

  const aioCfg = (cfg.platform_config || {}).aiocqhttp || {};
  const badges = [
    `推送 ${cfg.push_time}`,
    `默认格式 ${(cfg.platform_config || {}).default?.text_format || "plain"}`,
    cfg.send_as_image ? "按图发送" : "文本发送",
    aioCfg.use_forward ? "aiocqhttp 合并转发" : "逐条发送",
    storage.enabled ? `存储 ${storage.retention_days} 天` : "存储关闭",
  ];
  $("status-badges").innerHTML = badges
    .map((b) => `<span class="badge">${escapeHtml(b)}</span>`)
    .join("");

  // 会话候选列表
  $("session-list").innerHTML = (cfg.target_sessions || [])
    .map((s) => `<option value="${escapeHtml(s)}"></option>`)
    .join("");

  updateStorageInfo();
  updateSessionPlatform();
}

function updateStorageInfo() {
  const storage = state.overview?.storage;
  if (!storage) return;
  $("storage-info").textContent = storage.enabled
    ? `存储已开启 · 保留 ${storage.retention_days} 天 · 共 ${storage.count} 篇 · ${fmtSize(storage.total_size_bytes)}`
    : "存储已关闭（在插件配置中开启 store_papers 后，发送的论文与 PDF 才会保存到这里）";
}

// 根据所选会话推断平台类型并展示该平台的生效设置
function updateSessionPlatform() {
  const session = $("send-session").value.trim();
  const platformId = session.split(":")[0];
  const platforms = state.overview?.platforms || [];
  const hit = platforms.find((p) => p.id === platformId);
  if (!session) {
    $("session-platform").textContent = "";
    return;
  }
  if (!hit) {
    $("session-platform").textContent = `平台实例: ${platformId}（未在已连接平台中找到，将按默认平台设置发送）`;
    return;
  }
  const eff = effectivePlatformConfig(hit.name, hit.id);
  const parts = [
    `平台类型: ${hit.name}`,
    `格式 ${eff.text_format || "plain"}`,
  ];
  if (hit.name === "aiocqhttp") {
    parts.push(eff.use_forward ? "合并转发" : "逐条发送");
  }
  $("session-platform").textContent = parts.join(" · ");
}

// ── 论文库 ────────────────────────────────────────────

async function loadLibrary() {
  const res = await bridge.apiGet("papers", { limit: 100 });
  const list = $("library-list");
  const empty = $("library-empty");
  const items = res.items || [];

  if (!items.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = items
    .map((p, i) => {
      const cat = escapeHtml((p.categories || [])[0] || "");
      const authors = escapeHtml((p.authors || []).slice(0, 3).join(", "));
      return `
      <div class="paper-card" data-index="${i}">
        <div class="title">${escapeHtml(p.title)}</div>
        <div class="meta">
          <span class="cat">${cat}</span>
          <span>${escapeHtml(p.arxiv_id)}</span>
        </div>
        <div class="meta">
          <span>${authors}</span>
        </div>
        <div class="meta">
          <span>发送于 ${fmtTime(p.sent_at)}</span>
          <span>${(p.sessions || []).length} 个会话</span>
        </div>
        <div class="actions">
          ${p.pdf_file ? `<button class="btn" data-action="pdf" data-id="${escapeHtml(p.arxiv_id)}" data-name="${escapeHtml(p.pdf_file)}">查看 PDF</button>` : ""}
          <button class="btn" data-action="abstract" data-id="${escapeHtml(p.arxiv_id)}">摘要</button>
          <a class="btn" href="${escapeHtml(p.abs_url)}" target="_blank" rel="noopener">arXiv 页面</a>
        </div>
        <div class="abstract hidden" id="abstract-${i}"></div>
      </div>`;
    })
    .join("");
}

async function onLibraryClick(event) {
  const btn = event.target.closest("button[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  const arxivId = btn.dataset.id;

  if (action === "pdf") {
    await bridge.download("papers/pdf", { arxiv_id: arxivId }, btn.dataset.name);
    return;
  }

  if (action === "abstract") {
    const card = btn.closest(".paper-card");
    const absEl = card.querySelector(".abstract");
    if (!absEl.dataset.loaded) {
      const detail = await bridge.apiGet("papers/detail", { arxiv_id: arxivId });
      absEl.textContent = detail.abstract || "（无摘要）";
      absEl.dataset.loaded = "1";
    }
    absEl.classList.toggle("hidden");
  }
}

async function cleanupLibrary() {
  const btn = $("btn-cleanup");
  btn.disabled = true;
  try {
    const res = await bridge.apiPost("papers/cleanup", {});
    toast(`清理完成：移除 ${res.removed} 条过期记录，剩余 ${res.count} 篇（${fmtSize(res.total_size_bytes)}）`);
    await loadOverview();
    await loadLibrary();
  } catch (e) {
    toast(`清理失败: ${e.message || e}`);
  } finally {
    btn.disabled = false;
  }
}

// ── 获取论文 ──────────────────────────────────────────

async function fetchPapers() {
  const btn = $("btn-fetch");
  btn.disabled = true;
  btn.textContent = "获取中…";
  try {
    const mode = $("fetch-mode").value;
    const res = await bridge.apiPost("fetch", {
      mode,
      query: $("fetch-query").value.trim(),
      max_results: Number($("fetch-count").value) || 3,
    });
    state.fetchedPapers = res.papers || [];
    state.selectedIndex = -1;
    state.preview = null;
    renderFetchList();
    $("preview-pane").classList.add("hidden");
    if (!state.fetchedPapers.length) toast("未找到符合条件的论文");
  } catch (e) {
    toast(`获取失败: ${e.message || e}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "获取论文";
  }
}

function renderFetchList() {
  const list = $("fetch-list");
  if (!state.fetchedPapers.length) {
    list.innerHTML = '<div class="empty">未找到符合条件的论文</div>';
    return;
  }
  list.innerHTML = state.fetchedPapers
    .map(
      (p, i) => `
    <div class="fetch-item ${i === state.selectedIndex ? "selected" : ""}" data-index="${i}">
      <div class="title">${escapeHtml(p.title)}</div>
      <div class="meta">${escapeHtml(p.arxiv_id)} · ${escapeHtml((p.categories || []).join(", "))}</div>
    </div>`
    )
    .join("");
}

function selectPaper(index) {
  state.selectedIndex = index;
  state.preview = null;
  renderFetchList();
  const paper = state.fetchedPapers[index];
  $("preview-pane").classList.remove("hidden");
  $("preview-title").textContent = paper.title;
  $("preview-text").textContent = "点击「预览样式」查看发送效果";
  $("preview-image-wrap").classList.add("hidden");
  $("preview-text").classList.remove("hidden");
}

// ── 预览与发送 ────────────────────────────────────────

function currentPaper() {
  return state.selectedIndex >= 0 ? state.fetchedPapers[state.selectedIndex] : null;
}

function renderPreview() {
  const preview = state.preview;
  if (!preview) return;
  const tab = state.previewTab;
  if (tab === "image") {
    if (preview.image_base64) {
      $("preview-image").src = `data:image/jpeg;base64,${preview.image_base64}`;
      $("preview-image-wrap").classList.remove("hidden");
      $("preview-text").classList.add("hidden");
    } else {
      $("preview-text").textContent = preview.image_error
        ? `卡片渲染失败: ${preview.image_error}`
        : "勾选「按图发送」后点击「预览样式」生成卡片图片";
      $("preview-text").classList.remove("hidden");
      $("preview-image-wrap").classList.add("hidden");
    }
  } else {
    $("preview-text").textContent = preview[tab] || "";
    $("preview-text").classList.remove("hidden");
    $("preview-image-wrap").classList.add("hidden");
  }
}

async function loadPreview() {
  const paper = currentPaper();
  if (!paper) {
    toast("请先选择一篇论文");
    return;
  }
  const btn = $("btn-preview");
  btn.disabled = true;
  btn.textContent = "渲染中…";
  try {
    state.preview = await bridge.apiPost("preview", {
      paper,
      send_as_image: $("send-as-image").checked,
      send_abstract: $("send-abstract").checked,
    });
    renderPreview();
  } catch (e) {
    toast(`预览失败: ${e.message || e}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "预览样式";
  }
}

async function sendPaper() {
  const paper = currentPaper();
  const session = $("send-session").value.trim();
  if (!paper) {
    toast("请先选择一篇论文");
    return;
  }
  if (!session) {
    toast("请填写目标会话 UMO");
    return;
  }
  const btn = $("btn-send");
  btn.disabled = true;
  btn.textContent = "发送中…";
  try {
    const res = await bridge.apiPost("send", {
      paper,
      session,
      text_format: $("send-format").value,
      send_as_image: $("send-as-image").checked,
    });
    toast(`发送成功，共 ${res.chains} 条消息`);
    await loadOverview();
    await loadLibrary();
  } catch (e) {
    toast(`发送失败: ${e.message || e}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "发送到会话";
  }
}

async function triggerPush() {
  const btn = $("btn-trigger");
  btn.disabled = true;
  try {
    await bridge.apiPost("push/trigger", {});
    toast("定时推送已触发，请查看日志确认结果");
    await loadLibrary();
  } catch (e) {
    toast(`触发失败: ${e.message || e}`);
  } finally {
    btn.disabled = false;
  }
}

// ── 事件绑定与初始化 ──────────────────────────────────

function bindEvents() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      $("tab-library").classList.toggle("hidden", tab.dataset.tab !== "library");
      $("tab-fetcher").classList.toggle("hidden", tab.dataset.tab !== "fetcher");
    });
  });

  document.querySelectorAll(".ptab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".ptab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.previewTab = tab.dataset.ptab;
      renderPreview();
    });
  });

  $("fetch-mode").addEventListener("change", () => {
    const mode = $("fetch-mode").value;
    $("fetch-query").placeholder =
      mode === "search"
        ? "搜索关键词，如 diffusion model"
        : mode === "id"
          ? "arXiv ID 或链接，如 2501.12345"
          : "最新模式无需填写";
  });

  $("fetch-list").addEventListener("click", (event) => {
    const item = event.target.closest(".fetch-item");
    if (item) selectPaper(Number(item.dataset.index));
  });

  $("library-list").addEventListener("click", onLibraryClick);
  $("send-session").addEventListener("input", updateSessionPlatform);
  $("btn-fetch").addEventListener("click", fetchPapers);
  $("btn-preview").addEventListener("click", loadPreview);
  $("btn-send").addEventListener("click", sendPaper);
  $("btn-trigger").addEventListener("click", triggerPush);
  $("btn-cleanup").addEventListener("click", cleanupLibrary);
  $("btn-reload-library").addEventListener("click", async () => {
    await loadOverview();
    await loadLibrary();
    toast("已刷新");
  });
}

(async function init() {
  try {
    await bridge.ready();
    bindEvents();
    await loadOverview();
    await loadLibrary();
  } catch (e) {
    toast(`初始化失败: ${e.message || e}`, 6000);
  }
})();
