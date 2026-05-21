/**
 * LunarMediaDL — App Core
 * Connects the Universal Downloader UI to the MeTube backend via Socket.IO.
 * UI design: LunarMediaDL (Syawaliuz Octavian)
 * Backend: MeTube / yt-dlp engine
 */

(function LunarApp() {
  'use strict';

  // ─── State ────────────────────────────────────────────────
  let socket = null;
  let publicHostUrl      = 'download/';
  let publicHostAudioUrl = 'audio_download/';
  let currentDownloadId  = null;   // DownloadInfo.id being tracked
  let pendingAddUrl      = null;   // URL we just submitted, waiting for 'added' event
  let currentMode        = 'video'; // 'video' | 'audio'
  let allDownloads       = {};      // id -> DownloadInfo
  let localHistory       = [];      // persisted to localStorage

  const HISTORY_KEY = 'lunarmediadl_history';
  const MAX_HISTORY  = 50;

  // ─── DOM refs ─────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  // Steps
  const stepUrl      = $('stepUrl');
  const stepInfo     = $('stepInfo');
  const stepProgress = $('stepProgress');

  // Step 1
  const urlInput        = $('urlInput');
  const urlInputWrapper = $('urlInputWrapper');
  const pasteBtn        = $('pasteBtn');
  const clearBtn        = $('clearBtn');
  const fetchBtn        = $('fetchBtn');
  const urlValidation   = $('urlValidation');

  // Step 2
  const tabVideo          = $('tabVideo');
  const tabAudio          = $('tabAudio');
  const panelVideo        = $('panelVideo');
  const panelAudio        = $('panelAudio');
  const qualitySelect     = $('qualitySelect');
  const videoFormatSelect = $('videoFormatSelect');
  const codecSelect       = $('codecSelect');
  const audioFormatSelect = $('audioFormatSelect');
  const audioQualitySelect= $('audioQualitySelect');
  const audioQualityGroup = $('audioQualityGroup');
  const backBtn           = $('backBtn');
  const downloadBtn       = $('downloadBtn');
  const downloadBtnLabel  = $('downloadBtnLabel');
  const videoTitle        = $('videoTitle');
  const videoChannel      = $('videoChannel');
  const videoTypeBadge    = $('videoTypeBadge');
  const platformEyebrow   = $('platformEyebrow');

  // Step 3
  const progressTitle     = $('progressTitle');
  const progressFilename  = $('progressFilename');
  const progressBarFill   = $('progressBarFill');
  const progressPctText   = $('progressPctText');
  const progressSpeed     = $('progressSpeed');
  const progressEta       = $('progressEta');
  const progressStatusMsg = $('progressStatusMsg');
  const downloadFileBtn   = $('downloadFileBtn');
  const cancelDownloadBtn = $('cancelDownloadBtn');
  const newDownloadBtn    = $('newDownloadBtn');

  // History
  const historyToggleBtn = $('historyToggleBtn');
  const historyPanel     = $('historyPanel');
  const historyBackdrop  = $('historyBackdrop');
  const historyCloseBtn  = $('historyCloseBtn');
  const historyList      = $('historyList');
  const historyEmpty     = $('historyEmpty');
  const clearHistoryBtn  = $('clearHistoryBtn');

  // Misc
  const connStatusNav = $('connStatusNav');

  // ─── Helpers ─────────────────────────────────────────────
  function toast(msg, type = 'info') {
    if (typeof UI !== 'undefined') UI.showToast(msg, type);
  }

  function isValidUrl(s) {
    try {
      const u = new URL(s);
      return u.protocol === 'http:' || u.protocol === 'https:';
    } catch { return false; }
  }

  function detectPlatform(url) {
    const h = (() => { try { return new URL(url).hostname.replace('www.', ''); } catch { return ''; } })();
    if (h.includes('youtube.com') || h === 'youtu.be')  return { name: 'YouTube',   icon: '🔴' };
    if (h.includes('tiktok.com') || h === 'vm.tiktok.com' || h === 'vt.tiktok.com') return { name: 'TikTok', icon: '🎵' };
    if (h.includes('instagram.com'))  return { name: 'Instagram', icon: '📸' };
    if (h.includes('twitter.com') || h.includes('x.com')) return { name: 'Twitter / X', icon: '🐦' };
    if (h.includes('facebook.com') || h.includes('fb.watch')) return { name: 'Facebook', icon: '🔵' };
    if (h.includes('vimeo.com'))      return { name: 'Vimeo',     icon: '🎞️' };
    if (h.includes('reddit.com'))     return { name: 'Reddit',    icon: '🤖' };
    if (h.includes('twitch.tv'))      return { name: 'Twitch',    icon: '💜' };
    if (h.includes('soundcloud.com')) return { name: 'SoundCloud',icon: '🔊' };
    if (h.includes('dailymotion.com'))return { name: 'Dailymotion',icon: '▶️'};
    if (h.includes('bilibili.com'))   return { name: 'Bilibili',  icon: '📺' };
    return { name: h || 'Universal', icon: '🌐' };
  }

  function formatBytes(bytes) {
    if (!bytes) return '';
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
    if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + ' MB';
    if (bytes >= 1e3) return (bytes / 1e3).toFixed(0) + ' KB';
    return bytes + ' B';
  }

  function formatSpeed(bps) {
    if (!bps) return '—';
    return formatBytes(bps) + '/s';
  }

  function formatEta(secs) {
    if (secs == null || secs <= 0) return '—';
    if (secs >= 3600) return Math.floor(secs / 3600) + 'h ' + Math.floor((secs % 3600) / 60) + 'm';
    if (secs >= 60)   return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
    return secs + 's';
  }

  function shortFilename(path) {
    if (!path) return '';
    return path.split('/').pop().split('\\').pop();
  }

  // ─── Step navigation ─────────────────────────────────────
  function showStep(name) {
    [stepUrl, stepInfo, stepProgress].forEach(el => {
      if (el) { el.classList.remove('panel__step--active'); el.classList.add('hidden'); }
    });
    const target = name === 'url' ? stepUrl : name === 'info' ? stepInfo : stepProgress;
    if (target) {
      target.classList.remove('hidden');
      requestAnimationFrame(() => target.classList.add('panel__step--active', 'warp-in'));
    }
  }

  // ─── URL Input ─────────────────────────────────────────────
  function validateUrl(val) {
    if (!val) {
      fetchBtn.disabled = true;
      clearBtn.classList.add('hidden');
      urlValidation.textContent = '';
      urlValidation.className = 'url-validation';
      return;
    }
    clearBtn.classList.remove('hidden');
    if (isValidUrl(val)) {
      fetchBtn.disabled = false;
      urlInputWrapper.classList.add('valid-flash');
      urlValidation.textContent = '✓ Valid URL';
      urlValidation.className = 'url-validation success';
      setTimeout(() => urlInputWrapper.classList.remove('valid-flash'), 700);
    } else {
      fetchBtn.disabled = true;
      urlValidation.textContent = 'Please enter a valid URL (https://…)';
      urlValidation.className = 'url-validation error';
    }
  }

  urlInput.addEventListener('input', () => validateUrl(urlInput.value.trim()));
  urlInput.addEventListener('paste', () => setTimeout(() => validateUrl(urlInput.value.trim()), 50));

  urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !fetchBtn.disabled) fetchBtn.click();
  });

  pasteBtn.addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      urlInput.value = text.trim();
      validateUrl(urlInput.value);
    } catch {
      toast('Clipboard access denied. Paste manually.', 'warning');
    }
  });

  clearBtn.addEventListener('click', () => {
    urlInput.value = '';
    validateUrl('');
    urlInput.focus();
  });

  // Analyse button → go to step 2 with format options
  fetchBtn.addEventListener('click', () => {
    const url = urlInput.value.trim();
    if (!isValidUrl(url)) return;

    const plat = detectPlatform(url);
    platformEyebrow.textContent = `${plat.icon} ${plat.name} Downloader`;
    videoTypeBadge.textContent = plat.name;
    videoTitle.textContent = 'Ready to download';
    videoChannel.textContent = url;

    // Toggle fetch loading state briefly
    fetchBtn.classList.add('loading');
    setTimeout(() => {
      fetchBtn.classList.remove('loading');
      showStep('info');
    }, 400);
  });

  // ─── Tabs ─────────────────────────────────────────────────
  [tabVideo, tabAudio].forEach(tab => {
    tab.addEventListener('click', () => {
      const mode = tab.dataset.tab;
      currentMode = mode;
      tabVideo.classList.toggle('active', mode === 'video');
      tabAudio.classList.toggle('active', mode === 'audio');
      panelVideo.classList.toggle('tab-panel--active', mode === 'video');
      panelAudio.classList.toggle('tab-panel--active', mode === 'audio');
      downloadBtnLabel.textContent = mode === 'video' ? '⬇ Download Video' : '⬇ Download Audio';
    });
  });

  // Audio format change → update quality options
  audioFormatSelect.addEventListener('change', () => {
    const fmt = audioFormatSelect.value;
    // Only MP3 and M4A support bitrate selection
    const hasBitrate = fmt === 'mp3' || fmt === 'm4a';
    audioQualityGroup.style.display = hasBitrate ? '' : 'none';
    if (!hasBitrate) audioQualitySelect.value = 'best';

    // Update options for mp3 vs m4a
    if (fmt === 'mp3') {
      audioQualitySelect.innerHTML = `
        <option value="best">Best Available</option>
        <option value="320">320 kbps</option>
        <option value="192">192 kbps</option>
        <option value="128">128 kbps</option>
      `;
    } else if (fmt === 'm4a') {
      audioQualitySelect.innerHTML = `
        <option value="best">Best Available</option>
        <option value="192">192 kbps</option>
        <option value="128">128 kbps</option>
      `;
    }
  });

  // Back button
  backBtn.addEventListener('click', () => {
    showStep('url');
  });

  // ─── Download ─────────────────────────────────────────────
  downloadBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!isValidUrl(url)) { toast('Invalid URL', 'error'); return; }
    if (!socket || !socket.connected) {
      toast('Not connected to server. Please wait…', 'error');
      return;
    }

    // Build request params
    let body;
    if (currentMode === 'video') {
      body = {
        url,
        download_type: 'video',
        codec: codecSelect.value,
        format: videoFormatSelect.value,
        quality: qualitySelect.value,
        auto_start: true,
      };
    } else {
      body = {
        url,
        download_type: 'audio',
        codec: 'auto',
        format: audioFormatSelect.value,
        quality: audioQualitySelect.value,
        auto_start: true,
      };
    }

    // Show progress step immediately
    showStep('progress');
    progressTitle.textContent = 'Queuing download…';
    progressFilename.textContent = '';
    progressStatusMsg.textContent = 'Connecting to source and fetching metadata…';
    setProgress(0);
    progressSpeed.textContent = '—';
    progressEta.textContent = '—';
    downloadFileBtn.classList.add('hidden');
    cancelDownloadBtn.classList.remove('hidden');
    newDownloadBtn.classList.add('hidden');

    // Mark the pending URL so we can match the 'added' socket event
    pendingAddUrl = url;
    currentDownloadId = null;

    try {
      const resp = await fetch('/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const result = await resp.json();

      if (result.status === 'error') {
        pendingAddUrl = null;
        progressTitle.textContent = '❌ Error';
        progressStatusMsg.textContent = result.msg || 'An error occurred starting the download.';
        toast(result.msg || 'Download error', 'error');
        cancelDownloadBtn.classList.add('hidden');
        newDownloadBtn.classList.remove('hidden');
      }
    } catch (err) {
      pendingAddUrl = null;
      progressTitle.textContent = '❌ Network Error';
      progressStatusMsg.textContent = 'Could not reach the server.';
      toast('Network error: ' + err.message, 'error');
      cancelDownloadBtn.classList.add('hidden');
      newDownloadBtn.classList.remove('hidden');
    }
  });

  // Cancel button
  cancelDownloadBtn.addEventListener('click', async () => {
    if (currentDownloadId) {
      try {
        await fetch('/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids: [currentDownloadId], where: 'queue' }),
        });
      } catch {}
    }
    resetToStep1();
  });

  // New download button
  newDownloadBtn.addEventListener('click', () => resetToStep1());

  function resetToStep1() {
    currentDownloadId = null;
    pendingAddUrl = null;
    showStep('url');
    urlInput.value = '';
    validateUrl('');
  }

  // ─── Progress UI ──────────────────────────────────────────
  function setProgress(pct) {
    const p = Math.min(100, Math.max(0, pct || 0));
    progressBarFill.style.width = p + '%';
    progressPctText.textContent = Math.round(p) + '%';
  }

  function updateProgressUI(info) {
    // Title
    if (info.title && info.title !== 'NA') {
      progressTitle.textContent = info.title;
    }

    // Filename
    if (info.filename) {
      progressFilename.textContent = shortFilename(info.filename);
    }

    // Status
    const statusMap = {
      pending:     '⏳ Queued…',
      preparing:   '🔍 Fetching metadata…',
      downloading: '⬇ Downloading…',
      finished:    '✅ Download complete!',
      error:       '❌ Error',
      cancelled:   '🚫 Cancelled',
    };
    progressStatusMsg.textContent = statusMap[info.status] || info.status || '';

    // Progress
    if (info.percent != null) setProgress(info.percent);

    // Speed & ETA
    progressSpeed.textContent = info.speed ? formatSpeed(info.speed) : '—';
    progressEta.textContent   = info.eta   ? 'ETA: ' + formatEta(info.eta) : '—';

    // Error message
    if (info.error || info.msg) {
      progressStatusMsg.textContent = '❌ ' + (info.error || info.msg);
    }
  }

  function onDownloadCompleted(info) {
    setProgress(100);
    progressTitle.textContent    = info.title || 'Download complete!';
    progressStatusMsg.textContent = '✅ File ready!';
    progressSpeed.textContent    = '—';
    progressEta.textContent      = '—';
    cancelDownloadBtn.classList.add('hidden');
    newDownloadBtn.classList.remove('hidden');

    // Construct file download URL
    if (info.filename) {
      const isAudio = info.download_type === 'audio';
      const base = isAudio ? publicHostAudioUrl : publicHostUrl;
      const fn   = shortFilename(info.filename);
      const href = '/' + base.replace(/^\//, '') + fn;
      downloadFileBtn.href = href;
      downloadFileBtn.setAttribute('download', fn);
      downloadFileBtn.classList.remove('hidden');
    }

    toast('Download complete: ' + (info.title || 'file ready'), 'success');
    addToLocalHistory(info);
    renderHistoryPanel();
  }

  // ─── Socket.IO ────────────────────────────────────────────
  function initSocket() {
    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
      console.log('[LunarMediaDL] Socket connected:', socket.id);
      if (connStatusNav) {
        connStatusNav.textContent = '🟢 Connected';
        connStatusNav.style.opacity = '1';
      }
    });

    socket.on('disconnect', () => {
      console.warn('[LunarMediaDL] Socket disconnected');
      if (connStatusNav) {
        connStatusNav.textContent = '🔴 Disconnected';
        connStatusNav.style.opacity = '0.8';
      }
      toast('Lost connection to server. Reconnecting…', 'warning');
    });

    socket.on('connect_error', (err) => {
      console.error('[LunarMediaDL] Socket connect error:', err.message);
      if (connStatusNav) connStatusNav.textContent = '🔴 Error';
    });

    // Initial state — all current downloads
    socket.on('all', (rawData) => {
      try {
        const [queue, done] = JSON.parse(rawData);
        allDownloads = {};
        // queue = [[key, info], ...]
        if (Array.isArray(queue)) {
          queue.forEach(([, info]) => { if (info && info.id) allDownloads[info.id] = info; });
        }
        if (Array.isArray(done)) {
          done.forEach(([, info]) => { if (info && info.id) allDownloads[info.id] = info; });
        }
        // Sync server history to local history
        if (Array.isArray(done)) {
          done.forEach(([, info]) => {
            if (info && info.status === 'finished') addToLocalHistory(info);
          });
          renderHistoryPanel();
        }
      } catch (e) { console.error('all parse error', e); }
    });

    // New download added
    socket.on('added', (rawData) => {
      try {
        const info = JSON.parse(rawData);
        if (!info || !info.id) return;
        allDownloads[info.id] = info;

        // Match the URL we just submitted
        if (pendingAddUrl && info.url === pendingAddUrl) {
          currentDownloadId = info.id;
          pendingAddUrl = null;
          updateProgressUI(info);
        }
      } catch (e) { console.error('added parse error', e); }
    });

    // Download progress update
    socket.on('updated', (rawData) => {
      try {
        const info = JSON.parse(rawData);
        if (!info || !info.id) return;
        allDownloads[info.id] = info;

        if (info.id === currentDownloadId) {
          updateProgressUI(info);
        }
      } catch (e) { console.error('updated parse error', e); }
    });

    // Download completed
    socket.on('completed', (rawData) => {
      try {
        const info = JSON.parse(rawData);
        if (!info || !info.id) return;
        allDownloads[info.id] = info;

        if (info.id === currentDownloadId) {
          onDownloadCompleted(info);
        }
        // Always add to history
        addToLocalHistory(info);
        renderHistoryPanel();
      } catch (e) { console.error('completed parse error', e); }
    });

    // Download cancelled
    socket.on('canceled', (rawData) => {
      try {
        const id = JSON.parse(rawData);
        delete allDownloads[id];

        if (id === currentDownloadId) {
          currentDownloadId = null;
          progressTitle.textContent = '🚫 Cancelled';
          progressStatusMsg.textContent = 'This download was cancelled.';
          cancelDownloadBtn.classList.add('hidden');
          newDownloadBtn.classList.remove('hidden');
          toast('Download cancelled', 'warning');
        }
      } catch (e) { console.error('canceled parse error', e); }
    });

    // Download cleared from done list
    socket.on('cleared', (rawData) => {
      try {
        const id = JSON.parse(rawData);
        delete allDownloads[id];
      } catch (e) { console.error('cleared parse error', e); }
    });

    // Configuration from server
    socket.on('configuration', (rawData) => {
      try {
        const cfg = JSON.parse(rawData);
        if (cfg.PUBLIC_HOST_URL)       publicHostUrl      = cfg.PUBLIC_HOST_URL;
        if (cfg.PUBLIC_HOST_AUDIO_URL) publicHostAudioUrl = cfg.PUBLIC_HOST_AUDIO_URL;
        console.log('[LunarMediaDL] Config received:', cfg);
      } catch (e) { console.error('configuration parse error', e); }
    });
  }

  // ─── History Panel ────────────────────────────────────────
  function loadLocalHistory() {
    try {
      localHistory = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    } catch { localHistory = []; }
  }

  function saveLocalHistory() {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(localHistory.slice(0, MAX_HISTORY)));
    } catch {}
  }

  function addToLocalHistory(info) {
    if (!info || !info.id) return;
    // Avoid duplicates
    localHistory = localHistory.filter(h => h.id !== info.id);
    localHistory.unshift({
      id:            info.id,
      title:         info.title || 'Unknown title',
      url:           info.url   || '',
      download_type: info.download_type || 'video',
      format:        info.format || '',
      quality:       info.quality || '',
      status:        info.status || 'finished',
      filename:      info.filename || '',
      timestamp:     info.timestamp || Date.now(),
    });
    if (localHistory.length > MAX_HISTORY) localHistory.length = MAX_HISTORY;
    saveLocalHistory();
  }

  function renderHistoryPanel() {
    if (!historyList) return;

    // Remove all existing items (keep the empty state div)
    Array.from(historyList.querySelectorAll('.history-item')).forEach(el => el.remove());

    if (localHistory.length === 0) {
      historyEmpty.classList.remove('hidden');
      return;
    }
    historyEmpty.classList.add('hidden');

    localHistory.forEach(h => {
      const item = document.createElement('div');
      item.className = 'history-item';
      item.dataset.id = h.id;

      const isAudio = h.download_type === 'audio';
      const icon = isAudio ? '🎵' : '🎬';
      const statusIcon = h.status === 'finished' ? '✅' : h.status === 'error' ? '❌' : '⏳';

      const base = isAudio ? publicHostAudioUrl : publicHostUrl;
      const fn   = shortFilename(h.filename);
      const href = fn ? '/' + base.replace(/^\//, '') + fn : h.url;

      item.innerHTML = `
        <div class="history-item__icon">${icon}</div>
        <div class="history-item__info">
          <div class="history-item__title" title="${escapeHtml(h.title)}">${escapeHtml(truncate(h.title, 48))}</div>
          <div class="history-item__meta">${statusIcon} ${h.quality || ''} ${h.format ? '· ' + h.format.toUpperCase() : ''}</div>
        </div>
        <div class="history-item__actions">
          ${fn ? `<a class="btn btn--ghost btn--sm" href="${escapeHtml(href)}" download="${escapeHtml(fn)}" title="Download file">💾</a>` : ''}
          <a class="btn btn--ghost btn--sm" href="${escapeHtml(h.url)}" target="_blank" rel="noopener" title="Open source">🔗</a>
        </div>
      `;
      historyList.appendChild(item);
    });
  }

  function escapeHtml(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function truncate(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  // History panel open/close
  historyToggleBtn && historyToggleBtn.addEventListener('click', () => {
    historyPanel.classList.add('open');
    historyPanel.setAttribute('aria-hidden', 'false');
    renderHistoryPanel();
  });

  function closeHistoryPanel() {
    historyPanel.classList.remove('open');
    historyPanel.setAttribute('aria-hidden', 'true');
  }

  historyCloseBtn  && historyCloseBtn.addEventListener('click',  closeHistoryPanel);
  historyBackdrop  && historyBackdrop.addEventListener('click',   closeHistoryPanel);

  clearHistoryBtn && clearHistoryBtn.addEventListener('click', () => {
    if (confirm('Clear all download history?')) {
      localHistory = [];
      saveLocalHistory();
      renderHistoryPanel();
      toast('History cleared', 'info');
    }
  });

  // Keyboard close
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeHistoryPanel();
  });

  // ─── Init ─────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    loadLocalHistory();
    renderHistoryPanel();
    initSocket();
    // Init audio quality toggle
    audioFormatSelect.dispatchEvent(new Event('change'));
  });

})();
