/**
 * LunarMediaDL — YouTube Downloader Logic
 * Socket.IO realtime progress + MeTube engine
 * Author: Syawaliuz Octavian
 */

(function App() {
  'use strict';
  const $ = id => document.getElementById(id);

  const urlInput        = $('urlInput');
  const urlInputWrapper = $('urlInputWrapper');
  const fetchBtn        = $('fetchBtn');
  const pasteBtn        = $('pasteBtn');
  const clearBtn        = $('clearBtn');
  const playlistToggle  = $('playlistToggle');
  const urlValidation   = $('urlValidation');

  const videoThumb      = $('videoThumb');
  const videoDuration   = $('videoDuration');
  const videoTypeBadge  = $('videoTypeBadge');
  const videoTitle      = $('videoTitle');
  const videoChannel    = $('videoChannel');
  const videoViews      = $('videoViews');
  const videoDate       = $('videoDate');
  const videoDesc       = $('videoDesc');

  const qualityGroupUHD = $('qualityGroupUHD');
  const qualityGroupHD  = $('qualityGroupHD');
  const qualityGroupSD  = $('qualityGroupSD');
  const qualitySelect   = $('qualitySelect');
  const formatList      = $('formatList');

  const backBtn         = $('backBtn');
  const downloadBtn     = $('downloadBtn');
  const downloadBtnLabel = $('downloadBtnLabel');
  const newDownloadBtn  = $('newDownloadBtn');
  const cancelBtn       = $('cancelBtn');
  const downloadFileBtn = $('downloadFileBtn');

  const progressTitle   = $('progressTitle');
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

  // ── Progress ring ────────────────────────────────────────────────────────
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
      </linearGradient>`;
    svg.prepend(defs);
    if (progressRing) progressRing.setAttribute('stroke', 'url(#progressGradient)');
  }

  function setRingProgress(pct) {
    if (!progressRing) return;
    progressRing.style.strokeDashoffset = RING_CIRC - (pct / 100) * RING_CIRC;
    const pctEl = $('progressPct');
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
  }

  // ── Tab state ────────────────────────────────────────────────────────────
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
    tab.addEventListener('click', () => { activeTab = tab.dataset.tab || 'video'; updateDownloadButton(); });
  });

  // ── URL Validation ───────────────────────────────────────────────────────
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

  const isValidYT = url => YT_PATTERNS.some(p => p.test(url.trim()));

  function validateUrl(url) {
    if (!url) {
      setValidation('', 'neutral');
      fetchBtn.disabled = true;
      clearBtn.classList.add('hidden');
      return;
    }
    clearBtn.classList.remove('hidden');
    if (isValidYT(url)) {
      setValidation('✓ Valid YouTube URL', 'success');
      urlInputWrapper.className = 'url-input-wrapper valid';
      fetchBtn.disabled = false;
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

  urlInput.addEventListener('input',   () => validateUrl(urlInput.value));
  urlInput.addEventListener('paste',   () => setTimeout(() => validateUrl(urlInput.value), 0));
  urlInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !fetchBtn.disabled) fetchBtn.click(); });
  clearBtn.addEventListener('click', () => {
    urlInput.value = ''; validateUrl(''); urlInputWrapper.className = 'url-input-wrapper'; urlInput.focus();
  });
  pasteBtn.addEventListener('click', async () => {
    const text = await UI.pasteFromClipboard();
    if (text) { urlInput.value = text; validateUrl(text); urlInput.focus(); }
    else UI.toast('Could not access clipboard. Paste manually.', 'warning');
  });

  // ── Fetch Info ───────────────────────────────────────────────────────────
  fetchBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!url || !isValidYT(url)) return;
    fetchBtn.classList.add('loading');
    fetchBtn.setAttribute('aria-busy', 'true');
    fetchBtn.disabled = true;
    setValidation('Fetching video information…', 'neutral');
    try {
      const meta = await Downloader.fetchInfo(url);
      renderVideoMeta(meta);
      activeTab = 'video';
      updateDownloadButton();
      UI.showStep('stepInfo');
    } catch (err) {
      const msg = err.message || 'Could not connect to server.';
      UI.toast(msg, 'error', 5000);
      setValidation(msg, 'error');
    } finally {
      fetchBtn.classList.remove('loading');
      fetchBtn.setAttribute('aria-busy', 'false');
      fetchBtn.disabled = false;
    }
  });

  // ── Render Meta ──────────────────────────────────────────────────────────
  function renderVideoMeta(meta) {
    videoTitle.textContent   = meta.title    || 'Unknown Title';
    videoChannel.textContent = meta.uploader || 'Unknown Channel';
    if (videoViews) videoViews.textContent = meta.view_count ? UI.formatNumber(meta.view_count) + ' views' : '—';
    if (videoDate)  videoDate.textContent  = UI.formatDate(meta.upload_date);
    if (videoDesc)  videoDesc.textContent  = meta.description || '';
    if (meta.thumbnail) { videoThumb.src = meta.thumbnail; videoThumb.alt = meta.title || 'Thumbnail'; }
    if (videoDuration)  videoDuration.textContent  = UI.formatDuration(meta.duration);
    if (videoTypeBadge) videoTypeBadge.textContent = 'Video';

    // Populate quality select
    if (qualitySelect) {
      while (qualitySelect.options.length > 1) qualitySelect.remove(1);
      if (qualityGroupUHD) qualityGroupUHD.innerHTML = '';
      if (qualityGroupHD)  qualityGroupHD.innerHTML  = '';
      if (qualityGroupSD)  qualityGroupSD.innerHTML  = '';
    }
    if (formatList) {
      formatList.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--clr-text-faint);font-size:0.82rem;">Formats loaded via yt-dlp engine</div>';
    }
  }

  // ── Build Download Options ───────────────────────────────────────────────
  function buildDownloadOptions() {
    const meta    = Downloader.getCurrentMeta();
    const url     = meta?.webpage_url || urlInput.value.trim();
    const isAudio = activeTab === 'audio';

    // Map quality dropdown to numeric value
    const qVal = qualitySelect ? qualitySelect.value : '';
    let quality = 'best';
    if (['2160','1440','1080','720','480','360','240'].includes(qVal)) quality = qVal;

    return {
      url,
      audio_only:   isAudio,
      audio_format: $('audioFormatSelect')?.value || 'mp3',
      quality:      isAudio ? 'best' : quality,
    };
  }

  // ── Download Flow ────────────────────────────────────────────────────────
  async function initiateDownload() {
    const opts    = buildDownloadOptions();
    const isAudio = opts.audio_only;

    UI.showStep('stepProgress');
    setProgressState(0, isAudio ? 'Extracting audio…' : 'Preparing download…', '');
    if (cancelBtn)       cancelBtn.classList.remove('hidden');
    if (downloadFileBtn) downloadFileBtn.classList.add('hidden');
    if (newDownloadBtn)  newDownloadBtn.classList.add('hidden');
    if (progressBarFill) progressBarFill.style.background = '';

    try {
      const jobId = await Downloader.startDownload(opts);
      UI.toast(isAudio ? '🎵 Audio download started!' : '🎬 Video download started!', 'info');
      Downloader.pollStatus(jobId, onProgress, (s) => onComplete(s, opts), onError);
    } catch (err) {
      onError(err);
    }
  }

  function onProgress(status) {
    const pct = status.progress || 0;
    setProgressState(
      pct,
      pct < 5  ? 'Connecting to servers…'  :
      pct < 95 ? `Downloading… ${pct.toFixed(1)}%` :
                 'Merging & processing…',
      status.filename || '',
    );
    if (progressSpeed) progressSpeed.textContent = status.speed ? `⚡ ${status.speed}` : '—';
    if (progressEta)   progressEta.textContent   = status.eta   ? `⏱ ${status.eta}`   : '—';
  }

  function onComplete(status, opts) {
    setProgressState(100, '✓ Download Complete!', status.filename || '');
    if (progressBarFill) progressBarFill.style.background = 'linear-gradient(90deg,#22d3ee,#67e8f9)';
    const jobId = status.job_id || Downloader.getCurrentJobId();
    if (jobId && downloadFileBtn) {
      downloadFileBtn.href     = status.file_url || Downloader.getFileUrl(jobId);
      downloadFileBtn.download = status.filename || 'download';
      downloadFileBtn.classList.remove('hidden');
    }
    if (cancelBtn)      cancelBtn.classList.add('hidden');
    if (newDownloadBtn) newDownloadBtn.classList.remove('hidden');
    if (progressSpeed)  progressSpeed.textContent = '✓ Done';
    if (progressEta)    progressEta.textContent   = '';
    UI.toast('🌟 File ready! Click "Save File" to download.', 'success', 6000);
    if (jobId) {
      const meta = Downloader.getCurrentMeta();
      History.add({
        jobId,
        title:     meta?.title     || 'Unknown',
        thumbnail: meta?.thumbnail || '',
        filename:  status.filename || 'download',
        format:    opts?.audio_only ? (opts?.audio_format || 'mp3').toUpperCase() : 'Video',
        date:      Date.now(),
        fileUrl:   status.file_url || Downloader.getFileUrl(jobId),
      });
      renderHistory();
    }
  }

  function onError(err) {
    const msg = err?.message || 'Download failed. Please try again.';
    setProgressState(0, '✗ Error', msg);
    if (progressBarFill) progressBarFill.style.background = 'linear-gradient(90deg,var(--clr-error),#f87171)';
    if (cancelBtn)      cancelBtn.classList.add('hidden');
    if (newDownloadBtn) newDownloadBtn.classList.remove('hidden');
    UI.toast(msg, 'error', 6000);
  }

  function setProgressState(pct, title, subtitle) {
    const p = Math.min(Math.max(pct, 0), 100);
    if (progressTitle)    progressTitle.textContent    = title;
    if (progressFilename) progressFilename.textContent = subtitle;
    if (progressBarFill)  progressBarFill.style.width  = `${p}%`;
    if (progressPctText)  progressPctText.textContent  = `${Math.round(p)}%`;
    setRingProgress(p);
  }

  // ── Button events ────────────────────────────────────────────────────────
  if (downloadBtn)    downloadBtn.addEventListener('click', () => initiateDownload());
  if (backBtn)        backBtn.addEventListener('click', () => UI.showStep('stepUrl'));
  if (cancelBtn)      cancelBtn.addEventListener('click', async () => {
    const jobId = Downloader.getCurrentJobId();
    if (jobId) { await Downloader.cancelJob(jobId); UI.toast('Download cancelled.', 'warning'); }
    UI.showStep('stepInfo');
    if (cancelBtn) cancelBtn.classList.add('hidden');
  });
  if (newDownloadBtn) newDownloadBtn.addEventListener('click', () => {
    urlInput.value = ''; validateUrl(''); urlInputWrapper.className = 'url-input-wrapper';
    activeTab = 'video'; updateDownloadButton(); UI.showStep('stepUrl');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // ── History ──────────────────────────────────────────────────────────────
  const HIST_KEY = 'lunarytdl_youtube_history';
  const History = {
    load:  () => { try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch { return []; } },
    save:  (i) => { try { localStorage.setItem(HIST_KEY, JSON.stringify(i.slice(0, 50))); } catch {} },
    add:   (e) => { const i = History.load().filter(x => x.jobId !== e.jobId); i.unshift(e); History.save(i); },
    remove: (id) => { History.save(History.load().filter(i => i.jobId !== id)); },
    clear:  () => { try { localStorage.removeItem(HIST_KEY); } catch {} },
  };

  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function relTime(ts) {
    const d = Date.now() - ts;
    return d < 60e3 ? 'Just now' : d < 3600e3 ? `${Math.floor(d/60e3)}m ago`
         : d < 86400e3 ? `${Math.floor(d/3600e3)}h ago` : `${Math.floor(d/86400e3)}d ago`;
  }

  function renderHistory() {
    if (!historyList) return;
    historyList.querySelectorAll('.history-item').forEach(el => el.remove());
    const items = History.load();
    if (!items.length) { if (historyEmpty) historyEmpty.style.display = ''; return; }
    if (historyEmpty) historyEmpty.style.display = 'none';
    items.forEach(item => {
      const el = document.createElement('div');
      el.className = 'history-item';
      el.innerHTML = `
        <div class="history-item__thumb">
          ${item.thumbnail ? `<img src="${escHtml(item.thumbnail)}" alt="" loading="lazy"/>` : '<div class="history-item__thumb-placeholder"></div>'}
          <span class="history-item__format-badge">${escHtml(item.format || 'Video')}</span>
        </div>
        <div class="history-item__info">
          <div class="history-item__title">${escHtml(item.title)}</div>
          <div class="history-item__meta">
            <span>${escHtml(item.filename || '')}</span>
            <span class="history-item__date">${relTime(item.date)}</span>
          </div>
        </div>
        <div class="history-item__actions">
          <a href="${escHtml(item.fileUrl)}" class="btn btn--ghost btn--xs" download>
            <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M12 5v14M5 12l7 7 7-7" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
          </a>
          <button class="btn btn--ghost btn--xs history-item__del" data-job="${escHtml(item.jobId)}">
            <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          </button>
        </div>`;
      el.querySelector('.history-item__del').addEventListener('click', async e => {
        const jid = e.currentTarget.dataset.job;
        History.remove(jid);
        try { await Downloader.cancelJob(jid); } catch {}
        renderHistory();
      });
      historyList.appendChild(el);
    });
  }

  const openHistory  = () => { renderHistory(); historyPanel.classList.add('open'); historyPanel.setAttribute('aria-hidden','false'); document.body.style.overflow='hidden'; };
  const closeHistory = () => { historyPanel.classList.remove('open'); historyPanel.setAttribute('aria-hidden','true'); document.body.style.overflow=''; };

  if (historyToggleBtn) historyToggleBtn.addEventListener('click', openHistory);
  if (historyCloseBtn)  historyCloseBtn.addEventListener('click',  closeHistory);
  if (historyBackdrop)  historyBackdrop.addEventListener('click',  closeHistory);
  if (clearHistoryBtn)  clearHistoryBtn.addEventListener('click', () => {
    if (confirm('Clear all download history?')) { History.clear(); renderHistory(); UI.toast('History cleared.', 'info'); }
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && historyPanel?.classList.contains('open')) closeHistory(); });

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    UI.init();
    injectProgressGradient();
    updateDownloadButton();
    renderHistory();
    console.info('%cLunarMediaDL YouTube Module (MeTube engine)', 'color:#a78bfa;font-size:14px;font-weight:bold');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
