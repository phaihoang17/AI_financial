// Browser UI for the question API (src/service/app.py). No build step, no dependencies.
// It renders exactly what /v1/ask returns; every number shown comes from the response.
'use strict';

(() => {
  const CLIENT_TIMEOUT_MS = 75000; // server deadline is 60 s by default
  const SLOW_AFTER_MS = 8000; // SLO: 95% of answers within 8 s
  const MAX_SESSIONS = 50;
  const PREVIEW_ROWS = 5;
  const EXAMPLES = [
    ['Tỷ số', 'Hãy tính ROE của OGC năm 2024 theo số liệu báo cáo hợp nhất.'],
    ['Tra cứu', 'Cuối năm 2020, FPT có tổng tài sản là bao nhiêu đồng theo báo cáo hợp nhất?'],
    ['Tăng trưởng', 'Tốc độ tăng trưởng lợi nhuận gộp năm 2019 so với năm 2018 của MCH (BCTC riêng) là bao nhiêu %?'],
    ['Bình quân', 'Nợ ngắn hạn bình quân của FPT theo báo cáo hợp nhất trong hai năm 2020 và 2021 là bao nhiêu?'],
  ];
  const STATEMENTS = {
    INCOME_STATEMENT: 'Kết quả kinh doanh',
    BALANCE_SHEET: 'Cân đối kế toán',
    CASH_FLOW_STATEMENT: 'Lưu chuyển tiền tệ',
  };
  const SCOPES = { HOP_NHAT: 'hợp nhất', RIENG: 'riêng' };
  const COLUMN_LABELS = {
    fiscal_year: 'Năm', year: 'Năm', ticker: 'Mã CK', company_name: 'Tên công ty', statement_scope: 'Loại BC',
    metric: 'Chỉ tiêu', line_item: 'Dòng báo cáo', line_code: 'Mã số', value: 'Giá trị', unit: 'Đơn vị',
  };
  const VND_SCALES = { ty: [1e9, 'tỷ đồng', 2], trieu: [1e6, 'triệu đồng', 0], dong: [1, 'đồng', 0] };
  const FAILURES = {
    UNAUTHORIZED: 'Thiếu hoặc sai mã truy cập API. Nhập mã ở mục "Mã truy cập API".',
    NETWORK: 'Không kết nối được tới máy chủ. Kiểm tra máy chủ còn chạy rồi thử lại.',
    CLIENT_TIMEOUT: 'Máy chủ không phản hồi sau 75 giây.',
    TIMEOUT: 'Câu hỏi vượt quá thời hạn xử lý của máy chủ.',
    OVERLOADED: 'Máy chủ đang xử lý tối đa số câu hỏi cùng lúc. Thử lại sau vài giây.',
    NOT_READY: 'Máy chủ chưa nạp phiên bản dữ liệu nào.',
    INTERNAL_ERROR: 'Máy chủ gặp lỗi nội bộ khi xử lý câu hỏi.',
    INVALID_REQUEST: 'Câu hỏi không hợp lệ (rỗng hoặc dài quá 8000 ký tự).',
    SERVING_TRANSPORT_FAILED: 'Không gọi được mô hình sinh mã. Thử lại sau ít phút.',
  };
  const VERIFY_FAILURE = 'Hệ thống không kiểm chứng được kết quả nên không đưa ra con số.';
  // Gate refusals already carry Vietnamese messages; these come from verification.
  const NO_DATA_REASONS = {
    REFERENCE_INPUT_UNAVAILABLE: 'Thiếu số liệu gốc cần cho phép tính này trong báo cáo.',
    ZERO_DENOMINATOR: 'Mẫu số của phép tính bằng 0 nên không tính được.',
  };
  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
    table: '<rect x="4" y="5" width="16" height="14" rx="1.5"/><path d="M4 10h16M10 10v9"/>',
    code: '<path d="M9 8l-5 4 5 4M15 8l5 4-5 4"/>',
    doc: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="1.5"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    building: '<path d="M5 21V4h10v17M15 9h4v12M8 8h4M8 12h4M8 16h4M3 21h18"/>',
  };

  // ---- small helpers --------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const els = {};

  function icon(name) {
    const span = document.createElement('span');
    span.className = 'ico';
    span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[name] || ''}</svg>`; // constant markup only
    return span;
  }

  // Builds DOM with textContent only: model and server text is never parsed as HTML.
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : String(child));
    }
    return el;
  }

  function uid() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  const storage = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw == null ? fallback : JSON.parse(raw);
      } catch (error) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch (error) {
        /* storage full or blocked: the session still works in memory */
      }
    },
  };

  let toastTimer = null;
  function toast(text) {
    let el = document.querySelector('.toast');
    if (!el) {
      el = h('div', { class: 'toast', role: 'status' });
      document.body.append(el);
    }
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 1800);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      // navigator.clipboard needs a secure context; plain http falls back to a selection copy.
      const area = h('textarea', { class: 'sr-only' });
      area.value = text;
      document.body.append(area);
      area.select();
      try { document.execCommand('copy'); } catch (ignored) { /* nothing else to try */ }
      area.remove();
    }
    toast('Đã sao chép');
  }

  function downloadCsv(filename, header, rows) {
    const cell = (value) => {
      const text = value == null ? '' : String(value);
      return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const lines = [header, ...rows].map((row) => row.map(cell).join(','));
    // The BOM makes Excel read the file as UTF-8 (Vietnamese diacritics).
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = h('a', { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---- number formatting (display only; exports keep the exact values) ---------------------
  const formatters = {};
  function fmt(value, digits, minDigits = 0) {
    const key = `${digits}:${minDigits}`;
    formatters[key] = formatters[key] || new Intl.NumberFormat('vi-VN', {
      maximumFractionDigits: digits, minimumFractionDigits: minDigits,
    });
    return formatters[key].format(value);
  }

  function vndAuto(value) {
    const abs = Math.abs(value);
    if (abs >= 1e9) return `${fmt(value / 1e9, 2, 2)} tỷ đồng`;
    if (abs >= 1e6) return `${fmt(value / 1e6, 2, 2)} triệu đồng`;
    return `${fmt(value, 2)} đồng`;
  }

  function formatScalar(result, unit) {
    const value = result.value;
    if (result.missing || result.non_finite || value == null) {
      return { main: 'Không xác định', exact: 'Kết quả rỗng hoặc không hữu hạn (ví dụ chia cho 0).' };
    }
    if (typeof value !== 'number') return { main: String(value), exact: null };
    switch (unit) {
      case 'VND': return { main: vndAuto(value), exact: `Chính xác: ${fmt(value, 2)} đồng` };
      case 'VND/share': return { main: `${fmt(value, 0)} đồng/cổ phiếu`, exact: `Chính xác: ${fmt(value, 4)}` };
      case 'PERCENT': return { main: `${fmt(value, 2)}%`, exact: `Chính xác: ${fmt(value, 10)}%` };
      case 'TIMES': return { main: `${fmt(value, 2)} lần`, exact: `Chính xác: ${fmt(value, 10)} lần` };
      case 'COUNT': return { main: fmt(value, 0), exact: null };
      case 'YEAR': return { main: String(value), exact: null };
      default: return { main: fmt(value, 4), exact: unit && unit !== 'NONE' ? `Đơn vị: ${unit}` : null };
    }
  }

  // ---- result tables ---------------------------------------------------------------------
  const isYear = (name, values) => /(^|_)(fiscal_)?year$|^nam$/i.test(name)
    || (values.length > 0 && values.every((v) => Number.isInteger(v) && v >= 1900 && v <= 2100));

  function tableOf(result) {
    if (!result) return null;
    let names; let rows;
    if (result.kind === 'dataframe') {
      names = result.columns;
      rows = result.data;
    } else if (result.kind === 'series') {
      names = ['index', result.name == null ? 'value' : String(result.name)];
      rows = result.index.map((key, i) => [key, result.values[i]]);
    } else if (result.kind === 'list') {
      names = ['value'];
      rows = result.values.map((v) => [v]);
    } else if (result.kind === 'dict') {
      names = ['key', 'value'];
      rows = result.items;
    } else {
      return null;
    }
    const columns = names.map((name, i) => {
      const values = rows.map((row) => row[i]).filter((v) => v != null);
      const numeric = values.length > 0 && values.every((v) => typeof v === 'number');
      const type = numeric && isYear(name, values) ? 'year' : numeric ? 'num' : 'text';
      return { name, type };
    });
    return { columns, rows, rowCount: result.row_count ?? rows.length, truncated: Boolean(result.truncated) };
  }

  function columnLabel(name, type, unit, scaleKey) {
    const metrics = (state.catalog && state.catalog.metrics) || {};
    let label = metrics[name] || COLUMN_LABELS[name] || (name === 'index' ? 'Chỉ mục' : name);
    if (type === 'num' && unit === 'VND') label += ` (${VND_SCALES[scaleKey][1]})`;
    if (type === 'num' && unit === 'PERCENT') label += ' (%)';
    return label;
  }

  function cellText(value, column, unit, scaleKey) {
    if (value == null) return '–';
    if (column.type === 'year') return String(value);
    if (column.type === 'num') {
      if (unit === 'VND') {
        const [divisor, , digits] = VND_SCALES[scaleKey];
        return fmt(value / divisor, digits, digits);
      }
      if (unit === 'PERCENT' || unit === 'TIMES') return fmt(value, 2, 2);
      return fmt(value, 4);
    }
    const metrics = (state.catalog && state.catalog.metrics) || {};
    if (column.name === 'metric' && metrics[value]) return metrics[value];
    if (column.name === 'statement_scope' && SCOPES[value]) return SCOPES[value];
    return String(value);
  }

  function renderTable(table, unit, scaleKey, { limit, filter } = {}) {
    let rows = table.rows.map((row, index) => ({ row, index }));
    if (filter) {
      const needle = fold(filter);
      rows = rows.filter(({ row }) => row.some((value, i) => fold(cellText(value, table.columns[i], unit, scaleKey)).includes(needle)));
    }
    const shown = limit ? rows.slice(0, limit) : rows;
    const head = h('tr', null, table.columns.map((c) => h('th', { class: c.type === 'num' ? 'n' : null, scope: 'col' },
      columnLabel(c.name, c.type, unit, scaleKey))));
    const body = shown.map(({ row }) => h('tr', null, row.map((value, i) => h('td', {
      class: table.columns[i].type === 'num' ? 'n' : null,
      title: table.columns[i].type === 'num' && value != null ? String(value) : null,
    }, cellText(value, table.columns[i], unit, scaleKey)))));
    return { node: h('div', { class: 'table-wrap' }, h('table', { class: 'data' }, h('thead', null, head), h('tbody', null, body))), matched: rows.length };
  }

  function fold(text) {
    return String(text).normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
  }

  // ---- sources -----------------------------------------------------------------------------
  function reportOf(source) {
    const parts = String(source.source_ref || '').split('/');
    const folder = parts[2] || '';
    const scope = /_consolidated$/.test(folder) ? 'hợp nhất' : /_separate$/.test(folder) ? 'riêng' : null;
    return { key: parts.slice(0, 3).join('/'), ticker: parts[0] || '?', year: parts[1] || '?', scope };
  }

  function reportName(report) {
    return `${report.ticker} · BCTC${report.scope ? ' ' + report.scope : ''} ${report.year}`;
  }

  // ---- state -------------------------------------------------------------------------------
  const state = {
    token: storage.get('aifin.token', ''),
    sessions: [],
    activeId: storage.get('aifin.active', null),
    catalog: null,
    catalogError: null,
    pending: null,
    canvas: { open: false, mode: null, msgId: null, tab: null, query: '', scale: 'ty', focus: null },
  };

  function loadSessions() {
    const stored = storage.get('aifin.sessions', []);
    state.sessions = Array.isArray(stored) ? stored.filter((s) => s && Array.isArray(s.messages)) : [];
    for (const session of state.sessions) {
      for (const message of session.messages) {
        if (message.role === 'assistant' && message.pending) {
          // The page closed while this question was in flight; its answer is lost.
          message.pending = false;
          message.body = { status: 'CANCELLED', message: 'Trang đã đóng trước khi có câu trả lời.' };
        }
      }
    }
  }

  function save() {
    storage.set('aifin.sessions', state.sessions.slice(0, MAX_SESSIONS));
    storage.set('aifin.active', state.activeId);
  }

  const activeSession = () => state.sessions.find((s) => s.id === state.activeId) || null;

  function findMessage(id) {
    for (const session of state.sessions) {
      const message = session.messages.find((m) => m.id === id);
      if (message) return message;
    }
    return null;
  }

  function sessionTitle(session) {
    const first = session && session.messages.find((m) => m.role === 'user');
    return first ? first.text : 'Phiên mới';
  }

  function newSession() {
    const current = activeSession();
    if (current && current.messages.length === 0) {
      els.question.focus();
      return;
    }
    const session = { id: uid(), created: Date.now(), messages: [] };
    state.sessions.unshift(session);
    state.sessions = state.sessions.slice(0, MAX_SESSIONS);
    state.activeId = session.id;
    closeCanvas();
    save();
    renderAll();
    if (narrow()) setSidebar(false);
    els.question.focus();
  }

  const narrow = () => window.matchMedia('(max-width: 899px)').matches;
  const wide = () => window.matchMedia('(min-width: 1280px)').matches;

  // ---- API -----------------------------------------------------------------------------------
  function headers() {
    const result = { 'Content-Type': 'application/json' };
    if (state.token) result.Authorization = `Bearer ${state.token}`;
    return result;
  }

  async function loadCatalog() {
    try {
      const response = await fetch('/v1/catalog', { headers: headers() });
      if (response.status === 401) {
        state.catalog = null;
        state.catalogError = 'unauthorized';
      } else if (!response.ok) {
        state.catalogError = 'unavailable';
      } else {
        state.catalog = await response.json();
        state.catalogError = null;
      }
    } catch (error) {
      state.catalogError = 'network';
    }
    renderDataBox();
    // Column and metric names come from the catalog: redraw what was drawn without it.
    renderThread();
    if (state.canvas.open) renderCanvas();
  }

  async function checkReady() {
    try {
      const response = await fetch('/readyz');
      const body = await response.json();
      setConn(body.ready ? 'ready' : 'down', body.ready ? `Sẵn sàng · ${body.release}` : 'Chưa nạp release');
    } catch (error) {
      setConn('down', 'Mất kết nối');
    }
  }

  function setConn(stateName, text) {
    els.conn.dataset.state = stateName;
    els.conn.textContent = text;
    els.conn.title = text;
  }

  function localFailure(reason, message, http = 0) {
    return { http, body: { status: 'FAILED', reason_code: reason, message } };
  }

  async function ask(text) {
    const question = text.trim();
    if (!question || state.pending) return;
    let session = activeSession();
    if (!session) {
      session = { id: uid(), created: Date.now(), messages: [] };
      state.sessions.unshift(session);
      state.activeId = session.id;
    }
    const bot = { id: uid(), role: 'assistant', question, pending: true, at: Date.now() };
    session.messages.push({ id: uid(), role: 'user', text: question, at: Date.now() }, bot);
    const controller = new AbortController();
    state.pending = { message: bot, controller, started: Date.now(), timedOut: false, cancelled: false };
    const timer = setTimeout(() => { state.pending.timedOut = true; controller.abort(); }, CLIENT_TIMEOUT_MS);
    save();
    renderAll();
    const ticker = setInterval(updateElapsed, 500);

    let outcome;
    try {
      const response = await fetch('/v1/ask', {
        method: 'POST', headers: headers(), signal: controller.signal,
        body: JSON.stringify({ question, request_id: uid() }),
      });
      let body = null;
      try { body = await response.json(); } catch (error) { body = null; }
      if (response.status === 401) outcome = localFailure('UNAUTHORIZED', 'unauthorized', 401);
      else if (body && typeof body.status === 'string') outcome = { http: response.status, body };
      else outcome = localFailure(`HTTP_${response.status}`, (body && body.error) || response.statusText, response.status);
    } catch (error) {
      if (state.pending.cancelled) outcome = { http: 0, body: { status: 'CANCELLED', message: 'Bạn đã hủy câu hỏi này.' } };
      else if (state.pending.timedOut) outcome = localFailure('CLIENT_TIMEOUT', 'no response within 75 s');
      else outcome = localFailure('NETWORK', String(error && error.message || error));
    } finally {
      clearTimeout(timer);
      clearInterval(ticker);
    }
    Object.assign(bot, { pending: false, http: outcome.http, body: outcome.body, done: Date.now() });
    state.pending = null;
    save();
    renderAll();
    if (outcome.http === 401) openSettings('Máy chủ yêu cầu mã truy cập cho câu hỏi này.');
    if (bot.body.status === 'ANSWERED' && tableOf(bot.body.result) && wide()) openCanvas(bot.id, 'result');
  }

  function cancelPending() {
    if (!state.pending) return;
    state.pending.cancelled = true;
    state.pending.controller.abort();
  }

  // ---- rendering: sidebar ------------------------------------------------------------------
  function renderSessions() {
    const list = els.sessionList;
    list.replaceChildren();
    const sessions = state.sessions.filter((s) => s.messages.length || s.id === state.activeId);
    if (!sessions.length) {
      list.append(h('li', { class: 'session-empty' }, 'Chưa có phiên nào'));
      return;
    }
    for (const session of sessions) {
      const title = sessionTitle(session);
      list.append(h('li', { class: 'session-item', 'aria-current': session.id === state.activeId ? 'true' : null },
        h('button', {
          class: 'session-open', type: 'button', title,
          onclick: () => {
            state.activeId = session.id;
            closeCanvas();
            save();
            renderAll();
            if (narrow()) setSidebar(false);
          },
        }, title),
        h('button', {
          class: 'session-del', type: 'button', 'aria-label': `Xóa phiên: ${title}`, title: 'Xóa phiên',
          onclick: () => deleteSession(session.id),
        }, '×')));
    }
  }

  function deleteSession(id) {
    if (state.pending && activeSession() && activeSession().id === id) return;
    state.sessions = state.sessions.filter((s) => s.id !== id);
    if (state.activeId === id) {
      state.activeId = state.sessions.length ? state.sessions[0].id : null;
      closeCanvas();
    }
    save();
    renderAll();
  }

  function renderDataBox() {
    const box = els.dataBox;
    box.replaceChildren();
    const catalog = state.catalog;
    if (!catalog) {
      if (state.catalogError === 'unauthorized') {
        box.append(h('div', { class: 'muted' }, 'Cần mã truy cập để xem dữ liệu.'),
          h('button', { class: 'btn btn-sm', type: 'button', onclick: () => openSettings() }, 'Nhập mã truy cập'));
      } else if (state.catalogError) {
        box.append(h('div', { class: 'muted' }, 'Chưa tải được thông tin dữ liệu.'),
          h('button', { class: 'btn btn-sm', type: 'button', onclick: loadCatalog }, 'Tải lại'));
      } else {
        box.append(h('div', { class: 'muted' }, 'Đang tải…'));
      }
      return;
    }
    const years = catalog.companies.length
      ? `${Math.min(...catalog.companies.map((c) => c.first_year))}–${Math.max(...catalog.companies.map((c) => c.last_year))}`
      : '–';
    const build = String(catalog.build_version || '').replace(/^.*-v/, 'v');
    const row = (label, value, title) => h('div', { class: 'data-row' }, h('span', null, label), h('span', { title }, value));
    box.append(
      row('Catalog', `${build || '?'} · ${String(catalog.catalog_id).slice(0, 8)}`, catalog.catalog_id),
      row('Doanh nghiệp', fmt(catalog.companies.length, 0)),
      catalog.report_count != null ? row('Báo cáo', fmt(catalog.report_count, 0)) : null,
      row('Năm tài chính', years),
      row('Release', catalog.release),
      h('button', { class: 'btn btn-sm', type: 'button', onclick: openCoverage }, icon('building'), 'Danh sách doanh nghiệp'),
    );
  }

  function setSidebar(open) {
    els.app.dataset.sidebar = open ? 'open' : 'closed';
  }

  // ---- rendering: thread -----------------------------------------------------------------
  function renderAll() {
    renderSessions();
    renderThread();
    els.title.textContent = sessionTitle(activeSession());
    els.send.disabled = Boolean(state.pending);
    if (state.canvas.open) renderCanvas();
  }

  function renderThread() {
    const session = activeSession();
    const thread = els.thread;
    thread.replaceChildren();
    if (!session || !session.messages.length) {
      thread.append(renderWelcome());
      return;
    }
    for (const message of session.messages) {
      thread.append(message.role === 'user'
        ? h('div', { class: 'msg-user' }, h('div', { class: 'bubble' }, message.text))
        : renderAssistant(message));
    }
    requestAnimationFrame(() => { els.scroll.scrollTop = els.scroll.scrollHeight; });
  }

  function renderWelcome() {
    const catalog = state.catalog;
    const facts = catalog
      ? `${fmt(catalog.companies.length, 0)} doanh nghiệp${catalog.report_count ? ` · ${fmt(catalog.report_count, 0)} báo cáo` : ''} · số liệu năm từ báo cáo hợp nhất và riêng.`
      : 'Số liệu năm từ báo cáo tài chính hợp nhất và riêng của các doanh nghiệp niêm yết.';
    return h('section', { class: 'welcome' },
      h('h2', null, 'Hỏi đáp trên báo cáo tài chính'),
      h('p', null, facts),
      h('p', null, 'Mỗi câu trả lời được tính bằng mã Pandas chạy trong sandbox, kiểm chứng độc lập và dẫn về ô số liệu gốc. Nếu thiếu dữ liệu hoặc không kiểm chứng được, hệ thống sẽ từ chối thay vì đoán.'),
      h('div', { class: 'examples' }, EXAMPLES.map(([kind, text]) => h('button', {
        class: 'example', type: 'button', onclick: () => ask(text),
      }, h('small', null, kind), text))));
  }

  function statusPill(status) {
    switch (status) {
      case 'ANSWERED': return h('span', { class: 'pill pill-ok' }, 'Đã kiểm chứng');
      case 'NEEDS_CLARIFICATION': return h('span', { class: 'pill pill-warn' }, 'Cần làm rõ');
      case 'NO_DATA': return h('span', { class: 'pill pill-info' }, 'Không có dữ liệu');
      case 'UNSUPPORTED': return h('span', { class: 'pill pill-info' }, 'Ngoài phạm vi');
      case 'CANCELLED': return h('span', { class: 'pill pill-info' }, 'Đã hủy');
      default: return h('span', { class: 'pill pill-bad' }, 'Không hoàn tất');
    }
  }

  function seconds(ms) {
    return `${fmt(ms / 1000, 1)} giây`;
  }

  function renderAssistant(message) {
    if (message.pending) return renderPending(message);
    const body = message.body || {};
    const node = h('article', { class: 'msg-bot', id: `m-${message.id}` });
    const status = h('div', { class: 'status-line' }, statusPill(body.status));
    node.append(status);
    switch (body.status) {
      case 'ANSWERED': renderAnswer(node, status, message, body); break;
      case 'NEEDS_CLARIFICATION': renderClarification(node, message, body); break;
      case 'NO_DATA': renderNoData(node, message, body); break;
      case 'UNSUPPORTED': renderUnsupported(node, body); break;
      case 'CANCELLED': node.append(h('p', { class: 'message-text muted' }, body.message)); break;
      default: renderFailure(node, message, body);
    }
    node.append(renderFoot(message, body));
    return node;
  }

  function renderPending(message) {
    const elapsed = Math.max(0, Date.now() - (state.pending ? state.pending.started : message.at));
    return h('article', { class: 'msg-bot pending', id: `m-${message.id}` },
      h('div', { class: 'pending-row' },
        h('span', { id: 'pending-text' }, pendingText(elapsed)),
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: cancelPending }, 'Hủy')),
      h('div', { class: 'bar', role: 'progressbar', 'aria-label': 'Đang xử lý' }));
  }

  function pendingText(elapsed) {
    const base = `Đang hiểu câu hỏi, sinh mã, chạy và kiểm chứng · ${Math.floor(elapsed / 1000)} giây`;
    return elapsed > SLOW_AFTER_MS ? `${base} · lâu hơn thường lệ` : base;
  }

  function updateElapsed() {
    const el = document.getElementById('pending-text');
    if (el && state.pending) el.textContent = pendingText(Date.now() - state.pending.started);
  }

  function renderAnswer(node, status, message, body) {
    const result = body.result || { kind: 'none' };
    const program = body.program;
    if (program) {
      status.append(h('button', {
        class: 'run-btn', type: 'button', onclick: () => openCanvas(message.id, 'program'),
      }, icon('code'), `Đã chạy mã Pandas · ${seconds(body.elapsed_ms || 0)}`, ' ▸'));
    } else if (body.elapsed_ms != null) {
      status.append(h('span', null, seconds(body.elapsed_ms)));
    }
    const table = tableOf(result);
    if (result.kind === 'scalar') {
      const shown = formatScalar(result, body.unit);
      node.append(h('div', { class: 'answer' },
        h('div', { class: 'answer-value' }, shown.main),
        shown.exact ? h('div', { class: 'answer-exact' }, shown.exact) : null));
    } else if (table) {
      const preview = renderTable(table, body.unit, state.canvas.scale, { limit: PREVIEW_ROWS });
      node.append(h('div', { class: 'preview' }, preview.node,
        h('div', { class: 'preview-foot' },
          h('span', null, `${fmt(table.rowCount, 0)} dòng${table.rows.length > PREVIEW_ROWS ? `, đang hiện ${PREVIEW_ROWS}` : ''}`),
          h('button', { class: 'btn btn-sm', type: 'button', onclick: () => openCanvas(message.id, 'result') },
            icon('table'), `Xem bảng kết quả (${fmt(table.rowCount, 0)} dòng)`))));
    } else {
      node.append(h('p', { class: 'message-text answer-note' }, 'Chương trình chạy xong nhưng không trả về giá trị.'));
    }
    const sources = body.sources || [];
    if (sources.length) {
      const chips = sources.slice(0, 4).map((source) => {
        const report = reportOf(source);
        return h('button', {
          class: 'chip', type: 'button', title: `${source.label_text || ''} · ${source.raw_text || ''}`,
          onclick: () => openCanvas(message.id, 'sources', source.fact_id),
        }, icon('doc'), h('span', null, `${source.label_text || 'Ô số liệu'} · ${report.ticker} ${report.year}${report.scope ? ' ' + report.scope : ''}`));
      });
      if (sources.length > 4) {
        chips.push(h('button', { class: 'chip', type: 'button', onclick: () => openCanvas(message.id, 'sources') },
          h('span', null, `+${sources.length - 4} nguồn`)));
      }
      node.append(h('div', { class: 'sources' }, h('span', { class: 'sources-label' }, 'Nguồn'), chips));
    }
  }

  function insertPhrase(question, phrase) {
    const match = question.match(/\s+(là bao nhiêu|bằng bao nhiêu|bao nhiêu|là gì)/i);
    if (match) return question.slice(0, match.index) + phrase + question.slice(match.index);
    return `${question.replace(/[\s?.]+$/, '')}${phrase}?`;
  }

  function yearBounds() {
    const companies = (state.catalog && state.catalog.companies) || [];
    if (!companies.length) return [1990, 2100];
    return [Math.min(...companies.map((c) => c.first_year)), Math.max(...companies.map((c) => c.last_year))];
  }

  function renderClarification(node, message, body) {
    const notice = h('div', { class: 'notice notice-warn' }, h('p', { class: 'message-text' }, body.message || 'Câu hỏi cần thêm thông tin.'));
    const fix = h('div', { class: 'fix' });
    const disabled = Boolean(state.pending);
    if (body.reason_code === 'MISSING_PERIOD') {
      const [low, high] = yearBounds();
      const inputId = `year-${message.id}`;
      const input = h('input', {
        class: 'field field-year', id: inputId, type: 'number', inputmode: 'numeric', min: low, max: high, placeholder: high,
      });
      const go = () => {
        const year = Number.parseInt(input.value, 10);
        if (!(year >= 1900 && year <= 2100)) {
          input.focus();
          return;
        }
        ask(insertPhrase(message.question, ` năm ${year}`));
      };
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          go();
        }
      });
      fix.append(h('label', { for: inputId }, 'Năm tài chính'), input,
        h('button', { class: 'btn btn-primary btn-sm', type: 'button', disabled, onclick: go }, 'Hỏi lại'));
    } else if (body.reason_code === 'MISSING_SCOPE') {
      fix.append(h('span', { class: 'muted' }, 'Chọn loại báo cáo:'),
        h('button', { class: 'btn btn-sm', type: 'button', disabled, onclick: () => ask(insertPhrase(message.question, ' theo báo cáo hợp nhất')) }, 'Hợp nhất'),
        h('button', { class: 'btn btn-sm', type: 'button', disabled, onclick: () => ask(insertPhrase(message.question, ' theo báo cáo riêng')) }, 'Riêng'));
    } else if (body.reason_code === 'MISSING_METRIC') {
      const inputId = `metric-${message.id}`;
      const input = h('input', { class: 'field field-metric', id: inputId, type: 'text', placeholder: 'Ví dụ: doanh thu thuần' });
      const choose = (metric) => ask(insertPhrase(message.question, `, chỉ tiêu ${metric}`));
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          if (input.value.trim()) choose(input.value.trim());
        }
      });
      fix.append(h('label', { for: inputId }, 'Chỉ tiêu cần tra cứu'), input,
        h('button', { class: 'btn btn-primary btn-sm', type: 'button', disabled, onclick: () => {
          if (input.value.trim()) choose(input.value.trim());
          else input.focus();
        } }, 'Hỏi lại'));
    }
    fix.append(h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => editQuestion(message.question) }, 'Sửa câu hỏi'));
    notice.append(fix);
    node.append(notice);
  }

  function companiesIn(question) {
    const companies = (state.catalog && state.catalog.companies) || [];
    const tickers = new Set((question.match(/\b[A-Z][A-Z0-9]{2}\b/g) || []));
    return companies.filter((c) => tickers.has(c.ticker));
  }

  function coverageLine(company) {
    const scopes = company.statement_scopes.map((s) => SCOPES[s] || s).join(', ');
    return `${company.ticker} có số liệu năm ${company.first_year}–${company.last_year} (báo cáo ${scopes}).`;
  }

  function renderNoData(node, message, body) {
    const friendly = NO_DATA_REASONS[body.reason_code];
    const notice = h('div', { class: 'notice notice-info' },
      h('p', { class: 'message-text' }, friendly || body.message || 'Không có dữ liệu cho câu hỏi này.'));
    if (friendly && body.message) {
      notice.append(h('details', { class: 'details' }, h('summary', null, 'Chi tiết kỹ thuật'),
        h('dl', { class: 'kv' }, h('dt', null, 'reason_code'), h('dd', null, body.reason_code),
          h('dt', null, 'message'), h('dd', null, body.message))));
    }
    if (body.reason_code === 'PERIOD_OUTSIDE_COVERAGE') {
      for (const company of companiesIn(message.question)) notice.append(h('p', { class: 'message-text muted' }, coverageLine(company)));
    }
    const fix = h('div', { class: 'fix' });
    if (body.reason_code === 'UNKNOWN_COMPANY' && state.catalog) {
      fix.append(h('button', { class: 'btn btn-sm', type: 'button', onclick: openCoverage }, icon('building'), 'Xem danh sách doanh nghiệp'));
    }
    fix.append(h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => editQuestion(message.question) }, 'Sửa câu hỏi'));
    notice.append(fix);
    node.append(notice);
  }

  function renderUnsupported(node, body) {
    node.append(h('div', { class: 'notice notice-info' },
      h('p', { class: 'message-text' }, body.message || 'Câu hỏi nằm ngoài phạm vi dữ liệu.'),
      h('p', { class: 'message-text muted' }, 'Dữ liệu gồm số liệu năm của bảng cân đối kế toán, kết quả kinh doanh và lưu chuyển tiền tệ.')));
  }

  function renderFailure(node, message, body) {
    const reason = body.reason_code || '';
    const friendly = FAILURES[reason] || (/^HTTP_/.test(reason) ? `Máy chủ trả lỗi ${reason.slice(5)}.` : VERIFY_FAILURE);
    node.append(h('div', { class: 'notice notice-bad' },
      h('p', { class: 'message-text' }, friendly),
      h('div', { class: 'fix' },
        h('button', { class: 'btn btn-sm', type: 'button', disabled: Boolean(state.pending), onclick: () => ask(message.question) }, 'Thử lại'),
        reason === 'UNAUTHORIZED' ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => openSettings() }, 'Nhập mã truy cập') : null),
      h('details', { class: 'details' }, h('summary', null, 'Chi tiết kỹ thuật'),
        h('dl', { class: 'kv' },
          h('dt', null, 'reason_code'), h('dd', null, reason || '–'),
          h('dt', null, 'message'), h('dd', null, body.message || '–'),
          message.http ? [h('dt', null, 'HTTP'), h('dd', null, String(message.http))] : null))));
  }

  function renderFoot(message, body) {
    const time = new Date(message.done || message.at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    const parts = [h('span', null, time)];
    if (body.request_id) {
      parts.push(h('button', {
        class: 'link-btn', type: 'button', title: 'Sao chép mã yêu cầu (dùng khi báo lỗi)',
        onclick: () => copyText(body.request_id),
      }, `#${String(body.request_id).slice(0, 8)}`));
    }
    if (body.status === 'ANSWERED' && (body.sources || []).length) {
      parts.push(h('button', { class: 'link-btn', type: 'button', onclick: () => openCanvas(message.id, 'sources') },
        `${body.sources.length} ô nguồn`));
    }
    return h('div', { class: 'msg-foot' }, parts);
  }

  function editQuestion(text) {
    els.question.value = text;
    autosize();
    els.question.focus();
    els.question.setSelectionRange(text.length, text.length);
  }

  // ---- canvas --------------------------------------------------------------------------------
  function tabsFor(message) {
    const body = message.body || {};
    const tabs = [];
    const table = tableOf(body.result);
    if (table) tabs.push(['result', `Bảng dữ liệu (${fmt(table.rowCount, 0)})`]);
    if ((body.sources || []).length) tabs.push(['sources', `Nguồn (${body.sources.length})`]);
    if (body.program) tabs.push(['program', 'Code Python']);
    return tabs;
  }

  function openCanvas(msgId, tab, focus = null) {
    Object.assign(state.canvas, { open: true, mode: 'answer', msgId, tab, query: '', focus });
    renderCanvas();
    if (narrow()) setSidebar(false);
  }

  function openCoverage() {
    Object.assign(state.canvas, { open: true, mode: 'coverage', msgId: null, tab: 'companies', query: '', focus: null });
    renderCanvas();
    if (narrow()) setSidebar(false);
  }

  function closeCanvas() {
    state.canvas.open = false;
    els.app.dataset.canvas = 'closed';
  }

  function renderCanvas() {
    const canvas = state.canvas;
    els.app.dataset.canvas = canvas.open ? 'open' : 'closed';
    if (!canvas.open) return;
    if (canvas.mode === 'coverage') {
      renderCoverage();
      return;
    }
    const message = findMessage(canvas.msgId);
    const tabs = message && message.body ? tabsFor(message) : [];
    if (!tabs.length) {
      closeCanvas();
      return;
    }
    if (!tabs.some(([key]) => key === canvas.tab)) canvas.tab = tabs[0][0];
    els.canvasTitle.textContent = message.question;
    els.canvasTitle.title = message.question;
    els.tabs.replaceChildren(...tabs.map(([key, label]) => h('button', {
      class: 'tab', type: 'button', role: 'tab', 'aria-selected': key === canvas.tab ? 'true' : 'false',
      onclick: () => {
        Object.assign(canvas, { tab: key, query: '', focus: null });
        renderCanvas();
      },
    }, label)));
    if (canvas.tab === 'result') renderResultTab(message);
    else if (canvas.tab === 'sources') renderSourcesTab(message);
    else renderProgramTab(message);
  }

  function searchBox(placeholder, onInput) {
    const input = h('input', { class: 'field', id: 'canvas-search', type: 'search', placeholder, 'aria-label': placeholder });
    input.value = state.canvas.query;
    input.addEventListener('input', () => {
      state.canvas.query = input.value;
      onInput();
    });
    return h('div', { class: 'search' }, icon('search'), input);
  }

  function renderResultTab(message) {
    const body = message.body;
    const table = tableOf(body.result);
    const drawBody = () => {
      const drawn = renderTable(table, body.unit, state.canvas.scale, { filter: state.canvas.query });
      const notes = [h('div', { class: 'count' }, state.canvas.query
        ? `${fmt(drawn.matched, 0)} / ${fmt(table.rows.length, 0)} dòng khớp`
        : `${fmt(table.rowCount, 0)} dòng`)];
      if (table.truncated) {
        notes.push(h('p', { class: 'note' }, `Máy chủ chỉ trả ${fmt(table.rows.length, 0)} dòng đầu trên tổng ${fmt(table.rowCount, 0)} dòng.`));
      }
      els.canvasBody.replaceChildren(...notes, drawn.node,
        h('p', { class: 'note' }, 'Số hiển thị đã làm tròn; di chuột lên ô để xem giá trị đầy đủ. File xuất giữ giá trị đầy đủ.'));
    };
    const tools = [searchBox('Tìm trong bảng…', drawBody)];
    if (body.unit === 'VND') {
      const select = h('select', { class: 'select', 'aria-label': 'Đơn vị hiển thị' },
        Object.entries(VND_SCALES).map(([key, [, label]]) => h('option', { value: key, selected: key === state.canvas.scale }, label)));
      select.addEventListener('change', () => {
        state.canvas.scale = select.value;
        drawBody();
      });
      tools.push(select);
    }
    tools.push(h('button', {
      class: 'btn', type: 'button',
      onclick: () => downloadCsv(`ket-qua-${String(body.request_id || 'bang').slice(0, 12)}.csv`,
        table.columns.map((c) => columnLabel(c.name, c.type, body.unit, 'dong')), table.rows),
    }, icon('download'), 'Xuất CSV (Excel)'));
    els.canvasTools.replaceChildren(...tools);
    drawBody();
  }

  function renderSourcesTab(message) {
    const sources = message.body.sources || [];
    const drawBody = () => {
      const needle = fold(state.canvas.query);
      const matched = sources.filter((s) => !needle || fold(`${s.label_text} ${s.raw_text} ${s.source_ref}`).includes(needle));
      const groups = new Map();
      for (const source of matched) {
        const report = reportOf(source);
        if (!groups.has(report.key)) groups.set(report.key, { report, items: [] });
        groups.get(report.key).items.push(source);
      }
      const nodes = [h('div', { class: 'count' }, `${fmt(matched.length, 0)} ô số liệu gốc từ ${fmt(groups.size, 0)} báo cáo`)];
      for (const { report, items } of groups.values()) {
        nodes.push(h('section', { class: 'src-group' }, h('h3', null, reportName(report)), items.map(sourceCard)));
      }
      nodes.push(h('p', { class: 'note' }, 'Giá trị in trên báo cáo (raw_text) được giữ nguyên định dạng gốc; quy đổi đơn vị dựa trên ghi chú đơn vị của bảng.'));
      els.canvasBody.replaceChildren(...nodes);
      const focused = els.canvasBody.querySelector('.src-card.focus');
      if (focused) focused.scrollIntoView({ block: 'center' });
    };
    els.canvasTools.replaceChildren(
      searchBox('Tìm theo nhãn, giá trị…', drawBody),
      h('button', {
        class: 'btn', type: 'button',
        onclick: () => downloadCsv(`nguon-${String(message.body.request_id || 'bang').slice(0, 12)}.csv`,
          ['label_text', 'raw_text', 'statement', 'source_ref', 'report_id', 'table_id', 'source_cell_id', 'page_id', 'fact_id'],
          sources.map((s) => [s.label_text, s.raw_text, s.statement, s.source_ref, s.report_id, s.table_id, s.source_cell_id, s.page_id, s.fact_id])),
      }, icon('download'), 'Xuất CSV'));
    drawBody();
  }

  function sourceCard(source) {
    const idRow = (label, value) => (value ? h('div', { class: 'id-row' },
      h('dt', null, label), h('dd', { title: value }, value),
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Sao chép ${label}`, onclick: () => copyText(value) }, icon('copy'))) : null);
    return h('article', { class: `src-card${state.canvas.focus === source.fact_id ? ' focus' : ''}` },
      h('div', { class: 'src-top' },
        h('span', { class: 'src-label' }, source.label_text || 'Ô số liệu'),
        h('span', { class: 'src-raw' }, source.raw_text || '–')),
      source.statement ? h('div', { class: 'src-meta' }, STATEMENTS[source.statement] || source.statement) : null,
      h('details', { class: 'details' }, h('summary', null, 'Định danh truy vết'),
        h('dl', { class: 'id-list' },
          idRow('Ô', source.source_cell_id), idRow('Bảng', source.table_id), idRow('Trang', source.page_id),
          idRow('Báo cáo', source.report_id), idRow('Tệp', source.source_ref), idRow('Fact', source.fact_id))));
  }

  function renderProgramTab(message) {
    const program = message.body.program;
    els.canvasTools.replaceChildren(h('button', { class: 'btn', type: 'button', onclick: () => copyText(program.code || '') },
      icon('copy'), 'Sao chép code'));
    const lines = String(program.code || '').split('\n');
    els.canvasBody.replaceChildren(
      program.explanation ? h('p', { class: 'explain' }, program.explanation) : null,
      h('pre', { class: 'code' }, h('code', null, lines.map((line) => h('span', null, line || ' ')))),
      h('p', { class: 'note' }, `Mã do mô hình sinh (${fmt(program.attempts || 1, 0)} lần sinh mã), chạy trong sandbox cô lập trên lát dữ liệu đã duyệt; kết quả được đối chiếu với phép tính tham chiếu trước khi trả lời.`));
  }

  function renderCoverage() {
    const companies = (state.catalog && state.catalog.companies) || [];
    els.canvasTitle.textContent = 'Doanh nghiệp trong dữ liệu';
    els.canvasTitle.title = '';
    els.tabs.replaceChildren(h('button', { class: 'tab', type: 'button', role: 'tab', 'aria-selected': 'true' },
      `Doanh nghiệp (${fmt(companies.length, 0)})`));
    const drawBody = () => {
      const needle = fold(state.canvas.query);
      const rows = companies.filter((c) => !needle || fold(`${c.ticker} ${c.company_name}`).includes(needle));
      const table = h('table', { class: 'data' },
        h('thead', null, h('tr', null, h('th', null, 'Mã'), h('th', null, 'Tên công ty'), h('th', { class: 'n' }, 'Năm'), h('th', null, 'Báo cáo'))),
        h('tbody', null, rows.map((c) => h('tr', {
          class: 'clickable', tabindex: '0', title: `Chèn ${c.ticker} vào câu hỏi`,
          onclick: () => insertTicker(c.ticker),
          onkeydown: (event) => { if (event.key === 'Enter') insertTicker(c.ticker); },
        }, h('td', null, h('strong', null, c.ticker)), h('td', null, c.company_name),
          h('td', { class: 'n' }, `${c.first_year}–${c.last_year}`),
          h('td', null, c.statement_scopes.map((s) => SCOPES[s] || s).join(', '))))));
      els.canvasBody.replaceChildren(
        h('div', { class: 'count' }, `${fmt(rows.length, 0)} doanh nghiệp · bấm một dòng để chèn mã vào câu hỏi`),
        h('div', { class: 'table-wrap' }, table));
    };
    els.canvasTools.replaceChildren(searchBox('Tìm mã hoặc tên công ty…', drawBody));
    drawBody();
  }

  function insertTicker(ticker) {
    const current = els.question.value;
    editQuestion(current ? `${current.replace(/\s+$/, '')} ${ticker}` : `${ticker} `);
    if (!wide()) closeCanvas();
  }

  // ---- settings ------------------------------------------------------------------------------
  function openSettings(note) {
    els.token.value = state.token;
    els.settings.returnValue = ''; // Esc keeps the previous value otherwise
    els.settingsNote.textContent = note || 'Máy chủ bật xác thực khi đặt biến AIFIN_API_TOKEN. Mã chỉ lưu trong trình duyệt này.';
    if (typeof els.settings.showModal === 'function') els.settings.showModal();
    els.token.focus();
  }

  function onSettingsClose() {
    const action = els.settings.returnValue;
    if (action === 'save') state.token = els.token.value.trim();
    if (action === 'clear') state.token = '';
    if (action === 'save' || action === 'clear') {
      storage.set('aifin.token', state.token);
      toast(state.token ? 'Đã lưu mã truy cập' : 'Đã xóa mã truy cập');
      loadCatalog();
    }
  }

  // ---- composer --------------------------------------------------------------------------------
  function autosize() {
    const area = els.question;
    area.style.height = 'auto';
    area.style.height = `${Math.min(area.scrollHeight, 200)}px`;
  }

  function submit(event) {
    if (event) event.preventDefault();
    if (state.pending) return;
    const text = els.question.value;
    if (!text.trim()) return;
    els.question.value = '';
    autosize();
    ask(text);
  }

  // ---- boot --------------------------------------------------------------------------------------
  function init() {
    Object.assign(els, {
      app: $('app'), sessionList: $('session-list'), dataBox: $('data-box'), thread: $('thread'), scroll: $('scroll'),
      title: $('session-title'), conn: $('conn'), question: $('question'), send: $('send'),
      canvasTitle: $('canvas-title'), tabs: $('tabs'), canvasTools: $('canvas-tools'), canvasBody: $('canvas-body'),
      settings: $('settings'), token: $('token'), settingsNote: $('settings-note'),
    });
    for (const slot of document.querySelectorAll('[data-icon]')) slot.replaceWith(icon(slot.dataset.icon));

    loadSessions();
    if (!activeSession()) state.activeId = state.sessions.length ? state.sessions[0].id : null;
    setSidebar(!narrow());

    $('composer').addEventListener('submit', submit);
    els.question.addEventListener('input', autosize);
    els.question.addEventListener('keydown', (event) => {
      // isComposing: Vietnamese input methods (Telex/VNI) compose with Enter.
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) submit(event);
    });
    $('new-session').addEventListener('click', newSession);
    $('toggle-sidebar').addEventListener('click', () => setSidebar(els.app.dataset.sidebar !== 'open'));
    $('scrim').addEventListener('click', () => setSidebar(false));
    $('close-canvas').addEventListener('click', closeCanvas);
    $('open-settings').addEventListener('click', () => openSettings());
    els.settings.addEventListener('close', onSettingsClose);
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      if (state.canvas.open) closeCanvas();
      else if (narrow() && els.app.dataset.sidebar === 'open') setSidebar(false);
    });

    renderAll();
    renderDataBox();
    // Initial sidebar/canvas state is applied without sliding; transitions start after first paint.
    requestAnimationFrame(() => requestAnimationFrame(() => els.app.classList.remove('booting')));
    checkReady();
    loadCatalog();
    setInterval(checkReady, 30000);
    els.question.focus();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
