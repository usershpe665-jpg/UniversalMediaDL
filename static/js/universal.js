/**
 * LunarMediaDL — Universal Downloader Logic
 * Socket.IO realtime progress + MeTube engine
 * Author: Syawaliuz Octavian
 */
(function App() {
  'use strict';
  const $ = id => document.getElementById(id);

  const urlInput        = $('urlInput');
  const urlInputWrapper = $('urlInputWrapper');
  const fetchBtn        = $('fetchBtn');
  const clearBtn        = $('clearBtn');
  const pasteBtn        = $('pasteBtn');
  const urlValidation   = $('urlValidation');

  // ─── Tab System ────────────────────────────────────────────────────────────
  let activeTab = 'video';
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('tab-panel--active'));
      tab.classList.add('active');
      const panelId = tab.dataset.tab === 'video' ? 'panelVideo' : 'panelAudio';
      $(panelId).classList.add('tab-panel--active');
      activeTab = tab.dataset.tab;
      $('downloadBtnLabel').textContent = activeTab === 'audio' ? 'Download Sound' : 'Download Video';
    });
  });

  // ─── URL Validation — accepts any HTTP/HTTPS URL ──────────────────────────
  function isValidUrl(url) {
    return /^https?:\/\/.+/.test(url.trim());
  }

  function validateUrl(url) {
    if (!url) {
      urlValidation.textContent = '';
      fetchBtn.disabled = true;
      clearBtn.classList.add('hidden');
      return;
    }
    clearBtn.classList.remove('hidden');
    if (isValidUrl(url)) {
      urlValidation.textContent = '✓ Valid URL';
      urlValidation.className   = 'url-validation success';
      urlInputWrapper.className = 'url-input-wrapper valid';
      fetchBtn.disabled = false;
    } else {
      urlValidation.textContent = '✗ Masukkan URL yang valid (https://...)';
      urlValidation.className   = 'url-validation error';
      urlInputWrapper.className = 'url-input-wrapper invalid';
      fetchBtn.disabled = true;
    }
  }

  urlInput.addEventListener('input',   () => validateUrl(urlInput.value));
  urlInput.addEventListener('paste',   () => setTimeout(() => validateUrl(urlInput.value), 0));
  urlInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !fetchBtn.disabled) fetchBtn.click(); });
  clearBtn.addEventListener('click',   () => { urlInput.value = ''; validateUrl(''); urlInput.focus(); });
  pasteBtn.addEventListener('click',   async () => {
    try { urlInput.value = await navigator.clipboard.readText(); validateUrl(urlInput.value); } catch {}
  });

  // ─── Fetch Metadata ────────────────────────────────────────────────────────
  fetchBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!isValidUrl(url)) return;
    fetchBtn.disabled = true;
    fetchBtn.classList.add('loading');
    try {
      const meta = await Downloader.fetchInfo(url);
      $('videoTitle').textContent   = meta.title     || 'Media';
      $('videoChannel').textContent = meta.uploader  || 'Unknown';
      $('videoThumb').src           = meta.thumbnail || '';
      if (meta.duration_string) $('videoDuration').textContent = meta.duration_string;
      UI.showStep('stepInfo');
    } catch (err) {
      UI.toast(err.message || 'Error fetching media info', 'error');
    } finally {
      fetchBtn.disabled = false;
      fetchBtn.classList.remove('loading');
    }
  });

  // ─── Download ──────────────────────────────────────────────────────────────
  $('downloadBtn').addEventListener('click', async () => {
    const isAudio = activeTab === 'audio';
    const opts = {
      url:          urlInput.value.trim(),
      audio_only:   isAudio,
      audio_format: isAudio ? $('audioFormatSelect').value : 'mp3',
      quality:      isAudio ? 'best' : 'best',
    };
    UI.showStep('stepProgress');
    $('progressTitle').textContent   = 'Starting download...';
    $('progressBarFill').style.width = '0%';
    $('progressPctText').textContent = '0%';
    $('progressSpeed').textContent   = '';
    $('progressEta').textContent     = '';
    $('downloadFileBtn').classList.add('hidden');
    $('newDownloadBtn').classList.add('hidden');
    try {
      const jobId = await Downloader.startDownload(opts);
      Downloader.pollStatus(jobId, onProgress, onComplete, onError);
    } catch (e) { onError(e); }
  });

  function onProgress(status) {
    const pct = Math.min(99, Math.max(0, status.progress || 0));
    $('progressTitle').textContent   = `Downloading ${pct.toFixed(1)}%`;
    $('progressBarFill').style.width = `${pct}%`;
    $('progressPctText').textContent = `${Math.round(pct)}%`;
    $('progressSpeed').textContent   = status.speed || '';
    $('progressEta').textContent     = status.eta   || '';
  }

  function onComplete(status) {
    $('progressTitle').textContent   = '✓ Download Complete!';
    $('progressBarFill').style.width = '100%';
    $('progressPctText').textContent = '100%';
    $('progressSpeed').textContent   = '';
    $('progressEta').textContent     = '';
    const btn = $('downloadFileBtn');
    btn.href     = status.file_url || Downloader.getFileUrl(status.job_id);
    btn.download = status.filename || 'download';
    btn.classList.remove('hidden');
    $('newDownloadBtn').classList.remove('hidden');
    UI.toast('File ready! Click "Save File" to download.', 'success');

    const meta = Downloader.getCurrentMeta();
    History.add({
      jobId:     status.job_id,
      title:     meta?.title    || 'Media Download',
      uploader:  meta?.uploader || 'Unknown',
      thumbnail: meta?.thumbnail || '',
      filename:  status.filename || 'download',
      format:    activeTab === 'audio' ? 'Audio' : 'Video',
      fileUrl:   status.file_url || Downloader.getFileUrl(status.job_id),
      date:      Date.now(),
    });
    History.render();
  }

  function onError(err) {
    $('progressTitle').textContent = '✗ Download Error';
    $('progressBarFill').style.width = '0%';
    $('progressPctText').textContent = '0%';
    UI.toast(err.message || 'Download failed', 'error');
    $('newDownloadBtn').classList.remove('hidden');
  }

  $('backBtn').addEventListener('click', () => UI.showStep('stepUrl'));
  $('newDownloadBtn').addEventListener('click', () => {
    urlInput.value = '';
    validateUrl('');
    UI.showStep('stepUrl');
    $('downloadFileBtn').classList.add('hidden');
    $('newDownloadBtn').classList.add('hidden');
  });

  // ════════════════════════════════════════════════════════════════════════════
  //  HISTORY SYSTEM
  // ════════════════════════════════════════════════════════════════════════════
  const historyToggleBtn = $('historyToggleBtn');
  const historyPanel     = $('historyPanel');
  const historyBackdrop  = $('historyBackdrop');
  const historyCloseBtn  = $('historyCloseBtn');
  const historyList      = $('historyList');
  const historyEmpty     = $('historyEmpty');
  const clearHistoryBtn  = $('clearHistoryBtn');

  const History = (() => {
    const KEY = 'lunarytdl_universal_history';
    function load()  { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { return []; } }
    function save(i) { try { localStorage.setItem(KEY, JSON.stringify(i.slice(0, 100))); } catch {} }
    function add(e)  { const i = load().filter(x => x.jobId !== e.jobId); i.unshift(e); save(i); }
    function del(id) { save(load().filter(i => i.jobId !== id)); }
    function clear() { try { localStorage.removeItem(KEY); } catch {} }
    function render() {
      if (!historyList) return;
      historyList.querySelectorAll('.history-item').forEach(el => el.remove());
      const items = load();
      if (!items.length) { if (historyEmpty) historyEmpty.style.display = ''; return; }
      if (historyEmpty) historyEmpty.style.display = 'none';
      items.forEach(item => {
        const el = document.createElement('div');
        el.className = 'history-item';
        el.innerHTML = `
          <div class="history-item__thumb">
            ${item.thumbnail
              ? `<img src="${esc(item.thumbnail)}" alt="" loading="lazy"/>`
              : '<div class="history-item__thumb-placeholder"></div>'}
            <span class="history-item__format-badge">${esc(item.format || 'Video')}</span>
          </div>
          <div class="history-item__info">
            <div class="history-item__title">${esc(item.title)}</div>
            <div class="history-item__meta">
              <span>${esc(item.filename || '')}</span>
              <span class="history-item__date">${relTime(item.date)}</span>
            </div>
          </div>
          <div class="history-item__actions">
            <a href="${esc(item.fileUrl)}" class="btn btn--ghost btn--xs" download title="Download again">
              <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M12 5v14M5 12l7 7 7-7" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
            </a>
            <button class="btn btn--ghost btn--xs history-item__del" data-job="${esc(item.jobId)}" title="Remove">
              <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
            </button>
          </div>`;
        el.querySelector('.history-item__del').addEventListener('click', async e => {
          const jid = e.currentTarget.dataset.job;
          del(jid);
          try { await Downloader.cancelJob(jid); } catch {}
          render();
        });
        historyList.appendChild(el);
      });
    }
    return { load, add, del, clear, render };
  })();

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );
  }
  function relTime(ts) {
    const d = Date.now() - ts;
    if (d < 60e3)    return 'Just now';
    if (d < 3600e3)  return `${Math.floor(d/60e3)}m ago`;
    if (d < 86400e3) return `${Math.floor(d/3600e3)}h ago`;
    return `${Math.floor(d/86400e3)}d ago`;
  }

  function openHistory()  {
    History.render();
    historyPanel.classList.add('open');
    historyPanel.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  }
  function closeHistory() {
    historyPanel.classList.remove('open');
    historyPanel.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  if (historyToggleBtn) historyToggleBtn.addEventListener('click', openHistory);
  if (historyCloseBtn)  historyCloseBtn.addEventListener('click',  closeHistory);
  if (historyBackdrop)  historyBackdrop.addEventListener('click',  closeHistory);
  if (clearHistoryBtn)  clearHistoryBtn.addEventListener('click', () => {
    if (confirm('Clear all download history?')) {
      History.clear();
      History.render();
      UI.toast('History cleared.', 'info');
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && historyPanel?.classList.contains('open')) closeHistory();
  });

  // ─── Init ──────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    UI.init();
    History.render();
    console.info('%cLunarMediaDL Universal Module (MeTube engine)', 'color:#22d3ee;font-size:14px;font-weight:bold');
  });
})();
