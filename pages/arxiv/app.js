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
  detailId: null,
  detail: null,
  fullConfig: null,
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

  // 卡片模板候选列表
  $("template-list").innerHTML = (res.templates || [])
    .map((t) => `<option value="${escapeHtml(t)}"></option>`)
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

const libraryState = { selected: new Set(), items: [], currentIndex: -1 };

async function loadLibrary() {
  const res = await bridge.apiGet("papers", { limit: 100 });
  const items = res.items || [];
  libraryState.items = items;
  libraryState.selected.clear();
  updateDeleteButton();

  const empty = $("library-empty");
  const split = $("library-split");
  if (!items.length) {
    $("library-list").innerHTML = "";
    empty.classList.remove("hidden");
    split.classList.add("hidden");
    $("library-detail").classList.add("hidden");
    state.detailId = null;
    return;
  }
  empty.classList.add("hidden");
  split.classList.remove("hidden");
  renderLibraryList();
  // 保持选中刷新前查看的论文，否则默认选中第一篇
  const idx = items.findIndex((p) => p.arxiv_id === state.detailId);
  await selectLibraryPaper(idx >= 0 ? idx : 0);
}

function renderLibraryList() {
  $("library-list").innerHTML = libraryState.items
    .map((p, i) => {
      const cat = escapeHtml((p.categories || [])[0] || "");
      const checked = libraryState.selected.has(p.arxiv_id) ? "checked" : "";
      return `
      <div class="fetch-item ${i === libraryState.currentIndex ? "selected" : ""}" data-index="${i}">
        <div class="check-cell">
          <input type="checkbox" class="select-box" data-id="${escapeHtml(p.arxiv_id)}" ${checked} />
          <div class="title">${escapeHtml(p.title)}</div>
        </div>
        <div class="meta">
          <span class="cat">${cat}</span>
          <span>${escapeHtml(p.arxiv_id)}</span>
        </div>
        <div class="meta">发送于 ${fmtTime(p.sent_at)} · ${(p.sessions || []).length} 个会话</div>
      </div>`;
    })
    .join("");
}

async function selectLibraryPaper(index) {
  const paper = libraryState.items[index];
  if (!paper) return;
  libraryState.currentIndex = index;
  renderLibraryList();
  try {
    await openDetail(paper.arxiv_id);
  } catch (e) {
    toast(`加载详情失败: ${e.message || e}`);
  }
}

function updateDeleteButton() {
  const btn = $("btn-delete-selected");
  const n = libraryState.selected.size;
  btn.classList.toggle("hidden", n === 0);
  btn.textContent = n > 0 ? `删除选中（${n}）` : "删除选中";
}

// 详情面板：展示完整信息、摘要、首页截图、卡片图片与重发入口
async function openDetail(arxivId) {
  const detail = await bridge.apiGet("papers/detail", { arxiv_id: arxivId });
  state.detailId = arxivId;
  state.detail = detail;
  $("library-detail").classList.remove("hidden");
  $("detail-title").textContent = detail.title || arxivId;
  $("detail-meta").innerHTML = [
    escapeHtml(detail.arxiv_id || ""),
    escapeHtml((detail.authors || []).join(", ")),
    escapeHtml((detail.categories || []).join(", ")),
    `发送于 ${fmtTime(detail.sent_at)}`,
    escapeHtml((detail.sessions || []).join(" ・ ")),
  ]
    .filter(Boolean)
    .map((s) => `<span>${s}</span>`)
    .join("");

  $("detail-abstract").textContent = detail.abstract || "（无摘要）";
  // 操作按钮与截图 / 卡片可用性
  $("btn-detail-pdf").classList.toggle("hidden", !detail.pdf_file);
  $("detail-arxiv-link").href = detail.abs_url || "#";
  document.querySelector('[data-dtab="screenshot"]').style.display = detail.has_screenshot ? "" : "none";
  document.querySelector('[data-dtab="card"]').style.display = detail.has_card ? "" : "none";
  switchDetailTab("abstract", arxivId);
}

