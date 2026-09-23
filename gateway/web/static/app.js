let overviewChartInst = null;
let overviewChartData = null;
let sessionChartInst = null;
let sessionChartData = null;

// 取 token 的**已解析**色值给 canvas 用。
//
// 不能直接 getComputedStyle(el).getPropertyValue('--accent'):自定义属性返回的是原文,
// 而 tokens.css 用的是 light-dark(#157a54, #3ecf8e),canvas 不认这个字符串,
// uPlot 会静默退回黑色(2026-09-21 图表全黑就是这个原因)。
// 办法是让浏览器真解析一次:把 var(--x) 赋给一个探针元素的 color,再读回 rgb()。
let _probe = null;
const css = (name) => {
  if (!_probe) {
    _probe = document.createElement('span');
    _probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none';
    document.body.appendChild(_probe);
  }
  _probe.style.color = '';
  _probe.style.color = `var(${name})`;
  const v = getComputedStyle(_probe).color;
  return v && v !== 'rgba(0, 0, 0, 0)' ? v : getComputedStyle(document.documentElement).getPropertyValue(name).trim();
};

// Theme management
function setTheme(t) {
  if (t === 'system' || t === '') {
    delete document.documentElement.dataset.theme;
    localStorage.setItem('theme', 'system');
    t = '';
  } else {
    document.documentElement.dataset.theme = t;
    localStorage.setItem('theme', t);
  }
  document.querySelectorAll('#theme-seg button').forEach(function(b){
    const btnTheme = b.dataset.theme || 'system';
    const targetTheme = (t === '' || !t) ? 'system' : t;
    b.classList.toggle('active', btnTheme === targetTheme || (b.dataset.theme === '' && targetTheme === 'system'));
  });
  
  if (window.drawOverviewChart && overviewChartData) window.drawOverviewChart(overviewChartData);
  if (window.drawSessionChart && sessionChartData) window.drawSessionChart(sessionChartData);
}
// Init theme
let savedTheme = localStorage.getItem('theme');
if (!savedTheme) {
  savedTheme = 'dark';
}
setTheme(savedTheme === 'system' ? '' : savedTheme);

const resizeObserver = new ResizeObserver(() => {
  if (overviewChartInst && document.getElementById('overview-chart')) {
    overviewChartInst.setSize({width: document.getElementById('overview-chart').clientWidth, height: 250});
  }
  if (sessionChartInst && document.getElementById('session-chart')) {
    sessionChartInst.setSize({width: document.getElementById('session-chart').clientWidth, height: 250});
  }
});
resizeObserver.observe(document.body);
// Toast
function showToast(title, desc, type = 'info') {
  const stack = document.getElementById('toast-container');
  if (!stack) return;
  const t = document.createElement('div');
  t.className = `ui-toast ${type}`;
  t.innerHTML = `
    <div class="t-body">
      <div class="t-title">${title}</div>
      ${desc ? `<div class="t-desc">${desc}</div>` : ''}
    </div>
    <button class="t-close">×</button>
  `;
  t.querySelector('.t-close').onclick = () => t.remove();
  stack.appendChild(t);
  setTimeout(() => { if (t.parentNode) t.remove(); }, 5000);
}

async function apiAction(inst, action, data = {}) {
  const fd = new FormData();
  for (const k in data) fd.append(k, data[k]);
  fd.append('csrf', window.CSRF_TOKEN);
  try {
    const res = await fetch(`/api/i/${inst}/action/${action}`, { method: 'POST', body: fd });
    const json = await res.json();
    if (res.ok) {
      showToast('操作成功', json.message || '', 'ok');
      return true;
    } else {
      showToast('操作失败', json.message || '', 'danger');
      return false;
    }
  } catch (e) {
    showToast('请求失败', e.message, 'danger');
    return false;
  }
}

