/* DSH 适配器：读取同目录 adapter.config.json，加载 /embed.js 并挂载缩略组件。
 *
 * 使用方式（任选其一）：
 *   1) 在 DSH 页面通过调试台/用户脚本注入本文件（保证同目录可访问 adapter.config.json）；
 *   2) 直接把配置写到 window.DSH_HARDWARE_MONITOR_CONFIG；
 *   3) 无法注入时改用 plugin/adapters/userscript/（不依赖宿主 DOM 选择器），
 *      或独立窗口打开 /widget、/。
 *
 * 注意：DSH 的 DOM 结构会随版本变化，targetSelector 由使用方按实际页面填写；
 * 找不到目标容器时自动回退到视口左上角，并在控制台给出提示。
 */
(function () {
  'use strict';

  var DEFAULTS = {
    targetSelector: '#chat-container',
    position: 'top-left',
    theme: 'auto',
    baseUrl: 'http://127.0.0.1:5678',
    token: '',
    collapsed: false
  };

  var script = document.currentScript;
  var overrides = window.DSH_HARDWARE_MONITOR_CONFIG || {};
  var configuredUrl = window.DSH_HARDWARE_MONITOR_CONFIG_URL || '';

  function configUrl() {
    // 优先显式配置；否则按脚本自身位置解析同目录 adapter.config.json。
    // 控制台 / eval 注入时 document.currentScript 为 null，此时必须用显式配置。
    if (configuredUrl) return configuredUrl;
    try {
      if (script && script.src) return new URL('adapter.config.json', script.src).toString();
    } catch (err) { /* ignore */ }
    return '';
  }

  function loadConfig(done) {
    var merged = Object.assign({}, DEFAULTS, overrides);
    var settled = false;
    function finish(config) {
      if (settled) return;
      settled = true;
      done(Object.assign({}, config, overrides));
    }
    var url = configUrl();
    if (!url) {
      console.warn('[hardware-monitor] 无法定位 adapter.config.json（document.currentScript 不可用）。' +
        '请设置 window.DSH_HARDWARE_MONITOR_CONFIG 或在注入前设置 ' +
        'window.DSH_HARDWARE_MONITOR_CONFIG_URL。');
      finish(merged);
      return;
    }
    try {
      fetch(url, { cache: 'no-store' })
        .then(function (resp) {
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          return resp.json();
        })
        .then(function (fileConfig) { finish(Object.assign({}, merged, fileConfig)); })
        .catch(function (err) {
          console.warn('[hardware-monitor] 读取 adapter.config.json 失败，使用默认/页面覆盖配置: ' + err);
          finish(merged);
        });
    } catch (err) {
      console.warn('[hardware-monitor] 读取配置异常，使用默认配置: ' + err);
      finish(merged);
    }
  }

  function loadEmbed(baseUrl, done) {
    if (window.HardwareMonitor) { done(); return; }
    var src = String(baseUrl).replace(/\/+$/, '') + '/embed.js';
    var tag = document.createElement('script');
    tag.src = src;
    tag.async = true;
    tag.onload = done;
    tag.onerror = function () {
      console.warn('[hardware-monitor] embed.js 加载失败: ' + src +
        ' —— 可改用用户脚本，或独立窗口打开 ' + src.replace('/embed.js', '/'));
    };
    (document.head || document.documentElement).appendChild(tag);
  }

  function pickTarget(selector) {
    if (!selector) return '';
    try {
      return document.querySelector(selector) ? selector : '';
    } catch (err) {
      console.warn('[hardware-monitor] 无效的 targetSelector: ' + selector);
      return '';
    }
  }

  function boot() {
    loadConfig(function (config) {
      loadEmbed(config.baseUrl, function () {
        if (!window.HardwareMonitor) return;
        var target = pickTarget(config.targetSelector);
        if (!target) {
          console.warn('[hardware-monitor] 未找到目标选择器 ' + config.targetSelector +
            '，缩略组件回退到视口左上角');
        }
        window.HardwareMonitor.mount({
          target: target,
          position: config.position,
          theme: config.theme,
          baseUrl: config.baseUrl,
          token: config.token,
          collapsed: !!config.collapsed
        });
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
