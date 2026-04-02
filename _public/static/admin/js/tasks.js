let apiKey = '';
let pollTimer = null;
let loading = false;
let currentTypeFilter = 'all';
let currentStatusFilter = 'all';
let currentTasks = [];

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

function formatDuration(ms) {
  const seconds = Math.max(0, Math.floor((ms || 0) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainSeconds = seconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${remainSeconds}s`;
  if (minutes > 0) return `${minutes}m ${remainSeconds}s`;
  return `${remainSeconds}s`;
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

function setText(id, value) {
  const element = byId(id);
  if (element) element.textContent = value;
}

function setButtonLoading(isLoading) {
  const btn = byId('manual-refresh-btn');
  if (!btn) return;
  btn.disabled = isLoading;
  btn.classList.toggle('opacity-60', isLoading);
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
      <td><span class="task-type-badge task-type-${task.task_type === 'video' ? 'video' : 'image'}">${taskTypeLabel(task.task_type)}</span></td>
      <td>${taskSourceLabel(task.source)}</td>
      <td class="mono-cell">${task.model || '-'}</td>
      <td><span class="task-status-badge task-status-${task.status || 'running'}">${taskStatusLabel(task.status)}</span></td>
      <td class="mono-cell">${formatDateTime(task.created_at)}</td>
      <td class="mono-cell">${formatDuration(task.duration_ms)}</td>
      <td class="mono-cell">${task.endpoint || '-'}</td>
    </tr>
  `).join('');
}

function renderDailyStats(stats) {
  const grid = byId('daily-stats-grid');
  if (!grid) return;
  grid.innerHTML = stats.map(item => `
    <article class="daily-card">
      <div class="daily-date">${item.date}</div>
      <div class="daily-total">${item.total || 0}</div>
      <div class="daily-total-label">${tt('tasks.totalTasks')}</div>
      <div class="daily-breakdown">
        <div class="daily-metric">
          <div class="daily-metric-title">${tt('tasks.imageRunning')}</div>
          <div class="daily-metric-value">${item.image?.running || 0}</div>
        </div>
        <div class="daily-metric">
          <div class="daily-metric-title">${tt('tasks.imageSuccess')}</div>
          <div class="daily-metric-value">${item.image?.success || 0}</div>
        </div>
        <div class="daily-metric">
          <div class="daily-metric-title">${tt('tasks.imageFailure')}</div>
          <div class="daily-metric-value">${item.image?.failure || 0}</div>
        </div>
        <div class="daily-metric">
          <div class="daily-metric-title">${tt('tasks.videoRunning')}</div>
          <div class="daily-metric-value">${item.video?.running || 0}</div>
        </div>
        <div class="daily-metric">
          <div class="daily-metric-title">${tt('tasks.videoSuccess')}</div>
          <div class="daily-metric-value">${item.video?.success || 0}</div>
        </div>
        <div class="daily-metric">
          <div class="daily-metric-title">${tt('tasks.videoFailure')}</div>
          <div class="daily-metric-value">${item.video?.failure || 0}</div>
        </div>
      </div>
    </article>
  `).join('');
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
      const dailyStats = Array.isArray(data.daily_stats) ? data.daily_stats : [];
      renderSummary(data.summary_total || {});
      renderTaskList();
      renderDailyStats(dailyStats);
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
  await loadTasks(false);
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => loadTasks(false), 10000);
}

window.addEventListener('beforeunload', () => {
  if (pollTimer) clearInterval(pollTimer);
});

document.addEventListener('DOMContentLoaded', init);