// Instances rendering
function renderInstances(instances) {
  const wall = document.getElementById('instances-wall');
  if (!wall) return;
  
  if (instances.length === 0) {
    wall.className = '';
    wall.innerHTML = `
      <div class="ui-card">
        <div class="ui-empty">
          <div class="glyph">◇</div>
          <div class="title">还没有实例</div>
          <div class="desc">你可以新建一个实例来连接飞书应用与代码项目。</div>
          <div class="actions"><a href="/new" class="ui-btn primary">新建实例</a></div>
        </div>
      </div>
    `;
    return;
  }

  wall.className = 'grid-3';
  wall.innerHTML = instances.map(inst => {
    let healthColor = inst.health === 'green' ? 'ok' : (inst.health === 'yellow' ? 'warn' : (inst.health === 'red' ? 'danger' : 'idle'));
    let h_reason = inst.health === 'green' ? '正常' : inst.health_reason;
    let timeAgo = '';
    if (inst.heartbeat_ago_s !== null && inst.heartbeat_ago_s !== undefined) {
      if (inst.heartbeat_ago_s < 60) timeAgo = `${Math.floor(inst.heartbeat_ago_s)}秒前`;
      else if (inst.heartbeat_ago_s < 3600) timeAgo = `${Math.floor(inst.heartbeat_ago_s/60)}分钟前`;
      else timeAgo = `${Math.floor(inst.heartbeat_ago_s/3600)}小时前`;
    }
    
    let btnText = inst.paused ? '恢复' : '暂停';
    let btnAction = inst.paused ? 'resume' : 'pause';
    let pendingHighlight = inst.pending > 0 ? 'color: var(--warn); font-weight: bold;' : '';
    let costStr = inst.today_cost_usd > 0 ? `$${inst.today_cost_usd.toFixed(2)}` : '$0';
    let ctxStr = inst.context_tokens > 0 ? `${(inst.context_tokens/1000).toFixed(1)}k` : '0';
    
    return `
      <div class="ui-card">
        <div class="row" style="justify-content:space-between; margin-bottom:12px;">
          <h3 style="margin:0; display:flex; align-items:center; gap:6px;">
            <span class="ui-badge ${healthColor}"><span class="ui-dot"></span>${h_reason}</span>
            ${inst.inst}
          </h3>
          <span class="ui-muted" style="font-size:12px;">心跳: ${timeAgo || '--'}</span>
        </div>
        
        <div class="ui-desc" style="margin-bottom: 16px;">
          <dt>今日消息/卡/回复</dt><dd>${inst.today_msgs} / ${inst.today_cards} / ${inst.today_replies}</dd>
          <dt>今日用量(折算)</dt><dd>${costStr}</dd>
          <dt>待处理</dt><dd style="${pendingHighlight}">${inst.pending}</dd>
          <dt>上次上下文</dt><dd>${ctxStr}</dd>
          <dt>服务状态</dt>
          <dd>
            <span class="ui-badge ${inst.units.listener === 'active' ? 'ok' : ''}">监听</span>
            <span class="ui-badge ${inst.units.timer === 'active' ? 'ok' : ''}">定时</span>
          </dd>
          ${inst.last_alert ? `<dt>最近告警</dt><dd class="ui-muted" style="color:var(--danger)">${inst.last_alert.kind}</dd>` : ''}
        </div>
        
        <div class="row" style="border-top:1px solid var(--border-subtle); padding-top:12px; justify-content:space-between;">
          <button class="ui-btn" onclick="togglePause('${inst.inst}', '${btnAction}')">${btnText}</button>
          <a href="/i/${inst.inst}?tab=overview" class="ui-btn primary">详情 →</a>
        </div>
      </div>
    `;
  }).join('');
}

window.togglePause = async function(inst, action) {
  if (await apiAction(inst, action)) {
    loadInstances();
  }
};

async function loadInstances() {
  if (!document.getElementById('instances-wall')) return;
  try {
    const res = await fetch('/api/instances');
    if (res.ok) {
      const data = await res.json();
      renderInstances(data);
    }
  } catch (e) { console.error(e); }
}

if (window.INITIAL_INSTANCES) {
  renderInstances(window.INITIAL_INSTANCES);
  setInterval(loadInstances, 30000);
}


// Instance Tabs
let currentTab = null;

window.switchTab = function(tab) {
  currentTab = tab;
  
  // Update URL without reload
  const url = new URL(window.location);
  url.searchParams.set('tab', tab);
  window.history.pushState({}, '', url);
  
  // Update UI tabs
  document.querySelectorAll('#inst-tabs .ui-tab').forEach(b => {
    b.classList.toggle('active', b.innerText === getTabName(tab));
  });
  
  const content = document.getElementById('tab-content');
  if (!content) return;
  content.innerHTML = '<div style="padding:20px;text-align:center;"><span class="ui-spinner"></span> 加载中...</div>';
  
  closeSse(); // Close any active SSE connection
  
  if (tab === 'overview') renderOverview();
  else if (tab === 'cards') renderCards(1, '');
  else if (tab === 'session') renderSession();
  else if (tab === 'logs') renderLogsUI();
  else if (tab === 'config') renderConfig();
}

