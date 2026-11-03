/* Hardware Monitor 缩略组件渲染核心（零依赖、无构建）。
 *
 * 同时供两处使用：
 *   1) /widget 页面（iframe 模式，widget.js 调用）
 *   2) 宿主用户脚本（T5）直接引用，在 Shadow DOM 里渲染
 *
 * 暴露：
 *   window.HardwareMonitorWidget = {
 *     mount(container, options), render(data, options),
 *     setTheme(theme), destroy(), version
 *   }
 */
(function (global) {
  'use strict';

  var VERSION = '1.0.0';
  var current = null;
  var schemeMedia = global.matchMedia ? global.matchMedia('(prefers-color-scheme: dark)') : null;

  var METRICS = [
    { key: 'cpu', label: 'CPU', unit: '%', digits: 0, thresholds: true },
    { key: 'mem', label: 'MEM', unit: '%', digits: 0, thresholds: true },
    { key: 'gpu', label: 'GPU', unit: '%', digits: 0, thresholds: true },
    { key: 'temp', label: 'TEMP', unit: '°C', digits: 1, thresholds: true },
    { key: 'down', label: 'DOWN', unit: 'MB/s', digits: 1, thresholds: false },
    { key: 'up', label: 'UP', unit: 'MB/s', digits: 1, thresholds: false }
  ];

  function applyTheme(theme) {
    var mode = theme === 'light' || theme === 'dark' ? theme : 'auto';
    var effective = mode;
    if (mode === 'auto') {
      effective = schemeMedia && schemeMedia.matches ? 'dark' : 'light';
    }
    var root = global.document.documentElement;
    root.setAttribute('data-theme', mode);
    root.setAttribute('data-effective-theme', effective);
  }

  function onSchemeChange() {
    if (global.document.documentElement.getAttribute('data-theme') !== 'auto') return;
    applyTheme('auto');
  }

  if (schemeMedia) {
    if (schemeMedia.addEventListener) schemeMedia.addEventListener('change', onSchemeChange);
    else if (schemeMedia.addListener) schemeMedia.addListener(onSchemeChange);
  }

  function formatValue(value, digits) {
    var num = Number(value);
    if (value === null || value === undefined || !isFinite(num)) return '--';
    return num.toFixed(digits == null ? 0 : digits);
  }

  function classFor(metric, value) {
    if (value === null || value === undefined || !isFinite(Number(value))) return 'na';
    if (!metric.thresholds) return 'ok';
    var num = Number(value);
    if (num > 80) return 'bad';
    if (num > 60) return 'warn';
    return 'ok';
  }

  function Widget(container, options) {
    this.options = options || {};
    this.container = container;
    this.tiles = {};
    this.root = document.createElement('div');
    this.root.className = 'hmw-body';
    this.root.setAttribute('role', 'group');
    this.root.setAttribute('aria-label', '硬件监控缩略组件');

    METRICS.forEach(function (metric) {
      var tile = document.createElement('div');
      tile.className = 'hmw-tile';
      tile.setAttribute('data-metric', metric.key);

      var label = document.createElement('span');
      label.className = 'hmw-label';
      label.textContent = metric.label;

      var value = document.createElement('span');
      value.className = 'hmw-value na';
      value.textContent = '--';

      tile.appendChild(label);
      tile.appendChild(value);
      this.root.appendChild(tile);
      this.tiles[metric.key] = { metric: metric, value: value };
    }, this);

    container.appendChild(this.root);
    applyTheme(this.options.theme || 'auto');
  }

  Widget.prototype.render = function (data) {
    data = data || {};
    var temp = data.temperature || {};
    var cpu = temp.cpu || null;
    var gpu = temp.gpu || null;
    var values = {
      cpu: data.cpu ? data.cpu.usage : null,
      mem: data.memory ? data.memory.usage_percent : null,
      gpu: data.gpu ? data.gpu.usage : null,
      temp: cpu && cpu.value != null ? cpu.value : (gpu && gpu.value != null ? gpu.value : null),
      down: data.network_speed ? data.network_speed.download_mbps : null,
      up: data.network_speed ? data.network_speed.upload_mbps : null
    };

    Object.keys(this.tiles).forEach(function (key) {
      var tile = this.tiles[key];
      var raw = values[key];
      var text = formatValue(raw, tile.metric.digits);
      if (text !== '--') text += tile.metric.unit === '%' ? '%' : (' ' + tile.metric.unit);
      tile.value.textContent = text;
      tile.value.className = 'hmw-value ' + classFor(tile.metric, raw);
    }, this);
    return this;
  };

  Widget.prototype.setTheme = function (theme) {
    this.options.theme = theme || 'auto';
    applyTheme(this.options.theme);
    return this;
  };

  Widget.prototype.setCollapsed = function (collapsed) {
    global.document.body.classList.toggle('hmw-collapsed', !!collapsed);
    return this;
  };

  Widget.prototype.destroy = function () {
    if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
    if (schemeMedia) {
      if (schemeMedia.removeEventListener) schemeMedia.removeEventListener('change', onSchemeChange);
      else if (schemeMedia.removeListener) schemeMedia.removeListener(onSchemeChange);
    }
    this.root = null;
    this.tiles = {};
  };

  global.HardwareMonitorWidget = {
    mount: function (container, options) {
      if (current) current.destroy();
      current = new Widget(container, options);
      return current;
    },
    render: function (data) {
      if (current) current.render(data);
      return current;
    },
    setTheme: function (theme) {
      if (current) current.setTheme(theme);
      return current;
    },
    setCollapsed: function (collapsed) {
      if (current) current.setCollapsed(collapsed);
      return current;
    },
    destroy: function () {
      if (current) current.destroy();
      current = null;
    },
    version: VERSION
  };
})(window);
