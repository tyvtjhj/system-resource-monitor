/* /widget 页面控制器：轮询 /api/all、postMessage 握手、主题与折叠。
 *
 * 独立打开：直接读 ?token= / ?theme=，按 /api/all 轮询渲染。
 * iframe 嵌入（?embed=1）：等宿主 monitor:init 下发 baseUrl / token / theme / collapsed，
 * 之后回传 monitor:state；token 不会出现在 iframe URL 里。
 */
(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var embedded = params.get('embed') === '1';
  var baseUrl = location.origin;
  var token = params.get('token') || '';
  var theme = params.get('theme') || 'auto';
  var collapsed = params.get('collapsed') === '1';

  var state = { online: false, collapsed: collapsed, theme: theme };
  var BACKOFF = [2000, 5000, 10000];
  var REFRESH_MS = 2000;
  var failures = 0;
  var timer = null;
  var widget = null;
  var frame = document.getElementById('hmw-frame');
  var dot = document.getElementById('hmw-dot');
  var statusEl = document.getElementById('hmw-status');
  var openLink = document.getElementById('hmw-open');

  function post(type, payload) {
    if (!embedded || window.parent === window) return;
    var message = Object.assign({ type: type }, payload || {});
    window.parent.postMessage(message, '*');
  }

  function setStatus(online) {
    state.online = online;
    if (dot) {
      dot.classList.toggle('online', online);
      dot.classList.toggle('offline', online === false);
    }
    if (statusEl) statusEl.textContent = online ? '在线' : '离线';
  }

  function applyTheme(value) {
    theme = value === 'light' || value === 'dark' ? value : 'auto';
    state.theme = theme;
    if (window.HardwareMonitorWidget) window.HardwareMonitorWidget.setTheme(theme);
  }

  function applyCollapsed(value) {
    collapsed = !!value;
    state.collapsed = collapsed;
    document.body.classList.toggle('hmw-collapsed', collapsed);
    if (window.HardwareMonitorWidget) window.HardwareMonitorWidget.setCollapsed(collapsed);
  }

  function schedule(delayMs) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      if (!document.hidden) fetchOnce();
    }, delayMs);
  }

  function fetchOnce() {
    var headers = token ? { 'X-Monitor-Token': token } : {};
    fetch(baseUrl + '/api/all', { headers: headers, cache: 'no-store' })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function (data) {
        failures = 0;
        setStatus(true);
        if (widget) widget.render(data);
        post('monitor:state', {
          online: true,
          collapsed: state.collapsed,
          theme: state.theme,
          platform: data.platform || null
        });
        schedule(REFRESH_MS);
      })
      .catch(function () {
        failures += 1;
        setStatus(false);
        post('monitor:state', {
          online: false,
          collapsed: state.collapsed,
          theme: state.theme,
          error: true
        });
        schedule(BACKOFF[Math.min(failures - 1, BACKOFF.length - 1)]);
      });
  }

  function bindMessages() {
    window.addEventListener('message', function (event) {
      if (!embedded || event.source !== window.parent) return;
      var data = event.data || {};
      switch (data.type) {
        case 'monitor:init':
          if (data.baseUrl) baseUrl = String(data.baseUrl).replace(/\/+$/, '');
          if (data.token) token = String(data.token);
          if (data.theme) applyTheme(data.theme);
          if (data.collapsed !== undefined) applyCollapsed(!!data.collapsed);
          fetchOnce();
          break;
        case 'monitor:set-theme':
          applyTheme(data.theme);
          break;
        case 'monitor:collapse':
          applyCollapsed(!!data.collapsed);
          break;
        case 'monitor:refresh':
          fetchOnce();
          break;
      }
    });
  }

  function bindControls() {
    var collapseBtn = document.getElementById('hmw-collapse');
    var closeBtn = document.getElementById('hmw-close');
    if (collapseBtn) {
      collapseBtn.addEventListener('click', function () {
        applyCollapsed(!collapsed);
        post('monitor:state', { online: state.online, collapsed: collapsed, theme: theme });
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        if (embedded) post('monitor:close');
        else if (frame) frame.style.display = 'none';
      });
    }
    if (openLink) openLink.href = baseUrl.replace(/\/+$/, '') + '/';
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) fetchOnce();
    });
  }

  function boot() {
    document.body.classList.toggle('hmw-embed', embedded);
    bindMessages();
    bindControls();
    applyTheme(theme);
    applyCollapsed(collapsed);
    widget = window.HardwareMonitorWidget.mount(
      document.getElementById('widget-root'),
      { theme: theme, collapsed: collapsed }
    );
    if (embedded) {
      // 等宿主回 monitor:init；宿主迟迟不发时也主动报一次 ready
      post('monitor:ready');
      setTimeout(function () { if (!state.online) post('monitor:ready'); }, 800);
    } else {
      fetchOnce();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