function getTabName(t) {
  const map = {overview: '概览', cards: '卡', session: '会话', logs: '日志', config: '配置'};
  return map[t] || t;
}

// 1. Overview
async function renderOverview() {
  try {
    const res = await fetch(`/api/i/${window.INST}/overview`);
    const data = await res.json();
    
    let html = `
      <div class="grid-3 mb-4">
        <div class="ui-stat">
          <div class="label">心跳</div>
          <div class="value">${data.summary.heartbeat_ago_s !== null ? data.summary.heartbeat_ago_s.toFixed(1) + 's' : '--'}</div>
        </div>
        <div class="ui-stat">
          <div class="label">今日消息 / 卡</div>
          <div class="value">${data.summary.today_msgs} / ${data.summary.today_cards}</div>
        </div>
        <div class="ui-stat">
          <div class="label">待处理</div>
          <div class="value" style="color:${data.summary.pending>0?'var(--warn)':'var(--text)'}">${data.summary.pending}</div>
        </div>
        <div class="ui-stat">
          <div class="label">上次上下文</div>
          <div class="value">${(data.summary.context_tokens/1000).toFixed(1)}k</div>
        </div>
        <div class="ui-stat">
          <div class="label">卡状态分布</div>
          <div class="value" style="font-size:16px; margin-top:8px;">
             ${Object.entries(data.card_states).map(([k,v]) => `<span class="ui-tag">${k}: ${v}</span>`).join(' ')}
          </div>
        </div>
        <div class="ui-stat">
          <div class="label">服务与定时器</div>
          <div class="value" style="font-size:16px; margin-top:8px; line-height: 1.5;">
            <div style="font-size: 13px;">下一轮: <span class="ui-mono">${data.timer_next || (data.dispatcher_active ? '正在处理中' : '待定')}</span></div>
            <div style="font-size: 13px;">技能: <span class="ui-mono">${data.skill || '--'}</span></div>
          </div>
        </div>
      </div>
      
      <div class="grid-2">
        <div class="ui-card">
          <h3 class="ui-muted mb-4">今日趋势</h3>
          <div id="overview-chart" class="chart-container"></div>
        </div>
        <div class="ui-card">
          <h3 class="ui-muted mb-4">最近告警</h3>
          ${data.alerts.length === 0 ? '<p class="ui-muted">暂无告警</p>' : 
            `<div class="ui-timeline">
              ${data.alerts.map(a => `
                <div class="node danger">
                  <div class="t-title">${a.kind}</div>
                  <div class="t-time">${new Date(a.sent_at).toLocaleString()}</div>
                </div>
              `).join('')}
            </div>`
          }
        </div>
      </div>
    `;
    document.getElementById('tab-content').innerHTML = html;
    
    window.drawOverviewChart = function(chartData) {
      overviewChartData = chartData;
      const container = document.getElementById('overview-chart');
      if (!container) return;
      container.innerHTML = '';
      if (overviewChartInst) {
        overviewChartInst.destroy();
        overviewChartInst = null;
      }
      
      if (chartData.hourly && chartData.hourly.length > 0) {
        let x = chartData.hourly.map(h => {
          let d = new Date();
          d.setHours(parseInt(h.hour), 0, 0, 0);
          return Math.floor(d.getTime()/1000);
        });
        let msgs = chartData.hourly.map(h => h.msgs);
        let cards = chartData.hourly.map(h => h.cards);
        
        const legendDiv = document.createElement('div');
        legendDiv.style.textAlign = 'center';
        legendDiv.style.color = css('--text');
        legendDiv.style.minHeight = '24px';
        legendDiv.style.fontSize = '14px';
        legendDiv.style.marginBottom = '8px';
        legendDiv.innerHTML = '<span class="ui-muted">悬停查看数据</span>';
        container.appendChild(legendDiv);
    
        const chartDiv = document.createElement('div');
        container.appendChild(chartDiv);
    
        const opts = {
          width: container.clientWidth,
          height: 250,
          legend: { show: false },
          axes: [
            {
              stroke: css('--text-2'),
              grid: { stroke: css('--border-subtle'), width: 1 },
              ticks: { stroke: css('--border-subtle'), width: 1 },
              values: (u, vals) => vals.map(v => {
                let d = new Date(v * 1000);
                return `${d.getHours()}:00`;
              })
            },
            {
              stroke: css('--text-2'),
              grid: { stroke: css('--border-subtle'), width: 1 },
              ticks: { stroke: css('--border-subtle'), width: 1 },
              values: (u, vals) => vals.map(v => v >= 1000 ? (v/1000).toFixed(1) + 'k' : v)
            }
          ],
          series: [
            {},
            {
              label: "消息",
              stroke: css('--accent'),
              fill: css('--accent-ring'),
              width: 2,
              points: { show: true, fill: css('--bg'), stroke: css('--accent'), size: 6 }
            },
            {
              label: "卡片",
              stroke: css('--warn'),
              width: 2,
              points: { show: true, fill: css('--bg'), stroke: css('--warn'), size: 6 }
            }
          ],
          hooks: {
            setCursor: [
              (u) => {
                const idx = u.cursor.idx;
                if (idx === null || idx === undefined || idx < 0) {
                  legendDiv.innerHTML = '<span class="ui-muted">悬停查看数据</span>';
                  return;
                }
                let d = new Date(x[idx] * 1000);
                legendDiv.innerHTML = `${d.getHours()}:00 &middot; 消息 <strong style="color:${css('--accent')}">${msgs[idx]}</strong> &middot; 卡 <strong style="color:${css('--warn')}">${cards[idx]}</strong>`;
              }
            ]
          }
        };
        overviewChartInst = new uPlot(opts, [x, msgs, cards], chartDiv);
      } else {
        container.innerHTML = '<div class="ui-empty"><div class="desc">暂无趋势数据</div></div>';
      }
    };
    
    if (window.drawOverviewChart) window.drawOverviewChart(data);
    
  } catch(e) { console.error(e); }
}

