let apiKey = '';
let loading = false;
let currentTypeFilter = 'all';
let currentStatusFilter = 'all';
let currentTasks = [];
let currentDailyStats = [];
let currentRangeFilter = 'today';
let failureDialogBound = false;

const byId = (id) => document.getElementById(id);

const FALLBACK_TEXT = {
  'tasks.typeImage': 'Image',
  'tasks.typeVideo': 'Video',
  'tasks.statusRunning': 'Running',
  'tasks.statusSuccess': 'Success',
  'tasks.statusFailure': 'Failure',
  'tasks.sourceImagesApi': 'Images API',
  'tasks.sourceVideosApi': 'Videos API',
  'tasks.sourceChat': 'Chat Completions',
  'tasks.sourceFunctionImagine': 'Imagine Function',
  'tasks.sourceFunctionVideo': 'Video Function',
  'tasks.tableAssetLink': 'Public Link',
  'tasks.openAssetLink': 'Open',
  'tasks.failureLogEmpty': 'No failure log available.',
  'tasks.noTasks': 'No matching tasks right now.',
  'tasks.totalTasks': 'Total Tasks'
};

function tt(key) {
  const value = t(key);
  if (typeof value === 'string' && !value.startsWith(key)) return value;
  return FALLBACK_TEXT[key] || key;
}