function switchDetailTab(tab, arxivId) {
  const detail = state.detail;
  document.querySelectorAll(".dtab").forEach((t) =>
    t.classList.toggle("active", t.dataset.dtab === tab)
  );
  const textEl = $("detail-abstract");
  const imgWrap = $("detail-image-wrap");
  const img = $("detail-image");
  if (tab === "abstract") {
    textEl.classList.remove("hidden");
    imgWrap.classList.add("hidden");
    return;
  }
  textEl.classList.add("hidden");
  imgWrap.classList.remove("hidden");
  // 首页截图缩小展示，卡片图片保持较大尺寸
  imgWrap.classList.toggle("shot", tab === "screenshot");
  const endpoint = tab === "screenshot" ? "papers/screenshot" : "papers/card";
  img.src = "";
  img.alt = "加载中…";
  bridgeImageUrl(endpoint, arxivId)
    .then((url) => {
      if (url) img.src = url;
      else img.alt = "图片不可用";
    })
    .catch(() => {
      img.alt = "图片加载失败";
    });
}

// 经桥接 GET 拉取图片（base64 JSON），转为 data URL 展示
async function bridgeImageUrl(endpoint, arxivId) {
  const res = await bridge.apiGet(endpoint, { arxiv_id: arxivId, format: "base64" });
  return res && res.data_url ? res.data_url : "";
}

async function onLibraryClick(event) {
  const box = event.target.closest(".select-box");
  if (box) {
    if (box.checked) libraryState.selected.add(box.dataset.id);
    else libraryState.selected.delete(box.dataset.id);
    updateDeleteButton();
    return;
  }

  const item = event.target.closest(".fetch-item");
  if (item) await selectLibraryPaper(Number(item.dataset.index));
}

