/* Hardware Monitor 宿主注入脚本（T4.3）。
 *
 * 用法：
 *   <script src="http://127.0.0.1:5678/embed.js"
 *           data-target="#chat-container"      // 可选：挂载容器；缺省挂到视口左上角
 *           data-position="top-left"           // top-left|top-right|bottom-left|bottom-right
 *           data-theme="auto"                  // auto|light|dark
 *           data-base-url="http://127.0.0.1:5678"  // 缺省取脚本自身 origin
 *           data-token=""                      // 可选；仅在 postMessage 里传递，绝不进 iframe URL
 *           data-collapsed="0"                 // 初始是否折叠
 *           data-auto="true"                   // false 则等宿主手动 mount()
 *   ></script>
 *
 * 暴露：window.HardwareMonitor.mount(options) / unmount() / toggle() / setTheme(theme)
 *
 * 安全：
 *   - iframe 地址只有 /widget?embed=1，不带 token；
 *   - postMessage 校验 event.source === iframe.contentWindow 且 origin 属于允许列表；
 *   - 使用 Shadow DOM 隔离宿主样式。
 */
(function () {
  'use strict';

  var script = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  var scriptOrigin = null;
  try {
    scriptOrigin = script && script.src ? new URL(script.src).origin : null;
  } catch (err) {
    scriptOrigin = null;
  }

  var Z_INDEX = '2147483000';
  var EXPANDED = { width: 320, height: 180 };
  var COLLAPSED = { width: 132, height: 36 };
  var instance = null;

  function readOptions(source) {
    var data = (source && source.dataset) || {};
    return {
      target: data.target || '',
      position: data.position || 'top-left',
      theme: data.theme || 'auto',
      baseUrl: (data.baseUrl || scriptOrigin || location.origin).replace(/\/+$/, ''),
      token: data.token || '',
      collapsed: data.collapsed === '1' || data.collapsed === 'true',
      auto: data.auto === undefined ? true : !(data.auto === 'false' || data.auto === '0')
    };
  }

  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  var SHADOW_CSS = [
    ':host { all: initial; }',
    '.hw-wrap { position: fixed; z-index: ' + Z_INDEX + '; display: flex; flex-direction: column;',
    '  width: ' + EXPANDED.width + 'px; height: ' + EXPANDED.height + 'px;',
    '  background: var(--hw-bg); border: 1px solid var(--hw-border); border-radius: 10px;',
    '  overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,.18);',
    '  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--hw-text); }',
    '.hw-wrap[data-effective-theme="light"] { --hw-bg:#fff; --hw-bg-soft:#f4f6f8; --hw-border:#e2e8f0; --hw-text:#0f172a; --hw-dim:#64748b; --hw-ok:#10b981; --hw-warn:#f59e0b; --hw-bad:#ef4444; }',
    '.hw-wrap[data-effective-theme="dark"] { --hw-bg:#0f172a; --hw-bg-soft:#1e293b; --hw-border:#334155; --hw-text:#e2e8f0; --hw-dim:#94a3b8; --hw-ok:#34d399; --hw-warn:#fbbf24; --hw-bad:#f87171; }',
    '.hw-head { height: 36px; flex: 0 0 36px; display: flex; align-items: center; gap: 6px; padding: 0 8px;',
    '  background: var(--hw-bg-soft); border-bottom: 1px solid var(--hw-border); cursor: move; user-select: none; }',
    '.hw-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--hw-warn); flex: 0 0 auto; }',
    '.hw-dot.online { background: var(--hw-ok); } .hw-dot.offline { background: var(--hw-bad); }',
    '.hw-title { font-size: 11px; font-weight: 700; letter-spacing: .08em; }',
    '.hw-status { font-size: 10px; color: var(--hw-dim); flex: 1 1 auto; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }',
    '.hw-actions { display: flex; gap: 2px; }',
    '.hw-actions button, .hw-actions a { width: 20px; height: 20px; border: none; border-radius: 4px; background: transparent;',
    '  color: var(--hw-dim); font-size: 13px; line-height: 20px; text-align: center; cursor: pointer; text-decoration: none; padding: 0; }',
    '.hw-actions button:hover, .hw-actions a:hover { background: var(--hw-border); color: var(--hw-text); }',
    '.hw-frame { flex: 1 1 auto; width: 100%; border: none; background: var(--hw-bg); }',
    '.hw-wrap[data-collapsed="true"] { width: ' + COLLAPSED.width + 'px; height: ' + COLLAPSED.height + 'px; }',
    '.hw-wrap[data-collapsed="true"] .hw-frame { display: none; }',
    '.hw-wrap[data-collapsed="true"] .hw-status { display: none; }'
  ].join('\n');

  function ensureMountPoint(options) {
    if (options.target) {
      var target = document.querySelector(options.target);
      if (target) {
        var style = getComputedStyle(target);
        if (style.position === 'static') target.style.position = 'relative';
        return { container: target, absolute: true };
      }
    }
    return { container: document.body, absolute: false };
  }

  function positionWrap(wrap, options, mountPoint) {
    var pos = options.position || 'top-left';
    var offsets = { 'top-left': [12, 12], 'top-right': [12, 12], 'bottom-left': [12, 12], 'bottom-right': [12, 12] };
    var saved = null;
    try {
      saved = JSON.parse(localStorage.getItem('hw-monitor-widget-pos-v1') || 'null');
    } catch (err) { saved = null; }
    if (saved && isFinite(saved.left) && isFinite(saved.top)) {
      wrap.style.left = saved.left + 'px';
      wrap.style.top = saved.top + 'px';
      return;
    }
    var pair = offsets[pos] || offsets['top-left'];
    if (mountPoint.absolute) {
      wrap.style.left = (pos.indexOf('right') >= 0 ? null : pair[0]) === null ? '' : pair[0] + 'px';
      wrap.style.right = pos.indexOf('right') >= 0 ? pair[0] + 'px' : '';
      wrap.style.top = '12px';
      wrap.style.bottom = pos.indexOf('bottom') >= 0 ? '12px' : '';
    } else {
      wrap.style.left = pos.indexOf('right') >= 0 ? '' : pair[0] + 'px';
      wrap.style.right = pos.indexOf('right') >= 0 ? pair[0] + 'px' : '';
      wrap.style.top = pos.indexOf('bottom') >= 0 ? '' : pair[1] + 'px';
      wrap.style.bottom = pos.indexOf('bottom') >= 0 ? pair[1] + 'px' : '';
    }
  }

  function WidgetHost(options) {
    this.options = options;
    this.online = null;
    this.mountPoint = ensureMountPoint(options);
    this.host = document.createElement('div');
    this.host.setAttribute('data-hw-monitor-widget', '');
    this.host.style.position = this.mountPoint.absolute ? 'absolute' : 'fixed';
    this.host.style.top = this.host.style.left = '0';
    this.host.style.zIndex = Z_INDEX;
    this.shadow = this.host.attachShadow({ mode: 'open' });
    this.shadow.innerHTML = this._template();
    this.mountPoint.container.appendChild(this.host);

    this.wrap = this.shadow.querySelector('.hw-wrap');
    this.iframe = this.shadow.querySelector('.hw-frame');
    this.dot = this.shadow.querySelector('.hw-dot');
    this.statusEl = this.shadow.querySelector('.hw-status');
    this.openLink = this.shadow.querySelector('.hw-open');

    this._bind();
    this._applyTheme(options.theme);
    this._applyCollapsed(!!options.collapsed);
    this._position();
    this.iframe.src = options.baseUrl + '/widget?embed=1';
  }

  WidgetHost.prototype._template = function () {
    return '<style>' + SHADOW_CSS + '</style>' +
      '<div class="hw-wrap" data-collapsed="false" data-effective-theme="light">' +
      '  <div class="hw-head">' +
      '    <span class="hw-dot"></span><span class="hw-title">HARDWARE</span>' +
      '    <span class="hw-status">连接中…</span>' +
      '    <span class="hw-actions">' +
      '      <button class="hw-collapse" type="button" title="折叠 / 展开">−</button>' +
      '      <a class="hw-open" target="_blank" rel="noopener" title="打开完整面板">↗</a>' +
      '      <button class="hw-close" type="button" title="关闭">×</button>' +
      '    </span>' +
      '  </div>' +
      '  <iframe class="hw-frame" title="Hardware Monitor" loading="lazy"></iframe>' +
      '</div>';
  };

  WidgetHost.prototype._position = function () {
    positionWrap(this.wrap, this.options, this.mountPoint);
  };

  WidgetHost.prototype._applyTheme = function (theme) {
    this.options.theme = theme === 'light' || theme === 'dark' ? theme : 'auto';
    var effective = this.options.theme;
    if (effective === 'auto') {
      effective = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    this.wrap.setAttribute('data-effective-theme', effective);
  };

  WidgetHost.prototype._applyCollapsed = function (collapsed) {
    this.options.collapsed = !!collapsed;
    this.wrap.setAttribute('data-collapsed', collapsed ? 'true' : 'false');
    try { localStorage.setItem('hw-monitor-widget-collapsed', collapsed ? '1' : '0'); } catch (err) { /* ignore */ }
  };

  WidgetHost.prototype._send = function (message) {
    if (!this.iframe || !this.iframe.contentWindow) return;
    var origin = new URL(this.options.baseUrl).origin;
    this.iframe.contentWindow.postMessage(message, origin);
  };

  WidgetHost.prototype._sendInit = function () {
    this._send({
      type: 'monitor:init',
      baseUrl: this.options.baseUrl,
      token: this.options.token,
      theme: this.options.theme,
      collapsed: this.options.collapsed
    });
  };

  WidgetHost.prototype._setStatus = function (online) {
    this.online = online;
    if (this.dot) {
      this.dot.classList.toggle('online', online === true);
      this.dot.classList.toggle('offline', online === false);
    }
    if (this.statusEl) this.statusEl.textContent = online ? '在线' : (online === false ? '离线' : '连接中…');
  };

  WidgetHost.prototype._bind = function () {
    var self = this;
    this.openLink.href = this.options.baseUrl + '/';
    this.openLink.textContent = '↗';

    this.shadow.querySelector('.hw-collapse').addEventListener('click', function () {
      self.toggle();
    });
    this.shadow.querySelector('.hw-close').addEventListener('click', function () {
      self.unmount();
    });
    this.iframe.addEventListener('load', function () { self._sendInit(); });

    this._onMessage = function (event) {
      if (event.source !== self.iframe.contentWindow) return;
      var expected = new URL(self.options.baseUrl).origin;
      if (event.origin !== expected) return;
      var data = event.data || {};
      if (data.type === 'monitor:ready') self._sendInit();
      else if (data.type === 'monitor:state') self._setStatus(data.online);
      else if (data.type === 'monitor:close') self.unmount();
    };
    window.addEventListener('message', this._onMessage);
    setTimeout(function () { self._sendInit(); }, 900);

    // 拖动标题栏（位置存 localStorage）
    var drag = null;
    this.wrap.querySelector('.hw-head').addEventListener('mousedown', function (event) {
      if (event.target.closest('.hw-actions')) return;
      var rect = self.wrap.getBoundingClientRect();
      drag = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      event.preventDefault();
    });
    window.addEventListener('mousemove', function (event) {
      if (!drag) return;
      var left = drag.left + (event.clientX - drag.x);
      var top = drag.top + (event.clientY - drag.y);
      self.wrap.style.left = left + 'px';
      self.wrap.style.top = top + 'px';
      self.wrap.style.right = '';
      self.wrap.style.bottom = '';
    });
    window.addEventListener('mouseup', function () {
      if (!drag) return;
      drag = null;
      try {
        localStorage.setItem('hw-monitor-widget-pos-v1', JSON.stringify({
          left: parseInt(self.wrap.style.left, 10) || 0,
          top: parseInt(self.wrap.style.top, 10) || 0
        }));
      } catch (err) { /* ignore */ }
    });
  };

  WidgetHost.prototype.toggle = function () {
    this._applyCollapsed(!this.options.collapsed);
    this._send({ type: 'monitor:collapse', collapsed: this.options.collapsed });
  };

  WidgetHost.prototype.setTheme = function (theme) {
    this._applyTheme(theme);
    this._send({ type: 'monitor:set-theme', theme: this.options.theme });
  };

  WidgetHost.prototype.refresh = function () {
    this._send({ type: 'monitor:refresh' });
  };

  WidgetHost.prototype.unmount = function () {
    window.removeEventListener('message', this._onMessage);
    if (this.host && this.host.parentNode) this.host.parentNode.removeChild(this.host);
    instance = null;
  };

  function mount(options) {
    if (instance) instance.unmount();
    var merged = readOptions(script);
    Object.keys(options || {}).forEach(function (key) { merged[key] = options[key]; });
    merged.baseUrl = String(merged.baseUrl).replace(/\/+$/, '');
    instance = new WidgetHost(merged);
    return instance;
  }

  window.HardwareMonitor = {
    mount: mount,
    unmount: function () { if (instance) instance.unmount(); },
    toggle: function () { if (instance) instance.toggle(); },
    setTheme: function (theme) { if (instance) instance.setTheme(theme); },
    refresh: function () { if (instance) instance.refresh(); },
    getState: function () {
      return instance ? { online: instance.online, collapsed: instance.options.collapsed, theme: instance.options.theme } : null;
    }
  };

  if (readOptions(script).auto) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { mount(); });
    } else {
      mount();
    }
  }
})();
