/**
 * dsh-hardware-monitor 浏览器半（DSH UI 插件格式）。
 *
 * 在对话会话区右上角（conversation.session.header.corner）放一个常驻小挂件：
 *   - 每 2 秒从 /hardware-monitor-api/summary 读一次快照（同源代理，避免 CORS/混合内容）；
 *   - 显示 CPU / 内存 / GPU / 温度，点击展开明细并可直接打开完整面板；
 *   - 会话区 slot 不存在时回退到 composer dock（官方模板位）。
 *
 * 模块格式与 DSH 官方模板一致：
 * window.__ModuleLoader__.load({ id: <package name>, factory(require) { return { inject, apply } } })
 */
window.__ModuleLoader__.load({
  id: 'dsh-hardware-monitor',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    const REFRESH_MS = 2000;
    const BACKOFF = [2000, 5000, 10000];
    const DEFAULT_BASE = 'http://127.0.0.1:5678';

    function num(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function fmt(value, digits) {
      const parsed = num(value);
      return parsed === null ? '--' : parsed.toFixed(digits || 0);
    }

    function readLocalToken() {
      try { return localStorage.getItem('hardware-monitor-token') || ''; } catch { return ''; }
    }

    function dashboardUrl(baseUrl) {
      const base = String(baseUrl || DEFAULT_BASE).replace(/\/+$/, '');
      const token = readLocalToken();
      return token ? `${base}/?token=${encodeURIComponent(token)}` : `${base}/`;
    }

    function HardwareMonitorCorner() {
      const [snapshot, setSnapshot] = React.useState(null);
      const [baseUrl, setBaseUrl] = React.useState(DEFAULT_BASE);
      const [offline, setOffline] = React.useState(false);
      const [open, setOpen] = React.useState(false);
      const rootRef = React.useRef(null);

      // 轮询：完成后才排下一轮（避免请求叠加），失败退避 2s→5s→10s，隐藏时暂停。
      React.useEffect(() => {
        let cancelled = false;
        let timer = null;
        let failures = 0;

        async function poll() {
          if (cancelled) return;
          if (document.hidden) {
            timer = setTimeout(poll, REFRESH_MS);
            return;
          }
          try {
            const response = await fetch('/hardware-monitor-api/summary', { cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            if (cancelled) return;
            if (!payload || payload.ok !== true) throw new Error((payload && payload.error) || 'offline');
            failures = 0;
            if (payload.base_url) setBaseUrl(payload.base_url);
            setSnapshot(payload.data || null);
            setOffline(false);
            timer = setTimeout(poll, REFRESH_MS);
          } catch (error) {
            if (cancelled) return;
            failures += 1;
            setOffline(true);
            timer = setTimeout(poll, BACKOFF[Math.min(failures - 1, BACKOFF.length - 1)]);
          }
        }

        poll();
        const onVisibility = () => {
          if (!document.hidden) {
            if (timer) clearTimeout(timer);
            poll();
          }
        };
        document.addEventListener('visibilitychange', onVisibility);
        return () => {
          cancelled = true;
          if (timer) clearTimeout(timer);
          document.removeEventListener('visibilitychange', onVisibility);
        };
      }, []);

      // 点击外部关闭展开面板
      React.useEffect(() => {
        if (!open) return undefined;
        function onDown(event) {
          if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
        }
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
      }, [open]);

      const data = snapshot || {};
      const cpu = num((data.cpu || {}).usage);
      const mem = num((data.memory || {}).usage_percent);
      const gpu = num((data.gpu || {}).usage);
      const temperature = data.temperature || {};
      const tempEntry = temperature.cpu || temperature.gpu || {};
      const temp = num(tempEntry.value);
      const net = data.network_speed || {};
      const dotColor = offline ? '#9ca3af' : (cpu !== null && cpu > 80 ? '#ef4444' : '#10b981');
      const chipText = `CPU ${fmt(cpu)}% · MEM ${fmt(mem)}%`;
      const title = offline
        ? `Hardware Monitor 离线：${baseUrl}（先启动 run.bat）`
        : [
            `CPU ${fmt(cpu)}%`,
            `MEM ${fmt(mem)}%`,
            `GPU ${fmt(gpu)}%`,
            `TEMP ${fmt(temp, 1)}°C`,
            `↓ ${fmt(net.download_mbps, 1)} MB/s`,
            `↑ ${fmt(net.upload_mbps, 1)} MB/s`,
          ].join(' · ');

      const chipStyle = {
        display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '2px 9px',
        borderRadius: '999px', border: '1px solid rgba(148,163,184,.45)',
        background: open ? 'rgba(59,130,246,.14)' : 'transparent',
        color: 'inherit', cursor: 'pointer', fontSize: '11px', lineHeight: '18px',
        whiteSpace: 'nowrap', userSelect: 'none', fontFamily: 'ui-monospace,Menlo,Consolas,monospace',
      };
      const dotStyle = { width: '7px', height: '7px', borderRadius: '50%', background: dotColor, flex: '0 0 auto' };
      const panelStyle = {
        position: 'absolute', top: '30px', right: '0', zIndex: 60, minWidth: '230px',
        padding: '10px 12px', borderRadius: '10px', border: '1px solid rgba(148,163,184,.35)',
        background: '#0f172a', color: '#e2e8f0', fontSize: '12px', lineHeight: '20px',
        boxShadow: '0 10px 30px rgba(0,0,0,.35)', fontFamily: 'ui-monospace,Menlo,Consolas,monospace',
      };
      const rowStyle = { display: 'flex', justifyContent: 'space-between', gap: '18px' };
      const linkStyle = { color: '#60a5fa', textDecoration: 'none', marginTop: '6px', display: 'inline-block' };

      function onActivate(event) {
        if (event) event.preventDefault();
        setOpen((value) => !value);
      }

      const panel = open ? h('div', { key: 'panel', style: panelStyle },
        h('div', { style: rowStyle }, h('span', {}, 'CPU'), h('b', {}, `${fmt(cpu)}%`)),
        h('div', { style: rowStyle }, h('span', {}, 'MEM'), h('b', {}, `${fmt(mem)}%`)),
        h('div', { style: rowStyle }, h('span', {}, 'GPU'), h('b', {}, `${fmt(gpu)}%`)),
        h('div', { style: rowStyle }, h('span', {}, 'TEMP'), h('b', {}, `${fmt(temp, 1)}°C`)),
        h('div', { style: rowStyle }, h('span', {}, 'DOWN'), h('b', {}, `${fmt(net.download_mbps, 1)} MB/s`)),
        h('div', { style: rowStyle }, h('span', {}, 'UP'), h('b', {}, `${fmt(net.upload_mbps, 1)} MB/s`)),
        h('a', {
          key: 'open', style: linkStyle, href: dashboardUrl(baseUrl), target: '_blank', rel: 'noopener',
        }, '打开完整面板 ↗'),
        offline ? h('div', { key: 'offline', style: { color: '#fbbf24', marginTop: '6px' } },
          `离线：${baseUrl}`) : null,
      ) : null;

      return h('div', { ref: rootRef, style: { position: 'relative', display: 'inline-block' } },
        h('div', {
          key: 'chip', style: chipStyle, title: title, onClick: onActivate, role: 'button', tabIndex: 0,
          onKeyDown: (event) => { if (event.key === 'Enter' || event.key === ' ') onActivate(event); },
          'data-hardware-monitor': 'corner',
        }, [h('span', { key: 'dot', style: dotStyle }), chipText]),
        panel,
      );
    }

    /** 侧边栏图标：显示器 + 心跳波形，跟随宿主 currentColor。 */
    function HardwareMonitorIcon() {
      return h('svg', {
        viewBox: '0 0 16 16', width: 16, height: 16, fill: 'none',
        'aria-hidden': 'true', style: { display: 'block' },
      },
      h('rect', { x: 1.5, y: 2.5, width: 13, height: 9, rx: 1.5, stroke: 'currentColor', strokeWidth: 1.3 }),
      h('path', {
        d: 'M3.5 7h2l1.2-2.2L8.2 9l1.1-1.6h2.2',
        stroke: 'currentColor', strokeWidth: 1.3, strokeLinecap: 'round', strokeLinejoin: 'round',
      }),
      h('path', { d: 'M5.5 13.5h5', stroke: 'currentColor', strokeWidth: 1.3, strokeLinecap: 'round' }));
    }

    /** 侧边栏「完整面板」页：内嵌监控仪表盘（token 从 localStorage 读取）。 */
    function HardwareMonitorPanel() {
      return h('iframe', {
        src: dashboardUrl(DEFAULT_BASE),
        title: 'Hardware Monitor',
        style: { width: '100%', height: '100%', border: '0', display: 'block' },
      });
    }

    /**
     * 在侧边栏注册入口图标 + 对应的主面板（参考插件市场的 sidebar.panellist / main 槽位）。
     * 点击图标时由宿主切换 main(key=hardware-monitor)，即内嵌完整面板。
     */
    function mountSidebar(ctx) {
      let offRow = null;
      let offPanel = null;
      let offPanelInject = null;
      try {
        ctx.slots.inject('sidebar.panellist', () => {
          try {
            offRow = ctx.slots.register({
              name: 'sidebar.panellist',
              id: 'hardware-monitor',
              order: 2,
              label: () => '硬件监控',
            }, HardwareMonitorIcon);
          } catch { /* 宿主版本不支持侧边栏入口 */ }
        });
      } catch { /* ignore */ }
      try {
        // 与 sidebar.panellist 的 id 对应：点击侧边栏图标时宿主打开 main key=hardware-monitor 的面板。
        // 必须放在 slots.inject('main') 里注册，等布局声明 main 槽位后再注册，否则点击会因缺少条目抛错。
        offPanelInject = ctx.slots.inject('main', () => {
          try {
            offPanel = ctx.slots.register({ name: 'main', key: 'hardware-monitor' }, HardwareMonitorPanel);
          } catch { /* 宿主版本不支持主面板槽位 */ }
        });
      } catch { /* ignore */ }
      return () => {
        try { if (typeof offRow === 'function') offRow(); } catch { /* ignore */ }
        try { if (typeof offPanel === 'function') offPanel(); } catch { /* ignore */ }
        try { if (typeof offPanelInject === 'function') offPanelInject(); } catch { /* ignore */ }
      };
    }

    /**
     * 尝试把挂件注册进某个 slot。
     * @param {object} ctx - client 上下文（含 slots 服务）
     * @param {string} slot - slot 名
     * @param {number} order - 排序
     * @returns {boolean} 是否注册成功
     */
    function mountAt(ctx, slot, order) {
      let mounted = false;
      try {
        ctx.slots.inject(slot, () => {
          try {
            ctx.slots.register({ name: slot, id: 'hardware-monitor', order }, HardwareMonitorCorner);
            mounted = true;
          } catch {
            mounted = false;
          }
        });
      } catch {
        mounted = false;
      }
      return mounted;
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        // 侧边栏入口 + 完整面板（像插件市场一样有图标入口）
        mountSidebar(ctx);
        // 首选会话区右上角；若该 slot 在当前版本不可用，退回输入框 dock（官方模板位）。
        if (!mountAt(ctx, 'conversation.session.header.corner', 60)) {
          mountAt(ctx, 'conversation.composer.dock', 60);
        }
      },
    };
  },
});