async function deleteSelected() {
  const ids = [...libraryState.selected];
  if (!ids.length) return;
  if (!confirm(`确认删除选中的 ${ids.length} 篇论文及其 PDF / 截图 / 卡片？`)) return;
  const btn = $("btn-delete-selected");
  btn.disabled = true;
  try {
    const res = await bridge.apiPost("papers/delete", { arxiv_ids: ids });
    toast(`已删除 ${res.removed} 篇，剩余 ${res.count} 篇（${fmtSize(res.total_size_bytes)}）`);
    await loadOverview();
    await loadLibrary();
  } catch (e) {
    toast(`删除失败: ${e.message || e}`);
  } finally {
    btn.disabled = false;
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
      image_template: $("send-template").value.trim() || undefined,
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
      send_abstract: $("send-abstract").checked,
      attach_pdf: $("send-attach-pdf").checked,
      screenshot_pdf: $("send-screenshot").checked,
      image_template: $("send-template").value.trim() || undefined,
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

// ── 设置（在线修改配置） ──────────────────────────────

async function loadConfigForm() {
  try {
    const cfg = await bridge.apiGet("config");
    state.fullConfig = cfg;
    const push = cfg.push_config || {};
    const send = cfg.send_config || {};
    const pcfg = cfg.platform_config || {};
    const storage = cfg.storage_config || {};
    const llm = cfg.llm_config || {};

    $("cfg-push-time").value = push.push_time || "09:00";
    $("cfg-push-timezone").value = push.push_timezone || "Asia/Shanghai";
    $("cfg-target-sessions").value = (push.target_sessions || []).join("\n");

    $("cfg-send-abstract").checked = send.send_abstract !== false;
    $("cfg-attach-pdf").checked = send.attach_pdf !== false;
    $("cfg-screenshot-pdf").checked = send.screenshot_pdf !== false;
    $("cfg-send-as-image").checked = pcfg.send_as_image === true;

    $("cfg-store-papers").checked = storage.store_papers !== false;
    $("cfg-retention-days").value = storage.paper_retention_days ?? 7;

    $("cfg-abstract-mode").value = llm.abstract_mode || "original";
    $("cfg-llm-summarize").checked = llm.llm_summarize === true;

    $("config-status").textContent = "";
  } catch (e) {
    toast(`读取配置失败: ${e.message || e}`);
  }
}

async function saveConfigForm() {
  const btn = $("btn-save-config");
  btn.disabled = true;
  try {
    const updates = [
      ["push_config", {
        push_time: $("cfg-push-time").value.trim() || "09:00",
        push_timezone: $("cfg-push-timezone").value.trim() || "Asia/Shanghai",
        target_sessions: $("cfg-target-sessions").value
          .split("\n").map((s) => s.trim()).filter(Boolean),
      }],
      ["send_config", {
        ...(state.fullConfig?.send_config || {}),
        send_abstract: $("cfg-send-abstract").checked,
        attach_pdf: $("cfg-attach-pdf").checked,
        screenshot_pdf: $("cfg-screenshot-pdf").checked,
      }],
      ["platform_config", {
        ...(state.fullConfig?.platform_config || {}),
        send_as_image: $("cfg-send-as-image").checked,
      }],
      ["storage_config", {
        store_papers: $("cfg-store-papers").checked,
        paper_retention_days: Number($("cfg-retention-days").value) || 7,
      }],
      ["llm_config", {
        ...(state.fullConfig?.llm_config || {}),
        abstract_mode: $("cfg-abstract-mode").value,
        llm_summarize: $("cfg-llm-summarize").checked,
      }],
    ];
    for (const [group, values] of updates) {
      await bridge.apiPost("config", { group, values });
    }
    $("config-status").textContent = "已保存，配置即时生效。";
    toast("配置已保存");
    await loadOverview();
  } catch (e) {
    toast(`保存失败: ${e.message || e}`);
    $("config-status").textContent = "保存失败";
  } finally {
    btn.disabled = false;
  }
}

// ── 重发已推送论文 ─────────────────────────────────────

async function resendDetail() {
  const arxivId = state.detailId;
  const session = $("detail-session").value.trim();
  if (!arxivId) return;
  if (!session) {
    toast("请填写目标会话 UMO");
    return;
  }
  const btn = $("btn-resend");
  btn.disabled = true;
  btn.textContent = "发送中…";
  try {
    const res = await bridge.apiPost("resend", {
      arxiv_id: arxivId,
      session,
      send_as_image: $("detail-as-image").checked,
    });
    toast(`重发成功，共 ${res.chains} 条消息`);
    await loadOverview();
    await loadLibrary();
  } catch (e) {
    toast(`重发失败: ${e.message || e}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "重发此论文";
  }
}

// ── 事件绑定与初始化 ──────────────────────────────────

function bindEvents() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const name = tab.dataset.tab;
      $("tab-library").classList.toggle("hidden", name !== "library");
      $("tab-fetcher").classList.toggle("hidden", name !== "fetcher");
      $("tab-settings").classList.toggle("hidden", name !== "settings");
      if (name === "settings") loadConfigForm();
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
  $("btn-delete-selected").addEventListener("click", deleteSelected);
  $("btn-detail-pdf").addEventListener("click", async () => {
    if (state.detail?.pdf_file) {
      await bridge.download("papers/pdf", { arxiv_id: state.detailId }, state.detail.pdf_file);
    }
  });

  // 详情面板：tab 切换
  document.querySelectorAll(".dtab").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (state.detailId) switchDetailTab(tab.dataset.dtab, state.detailId);
    });
  });

  $("send-session").addEventListener("input", updateSessionPlatform);
  $("btn-fetch").addEventListener("click", fetchPapers);
  $("btn-preview").addEventListener("click", loadPreview);
  $("btn-send").addEventListener("click", sendPaper);
  $("btn-trigger").addEventListener("click", triggerPush);
  $("btn-cleanup").addEventListener("click", cleanupLibrary);
  $("btn-resend").addEventListener("click", resendDetail);
  $("btn-save-config").addEventListener("click", saveConfigForm);
  $("btn-reload-config").addEventListener("click", loadConfigForm);
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
