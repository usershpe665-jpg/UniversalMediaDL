/**
 * LunarMediaDL — Downloader Core (MeTube Engine)
 * Real-time Socket.IO progress + simple REST endpoints
 * Author: Syawaliuz Octavian
 */

const Downloader = (function () {
  'use strict';

  // ── Socket.IO connection ─────────────────────────────────────────────────────
  let _socket = null;
  let _socketReady = false;
  const _socketListeners = {};

  function _getSocket() {
    if (_socket) return _socket;
    // Use socket.io-client loaded from CDN (in HTML)
    _socket = typeof io !== 'undefined'
      ? io(window.location.origin, {
          transports: ['websocket', 'polling'],
          reconnectionAttempts: 10,
          reconnectionDelay: 1500,
        })
      : null;

    if (!_socket) {
      console.error('[Downloader] socket.io not loaded');
      return null;
    }

    _socket.on('connect', () => {
      _socketReady = true;
      console.info('%c[Downloader] Socket.IO connected', 'color:#22d3ee');
    });

    _socket.on('disconnect', () => {
      _socketReady = false;
      console.warn('[Downloader] Socket.IO disconnected');
    });

    // Forward lunar events to registered listeners
    ['lunar_progress', 'lunar_completed', 'lunar_canceled', 'lunar_added', 'lunar_state'].forEach(evt => {
      _socket.on(evt, (raw) => {
        let data;
        try { data = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { data = raw; }
        (_socketListeners[evt] || []).forEach(fn => fn(data));
      });
    });

    return _socket;
  }

  function onSocketEvent(event, callback) {
    if (!_socketListeners[event]) _socketListeners[event] = [];
    _socketListeners[event].push(callback);
  }

  function offSocketEvent(event, callback) {
    if (!_socketListeners[event]) return;
    _socketListeners[event] = _socketListeners[event].filter(fn => fn !== callback);
  }

  // ── State ────────────────────────────────────────────────────────────────────
  let _currentJobId  = null;
  let _currentJobUrl = null;
  let _currentMeta   = null;

  // ── Helpers ──────────────────────────────────────────────────────────────────
  async function _apiCall(endpoint, options = {}) {
    const controller = new AbortController();
    const timeoutId  = setTimeout(() => controller.abort(), 45000);

    try {
      const res = await fetch(endpoint, {
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        ...options,
      });
      clearTimeout(timeoutId);

      // 202 = file not ready yet, not a real error
      if (res.status === 202) {
        throw new DownloaderError('file_not_ready', 202);
      }
      if (!res.ok) {
        let errMsg;
        try {
          const errData = await res.json();
          errMsg = errData.error || errData.reason || errData.message || res.statusText;
        } catch {
          errMsg = res.statusText;
        }
        throw new DownloaderError(errMsg, res.status);
      }
      return await res.json();
    } catch (err) {
      clearTimeout(timeoutId);
      if (err.name === 'AbortError') throw new DownloaderError('Request timed out. Check your connection.', 408);
      throw err;
    }
  }

  class DownloaderError extends Error {
    constructor(message, status) {
      super(message);
      this.name   = 'DownloaderError';
      this.status = status;
    }
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  /** Extract metadata for a URL without starting download */
  async function fetchInfo(url) {
    const data = await _apiCall('/metadata', {
      method: 'POST',
      body: JSON.stringify({ url }),
    });
    _currentMeta = data;
    return data;
  }

  /** Start a download — returns job_id */
  async function startDownload(options) {
    const data = await _apiCall('/download', {
      method: 'POST',
      body: JSON.stringify(options),
    });
    _currentJobId  = data.job_id;
    _currentJobUrl = options.url;
    return data.job_id;
  }

  /**
   * Listen for real-time progress via Socket.IO.
   * onProgress(status), onComplete(status), onError(err)
   */
  function pollStatus(jobId, onProgress, onComplete, onError) {
    const sock = _getSocket();
    if (!sock) {
      onError(new DownloaderError('Socket.IO not available — cannot track progress', 503));
      return;
    }

    // Store handlers so we can remove them later
    const progressHandler = (data) => {
      if (!_matchesJob(data, jobId)) return;
      const status = data.status || '';

      if (status === 'finished' || status === 'completed') {
        _cleanup(progressHandler, completedHandler, canceledHandler);
        onComplete({
          job_id:   data.job_id || jobId,
          filename: data.filename || 'download',
          progress: 100,
          file_url: data.file_url || `/download/file/${jobId}`,
        });
      } else if (status === 'error') {
        _cleanup(progressHandler, completedHandler, canceledHandler);
        onError(new DownloaderError(data.error || 'Download failed', 500));
      } else {
        onProgress({
          progress: typeof data.progress === 'number' ? data.progress : 0,
          speed:    data.speed  || '',
          eta:      data.eta    || '',
          status:   status,
          filename: data.filename || '',
        });
      }
    };

    const completedHandler = (data) => {
      if (!_matchesJob(data, jobId)) return;
      _cleanup(progressHandler, completedHandler, canceledHandler);
      onComplete({
        job_id:   data.job_id || jobId,
        filename: data.filename || 'download',
        progress: 100,
        file_url: data.file_url || `/download/file/${jobId}`,
      });
    };

    const canceledHandler = (data) => {
      if (!_matchesJob(data, jobId)) return;
      _cleanup(progressHandler, completedHandler, canceledHandler);
      onError(new DownloaderError('Download was canceled', 0));
    };

    onSocketEvent('lunar_progress',  progressHandler);
    onSocketEvent('lunar_completed', completedHandler);
    onSocketEvent('lunar_canceled',  canceledHandler);

    // Also check current queue state immediately (in case already done)
    _checkCurrentStatus(jobId, onProgress, onComplete, onError,
      progressHandler, completedHandler, canceledHandler);
  }

  function _matchesJob(data, jobId) {
    return data && (data.job_id === jobId);
  }

  function _cleanup(ph, ch, cah) {
    offSocketEvent('lunar_progress',  ph);
    offSocketEvent('lunar_completed', ch);
    offSocketEvent('lunar_canceled',  cah);
  }

  async function _checkCurrentStatus(jobId, onProgress, onComplete, onError, ph, ch, cah) {
    try {
      const queue = await _apiCall('/queue');
      const allItems = [...(queue.queue || []), ...(queue.done || [])];
      const item = allItems.find(i => i.job_id === jobId);
      if (!item) return;

      if (item.status === 'finished' || item.status === 'completed') {
        _cleanup(ph, ch, cah);
        onComplete({
          job_id:   item.job_id,
          filename: item.filename || 'download',
          progress: 100,
          file_url: item.file_url || `/download/file/${jobId}`,
        });
      } else if (item.status === 'error') {
        _cleanup(ph, ch, cah);
        onError(new DownloaderError(item.error || 'Download failed', 500));
      } else if (item.progress > 0) {
        onProgress({
          progress: item.progress,
          speed:    item.speed || '',
          eta:      item.eta   || '',
          status:   item.status,
        });
      }
    } catch { /* ignore — will catch via socket */ }
  }

  /** Cancel a running download */
  async function cancelJob(jobId) {
    if (!jobId) return;
    try {
      await _apiCall('/cancel', {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId }),
      });
    } catch { /* already done */ }
    _currentJobId  = null;
    _currentJobUrl = null;
  }

  /** URL for downloading the completed file */
  function getFileUrl(jobId) {
    return `/download/file/${jobId}`;
  }

  /** Getters */
  function getCurrentMeta()  { return _currentMeta; }
  function getCurrentJobId() { return _currentJobId; }

  // Init socket eagerly on load
  document.addEventListener('DOMContentLoaded', () => {
    _getSocket();
  });

  // Public
  return {
    fetchInfo,
    startDownload,
    pollStatus,
    cancelJob,
    getFileUrl,
    getCurrentMeta,
    getCurrentJobId,
    onSocketEvent,
    offSocketEvent,
    DownloaderError,
    // Aliases for backward compat with existing page scripts
    checkHealth:       () => fetch('/health').then(r => r.json()).catch(() => null),
    clearPolling:      () => {},
    APIError:          DownloaderError,
    setSelectedFormat: () => {},
    getSelectedFormat: () => null,
  };
})();
