let apiKey = '';
let pollTimer = null;

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
  'tasks.noActiveTasks': 'No running tasks right now.'
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

function renderSummary(summary, activeCount) {
  const image = summary.image || {};
  const video = summary.video || {};
  setText('summary-total', String(summary.total || 0));
  setText('summary-image-running', String(image.running || 0));
  setText('summary-image-success', String(image.success || 0));
  setText('summary-image-failure', String(image.failure || 0));
  setText('summary-video-running', String(video.running || 0));
  setText('summary-video-success', String(video.success || 0));
  setText('summary-video-failure', String(video.failure || 0));
  setText('active-count', String(activeCount || 0));
}

function renderActiveTasks(tasks) {
  const body = byId('active-task-body');
  const empty = byId('active-empty');
  if (!body || !empty) return;

  if (!tasks.length) {
    body.innerHTML = '';
    empty.classList.remove('hidden');
    empty.textContent = tt('tasks.noActiveTasks');
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
  const body = byId('daily-stats-body');
  if (!body) return;
  body.innerHTML = stats.map(item => `
    <tr>
      <td class="mono-cell">${item.date}</td>
      <td>${item.total || 0}</td>
      <td>${item.image?.running || 0}</td>
      <td>${item.image?.success || 0}</td>
      <td>${item.image?.failure || 0}</td>
      <td>${item.video?.running || 0}</td>
      <td>${item.video?.success || 0}</td>
      <td>${item.video?.failure || 0}</td>
    </tr>
  `).join('');
}

async function loadTasks() {
  try {
    const res = await fetch('/v1/admin/tasks', {
      headers: buildAuthHeaders(apiKey)
    });
    if (res.ok) {
      const data = await res.json();
      const activeTasks = Array.isArray(data.active_tasks) ? data.active_tasks : [];
      const dailyStats = Array.isArray(data.daily_stats) ? data.daily_stats : [];
      renderSummary(data.summary_today || {}, activeTasks.length);
      renderActiveTasks(activeTasks);
      renderDailyStats(dailyStats);
      return;
    }
    if (res.status === 401) {
      logout();
      return;
    }
    throw new Error(`HTTP ${res.status}`);
  } catch (error) {
    showToast(t('common.loadError', { msg: error.message }), 'error');
  }
}

async function init() {
  apiKey = await ensureAdminKey();
  if (apiKey === null) return;
  await loadTasks();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(loadTasks, 10000);
}

window.addEventListener('beforeunload', () => {
  if (pollTimer) clearInterval(pollTimer);
});

document.addEventListener('DOMContentLoaded', init);