// 2. Cards
let currentCardsPage = 1;
let currentCardsState = '';
window.renderCards = async function(page = 1, state = '') {
  currentCardsPage = page;
  currentCardsState = state;
  try {
    const res = await fetch(`/api/i/${window.INST}/cards?page=${page}&state=${state}`);
    const data = await res.json();
    
    let html = `
      <div class="ui-card">
        <div class="row mb-4">
          <select class="ui-select" onchange="renderCards(1, this.value)">
            <option value="">全部状态</option>
            <option value="open" ${state==='open'?'selected':''}>Open</option>
            <option value="answering" ${state==='answering'?'selected':''}>Answering</option>
            <option value="answered" ${state==='answered'?'selected':''}>Answered</option>
            <option value="waiting" ${state==='waiting'?'selected':''}>Waiting</option>
            <option value="stuck" ${state==='stuck'?'selected':''}>Stuck</option>
          </select>
        </div>
        <table class="ui-table">
          <thead><tr><th>编号</th><th>状态</th><th>发起人</th><th>消息数</th><th>建于</th><th>首回耗时</th><th>重派</th></tr></thead>
          <tbody>
            ${data.items.map(item => {
              let badge = '';
              if (item.state === 'answered') badge = 'ok';
              else if (item.state === 'open') badge = 'info';
              else if (item.state === 'answering') badge = 'running';
              else if (item.state === 'stuck') badge = 'danger';
              else badge = 'idle';
              
              let badgeHtml = `<span class="ui-badge ${badge}"><span class="ui-dot"></span>${item.state}</span>`;
              if(item.state === 'open') badgeHtml = `<span class="ui-badge" style="color:var(--accent-text);border-color:var(--accent)"><span class="ui-dot"></span>open</span>`;
              
              return `
              <tr style="cursor:pointer" onclick="openCardDrawer(${item.id})">
                <td class="num">#${item.id}</td>
                <td>${badgeHtml}</td>
                <td style="max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${item.sender_name}">${item.sender_name}</td>
                <td class="num">${item.n_msgs}</td>
                <td class="num">${new Date(item.created_at).toLocaleString()}</td>
                <td class="num">${item.first_reply_s !== null ? item.first_reply_s + 's' : '--'}</td>
                <td class="num">${item.redispatch}</td>
              </tr>
            `}).join('')}
          </tbody>
        </table>
        
        <div class="ui-pagination mt-4">
          <span class="info">共 ${data.total} 条 · 页 ${data.page} / ${Math.ceil(data.total / data.size) || 1}</span>
          <button class="ui-page-btn" ${data.page <= 1 ? 'disabled' : ''} onclick="renderCards(${data.page - 1}, '${state}')">‹</button>
          <button class="ui-page-btn" ${data.page * data.size >= data.total ? 'disabled' : ''} onclick="renderCards(${data.page + 1}, '${state}')">›</button>
        </div>
      </div>
      
      <!-- Drawer -->
      <aside class="ui-drawer" id="card-drawer" style="display:none; width:min(600px, 92vw);">
        <div class="head">
          <span class="title" id="cd-title">卡详情</span>
          <button class="x" onclick="document.getElementById('card-drawer').style.display='none'">×</button>
        </div>
        <div class="body" id="cd-body">加载中...</div>
        <div class="foot" id="cd-foot"></div>
      </aside>
    `;
    document.getElementById('tab-content').innerHTML = html;
  } catch(e) { console.error(e); }
}