function formatDateTime(ms) {
  if (!ms) return '-';
  const date = new Date(ms);
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatDurationSeconds(ms) {
  return String(Math.max(0, Math.round((ms || 0) / 1000)));
}

function taskTypeLabel(type) {
  return type === 'video' ? tt('tasks.typeVideo') : tt('tasks.typeImage');
}

function taskStatusLabel(status) {
  if (status === 'success') return tt('tasks.statusSuccess');
  if (status === 'failure') return tt('tasks.statusFailure');
  return tt('tasks.statusRunning');
}

function taskSourceLabel(source) {
  const map = {
    images_api: tt('tasks.sourceImagesApi'),
    videos_api: tt('tasks.sourceVideosApi'),
    chat_completions: tt('tasks.sourceChat'),
    function_imagine: tt('tasks.sourceFunctionImagine'),
    function_video: tt('tasks.sourceFunctionVideo')
  };
  return map[source] || source || '-';
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function setText(id, value) {
  const element = byId(id);
  if (element) element.textContent = value;
}

function renderTaskResultLink(task) {
  const url = String(task?.result_url || '').trim();
  if (!url) return '<span class="task-link-empty">-</span>';
  return `<a class="task-link-button" href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener" title="${escapeHtml(url)}">${escapeHtml(tt('tasks.openAssetLink'))}</a>`;
}

function setButtonLoading(isLoading) {
  const btn = byId('manual-refresh-btn');
  if (!btn) return;
  btn.disabled = isLoading;
  btn.classList.toggle('opacity-60', isLoading);
}

function summarizeStats(rangeKey) {
  const stats = Array.isArray(currentDailyStats) ? currentDailyStats : [];
  if (!stats.length) {
    return {
      total: 0,
      image: { running: 0, success: 0, failure: 0 },
      video: { running: 0, success: 0, failure: 0 }
    };
  }

  let selected = [];
  if (rangeKey === 'yesterday') {
    selected = stats.slice(-2, -1);
  } else if (rangeKey === 'last3') {
    selected = stats.slice(-3);
  } else if (rangeKey === 'last7') {
    selected = stats.slice(-7);
  } else {
    selected = stats.slice(-1);
  }

  return selected.reduce((acc, item) => {
    acc.total += item.total || 0;
    acc.image.running += item.image?.running || 0;
    acc.image.success += item.image?.success || 0;
    acc.image.failure += item.image?.failure || 0;
    acc.video.running += item.video?.running || 0;
    acc.video.success += item.video?.success || 0;
    acc.video.failure += item.video?.failure || 0;
    return acc;
  }, {
    total: 0,
    image: { running: 0, success: 0, failure: 0 },
    video: { running: 0, success: 0, failure: 0 }
  });
}

function renderSummary(summary) {
  const image = summary.image || {};
  const video = summary.video || {};
  setText('summary-total', String(summary.total || 0));
  setText('summary-image-running', String(image.running || 0));
  setText('summary-image-success', String(image.success || 0));
  setText('summary-image-failure', String(image.failure || 0));
  setText('summary-video-running', String(video.running || 0));
  setText('summary-video-success', String(video.success || 0));
  setText('summary-video-failure', String(video.failure || 0));
  setText('active-count', String((image.running || 0) + (video.running || 0)));
}

function getFilteredTasks() {
  return currentTasks.filter((task) => {
    const matchesType = currentTypeFilter === 'all' || task.task_type === currentTypeFilter;
    const matchesStatus = currentStatusFilter === 'all' || task.status === currentStatusFilter;
    return matchesType && matchesStatus;
  });
}

function renderTaskList() {
  const body = byId('task-list-body');
  const empty = byId('task-list-empty');
  if (!body || !empty) return;

  const tasks = getFilteredTasks();
  if (!tasks.length) {
    body.innerHTML = '';
    empty.classList.remove('hidden');
    empty.textContent = tt('tasks.noTasks');
    return;
  }

  empty.classList.add('hidden');
  body.innerHTML = tasks.map(task => `
    <tr>
      <td><span class="task-type-badge task-type-${task.task_type === 'video' ? 'video' : 'image'}">${escapeHtml(taskTypeLabel(task.task_type))}</span></td>
      <td>${escapeHtml(taskSourceLabel(task.source))}</td>
      <td class="mono-cell">${escapeHtml(task.model || '-')}</td>
      <td>${task.status === 'failure'
        ? `<button type="button" class="task-status-badge task-status-button task-status-failure" data-task-id="${escapeHtml(task.task_id || '')}">${escapeHtml(taskStatusLabel(task.status))}</button>`
        : `<span class="task-status-badge task-status-${escapeHtml(task.status || 'running')}">${escapeHtml(taskStatusLabel(task.status))}</span>`}</td>
      <td class="mono-cell">${escapeHtml(formatDateTime(task.created_at))}</td>
      <td class="mono-cell">${escapeHtml(formatDurationSeconds(task.duration_ms))}</td>
      <td class="task-link-cell">${renderTaskResultLink(task)}</td>
      <td class="mono-cell">${escapeHtml(task.endpoint || '-')}</td>
    </tr>
  `).join('');
}

function findTaskById(taskId) {
  return currentTasks.find((task) => String(task.task_id || '') === String(taskId || ''));
}

function ensureFailureDialogBound() {
  if (failureDialogBound) return;
  const closeBtn = byId('task-failure-close');
  const dialog = byId('task-failure-dialog');
  const body = byId('task-list-body');

  if (closeBtn && dialog) {
    closeBtn.addEventListener('click', () => dialog.close());
  }

  if (body) {
    body.addEventListener('click', (event) => {
      const target = event.target.closest('[data-task-id]');
      if (!target) return;
      showFailureLog(target.getAttribute('data-task-id'));
    });
  }

  failureDialogBound = true;
}

function showFailureLog(taskId) {
  const task = findTaskById(taskId);
  const dialog = byId('task-failure-dialog');
  if (!task || !dialog) return;

  setText('task-failure-type', taskTypeLabel(task.task_type));
  setText('task-failure-source', taskSourceLabel(task.source));
  setText('task-failure-model', task.model || '-');
  setText('task-failure-time', formatDateTime(task.created_at));

  const logEl = byId('task-failure-log');
  if (logEl) {
    const message = String(task.error_message || '').trim();
    logEl.textContent = message || tt('tasks.failureLogEmpty') || t('common.unknownError');
  }

  dialog.showModal();
}

function setRangeFilter(value, button) {
  currentRangeFilter = value;
  document.querySelectorAll('[data-range-filter]').forEach((item) => {
    item.classList.toggle('active', item === button);
  });
  renderSummary(summarizeStats(currentRangeFilter));
}

function setTypeFilter(value, button) {
  currentTypeFilter = value;
  document.querySelectorAll('[data-filter-group="type"]').forEach((item) => {
    item.classList.toggle('active', item === button);
  });
  renderTaskList();
}

function setStatusFilter(value, button) {
  currentStatusFilter = value;
  document.querySelectorAll('[data-filter-group="status"]').forEach((item) => {
    item.classList.toggle('active', item === button);
  });
  renderTaskList();
}

async function loadTasks(showNotice = false) {
  if (loading) return;
  loading = true;
  setButtonLoading(true);
  try {
    const res = await fetch('/v1/admin/tasks', {
      headers: buildAuthHeaders(apiKey)
    });
    if (res.ok) {
      const data = await res.json();
      currentTasks = Array.isArray(data.task_list) ? data.task_list : [];
      currentDailyStats = Array.isArray(data.daily_stats) ? data.daily_stats : [];
      renderSummary(summarizeStats(currentRangeFilter));
      renderTaskList();
      if (showNotice) showToast(t('common.operationSuccess'), 'success');
      return;
    }
    if (res.status === 401) {
      logout();
      return;
    }
    throw new Error(`HTTP ${res.status}`);
  } catch (error) {
    showToast(t('common.loadError', { msg: error.message }), 'error');
  } finally {
    loading = false;
    setButtonLoading(false);
  }
}

async function init() {
  apiKey = await ensureAdminKey();
  if (apiKey === null) return;
  ensureFailureDialogBound();
  await loadTasks(false);
}

document.addEventListener('DOMContentLoaded', init);
