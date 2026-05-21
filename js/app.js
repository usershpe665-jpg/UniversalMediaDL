/**
 * LunarMediaDL — YouTube Downloader App
 * Engine: metube yt-dlp Python API | UI: UniversalMediaDL space theme
 * Proxy: removed | Cookies: via server env var YOUTUBE_COOKIES
 * Author: Syawaliuz Octavian (UI) + Merged engine
 */

(function App() {
  'use strict';

  // ════════════════════════════════════════════════════════
  //  DOM References
  // ════════════════════════════════════════════════════════
  const $ = id => document.getElementById(id);

  const urlInput        = $('urlInput');
  const urlInputWrapper = $('urlInputWrapper');
  const fetchBtn        = $('fetchBtn');
  const pasteBtn        = $('pasteBtn');
  const clearBtn        = $('clearBtn');
  const playlistToggle  = $('playlistToggle');
  const urlValidation   = $('urlValidation');

  const videoThumb     = $('videoThumb');
  const videoDuration  = $('videoDuration');
  const videoTypeBadge = $('videoTypeBadge');
  const videoTitle     = $('videoTitle');
  const videoChannel   = $('videoChannel');
  const videoViews     = $('videoViews');
  const videoDate      = $('videoDate');
  const videoDesc      = $('videoDesc');

  const qualityGroupUHD  = $('qualityGroupUHD');
  const qualityGroupHD   = $('qualityGroupHD');
  const qualityGroupSD   = $('qualityGroupSD');
  const qualitySelect    = $('qualitySelect');
  const formatList       = $('formatList');

  const backBtn          = $('backBtn');
  const downloadBtn      = $('downloadBtn');
  const downloadBtnLabel = $('downloadBtnLabel');
  const newDownloadBtn   = $('newDownloadBtn');
  const cancelBtn        = $('cancelBtn');
  const downloadFileBtn  = $('downloadFileBtn');

  const progressTitle    = $('progressTitle');
  const progressFilename = $('progressFilename');
  const progressBarFill  = $('progressBarFill');
  const progressPctText  = $('progressPctText');
  const progressSpeed    = $('progressSpeed');
  const progressEta      = $('progressEta');
  const progressSize     = $('progressSize');
  const progressRing     = $('progressRing');

  const historyToggleBtn = $('historyToggleBtn');
  const historyPanel     = $('historyPanel');
  const historyBackdrop  = $('historyBackdrop');
  const historyCloseBtn  = $('historyCloseBtn');
  const historyList      = $('historyList');
  const historyEmpty     = $('historyEmpty');
  const clearHistoryBtn  = $('clearHistoryBtn');

  // ════════════════════════════════════════════════════════
  //  Tab State → Dynamic Download Button
  // ════════════════════════════════════════════════════════
  let activeTab = 'video';

  function updateDownloadButton() {
    if (!downloadBtn || !downloadBtnLabel) return;
    const isAudio = activeTab === 'audio';
    downloadBtn.dataset.mode = isAudio ? 'audio' : 'video';

    const iconVideo = downloadBtn.querySelector('.download-btn__icon--video');
    const iconAudio = downloadBtn.querySelector('.download-btn__icon--audio');
    if (iconVideo) iconVideo.style.display = isAudio ? 'none' : '';
    if (iconAudio) iconAudio.style.display = isAudio ? '' : 'none';

    downloadBtnLabel.textContent = isAudio ? 'Download Audio' : 'Download Video';
  }

  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      activeTab = tab.dataset.tab || 'video';
      updateDownloadButton();
    });
  });

  // ════════════════════════════════════════════════════════
  //  Progress Ring
  // ════════════════════════════════════════════════════════
  const RING_R    = 28;
  const RING_CIRC = 2 * Math.PI * RING_R;

  function injectProgressGradient() {
    const svg = progressRing?.closest('svg');
    if (!svg) return;
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    defs.innerHTML = `
      <linearGradient id="progressGradient" x1="0%" y1="0%" x2="100%" y2="0%">
        <stop offset="0%"   stop-color="#7c3aed"/>
        <stop offset="50%"  stop-color="#a78bfa"/>
        <stop offset="100%" stop-color="#67e8f9"/>
      </linearGradient>
    `;
    svg.prepend(defs);
    if (progressRing) progressRing.setAttribute('stroke', 'url(#progressGradient)');
    if (progressRing) {
      progressRing.style.strokeDasharray  = RING_CIRC;
      progressRing.style.strokeDashoffset = RING_CIRC;
    }
  }

  function setRingProgress(pct) {
    if (!progressRing) return;
    const offset = RING_CIRC - (pct / 100) * RING_CIRC;
    progressRing.style.strokeDashoffset = offset;
    const pctEl = $('progressPct');
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
  }

  // ════════════════════════════════════════════════════════
  //  URL Validation
  // ════════════════════════════════════════════════════════
  const YT_PATTERNS = [
    /^https?:\/\/(www\.)?youtube\.com\/watch\?v=[\w-]{11}/,
    /^https?:\/\/youtu\.be\/[\w-]{11}/,
    /^https?:\/\/(www\.)?youtube\.com\/shorts\/[\w-]{11}/,
    /^https?:\/\/(www\.)?youtube\.com\/playlist\?list=[\w-]+/,
    /^https?:\/\/(www\.)?youtube\.com\/@[\w.-]+/,
    /^https?:\/\/(www\.)?youtube\.com\/channel\/[\w-]+/,
    /^https?:\/\/(www\.)?youtube\.com\/c\/[\w-]+/,
    /^https?:\/\/(www\.)?youtube\.com\/embed\/[\w-]{11}/,
  ];

  function isValidYouTubeUrl(url) {
    return YT_PATTERNS.some(p => p.test(url.trim()));
  }

  function validateUrl(url) {
    if (!url) {
      setValidation('', 'neutral');
      fetchBtn.disabled = true;
      clearBtn.classList.add('hidden');
      return;
    }
    clearBtn.classList.remove('hidden');

    if (isValidYouTubeUrl(url)) {
      setValidation('✓ Valid YouTube URL', 'success');
      urlInputWrapper.className = 'url-input-wrapper valid';
      fetchBtn.disabled = false;
    } else if (url.includes('youtube') || url.includes('youtu.be')) {
      setValidation('⚠ URL format not recognized. Is this a valid YouTube link?', 'error');
      urlInputWrapper.className = 'url-input-wrapper invalid';
      fetchBtn.disabled = true;
    } else {
      setValidation('✗ Only YouTube URLs are supported on this page', 'error');
      urlInputWrapper.className = 'url-input-wrapper invalid';
      fetchBtn.disabled = true;
    }
  }

  function setValidation(msg, type) {
    urlValidation.textContent = msg;
    urlValidation.className   = `url-validation ${type !== 'neutral' ? type : ''}`;
  }

  // ════════════════════════════════════════════════════════
  //  URL Events
  // ════════════════════════════════════════════════════════
  urlInput.addEventListener('input', () => validateUrl(urlInput.value));
  urlInput.addEventListener('paste', () => setTimeout(() => validateUrl(urlInput.value), 0));
  urlInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !fetchBtn.disabled) fetchBtn.click(); });

  pasteBtn.addEventListener('click', async () => {
    const text = await UI.pasteFromClipboard();
    if (text) {
      urlInput.value = text;
      validateUrl(text);
      urlInput.focus();
    } else {
      UI.toast('Could not access clipboard. Paste manually.', 'warning');
    }
  });

  clearBtn.addEventListener('click', () => {
    urlInput.value = '';
    validateUrl('');
    urlInputWrapper.className = 'url-input-wrapper';
    urlInput.focus();
  });

  // ════════════════════════════════════════════════════════
  //  Fetch Info
  // ════════════════════════════════════════════════════════
  fetchBtn.addEventListener('click', async () => {
    const url     = urlInput.value.trim();
    const playlist = playlistToggle?.checked || false;
    if (!isValidYouTubeUrl(url)) return;

    fetchBtn.disabled = true;
    fetchBtn.setAttribute('aria-busy', 'true');
    fetchBtn.classList.add('loading');

    try {
      const meta = await Downloader.fetchInfo(url, playlist);

      // Populate video card
      videoTitle.textContent   = meta.title    || 'Unknown';
      videoChannel.textContent = meta.uploader || 'Unknown';
      videoDesc.textContent    = meta.description || '';

      if (meta.thumbnail) {
        videoThumb.src = meta.thumbnail;
        videoThumb.style.display = '';
      }
      if (meta.duration_string) videoDuration.textContent = meta.duration_string;

      // Views & date
      if (meta.view_count) {
        videoViews.textContent = `${(meta.view_count / 1000).toFixed(0)}K views`;
      }
      if (meta.upload_date && meta.upload_date.length === 8) {
        const y = meta.upload_date.slice(0,4);
        const m = meta.upload_date.slice(4,6);
        const d = meta.upload_date.slice(6,8);
        videoDate.textContent = new Date(`${y}-${m}-${d}`).toLocaleDateString();
      }

      // Badge
      if (meta.is_playlist) {
        videoTypeBadge.textContent = `Playlist · ${meta.playlist_count || '?'} items`;
      } else {
        videoTypeBadge.textContent = 'Video';
      }

      // Populate format list (metube-style)
      populateFormats(meta.formats || []);

      // Populate quality select
      populateQualitySelect(meta.formats || []);

      UI.showStep('stepInfo');
    } catch (err) {
      UI.toast(err.message || 'Failed to fetch media info.', 'error');
    } finally {
      fetchBtn.disabled = false;
      fetchBtn.setAttribute('aria-busy', 'false');
      fetchBtn.classList.remove('loading');
    }
  });

  // ── Format List ────────────────────────────────────────────────────────────
  function populateFormats(formats) {
    formatList.innerHTML = '';
    let selectedItem = null;

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
          selectedItem = f;
          Downloader.setSelectedFormat(f);
        });

        formatList.appendChild(item);
      });
    };

    addGroup('Video Formats', videoFmts);
    addGroup('Audio Formats', audioFmts);

    // Auto-select best video
    const best = formatList.querySelector('.format-item');
    if (best) {
      best.click();
    }
  }

  function populateQualitySelect(formats) {
    qualityGroupUHD.innerHTML = '';
    qualityGroupHD.innerHTML  = '';
    qualityGroupSD.innerHTML  = '';

    const seen = new Set();
    formats
      .filter(f => f.category === 'video' && f.height)
      .sort((a,b) => (b.height||0) - (a.height||0))
      .forEach(f => {
        const h = f.height;
        if (seen.has(h)) return;
        seen.add(h);

        const opt = document.createElement('option');
        opt.value = String(h);
        opt.textContent = `${h}p${f.fps > 30 ? ` ${Math.round(f.fps)}fps` : ''}`;

        if (h >= 2160)     qualityGroupUHD.appendChild(opt);
        else if (h >= 720) qualityGroupHD.appendChild(opt);
        else               qualityGroupSD.appendChild(opt);
      });
  }

  // ════════════════════════════════════════════════════════
  //  Download
  // ════════════════════════════════════════════════════════
  downloadBtn.addEventListener('click', async () => {
    const isAudio = activeTab === 'audio';
    const url     = urlInput.value.trim();
    const playlist = playlistToggle?.checked || false;

    let opts = { url, playlist };

    if (isAudio) {
      opts.audio_only    = true;
      opts.audio_format  = $('audioFormatSelect')?.value  || 'mp3';
      opts.audio_quality = $('audioQualitySelect')?.value || '0';
      opts.embed_thumbnail = $('embedThumbAudio')?.checked ?? true;
      opts.embed_metadata  = $('embedMetaAudio')?.checked  ?? true;
    } else {
      opts.audio_only = false;
      const selFmt = Downloader.getSelectedFormat();
      opts.format_id    = selFmt?.format_id || '';
      opts.quality      = qualitySelect?.value || '';
      opts.container    = $('containerSelect')?.value || 'mp4';
      opts.embed_thumbnail = $('embedThumbVideo')?.checked ?? false;
      opts.embed_metadata  = $('embedMetaVideo')?.checked  ?? true;
    }

    // Advanced options
    opts.write_subtitles  = $('writeSubtitles')?.checked  || false;
    opts.auto_subtitles   = $('writeAutoSubs')?.checked   || false;
    opts.embed_subs       = $('embedSubs')?.checked       || false;
    opts.subtitle_lang    = $('subLangInput')?.value.trim()  || 'en';
    opts.subtitle_format  = $('subFormatSelect')?.value      || 'srt';
    opts.rate_limit       = $('rateLimitInput')?.value.trim() || '';

    UI.showStep('stepProgress');
    setRingProgress(0);
    progressTitle.textContent = 'Initializing…';
    progressFilename.textContent = '';
    if (cancelBtn) cancelBtn.classList.remove('hidden');
    if (downloadFileBtn) downloadFileBtn.classList.add('hidden');

    try {
      const jobId = await Downloader.startDownload(opts);

      cancelBtn && cancelBtn.addEventListener('click', async () => {
        await Downloader.cancelJob(jobId);
        progressTitle.textContent = 'Cancelled.';
        cancelBtn.classList.add('hidden');
        newDownloadBtn.classList.remove('hidden');
      }, { once: true });

      Downloader.pollStatus(jobId, onProgress, onComplete, onError);
    } catch (err) {
      onError(err);
    }
  });

  // ── Progress Callbacks ─────────────────────────────────────────────────────
  function onProgress(status) {
    const pct = status.progress || 0;
    setRingProgress(pct);
    progressTitle.textContent   = `Downloading ${pct.toFixed(1)}%`;
    if (progressBarFill) progressBarFill.style.width = `${pct}%`;
    if (progressPctText) progressPctText.textContent  = `${Math.round(pct)}%`;
    if (progressSpeed)   progressSpeed.textContent    = status.speed   || '—';
    if (progressEta)     progressEta.textContent      = status.eta     || '—';
    if (progressSize)    progressSize.textContent     = status.filesize || '—';
  }

  function onComplete(status) {
    setRingProgress(100);
    progressTitle.textContent = '✓ Download Complete!';
    if (progressBarFill) progressBarFill.style.width = '100%';
    if (progressPctText) progressPctText.textContent = '100%';
    if (cancelBtn)       cancelBtn.classList.add('hidden');
    if (progressFilename) progressFilename.textContent = status.filename || '';

    const fileUrl = Downloader.getFileUrl(status.job_id || Downloader.getCurrentJobId());
    if (downloadFileBtn) {
      downloadFileBtn.href     = fileUrl;
      downloadFileBtn.download = status.filename || 'download';
      downloadFileBtn.classList.remove('hidden');
    }

    UI.toast('File ready! Click "Save File" to download.', 'success');
    newDownloadBtn && newDownloadBtn.classList.remove('hidden');

    // History
    const meta = Downloader.getCurrentMeta();
    History.add({
      jobId:     status.job_id || Downloader.getCurrentJobId(),
      title:     meta?.title    || 'YouTube Video',
      uploader:  meta?.uploader || 'Unknown',
      thumbnail: meta?.thumbnail || '',
      filename:  status.filename || 'download',
      format:    activeTab === 'audio' ? 'Audio' : 'Video',
      fileUrl:   fileUrl,
      date:      Date.now(),
    });
  }

  function onError(err) {
    progressTitle.textContent = '✗ Error';
    UI.toast(err.message || 'Download failed.', 'error');
    if (cancelBtn)     cancelBtn.classList.add('hidden');
    if (newDownloadBtn) newDownloadBtn.classList.remove('hidden');
  }

  // ── Navigation ─────────────────────────────────────────────────────────────
  backBtn && backBtn.addEventListener('click', () => UI.showStep('stepUrl'));

  newDownloadBtn && newDownloadBtn.addEventListener('click', () => {
    urlInput.value = '';
    validateUrl('');
    UI.showStep('stepUrl');
    if (downloadFileBtn) downloadFileBtn.classList.add('hidden');
    newDownloadBtn.classList.add('hidden');
  });

  // ════════════════════════════════════════════════════════
  //  History System
  // ════════════════════════════════════════════════════════
  const History = (function () {
    const HIST_KEY = 'lunarmediadl_yt_history';

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

  // ════════════════════════════════════════════════════════
  //  Init
  // ════════════════════════════════════════════════════════
  function init() {
    UI.init();
    injectProgressGradient();
    updateDownloadButton();
    renderHistory();

    // Auto-focus URL input
    if (urlInput) urlInput.focus();

    console.info('%cLunarMediaDL YouTube Module', 'color:#7c3aed;font-size:14px;font-weight:bold');
    console.info('Engine: metube yt-dlp Python API | Proxy: removed | Cookies: YOUTUBE_COOKIES env var');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
