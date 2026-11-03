// ==UserScript==
// @name         Hardware Monitor Widget
// @namespace    hardware-monitor
// @version      1.0.0
// @description  在任意网页（含 HTTPS 聊天页）左上角/对话区显示本机硬件监控缩略组件
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @connect      *
// @run-at       document-idle
// ==/UserScript==

/* T5.2 用户脚本适配器
 *
 * 为什么不用 iframe：HTTPS 页面嵌入 http://127.0.0.1 会被 Safari/iOS 的混合内容策略拦截。
 * 本脚本用 GM_xmlhttpRequest（不受页面 CSP/混合内容限制）直接取数，在 Shadow DOM 中渲染，
 * 渲染核心优先复用服务端的 widget-core.js；取不到时退回内置的极简渲染。
 *
 * Token 用 GM_getValue 保存，不写入 URL；可在脚本管理器菜单里设置。
 */
(function () {
  'use strict';

  var DEFAULTS = {
    baseUrl: 'http://127.0.0.1:5678',
    token: '',
    targetSelector: '',
    theme: 'auto',
    position: 'top-left',
    refreshMs: 2000
  };
  var BACKOFF = [2000, 5000, 10000];

  var config = {
    baseUrl: GM_getValue('hm_base_url', DEFAULTS.baseUrl),
    token: GM_getValue('hm_token', DEFAULTS.token),
    targetSelector: GM_getValue('hm_target', DEFAULTS.targetSelector),
    theme: GM_getValue('hm_theme', DEFAULTS.theme),
    position: GM_getValue('hm_position', DEFAULTS.position)
  };

  var host = null;
  var shadow = null;
  var rootEl = null;
  var statusEl = null;
  var dotEl = null;
  var widget = null;
  var timer = null;
  var failures = 0;
  var running = true;

  function gmGet(url, headers) {
    return new Promise(function (resolve, reject) {
      GM_xmlhttpRequest({
        method: 'GET',
        url: url,
        headers: headers || {},
        timeout: 4000,
        onload: function (res) {
          if (res.status >= 200 && res.status < 300) {
            try { resolve(JSON.parse(res.responseText)); } catch (err) { reject(err); }
          } else {
            reject(new Error('HTTP ' + res.status));
          }
        },
        onerror: function () { reject(new Error('network error')); },
        ontimeout: function () { reject(new Error('timeout')); }
      });
    });
  }

  function loadCore() {
    return new Promise(function (resolve) {
      GM_xmlhttpRequest({
        method: 'GET',
        url: config.baseUrl.replace(/\/+$/, '') + '/widget-core.js',
        timeout: 4000,
        onload: function (res) {
          if (res.status !== 200) { resolve(false); return; }
          try {
            // 在用户脚本作用域执行渲染核心（不经过页面 CSP；只从用户配置的服务地址加载）
            (0, eval)(res.responseText);
            resolve(!!window.HardwareMonitorWidget);
          } catch (err) {
            resolve(false);
          }
        },
        onerror: function () { resolve(false); },
        ontimeout: function () { resolve(false); }
      });
    });
  }

  function effectiveTheme() {
    if (config.theme === 'light' || config.theme === 'dark') return config.theme;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme() {
    if (host) host.setAttribute('data-hm-theme', effectiveTheme());
  }

  function buildUi() {
    host = document.createElement('div');
    host.setAttribute('data-hardware-monitor', '');
    host.style.cssText = 'position:fixed;z-index:2147483000;left:12px;top:12px;';
    shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = [
      '<style>',
      ':host([data-hm-theme="light"]){--bg:#fff;--bg2:#f4f6f8;--bd:#e2e8f0;--fg:#0f172a;--dim:#64748b;--ok:#10b981;--warn:#f59e0b;--bad:#ef4444;}',
      ':host([data-hm-theme="dark"]){--bg:#0f172a;--bg2:#1e293b;--bd:#334155;--fg:#e2e8f0;--dim:#94a3b8;--ok:#34d399;--warn:#fbbf24;--bad:#f87171;}',
      '.wrap{width:320px;height:180px;display:flex;flex-direction:column;background:var(--bg);border:1px solid var(--bd);border-radius:10px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.18);font-family:ui-monospace,Menlo,Consolas,monospace;color:var(--fg);}',
      '.wrap[data-collapsed="true"]{width:132px;height:36px;}',
      '.head{height:36px;flex:0 0 36px;display:flex;align-items:center;gap:6px;padding:0 8px;background:var(--bg2);border-bottom:1px solid var(--bd);cursor:move;user-select:none;}',
      '.dot{width:7px;height:7px;border-radius:50%;background:var(--warn);}',
      '.dot.online{background:var(--ok);}.dot.offline{background:var(--bad);}',
      '.title{font-size:11px;font-weight:700;letter-spacing:.08em;}',
      '.status{font-size:10px;color:var(--dim);flex:1 1 auto;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}',
      'button,a{width:20px;height:20px;border:none;background:transparent;color:var(--dim);font-size:13px;line-height:20px;text-align:center;cursor:pointer;text-decoration:none;padding:0;border-radius:4px;}',
      'button:hover,a:hover{background:var(--bd);color:var(--fg);}',
      '.body{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:repeat(3,1fr);grid-auto-rows:1fr;gap:4px;padding:6px;}',
      '.wrap[data-collapsed="true"] .body{display:none;}.wrap[data-collapsed="true"] .status{display:none;}',
      '.hmw-body{display:grid;grid-template-columns:repeat(3,1fr);grid-auto-rows:1fr;gap:4px;padding:6px;flex:1 1 auto;min-height:0;}',
      '.hmw-tile{display:flex;flex-direction:column;justify-content:center;gap:2px;padding:2px 4px;border-radius:6px;background:var(--bg2);min-width:0;}',
      '.hmw-label{font-size:9px;letter-spacing:.06em;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '.hmw-value{font-size:12px;font-weight:700;white-space:nowrap;}',
      '.hmw-value.na{color:var(--dim);}.hmw-value.ok{color:var(--ok);}.hmw-value.warn{color:var(--warn);}.hmw-value.bad{color:var(--bad);}',
      '</style>',
      '<div class="wrap" data-collapsed="false">',
      '  <div class="head"><span class="dot"></span><span class="title">HARDWARE</span>',
      '    <span class="status">连接中…</span>',
      '    <button class="collapse" title="折叠/展开">−</button>',
      '    <a class="open" target="_blank" rel="noopener" title="打开完整面板">↗</a>',
      '    <button class="close" title="关闭">×</button></div>',
      '  <div class="body" id="root"></div>',
      '</div>'
    ].join('');

    rootEl = shadow.getElementById('root');
    statusEl = shadow.querySelector('.status');
    dotEl = shadow.querySelector('.dot');
    shadow.querySelector('.open').href = config.baseUrl.replace(/\/+$/, '') + '/';
    shadow.querySelector('.open').textContent = '↗';
    applyTheme();

    shadow.querySelector('.collapse').addEventListener('click', function () {
      var wrap = shadow.querySelector('.wrap');
      var collapsed = wrap.getAttribute('data-collapsed') !== 'true';
      wrap.setAttribute('data-collapsed', collapsed ? 'true' : 'false');
      GM_setValue('hm_collapsed', collapsed);
    });
    shadow.querySelector('.close').addEventListener('click', destroy);

    // 拖动标题栏，位置存 GM 存储
    var saved = GM_getValue('hm_pos', null);
    if (saved && isFinite(saved.left) && isFinite(saved.top)) {
      host.style.left = saved.left + 'px';
      host.style.top = saved.top + 'px';
    }
    var drag = null;
    shadow.querySelector('.head').addEventListener('mousedown', function (event) {
      if (event.target.closest('button,a')) return;
      drag = { x: event.clientX, y: event.clientY, left: host.offsetLeft, top: host.offsetTop };
      event.preventDefault();
    });
    window.addEventListener('mousemove', function (event) {
      if (!drag) return;
      host.style.left = drag.left + (event.clientX - drag.x) + 'px';
      host.style.top = drag.top + (event.clientY - drag.y) + 'px';
    });
    window.addEventListener('mouseup', function () {
      if (!drag) return;
      drag = null;
      GM_setValue('hm_pos', { left: host.offsetLeft, top: host.offsetTop });
    });

    var target = config.targetSelector ? document.querySelector(config.targetSelector) : null;
    (target || document.body).appendChild(host);
    if (target) {
      target.style.position = target.style.position || 'relative';
      host.style.position = 'absolute';
    }
  }

  function fallbackRender(data) {
    var temp = (data.temperature && (data.temperature.cpu || data.temperature.gpu)) || {};
    var values = [
      ['CPU', data.cpu ? data.cpu.usage + '%' : '--'],
      ['MEM', data.memory ? data.memory.usage_percent + '%' : '--'],
      ['GPU', data.gpu ? data.gpu.usage + '%' : '--'],
      ['TEMP', temp.value != null ? temp.value + ' °C' : '--'],
      ['DOWN', data.network_speed ? data.network_speed.download_mbps + ' MB/s' : '--'],
      ['UP', data.network_speed ? data.network_speed.upload_mbps + ' MB/s' : '--']
    ];
    var html = values.map(function (item) {
      return '<div class="hmw-tile"><span class="hmw-label">' + item[0] +
        '</span><span class="hmw-value ok">' + item[1] + '</span></div>';
    }).join('');
    rootEl.innerHTML = '<div class="hmw-body">' + html + '</div>';
  }

  function setStatus(online) {
    if (!dotEl || !statusEl) return;
    dotEl.className = 'dot ' + (online ? 'online' : 'offline');
    statusEl.textContent = online ? '在线' : '离线';
  }

  function schedule(delay) {
    if (!running) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(poll, delay);
  }

  function poll() {
    if (!running) return;
    if (document.hidden) { schedule(DEFAULTS.refreshMs); return; }
    gmGet(config.baseUrl.replace(/\/+$/, '') + '/api/all',
      config.token ? { 'X-Monitor-Token': config.token } : {})
      .then(function (data) {
        failures = 0;
        setStatus(true);
        if (widget) widget.render(data);
        else fallbackRender(data);
        schedule(DEFAULTS.refreshMs);
      })
      .catch(function () {
        failures += 1;
        setStatus(false);
        schedule(BACKOFF[Math.min(failures - 1, BACKOFF.length - 1)]);
      });
  }

  function destroy() {
    running = false;
    if (timer) clearTimeout(timer);
    if (host && host.parentNode) host.parentNode.removeChild(host);
    host = null;
  }

  function registerMenus() {
    if (typeof GM_registerMenuCommand !== 'function') return;
    GM_registerMenuCommand('设置服务地址', function () {
      var value = window.prompt('服务地址（如 http://127.0.0.1:5678）', config.baseUrl);
      if (value) {
        config.baseUrl = value.trim().replace(/\/+$/, '');
        GM_setValue('hm_base_url', config.baseUrl);
        location.reload();
      }
    });
    GM_registerMenuCommand('设置 Token', function () {
      var value = window.prompt('X-Monitor-Token（留空表示无 token）', config.token);
      if (value !== null) {
        config.token = value.trim();
        GM_setValue('hm_token', config.token);
        location.reload();
      }
    });
    GM_registerMenuCommand('设置挂载目标选择器', function () {
      var value = window.prompt('CSS 选择器（留空 = 视口左上角）', config.targetSelector);
      if (value !== null) {
        config.targetSelector = value.trim();
        GM_setValue('hm_target', config.targetSelector);
        location.reload();
      }
    });
    GM_registerMenuCommand('重置位置', function () {
      GM_setValue('hm_pos', null);
      location.reload();
    });
  }

  function start() {
    buildUi();
    var collapsed = GM_getValue('hm_collapsed', false);
    var wrap = shadow.querySelector('.wrap');
    wrap.setAttribute('data-collapsed', collapsed ? 'true' : 'false');

    loadCore().then(function (ok) {
      if (ok && window.HardwareMonitorWidget) {
        var html = document.documentElement;
        var previousTheme = html.getAttribute('data-theme');
        var previousEffective = html.getAttribute('data-effective-theme');
        widget = window.HardwareMonitorWidget.mount(rootEl, { theme: config.theme });
        // widget-core 会把主题写到宿主 <html>；用户脚本里必须还原，避免改变宿主站点主题
        if (previousTheme === null) html.removeAttribute('data-theme');
        else html.setAttribute('data-theme', previousTheme);
        if (previousEffective === null) html.removeAttribute('data-effective-theme');
        else html.setAttribute('data-effective-theme', previousEffective);
      }
      poll();
    });

    if (window.matchMedia) {
      var scheme = window.matchMedia('(prefers-color-scheme: dark)');
      var onSchemeChange = function () { applyTheme(); };
      if (scheme.addEventListener) scheme.addEventListener('change', onSchemeChange);
      else if (scheme.addListener) scheme.addListener(onSchemeChange);
    }

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        if (timer) { clearTimeout(timer); timer = null; }
        return;
      }
      if (!running) return;
      if (timer) { clearTimeout(timer); timer = null; }
      poll();
    });
    registerMenus();
  }

  start();
})();