window.openCardDrawer = async function(id) {
  const d = document.getElementById('card-drawer');
  d.style.display = 'flex';
  document.getElementById('cd-title').innerText = `卡详情 #${id}`;
  document.getElementById('cd-body').innerHTML = '<span class="ui-spinner"></span>';
  document.getElementById('cd-foot').innerHTML = '<button class="ui-btn" onclick="document.getElementById(\'card-drawer\').style.display=\'none\'">关闭</button>';
  
  try {
    const res = await fetch(`/api/i/${window.INST}/cards/${id}`);
    const data = await res.json();
    
    let html = `
      <div class="ui-desc">
        <dt>状态</dt><dd>${data.card.state}</dd>
        <dt>发起人</dt><dd>${data.card.sender_id}</dd>
      </div>
      
      <p class="sec-label mt-4 mb-4">消息</p>
      ${data.messages.map(m => `
        <div style="background:var(--surface-2); padding:10px; border-radius:var(--radius); margin-bottom:8px;">
          <div class="ui-muted mb-4" style="font-size:12px;">${m.sender_name} @ ${new Date(m.at).toLocaleString()}</div>
          <div style="white-space:pre-wrap">${m.text}</div>
          ${m.images ? m.images.map(img => `<img src="${img}" style="max-width:100%; margin-top:8px; border-radius:4px;">`).join('') : ''}
        </div>
      `).join('')}
      
      <p class="sec-label mt-4 mb-4">回复</p>
      ${data.replies.map(r => `
        <div style="background:var(--surface); border:1px solid var(--border); padding:10px; border-radius:var(--radius); margin-bottom:8px;">
          <div class="row mb-4"><span class="ui-tag accent">${r.type}</span></div>
          <div style="white-space:pre-wrap; font-weight:500;" class="mb-4">${r.summary}</div>
          <div style="background:var(--bg); border-left:3px solid var(--danger); padding:8px; font-size:12px; white-space:pre-wrap; font-weight: bold;">证据: ${r.evidence}</div>
        </div>
      `).join('')}
    `;
    
    document.getElementById('cd-body').innerHTML = html;
    
    if (data.card.state === 'stuck') {
      document.getElementById('cd-foot').innerHTML += `
        <button class="ui-btn primary" onclick="reopenCard(${id})">重开此卡</button>
      `;
    }
    
  } catch(e) { document.getElementById('cd-body').innerText = '加载失败'; }
}

window.reopenCard = async function(id) {
  await apiAction(window.INST, 'reopen-card', {card_id: id});
  document.getElementById('card-drawer').style.display='none';
  renderCards(currentCardsPage, currentCardsState);
}

