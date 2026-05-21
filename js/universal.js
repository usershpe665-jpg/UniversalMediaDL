/**
 * LunarMediaDL — Universal Downloader Logic
 * Engine: metube yt-dlp Python API | UI: UniversalMediaDL space theme
 * Proxy: removed | Accepts any HTTP/HTTPS URL (1000+ platforms)
 * Author: Syawaliuz Octavian (UI) + Merged engine
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
      const panel = $(panelId);
      if (panel) panel.classList.add('tab-panel--active');
      activeTab = tab.dataset.tab;
      const lbl = $('downloadBtnLabel');
      if (lbl) lbl.textContent = activeTab === 'audio' ? 'Download Sound' : 'Download Video';
    });
  });

  // ─── URL Validation — accepts any HTTP/HTTPS URL ────────────────────────────
  function isValidUrl(url) {
    return /^https?:\/\/.+/.test(url.trim());
  }

  function validateUrl(url) {
    if (!url) {
      urlValidation.textContent = '';
      urlValidation.className   = 'url-validation';
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

  urlInput.addEventListener('input',  () => validateUrl(urlInput.value));
  urlInput.addEventListener('paste',  () => setTimeout(() => validateUrl(urlInput.value), 0));
  urlInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !fetchBtn.disabled) fetchBtn.click(); });
  clearBtn.addEventListener('click',  () => { urlInput.value = ''; validateUrl(''); urlInput.focus(); });
  pasteBtn.addEventListener('click',  async () => {
    try {
      urlInput.value = await navigator.clipboard.readText();
      validateUrl(urlInput.value);
    } catch {}
  });

  // ─── Fetch Info ────────────────────────────────────────────────────────────
  fetchBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!isValidUrl(url)) return;

    fetchBtn.disabled = true;
    fetchBtn.classList.add('loading');

    try {
      const meta = await Downloader.fetchInfo(url, false);
      $('videoTitle').textContent   = meta.title    || 'Media';
      $('videoChannel').textContent = meta.uploader || 'Unknown';
      const thumb = $('videoThumb');
      if (thumb) thumb.src = meta.thumbnail || '';
      const dur = $('videoDuration');
      if (dur && meta.duration_string) dur.textContent = meta.duration_string;

      // Populate format list if present
      if (typeof populateFormats === 'function' && meta.formats) {
        populateFormats(meta.formats);
      }

      UI.showStep('stepInfo');
    } catch (err) {
      UI.toast(err.message || 'Error fetching media info', 'error');
    } finally {
      fetchBtn.disabled = false;
      fetchBtn.classList.remove('loading');
    }
  });

  // ─── Format List (if element exists) ───────────────────────────────────────
  function populateFormats(formats) {
    const formatList = $('formatList');
    if (!formatList) return;
    formatList.innerHTML = '';

    const videoFmts = formats.filter(f => f.category === 'video');
    const audioFmts = formats.filter(f => f.category === 'audio');

    const addGroup = (label, items) => {
      if (!items.length) return;
      const hdr = document.createElement('div');
      hdr.className = 'format-group-header';
      hdr.textContent = label;
      formatList.appendChild(hdr);

      items.forEach(f => {
        const item = document.createElement('div');
        item.className = 'format-item';
        item.setAttribute('role', 'option');
        item.dataset.formatId = f.format_id;

        const size = f.filesize
          ? ` · ${(f.filesize / 1_048_576).toFixed(1)} MB`
          : f.tbr ? ` · ~${Math.round(f.tbr)}kbps` : '';

        item.innerHTML = `
          <span class="format-item__label">${f.label}</span>
          <span class="format-item__meta">${f.ext.toUpperCase()}${size}</span>
        `;

        item.addEventListener('click', () => {
          formatList.querySelectorAll('.format-item').forEach(i => i.classList.remove('selected'));
          item.classList.add('selected');
          Downloader.setSelectedFormat(f);
        });

        formatList.appendChild(item);
      });
    };

    addGroup('Video Formats', videoFmts);
    addGroup('Audio Formats', audioFmts);

    // Auto-select best
    const first = formatList.querySelector('.format-item');
    if (first) first.click();
  }

  // ─── Download ──────────────────────────────────────────────────────────────
  $('downloadBtn').addEventListener('click', async () => {
    const isAudio = activeTab === 'audio';
    const url     = urlInput.value.trim();

    const opts = {
      url,
      audio_only:    isAudio,
      audio_format:  isAudio ? ($('audioFormatSelect')?.value || 'mp3') : '',
      audio_quality: isAudio ? ($('audioQualitySelect')?.value || '0')  : '',
      quality:       isAudio ? '' : 'bestvideo+bestaudio/best',
      container:     $('containerSelect')?.value || 'mp4',
      embed_thumbnail: $('embedThumbVideo')?.checked || false,
      embed_metadata:  $('embedMetaVideo')?.checked  || true,
      rate_limit:    $('rateLimitInput')?.value.trim() || '',
    };

    UI.showStep('stepProgress');

    // Progress ring init
    const ring = $('progressRing');
    if (ring) {
      const circ = 2 * Math.PI * 28;
      ring.style.strokeDasharray  = circ;
      ring.style.strokeDashoffset = circ;
    }

    try {
      const jobId = await Downloader.startDownload(opts);
      Downloader.pollStatus(jobId, onProgress, onComplete, onError);
    } catch (e) { onError(e); }
  });

  // ── Callbacks ──────────────────────────────────────────────────────────────
  function onProgress(status) {
    const pct  = status.progress || 0;
    const circ = 2 * Math.PI * 28;
    const ring = $('progressRing');
    if (ring) ring.style.strokeDashoffset = circ - (pct / 100) * circ;
    const pctEl = $('progressPct');
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;

    $('progressTitle').textContent   = `Downloading ${pct.toFixed(1)}%`;
    $('progressBarFill').style.width = `${pct}%`;
    $('progressPctText').textContent = `${Math.round(pct)}%`;
    if ($('progressSpeed')) $('progressSpeed').textContent = status.speed    || '—';
    if ($('progressEta'))   $('progressEta').textContent   = status.eta      || '—';
    if ($('progressSize'))  $('progressSize').textContent  = status.filesize || '—';
  }

  function onComplete(status) {
    $('progressTitle').textContent   = '✓ Download Complete!';
    $('progressBarFill').style.width = '100%';
    $('progressPctText').textContent = '100%';
    const ring = $('progressRing');
    if (ring) ring.style.strokeDashoffset = 0;
    const pctEl = $('progressPct');
    if (pctEl) pctEl.textContent = '100%';

    const jobId  = status.job_id || Downloader.getCurrentJobId();
    const fileUrl = Downloader.getFileUrl(jobId);
    const btn = $('downloadFileBtn');
    if (btn) {
      btn.href     = fileUrl;
      btn.download = status.filename || 'download';
      btn.classList.remove('hidden');
    }

    UI.toast('File ready! Click "Save File" to download.', 'success');
    $('newDownloadBtn')?.classList.remove('hidden');

    // History
    const meta = Downloader.getCurrentMeta();
    History.add({
      jobId,
      title:     meta?.title    || 'Media Download',
      uploader:  meta?.uploader || 'Unknown',
      thumbnail: meta?.thumbnail || '',
      filename:  status.filename || 'download',
      format:    activeTab === 'audio' ? 'Audio' : 'Video',
      fileUrl,
      date: Date.now(),
    });
  }

  function onError(err) {
    $('progressTitle').textContent = '✗ Error';
    UI.toast(err.message || 'Download failed', 'error');
    $('newDownloadBtn')?.classList.remove('hidden');
  }

  $('backBtn')?.addEventListener('click', () => UI.showStep('stepUrl'));
  $('newDownloadBtn')?.addEventListener('click', () => {
    urlInput.value = '';
    validateUrl('');
    UI.showStep('stepUrl');
    $('downloadFileBtn')?.classList.add('hidden');
    $('newDownloadBtn')?.classList.add('hidden');
  });

  // ════════════════════════════════════════════════════════
  //  History System
  // ════════════════════════════════════════════════════════
  const historyToggleBtn = $('historyToggleBtn');
  const historyPanel     = $('historyPanel');
  const historyBackdrop  = $('historyBackdrop');
  const historyCloseBtn  = $('historyCloseBtn');
  const historyList      = $('historyList');
  const historyEmpty     = $('historyEmpty');
  const clearHistoryBtn  = $('clearHistoryBtn');

  const History = (function () {
    const HIST_KEY = 'lunarmediadl_universal_history';
    function load() {
      try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch { return []; }
    }
    function save(items) {
      try { localStorage.setItem(HIST_KEY, JSON.stringify(items.slice(0, 50))); } catch {}
    }
    function add(entry) {
      const items = load().filter(i => i.jobId !== entry.jobId);
      items.unshift(entry);
      save(items);
      renderHistory();
    }
    async function remove(jobId) {
      save(load().filter(i => i.jobId !== jobId));
      try { await Downloader.cancelJob(jobId); } catch {}
      renderHistory();
    }
    function clear() {
      try { localStorage.removeItem(HIST_KEY); } catch {}
      renderHistory();
    }
    return { load, add, remove, clear };
  })();

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function relativeTime(ts) {
    const d = Date.now() - ts;
    if (d < 60000)    return 'Just now';
    if (d < 3600000)  return `${Math.floor(d/60000)}m ago`;
    if (d < 86400000) return `${Math.floor(d/3600000)}h ago`;
    return `${Math.floor(d/86400000)}d ago`;
  }

  function renderHistory() {
    if (!historyList) return;
    historyList.querySelectorAll('.history-item').forEach(el => el.remove());
    const items = History.load();
    if (!items.length) {
      if (historyEmpty) historyEmpty.style.display = '';
      return;
    }
    if (historyEmpty) historyEmpty.style.display = 'none';

    items.forEach(item => {
      const el = document.createElement('div');
      el.className = 'history-item';
      el.innerHTML = `
        <div class="history-item__thumb">
          ${item.thumbnail ? `<img src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy"/>` : '<div class="history-item__thumb-placeholder"></div>'}
          <span class="history-item__format-badge">${escapeHtml(item.format||'Video')}</span>
        </div>
        <div class="history-item__info">
          <div class="history-item__title">${escapeHtml(item.title)}</div>
          <div class="history-item__meta">
            <span>${escapeHtml(item.filename||'')}</span>
            <span class="history-item__date">${relativeTime(item.date)}</span>
          </div>
        </div>
        <div class="history-item__actions">
          <a href="${escapeHtml(item.fileUrl)}" class="btn btn--ghost btn--xs" download title="Re-download">
            <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M12 5v14M5 12l7 7 7-7" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
          </a>
          <button class="btn btn--ghost btn--xs history-item__del" data-job="${escapeHtml(item.jobId)}" title="Remove">
            <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          </button>
        </div>
      `;
      el.querySelector('.history-item__del').addEventListener('click', async e => {
        await History.remove(e.currentTarget.dataset.job);
      });
      historyList.appendChild(el);
    });
  }

  function openHistory()  { renderHistory(); historyPanel.classList.add('open'); historyPanel.setAttribute('aria-hidden','false'); document.body.style.overflow = 'hidden'; }
  function closeHistory() { historyPanel.classList.remove('open'); historyPanel.setAttribute('aria-hidden','true'); document.body.style.overflow = ''; }

  historyToggleBtn && historyToggleBtn.addEventListener('click', openHistory);
  historyCloseBtn  && historyCloseBtn.addEventListener('click', closeHistory);
  historyBackdrop  && historyBackdrop.addEventListener('click', closeHistory);
  clearHistoryBtn  && clearHistoryBtn.addEventListener('click', () => {
    if (confirm('Clear all download history?')) { History.clear(); UI.toast('History cleared.', 'info'); }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && historyPanel?.classList.contains('open')) closeHistory();
  });

  // ─── Init ──────────────────────────────────────────────────────────────────
  function init() {
    UI.init();
    renderHistory();
    if (urlInput) urlInput.focus();
    console.info('%cLunarMediaDL Universal Module', 'color:#22d3ee;font-size:14px;font-weight:bold');
    console.info('Engine: metube yt-dlp Python API | Proxy: removed | Cookies: YOUTUBE_COOKIES env var');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
