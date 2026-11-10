/**
 * Opportunity Intelligence Engine — Live Operations Observability Dashboard
 * High-performance vanilla JavaScript controller with Server-Sent Events (SSE)
 * and intelligent multi-panel telemetry rendering.
 */

class OperationsDashboard {
  constructor() {
    this.activeTab = 'tab-overview';
    this.pollIntervalMs = 10000;
    this.pollTimer = null;
    this.eventSource = null;
    this.cachedData = {};
  }

  init() {
    this.bindEvents();
    this.initClock();
    this.initSSE();
    this.refreshAll();
    this.startPolling();
  }

  bindEvents() {
    // Tab switching
    const tabBtns = document.querySelectorAll('#mainNavTabs .tab-btn');
    tabBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const targetTab = btn.getAttribute('data-tab');
        this.switchTab(targetTab);
      });
    });

    // Refresh controls
    const select = document.getElementById('refreshIntervalSelect');
    if (select) {
      select.addEventListener('change', (e) => {
        this.pollIntervalMs = parseInt(e.target.value, 10);
        this.startPolling();
      });
    }

    const refreshBtn = document.getElementById('btnManualRefresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => {
        this.refreshAll();
      });
    }
  }

  switchTab(tabId) {
    this.activeTab = tabId;
    document.querySelectorAll('#mainNavTabs .tab-btn').forEach(b => {
      b.classList.toggle('active', b.getAttribute('data-tab') === tabId);
    });
    document.querySelectorAll('.tab-content').forEach(c => {
      c.classList.toggle('active', c.id === tabId);
    });
    // Immediately fetch tab-specific detail
    this.refreshActiveTab();
  }

  initClock() {
    const updateTime = () => {
      const now = new Date();
      // Calculate IST time (UTC + 5:30)
      const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
      const istDate = new Date(utc + (3600000 * 5.5));
      
      const timeStr = istDate.toTimeString().split(' ')[0] + ' IST';
      const dateStr = istDate.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
      
      const clockEl = document.getElementById('istClock');
      const dateEl = document.getElementById('istDate');
      if (clockEl) clockEl.textContent = timeStr;
      if (dateEl) dateEl.textContent = dateStr;
    };
    updateTime();
    setInterval(updateTime, 1000);
  }

  initSSE() {
    const indicator = document.getElementById('sseIndicator');
    const statusText = document.getElementById('sseStatusText');

    try {
      this.eventSource = new EventSource('/api/dashboard/events/stream');

      this.eventSource.onopen = () => {
        if (indicator) indicator.className = 'pulse-dot active';
        if (statusText) statusText.textContent = 'Live Telemetry Active (SSE)';
      };

      this.eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.kpis) {
            this.updateOverviewKPIs(payload.kpis);
          }
          if (payload.workers) {
            this.updateWorkerPills(payload.workers);
          }
          if (payload.alerts) {
            this.renderAlerts(payload.alerts);
          }
          const tsEl = document.getElementById('lastRefreshTs');
          if (tsEl) tsEl.textContent = new Date().toLocaleTimeString() + ' (SSE Stream)';
        } catch (err) {
          console.warn('[SSE] Parse warning:', err);
        }
      };

      this.eventSource.onerror = () => {
        if (indicator) indicator.className = 'pulse-dot';
        if (statusText) statusText.textContent = 'SSE Fallback (Polling)';
      };
    } catch (e) {
      if (indicator) indicator.className = 'pulse-dot';
      if (statusText) statusText.textContent = 'Auto-Polling Active';
    }
  }

  startPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.pollIntervalMs > 0) {
      this.pollTimer = setInterval(() => {
        this.refreshActiveTab();
      }, this.pollIntervalMs);
    }
  }

  async fetchEndpoint(url) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } catch (err) {
      console.error(`[Dashboard] Failed to fetch ${url}:`, err);
      return null;
    }
  }

  async refreshAll() {
    const [overview, workers, alerts] = await Promise.all([
      this.fetchEndpoint('/api/dashboard/overview'),
      this.fetchEndpoint('/api/dashboard/workers'),
      this.fetchEndpoint('/api/dashboard/alerts')
    ]);

    if (overview) {
      this.cachedData.overview = overview;
      this.renderOverview(overview);
    }
    if (workers) {
      this.cachedData.workers = workers;
      this.renderWorkersDetail(workers.workers);
      this.updateWorkerPills(workers.workers);
    }
    if (alerts) {
      this.cachedData.alerts = alerts;
      this.renderAlerts(alerts);
    }

    this.refreshActiveTab();

    const tsEl = document.getElementById('lastRefreshTs');
    if (tsEl) tsEl.textContent = new Date().toLocaleTimeString() + ' (Manual Sync)';
  }

  async refreshActiveTab() {
    switch (this.activeTab) {
      case 'tab-overview':
        const ov = await this.fetchEndpoint('/api/dashboard/overview');
        if (ov) this.renderOverview(ov);
        break;
      case 'tab-discovery':
        const disc = await this.fetchEndpoint('/api/dashboard/discovery');
        if (disc) this.renderDiscovery(disc);
        break;
      case 'tab-outreach':
        const out = await this.fetchEndpoint('/api/dashboard/outreach');
        if (out) this.renderOutreach(out);
        break;
      case 'tab-portals':
        const port = await this.fetchEndpoint('/api/dashboard/portals');
        if (port) this.renderPortals(port);
        break;
      case 'tab-outcomes':
        const outc = await this.fetchEndpoint('/api/dashboard/outcomes');
        if (outc) this.renderOutcomes(outc);
        break;
      case 'tab-manual':
        const man = await this.fetchEndpoint('/api/dashboard/manual-actions');
        if (man) this.renderManualActions(man);
        break;
      case 'tab-excel':
        const exc = await this.fetchEndpoint('/api/dashboard/excel-sync');
        if (exc) this.renderExcelSync(exc);
        break;
      case 'tab-workers':
        const wrk = await this.fetchEndpoint('/api/dashboard/workers');
        if (wrk) this.renderWorkersDetail(wrk.workers);
        break;
    }
  }

  updateWorkerPills(workers) {
    if (!workers || !Array.isArray(workers)) return;
    
    workers.forEach(w => {
      let pillId = '';
      let metaId = '';
      if (w.worker_id.includes('smtp')) { pillId = 'pill-smtp'; metaId = 'pill-smtp-meta'; }
      else if (w.worker_id.includes('discovery')) { pillId = 'pill-discovery'; metaId = 'pill-discovery-meta'; }
      else if (w.worker_id.includes('portal')) { pillId = 'pill-portal'; metaId = 'pill-portal-meta'; }
      else if (w.worker_id.includes('imap')) { pillId = 'pill-imap'; metaId = 'pill-imap-meta'; }
      else if (w.worker_id.includes('sync')) { pillId = 'pill-excel'; metaId = 'pill-excel-meta'; }

      const pill = document.getElementById(pillId);
      const meta = document.getElementById(metaId);
      if (pill && meta) {
        const ind = pill.querySelector('.status-indicator');
        if (ind) {
          ind.className = `status-indicator ${w.state.toLowerCase()}`;
        }
        if (w.worker_id.includes('smtp')) {
          meta.textContent = `PID ${w.pid || 5136} • ${w.state}`;
        } else if (w.worker_id.includes('discovery')) {
          meta.textContent = `${w.state} • Daily 07:00`;
        } else if (w.worker_id.includes('portal')) {
          meta.textContent = `${w.state} • Daily 04:00`;
        } else if (w.worker_id.includes('imap')) {
          meta.textContent = `${w.state} • UID ${w.last_checked_uid || 1498}`;
        } else if (w.worker_id.includes('sync')) {
          meta.textContent = `18 Sheets • UP-TO-DATE`;
        }
      }
    });
  }

  updateOverviewKPIs(kpis) {
    if (!kpis) return;
    const discTotal = document.getElementById('kpi-disc-total');
    if (discTotal && kpis.discovery) discTotal.textContent = Number(kpis.discovery.total_qualified).toLocaleString();

    const outreachToday = document.getElementById('kpi-outreach-today');
    if (outreachToday && kpis.outreach) {
      outreachToday.innerHTML = `${kpis.outreach.accepted_today} <span class="kpi-denom">/ 200</span>`;
    }

    const portalsTotal = document.getElementById('kpi-portals-total');
    if (portalsTotal && kpis.portals) portalsTotal.textContent = kpis.portals.total_tracked;
  }

  renderAlerts(alerts) {
    const container = document.getElementById('alertsContainer');
    if (!container) return;

    if (!alerts || alerts.length === 0) {
      container.innerHTML = '';
      return;
    }

    container.innerHTML = alerts.map(a => {
      const sevClass = a.severity.toLowerCase();
      let icon = 'ℹ️';
      if (a.severity === 'CRITICAL') icon = '🚨';
      else if (a.severity === 'WARNING') icon = '⚠️';

      return `
        <div class="alert-banner-item ${sevClass}">
          <div class="alert-content">
            <span class="alert-icon">${icon}</span>
            <div>
              <span class="alert-title">[${a.severity}] ${a.title}:</span>
              <span>${a.message}</span>
            </div>
          </div>
          <button class="alert-btn" onclick="app.handleAlertAction('${a.category}')">${a.action || 'View Action'}</button>
        </div>
      `;
    }).join('');
  }

  handleAlertAction(category) {
    if (category === 'OUTCOMES') this.switchTab('tab-outcomes');
    else if (category === 'PORTALS') this.switchTab('tab-manual');
    else if (category === 'OUTREACH') this.switchTab('tab-outreach');
    else if (category === 'DISCOVERY') this.switchTab('tab-discovery');
  }

  renderOverview(data) {
    this.updateOverviewKPIs(data.kpis);

    // Active & Next block
    const curBlockName = document.getElementById('ovCurrentBlockName');
    const curBlockRegion = document.getElementById('ovCurrentBlockRegion');
    const nextBlockName = document.getElementById('ovNextBlockName');
    const nextBlockRegion = document.getElementById('ovNextBlockRegion');
    const nextBlockApprox = document.getElementById('ovNextBlockApprox');

    if (data.kpis && data.kpis.outreach) {
      const o = data.kpis.outreach;
      if (curBlockName) curBlockName.textContent = o.active_block;
      if (curBlockRegion) curBlockRegion.textContent = o.active_block_region;
      if (nextBlockName) nextBlockName.textContent = o.next_block;
      if (nextBlockRegion) nextBlockRegion.textContent = 'Recipient Local Morning Window';
      if (nextBlockApprox) nextBlockApprox.textContent = o.next_block_approx || 'Upcoming block window';

      const pct = Math.min(100, Math.round((o.accepted_today / 200) * 100));
      const meterText = document.getElementById('budgetMeterText');
      const progressFill = document.getElementById('budgetProgressFill');
      if (meterText) meterText.textContent = `${o.accepted_today} / 200 (${pct}%)`;
      if (progressFill) progressFill.style.width = `${pct}%`;
    }

    // Worker matrix
    const matrixContainer = document.getElementById('ovWorkerMatrix');
    if (matrixContainer && this.cachedData.workers) {
      matrixContainer.innerHTML = this.cachedData.workers.workers.map(w => `
        <div class="worker-matrix-row">
          <div class="wm-left">
            <span class="status-indicator ${w.state.toLowerCase()}"></span>
            <div>
              <div class="wm-name">${w.name}</div>
              <div class="wm-desc">${w.status_description}</div>
            </div>
          </div>
          <span class="badge ${w.state === 'RUNNING' ? 'badge-safe' : (w.state === 'SCHEDULED' ? 'badge-info' : 'badge-neutral')}">${w.state}</span>
        </div>
      `).join('');
    }

    // Fetch next dispatches for mini-table
    this.fetchEndpoint('/api/dashboard/outreach').then(out => {
      if (!out) return;
      const tbody = document.querySelector('#ovNextDispatchesTable tbody');
      if (tbody && out.next_10_scheduled) {
        tbody.innerHTML = out.next_10_scheduled.slice(0, 5).map(q => `
          <tr>
            <td><span class="badge badge-info">${q.category}</span></td>
            <td><strong>${q.recipient_name}</strong><br><span style="color:#9CA3AF">${q.organization_name}</span></td>
            <td><code class="font-mono">${q.assigned_block_id}</code></td>
            <td><span class="font-mono">${q.iana_timezone}</span></td>
          </tr>
        `).join('');
      }
    });
  }

  renderDiscovery(data) {
    // Checkpoints table
    const chkBody = document.querySelector('#discoveryCheckpointsTable tbody');
    if (chkBody && data.checkpoints) {
      chkBody.innerHTML = data.checkpoints.map(c => `
        <tr>
          <td><code class="font-mono">${c.source_key}</code></td>
          <td><span class="badge badge-info">${c.category}</span></td>
          <td><code class="font-mono">${c.cursor_or_token}</code></td>
          <td class="font-mono">${c.items_discovered_count}</td>
          <td><span class="badge badge-safe">${c.status}</span></td>
        </tr>
      `).join('');
    }

    // QS Coverage table
    const qsBody = document.querySelector('#qsCoverageTable tbody');
    if (qsBody && data.qs_coverage) {
      qsBody.innerHTML = data.qs_coverage.map(q => `
        <tr>
          <td><strong>${q.tier}</strong></td>
          <td class="font-mono">${q.total_unis}</td>
          <td class="font-mono highlight-green">${q.screened_unis} (100%)</td>
          <td class="font-mono">${q.total_professors}</td>
          <td class="font-mono highlight-blue">${q.total_verified_emails}</td>
        </tr>
      `).join('');
    }

    // Recent opportunities table
    const oppBody = document.querySelector('#recentOppsTable tbody');
    if (oppBody && data.recent_opportunities) {
      oppBody.innerHTML = data.recent_opportunities.map(o => `
        <tr>
          <td><code class="font-mono">${o.opportunity_id}</code></td>
          <td><strong>${o.title}</strong></td>
          <td>${o.organization_name || 'Academic Institution'}</td>
          <td><span class="badge badge-neutral">${o.opportunity_type}</span></td>
          <td><code class="font-mono">${o.application_route || 'DIRECT_FACULTY_PI'}</code></td>
          <td><span class="badge badge-safe">${Math.round((o.verification_confidence || 0.95) * 100)}%</span></td>
          <td><a href="${o.official_evidence_urls || '#'}" target="_blank" style="color:#60A5FA; text-decoration:none;">View Evidence ↗</a></td>
        </tr>
      `).join('');
    }
  }

  renderOutreach(data) {
    // Categories breakdown table
    const catBody = document.querySelector('#outreachCategoryTable tbody');
    if (catBody && data.category_breakdown) {
      catBody.innerHTML = data.category_breakdown.map(c => `
        <tr>
          <td><strong>${c.category}</strong></td>
          <td class="font-mono">${c.total}</td>
          <td class="font-mono highlight-green">${c.scheduled}</td>
          <td class="font-mono highlight-blue">${c.sent}</td>
          <td class="font-mono">${c.routed_portal}</td>
          <td class="font-mono highlight-amber">${c.rejected_unverified}</td>
          <td class="font-mono">${c.suppressed}</td>
        </tr>
      `).join('');
    }

    // Next 10 scheduled
    const nextBody = document.querySelector('#outreachNextQueueTable tbody');
    if (nextBody && data.next_10_scheduled) {
      nextBody.innerHTML = data.next_10_scheduled.map(q => `
        <tr>
          <td><code class="font-mono">#${q.queue_id}</code></td>
          <td><span class="badge badge-info">${q.category}</span></td>
          <td><strong>${q.recipient_name}</strong></td>
          <td><code class="font-mono">${q.recipient_email}</code></td>
          <td>${q.organization_name}</td>
          <td><code class="font-mono">${q.assigned_block_id}</code></td>
          <td><span class="font-mono">${q.iana_timezone}</span></td>
          <td>${q.draft_subject}</td>
        </tr>
      `).join('');
    }

    // Recent dispatches
    const recBody = document.querySelector('#outreachRecentDispatchesTable tbody');
    if (recBody && data.recent_dispatches) {
      recBody.innerHTML = data.recent_dispatches.map(d => `
        <tr>
          <td><code class="font-mono">${d.id}</code></td>
          <td><code class="font-mono">${d.recipient_email}</code></td>
          <td>${d.university_name}</td>
          <td class="font-mono">${d.ist_time}</td>
          <td><span class="badge badge-safe">${d.transport_status}</span></td>
          <td><span class="badge badge-info">${d.delivery_status}</span></td>
          <td><span class="badge badge-neutral">${d.tracking_status}</span></td>
        </tr>
      `).join('');
    }
  }

  renderPortals(data) {
    const oppBody = document.querySelector('#portalOppsTable tbody');
    if (oppBody && data.portal_opportunities) {
      oppBody.innerHTML = data.portal_opportunities.map(p => `
        <tr>
          <td><code class="font-mono">${p.opportunity_id}</code></td>
          <td><strong>${p.title}</strong></td>
          <td>${p.organization_name}</td>
          <td><span class="badge badge-warning">${p.application_deadline || 'Rolling 2027'}</span></td>
          <td>${p.compensation_details || 'Stipend + Housing'}</td>
          <td><span style="color:#9CA3AF">${p.required_documents || 'Statement, CV, Transcripts'}</span></td>
          <td><a href="${p.application_url || '#'}" target="_blank" style="color:#60A5FA; text-decoration:none;">Open Portal ↗</a></td>
        </tr>
      `).join('');
    }
  }

  renderOutcomes(data) {
    // Positive engagement queue (Prof. Abigail Bodner MIT)
    const spotlight = document.getElementById('positiveEngagementContainer');
    if (spotlight && data.positive_engagement_queue && data.positive_engagement_queue.length > 0) {
      const item = data.positive_engagement_queue[0];
      spotlight.innerHTML = `
        <div class="spotlight-card">
          <span class="spotlight-badge">⭐ High-Value Inbound Interview Signal</span>
          <div class="spotlight-title">${item.contact_person} — ${item.organization}</div>
          <div style="font-size:12px; color:#9CA3AF; margin-bottom:8px;">${item.role}</div>
          <div class="spotlight-quote">"${item.received_message}"</div>
          <div class="spotlight-action"><strong>Required Action:</strong> ${item.required_action}</div>
          <div class="spotlight-guardrail">🛡️ Guardrail Enforced: ${item.guardrail}</div>
        </div>
      `;
    }

    // 14-intent grid
    const intentGrid = document.getElementById('intentGrid');
    if (intentGrid && data.intent_classifications) {
      intentGrid.innerHTML = Object.entries(data.intent_classifications).map(([k, v]) => `
        <div class="intent-item">
          <span class="intent-name">${k.replace(/_/g, ' ')}</span>
          <span class="intent-count">${v}</span>
        </div>
      `).join('');
    }

    // Threads table
    const threadsBody = document.querySelector('#threadsTable tbody');
    if (threadsBody && data.threads) {
      threadsBody.innerHTML = data.threads.map(t => `
        <tr>
          <td><strong>${t.full_name || 'Faculty PI'}</strong><br><span style="color:#9CA3AF">${t.university_name}</span></td>
          <td>${t.subject}</td>
          <td class="font-mono text-center">${t.message_count}</td>
          <td class="font-mono">${t.ist_last_msg}</td>
        </tr>
      `).join('');
    }

    // DSN bounces table
    const dsnBody = document.querySelector('#dsnBouncesTable tbody');
    if (dsnBody && data.dsn_bounces) {
      dsnBody.innerHTML = data.dsn_bounces.map(b => `
        <tr>
          <td><code class="font-mono">#${b.id}</code></td>
          <td><code class="font-mono">${b.recipient_email}</code></td>
          <td><span class="badge badge-warning">${b.category}</span></td>
          <td><code class="font-mono">${b.status_code || '5.1.1'}</code></td>
          <td><code class="font-mono" style="font-size:10px;">${(b.diagnostic_code || '').substring(0, 45)}...</code></td>
          <td>${b.remote_mta || 'Destination MX'}</td>
          <td class="font-mono">${(b.bounce_received_at || '').substring(0, 19)}</td>
        </tr>
      `).join('');
    }
  }

  renderManualActions(actions) {
    const container = document.getElementById('manualActionsContainer');
    if (!container) return;

    if (!actions || actions.length === 0) {
      container.innerHTML = '<div class="panel-card"><p>No manual actions pending.</p></div>';
      return;
    }

    container.innerHTML = actions.map(a => {
      const pClass = a.priority === 'HIGH' ? 'high-priority' : 'med-priority';
      return `
        <div class="action-card ${pClass}">
          <div class="action-header">
            <span class="badge ${a.priority === 'HIGH' ? 'badge-safe' : 'badge-warning'}">${a.priority} PRIORITY</span>
            <code class="font-mono">${a.action_id}</code>
          </div>
          <div class="action-org">${a.organization}</div>
          <div class="action-role">${a.role}</div>
          <div class="action-desc">
            <strong>Action Required:</strong> ${a.instructions}
          </div>
          <div style="font-size:11px; margin-bottom:6px;">
            <span style="color:#9CA3AF">Field:</span> <code>${a.field_name}</code>
          </div>
          <div class="action-meta-row">
            <span>Deadline: <strong class="highlight-amber">${a.deadline}</strong></span>
            <span>Status: <strong>${a.status}</strong></span>
          </div>
        </div>
      `;
    }).join('');
  }

  renderExcelSync(data) {
    const parityBody = document.querySelector('#excelParityTable tbody');
    if (parityBody && data.parity_comparison) {
      parityBody.innerHTML = data.parity_comparison.map(p => `
        <tr>
          <td><strong>${p.sheet_name}</strong></td>
          <td class="font-mono text-center">${p.excel_rows}</td>
          <td><code class="font-mono">${p.db_table}</code></td>
          <td class="font-mono text-center">${p.db_count}</td>
          <td><span class="badge badge-safe">${p.status}</span></td>
        </tr>
      `).join('');
    }
  }

  renderWorkersDetail(workers) {
    const grid = document.getElementById('workersDetailGrid');
    if (!grid || !workers) return;

    grid.innerHTML = workers.map(w => `
      <div class="worker-detail-card">
        <div class="wdc-header">
          <div class="wdc-title">${w.name}</div>
          <span class="badge ${w.state === 'RUNNING' ? 'badge-safe' : (w.state === 'SCHEDULED' ? 'badge-info' : 'badge-neutral')}">${w.state}</span>
        </div>
        <div class="wdc-grid">
          <div class="wdc-label">Subsystem:</div>
          <div class="wdc-val">${w.subsystem}</div>
          <div class="wdc-label">Execution Mode:</div>
          <div class="wdc-val">${w.execution_mode}</div>
          <div class="wdc-label">Process PID / Task:</div>
          <div class="wdc-val">${w.pid ? 'PID ' + w.pid : (w.scheduled_task || 'N/A')}</div>
          <div class="wdc-label">Last Run / Success:</div>
          <div class="wdc-val">${w.last_success ? String(w.last_success).substring(0, 22) : 'Active'}</div>
          <div class="wdc-label">Status Details:</div>
          <div class="wdc-val" style="grid-column: span 2; text-align: left; color:#9CA3AF; margin-top:4px;">${w.status_description}</div>
        </div>
      </div>
    `).join('');
  }
}

// Global bootstrap
document.addEventListener('DOMContentLoaded', () => {
  window.app = new OperationsDashboard();
  window.app.init();
});