// 3. Session
async function renderSession() {
  try {
    const res = await fetch(`/api/i/${window.INST}/session`);
    const data = await res.json();
    
    let html = `
      <div class="ui-card mb-4">
        <div class="ui-desc">
          <dt>当前 Session ID</dt>
          <dd class="ui-mono" style="display:flex; align-items:center; gap:8px;">
            ${data.session && data.session.session_id ? 
              `${data.session.session_id.substring(0,8)} 
               <button class="ui-btn" style="padding: 2px 6px; font-size: 11px;" onclick="navigator.clipboard.writeText('${data.session.session_id}'); showToast('已复制', '', 'ok')">复制</button>` 
              : '--'}
          </dd>
          <dt>新鲜度 (Fresh)</dt><dd>${data.session ? (data.session.fresh ? '是' : '否') : '--'}</dd>
          <dt>创建时间</dt><dd>${data.session ? new Date(data.session.created_at).toLocaleString() : '--'}</dd>
          <dt>会话累计(API 折算)</dt><dd>$${(data.total_cost_usd || 0).toFixed(2)} <span class="ui-muted" style="font-size:12px">订阅账号不按此计费,反映的是额度消耗</span></dd>
        </div>
      </div>
      
      <div class="ui-card mb-4">
        <h3 class="ui-muted mb-4">轮次上下文图表</h3>
        <div id="session-chart" class="chart-container"></div>
      </div>
      
      <div class="ui-card">
        <h3 class="ui-muted mb-4">轮换历史</h3>
        <table class="ui-table">
          <thead><tr><th>Session ID</th><th>建于</th><th>结束于</th><th>原因</th></tr></thead>
          <tbody>
            ${data.rotations && data.rotations.length > 0 ? data.rotations.map(r => `
              <tr>
                <td class="ui-mono">${r.session_id.substring(0,8)}...</td>
                <td class="num">${new Date(r.created_at).toLocaleString()}</td>
                <td class="num">${r.ended_at ? new Date(r.ended_at).toLocaleString() : '--'}</td>
                <td>${r.reason || '--'}</td>
              </tr>
            `).join('') : '<tr><td colspan="4" class="ui-muted" style="text-align:center">暂无轮换历史</td></tr>'}
          </tbody>
        </table>
      </div>
    `;
    document.getElementById('tab-content').innerHTML = html;
    
    window.drawSessionChart = function(chartData) {
      sessionChartData = chartData;
      const container = document.getElementById('session-chart');
      if (!container) return;
      container.innerHTML = '';
      if (sessionChartInst) {
        sessionChartInst.destroy();
        sessionChartInst = null;
      }
      
      if (chartData.rounds && chartData.rounds.length > 0) {
        let seqX = chartData.rounds.map((r, i) => i);
        let series_answered = [];
        let series_stuck = [];
        let series_waiting = [];
        let series_other = [];
    
        chartData.rounds.forEach(r => {
          let v = r.context_tokens;
          series_answered.push(r.outcome === 'answered' ? v : null);
          series_stuck.push(r.outcome === 'stuck' ? v : null);
          series_waiting.push(r.outcome === 'waiting' ? v : null);
          series_other.push(!['answered', 'stuck', 'waiting'].includes(r.outcome) ? v : null);
        });
        
        const legendDiv = document.createElement('div');
        legendDiv.style.textAlign = 'center';
        legendDiv.style.color = css('--text');
        legendDiv.style.minHeight = '24px';
        legendDiv.style.fontSize = '14px';
        legendDiv.style.marginBottom = '8px';
        legendDiv.innerHTML = '<span class="ui-muted">悬停查看数据</span>';
        container.appendChild(legendDiv);
    
        const chartDiv = document.createElement('div');
        container.appendChild(chartDiv);
        
        let timeFmt = (ts) => {
          let d = new Date(ts);
          return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
        };
    
        const opts = {
          width: container.clientWidth,
          height: 250,
          legend: { show: false },
          scales: {
            x: { time: false, range: [-0.5, Math.max(seqX.length - 0.5, 4.5)] },
            // 柱状图的基线必须是 0。截断基线会把 34.2 万 vs 35.7 万(差 4%)
            // 画成一根矮墩一根高柱,看着像差好几倍(2026-09-21 截图时发现)。
            y: { range: (u, min, max) => [0, (max || 1) * 1.15] }
          },
          axes: [
            {
              stroke: css('--text-2'),
              grid: { show: false },
              ticks: { stroke: css('--border-subtle'), width: 1 },
              values: (u, vals) => vals.map(v => {
                if (v !== Math.floor(v)) return ''; // 绝不出现小数
                if (v >= 0 && v < chartData.rounds.length) {
                  return timeFmt(chartData.rounds[v].started_at); // 标签用 HH:MM
                }
                return '';
              })
            },
            {
              stroke: css('--text-2'),
              grid: { stroke: css('--border-subtle'), width: 1 },
              ticks: { stroke: css('--border-subtle'), width: 1 },
              values: (u, vals) => vals.map(v => v >= 1000 ? (v/1000).toFixed(1) + 'k' : v)
            }
          ],
          series: [
            {},
            { label: "answered", stroke: css('--accent'), fill: css('--accent'), paths: uPlot.paths.bars ? uPlot.paths.bars({ size: [0.6, 60] }) : null },
            { label: "stuck", stroke: css('--danger'), fill: css('--danger'), paths: uPlot.paths.bars ? uPlot.paths.bars({ size: [0.6, 60] }) : null },
            { label: "waiting", stroke: css('--warn'), fill: css('--warn'), paths: uPlot.paths.bars ? uPlot.paths.bars({ size: [0.6, 60] }) : null },
            { label: "other", stroke: css('--idle'), fill: css('--idle'), paths: uPlot.paths.bars ? uPlot.paths.bars({ size: [0.6, 60] }) : null }
          ],
          hooks: {
            setCursor: [
              (u) => {
                const idx = u.cursor.idx;
                if (idx === null || idx === undefined || idx < 0 || idx >= chartData.rounds.length) {
                  legendDiv.innerHTML = '<span class="ui-muted">悬停查看数据</span>';
                  return;
                }
                let r = chartData.rounds[idx];
                let ctx = r.context_tokens >= 1000 ? (r.context_tokens/1000).toFixed(1) + 'k' : r.context_tokens;
                legendDiv.innerHTML = `卡 #${r.issue_id} &middot; ${timeFmt(r.started_at)} &middot; 上下文 ${ctx} &middot; 输出 ${r.output_tokens} &middot; ${r.cost_usd == null ? '成本未知' : '$' + r.cost_usd.toFixed(2)} &middot; <strong style="color:${css('--text')}">${r.outcome}</strong>`;
              }
            ]
          }
        };
        
        sessionChartInst = new uPlot(opts, [seqX, series_answered, series_stuck, series_waiting, series_other], chartDiv);
        
        sessionChartInst.root.addEventListener('click', (e) => {
          const idx = sessionChartInst.cursor.idx;
          if (idx != null && idx >= 0 && idx < chartData.rounds.length) {
            const issueId = chartData.rounds[idx].issue_id;
            if (issueId) {
               switchTab('cards');
               setTimeout(() => openCardDrawer(issueId), 500);
            }
          }
        });
      } else {
        container.innerHTML = '<div class="ui-empty"><div class="desc">暂无轮次数据</div></div>';
      }
    };
    
    if (window.drawSessionChart) window.drawSessionChart(data);
  } catch (e) { console.error(e); }
}

// 4. Logs
let sseConn = null;
let logLines = [];
let lastLogAt = 0;
let currentStream = 'listener';

window.renderLogsUI = function() {
  let html = `
    <div class="ui-card">
      <div class="row mb-4">
        <select class="ui-select" id="log-stream-sel" onchange="startLogs(this.value, true)">
          ${window.STREAMS.map(s => `<option value="${s}">${s}</option>`).join('')}
        </select>
        <input class="ui-input" id="log-q" placeholder="过滤关键字..." onchange="startLogs(document.getElementById('log-stream-sel').value, true)">
        <label class="ui-switch" style="margin-left:auto"><input type="checkbox" id="log-scroll-lock"><span class="track"></span>暂停滚动</label>
        <button class="ui-btn" onclick="document.getElementById('log-container').innerHTML=''; logLines=[];">清屏</button>
      </div>
      
      <div id="log-container" style="background:var(--bg); border:1px solid var(--border); border-radius:var(--radius);"></div>
    </div>
  `;
  document.getElementById('tab-content').innerHTML = html;
  startLogs('listener', true);
}

function closeSse() {
  if (sseConn) {
    sseConn.close();
    sseConn = null;
  }
}

window.startLogs = function(stream, reset = false) {
  closeSse();
  if (reset) {
    logLines = [];
    document.getElementById('log-container').innerHTML = '';
    lastLogAt = 0;
  }
  currentStream = stream;
  
  const q = document.getElementById('log-q').value;
  let url = `/api/i/${window.INST}/logs/${stream}?since=${lastLogAt}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;
  
  sseConn = new EventSource(url);
  sseConn.addEventListener('log', function(e) {
    const data = JSON.parse(e.data);
    lastLogAt = data.at;
    appendLog(data);
  });
  
  sseConn.onerror = function() {
    closeSse();
    setTimeout(() => { if (currentTab === 'logs' && currentStream === stream) startLogs(stream, false); }, 2000);
  };
}

function appendLog(data) {
  logLines.push(data);
  if (logLines.length > 500) logLines.shift();
  
  const container = document.getElementById('log-container');
  if (!container) return; // switched tab
  
  // 本地时区。用 toISOString 会显示 UTC,和日志正文里的北京时间差 8 小时
  const d = new Date(data.at);
  const at = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
           + `:${String(d.getSeconds()).padStart(2,'0')}.${String(d.getMilliseconds()).padStart(3,'0')}`;
  
  const div = document.createElement('div');
  div.className = 'log-line';
  div.innerHTML = `
    <span class="log-time">${at}</span>
    <span class="log-level ${data.level}">${data.level}</span>
    <span class="log-text">${data.text}</span>
  `;
  container.appendChild(div);
  
  if (container.children.length > 500) {
    container.removeChild(container.firstChild);
  }
  
  if (!document.getElementById('log-scroll-lock').checked) {
    container.scrollTop = container.scrollHeight;
  }
}

// 5. Config
async function renderConfig() {
  try {
    const res = await fetch(`/api/i/${window.INST}/config`);
    const data = await res.json();
    
    let html = `
      <div class="ui-card">
        <form id="config-form" onsubmit="submitConfig(event)">
          <div class="grid-2" style="gap:24px;">
            ${data.fields.map(f => {
              let val = data.values[f.name];
              let inputHtml = '';
              
              if (f.type === 'bool') {
                inputHtml = `<label class="ui-switch"><input type="checkbox" name="${f.name}" ${val ? 'checked' : ''}><span class="track"></span></label>`;
              } else if (f.type === 'list') {
                val = Array.isArray(val) ? val.join('\\n') : '';
                inputHtml = `<textarea name="${f.name}" class="ui-textarea" rows="3" placeholder="每行一个">${val}</textarea>`;
              } else if (f.type === 'int' && (val === 0 || val === '0')) {
                val = '0';                 // 0 是有意义的值(关闭阈值),别渲染成空
              } else if (f.type === 'table') {
                val = typeof val === 'object' ? JSON.stringify(val, null, 2) : '';
                inputHtml = `<textarea name="${f.name}" class="ui-textarea" rows="4" placeholder="JSON格式字典">${val}</textarea>`;
              } else {
                if (f.secret) {
                  inputHtml = `
                    <div class="row">
                      <input name="${f.name}" type="text" class="ui-input" value="${val || ''}" style="flex:1">
                    </div>
                  `;
                } else {
                  inputHtml = `<input name="${f.name}" type="text" class="ui-input" value="${val || ''}" style="width:100%">`;
                }
              }
              
              return `
                <div class="field" style="${f.type === 'table' || f.type === 'list' ? 'grid-column:1/-1' : ''}">
                  <label>${f.name} <span class="ui-muted" style="font-weight:normal"> - ${f.desc}</span></label>
                  ${inputHtml}
                  <div class="err-msg" id="err-${f.name}" style="color:var(--danger); font-size:12px;"></div>
                </div>
              `;
            }).join('')}
          </div>
          <div class="row mt-4 pt-4" style="border-top: 1px solid var(--border); justify-content: flex-end;">
            <button type="submit" class="ui-btn primary">保存配置</button>
          </div>
        </form>
      </div>
    `;
    document.getElementById('tab-content').innerHTML = html;
    
  } catch(e) { console.error(e); }
}

window.submitConfig = async function(e) {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData();
  
  const inputs = form.querySelectorAll('input, textarea, select');
  inputs.forEach(i => {
    if (i.type === 'checkbox') {
      fd.append(i.name, i.checked ? 'true' : 'false');
    } else {
      fd.append(i.name, i.value);
    }
  });
  
  fd.append('csrf', window.CSRF_TOKEN);
  
  document.querySelectorAll('.err-msg').forEach(el => el.innerText = '');
  
  const res = await fetch(`/api/i/${window.INST}/config`, { method: 'POST', body: fd });
  const json = await res.json();
  
  if (res.ok) {
    if (json.needs_restart && json.needs_restart.length > 0) {
      showToast('保存成功', '部分配置需要重启生效', 'warn');
      const t = document.createElement('div');
      t.className = 'ui-toast warn';
      t.innerHTML = `
        <div class="t-body">
          <div class="t-title">需要重启</div>
          <div class="t-desc">需要重启以下组件: ${json.needs_restart.join(', ')}</div>
          <button class="ui-btn mt-4" onclick="apiAction('${window.INST}', 'restart-listener'); this.parentElement.parentElement.remove()">立刻重启 Listener</button>
        </div>
        <button class="t-close" onclick="this.parentElement.remove()">×</button>
      `;
      document.getElementById('toast-container').appendChild(t);
    } else {
      showToast('保存成功', '', 'ok');
    }
  } else {
    showToast('保存失败', '请检查配置项', 'danger');
    if (json.errors) {
      for (const k in json.errors) {
        const el = document.getElementById('err-' + k);
        if (el) el.innerText = json.errors[k];
      }
    }
  }
}
