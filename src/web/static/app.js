/**
 * Outreach Intelligence Engine - Operator Dashboard Frontend Client
 */

let currentProfessors = [];
let selectedReplyDraft = null;
let currentDraftProfId = null;

document.addEventListener("DOMContentLoaded", () => {
  initClock();
  initTabs();
  loadMetrics();
  loadProfessors();
  loadTimeBlocks();
  loadReplies();
  loadFollowups();
  loadSuppressions();
  loadAuditLogs();
  initEventHandlers();
});

// -----------------------------------------------------------------------------
// Live IST Clock
// -----------------------------------------------------------------------------
function initClock() {
  const clockEl = document.getElementById("istClock");
  function update() {
    const now = new Date();
    // Format to Asia/Kolkata
    const options = {
      timeZone: "Asia/Kolkata",
      hour12: true,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    };
    clockEl.textContent = new Intl.DateTimeFormat("en-US", options).format(now) + " IST";
  }
  update();
  setInterval(update, 1000);
}

// -----------------------------------------------------------------------------
// Tabs Navigation
// -----------------------------------------------------------------------------
function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      document.getElementById(targetId).classList.add("active");

      // Auto-refresh data on tab switch
      if (targetId === "tab-prospects") loadProfessors();
      if (targetId === "tab-scheduler") loadTimeBlocks();
      if (targetId === "tab-replies") loadReplies();
      if (targetId === "tab-followups") loadFollowups();
      if (targetId === "tab-suppression") loadSuppressions();
      if (targetId === "tab-diagnostics") loadAuditLogs();
    });
  });
}

// -----------------------------------------------------------------------------
// API Calls & Data Loading
// -----------------------------------------------------------------------------
async function loadMetrics() {
  try {
    const res = await fetch("/api/metrics");
    const data = await res.json();

    document.getElementById("mTotalProspects").textContent = data.total_prospects.toLocaleString();
    document.getElementById("mVerifiedEmails").textContent = data.verified_emails.toLocaleString();
    document.getElementById("mTotalUniversities").textContent = data.total_universities.toLocaleString();
    document.getElementById("mDraftsReady").textContent = (data.drafts_awaiting_review + data.drafts_approved).toLocaleString();
    document.getElementById("mRepliesCount").textContent = (data.reply_drafts_pending || 0).toLocaleString();
    document.getElementById("mSuppressedCount").textContent = data.suppressed_count.toLocaleString();
    document.getElementById("replyPillCount").textContent = data.reply_drafts_pending || 0;

    // Badges
    const emBadge = document.getElementById("emergencyBadge");
    if (data.emergency_stop) {
      emBadge.className = "badge badge-danger";
      emBadge.textContent = "Emergency Stop: ACTIVE";
    } else {
      emBadge.className = "badge badge-safe";
      emBadge.textContent = "Emergency Stop: INACTIVE";
    }

    const gwBadge = document.getElementById("gatewayBadge");
    if (data.gateway_enabled) {
      gwBadge.className = "badge badge-safe";
      gwBadge.textContent = "Sending Gateway: ENABLED";
    } else {
      gwBadge.className = "badge badge-warning";
      gwBadge.textContent = "Sending Gateway: OFF (Dry-Run)";
    }
  } catch (err) {
    console.error("Failed to load metrics:", err);
  }
}

async function loadProfessors() {
  const tbody = document.getElementById("profsTableBody");
  tbody.innerHTML = '<tr><td colspan="8" class="text-center">Loading faculty prospects...</td></tr>';

  const search = document.getElementById("profSearch").value;
  const country = document.getElementById("countryFilter").value;
  const status = document.getElementById("statusFilter").value;

  let url = `/api/professors?limit=100`;
  if (country) url += `&country=${encodeURIComponent(country)}`;
  if (status) url += `&status=${encodeURIComponent(status)}`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    currentProfessors = data.professors;

    // Populate country filter if not already populated
    const cFilter = document.getElementById("countryFilter");
    if (cFilter.options.length <= 1) {
      const countries = [...new Set(currentProfessors.map(p => p.country).filter(Boolean))].sort();
      countries.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        cFilter.appendChild(opt);
      });
    }

    renderProfessorsTable(currentProfessors);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center text-danger">Error loading professors: ${err}</td></tr>`;
  }
}

function renderProfessorsTable(profs) {
  const tbody = document.getElementById("profsTableBody");
  const filterText = document.getElementById("profSearch").value.toLowerCase();

  const filtered = profs.filter(p => {
    if (!filterText) return true;
    return (
      (p.full_name && p.full_name.toLowerCase().includes(filterText)) ||
      (p.university_name && p.university_name.toLowerCase().includes(filterText)) ||
      (p.research_areas && p.research_areas.toLowerCase().includes(filterText))
    );
  });

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center">No matching faculty found.</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map(p => {
    const isSupp = p.outreach_status === "SUPPRESSED";
    const statusClass = isSupp ? "badge-danger" : p.outreach_status === "APPROVED" ? "badge-safe" : "badge-neutral";
    const rankBadge = p.qs_rank ? `<span class="badge badge-neutral">#${p.qs_rank}</span>` : "-";
    const emailBadge = p.email ? `<span style="font-family:var(--font-mono); font-size:11px;">${p.email}</span>` : '<span class="text-muted">Unverified</span>';

    return `
      <tr>
        <td>${rankBadge}</td>
        <td>
          <strong>${p.full_name}</strong><br>
          <small class="text-muted">${p.title || ""}</small>
        </td>
        <td>
          ${p.university_name}<br>
          <small class="text-muted">${p.country}</small>
        </td>
        <td><small>${p.department || "-"}</small></td>
        <td>${emailBadge}</td>
        <td><small>${p.research_overlap ? p.research_overlap.slice(0, 75) + "..." : "-"}</small></td>
        <td><span class="badge ${statusClass}">${p.outreach_status}</span></td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick="openDraftModal('${p.professor_id}')">Inspect / Draft</button>
        </td>
      </tr>
    `;
  }).join("");
}

// -----------------------------------------------------------------------------
// Draft Inspection & Generation Modal
// -----------------------------------------------------------------------------
async function openDraftModal(profId) {
  currentDraftProfId = profId;
  const modal = document.getElementById("draftModal");
  modal.classList.add("active");

  document.getElementById("modalRecipient").textContent = "Loading...";
  document.getElementById("modalSubject").textContent = "Loading...";
  document.getElementById("modalWordCount").textContent = "--";
  document.getElementById("modalBodyText").textContent = "Preparing four-paragraph personalized draft...";

  try {
    const res = await fetch(`/api/professors/${profId}`);
    const data = await res.json();
    const prof = data.professor;

    let draft = data.draft;
    if (!draft) {
      // Auto-generate if not yet existing
      const genRes = await fetch(`/api/professors/${profId}/generate-draft`, { method: "POST" });
      const genData = await genRes.json();
      draft = genData.draft;
    }

    document.getElementById("modalRecipient").textContent = `${prof.full_name} <${draft.recipient_email}> (${prof.university_name})`;
    document.getElementById("modalSubject").textContent = draft.subject;
    document.getElementById("modalWordCount").textContent = `${draft.word_count} words`;
    document.getElementById("modalBodyText").textContent = draft.body_text;

    const approveBtn = document.getElementById("btnModalApprove");
    if (draft.status === "APPROVED") {
      approveBtn.textContent = "Already Approved";
      approveBtn.disabled = true;
    } else {
      approveBtn.textContent = "Approve Draft for Schedule";
      approveBtn.disabled = false;
    }
  } catch (err) {
    document.getElementById("modalBodyText").textContent = `Error loading draft: ${err}`;
  }
}

// -----------------------------------------------------------------------------
// IST Scheduler Blocks
// -----------------------------------------------------------------------------
async function loadTimeBlocks() {
  const container = document.getElementById("timeBlocksList");
  try {
    const res = await fetch("/api/scheduler/blocks");
    const data = await res.json();

    container.innerHTML = data.blocks.map(b => `
      <div class="block-card">
        <div class="block-header">
          <span class="block-title">${b.region}</span>
          <span class="block-ist">${b.ist_time}</span>
        </div>
        <div class="block-details">
          <div><strong>Arrival Target:</strong> 8:00 AM – 9:00 AM Local Time</div>
          <div><strong>Countries:</strong> ${b.countries}</div>
          <div><strong>Universities Covered:</strong> ${b.university_count} institutions</div>
          <div><strong>Faculty Prospects:</strong> ${b.faculty_count} professors</div>
          <div style="font-size:11px; margin-top:4px;" class="text-muted">${b.notes}</div>
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<div class="text-danger">Failed to load schedule blocks: ${err}</div>`;
  }
}

// -----------------------------------------------------------------------------
// Reply Intelligence Inbox
// -----------------------------------------------------------------------------
async function loadReplies() {
  const listEl = document.getElementById("repliesList");
  try {
    const res = await fetch("/api/replies");
    const data = await res.json();

    if (data.count === 0) {
      listEl.innerHTML = '<div class="text-center text-muted" style="padding:20px;">No incoming replies currently pending review.</div>';
      return;
    }

    listEl.innerHTML = data.reply_drafts.map(r => {
      const cat = r.reply_category || "INQUIRY";
      return `
        <div class="reply-item" onclick="selectReply('${r.reply_draft_id}')">
          <div class="reply-sender">${r.professor_name}</div>
          <div class="reply-uni">${r.university_name}</div>
          <span class="reply-category-badge badge-warning">${cat}</span>
        </div>
      `;
    }).join("");
  } catch (err) {
    listEl.innerHTML = `<div class="text-danger">Error loading replies: ${err}</div>`;
  }
}

async function selectReply(replyDraftId) {
  const pane = document.getElementById("replyDetailPane");
  try {
    const res = await fetch("/api/replies");
    const data = await res.json();
    const draft = data.reply_drafts.find(d => d.reply_draft_id === replyDraftId);
    if (!draft) return;

    selectedReplyDraft = draft;

    pane.innerHTML = `
      <div style="margin-bottom:16px;">
        <h3>Incoming Response from ${draft.professor_name}</h3>
        <p class="text-muted">${draft.university_name} &bull; ${draft.professor_email}</p>
        <div style="margin-top:8px;">
          <span class="badge badge-warning">CLASSIFICATION: ${draft.reply_category}</span>
          <span class="text-muted" style="font-size:12px; margin-left:8px;">${draft.reply_reasoning || ""}</span>
        </div>
      </div>

      <div style="margin-bottom:20px;">
        <h4 style="margin-bottom:8px;">AI-Prepared Contextual Reply Draft (For Your Review)</h4>
        <div class="draft-text-box">
          <pre style="white-space:pre-wrap; font-family:var(--font-sans);">${draft.draft_body}</pre>
        </div>
      </div>

      <div style="background:#151E2E; border:1px solid var(--border-color); padding:16px; border-radius:6px;">
        <h4 style="color:#F59E0B; margin-bottom:6px;">HUMAN-OPERATOR PERMISSION CHECK</h4>
        <p style="font-size:12px; color:var(--text-muted); margin-bottom:12px;">
          The AI engine cannot transmit this response. You must explicitly confirm and initiate transmission as the human operator.
        </p>
        <div style="display:flex; gap:10px; align-items:center;">
          <input type="text" id="humanOperatorId" value="Harshith N" placeholder="Enter operator identity" class="input-text" style="max-width:200px;">
          <button class="btn btn-primary" onclick="executeHumanReplySend('${draft.reply_draft_id}')">Approve & Transmit (Dry-Run)</button>
        </div>
        <div id="replyActionStatus" class="sync-status-box"></div>
      </div>
    `;
  } catch (err) {
    pane.innerHTML = `<div class="text-danger">Error: ${err}</div>`;
  }
}

async function executeHumanReplySend(draftId) {
  const opId = document.getElementById("humanOperatorId").value.trim();
  const statusBox = document.getElementById("replyActionStatus");
  if (!opId) {
    alert("Operator identifier is required for human approval.");
    return;
  }

  statusBox.textContent = "Executing human-authorized transmission...";
  try {
    const res = await fetch(`/api/replies/${draftId}/approve-and-send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operator_id: opId, dry_run: true })
    });
    const data = await res.json();
    if (res.ok) {
      statusBox.textContent = `SUCCESS: Reply transmitted (Dry-Run). Message-ID: ${data.message_id}`;
      loadReplies();
      loadMetrics();
    } else {
      statusBox.textContent = `FAILED: ${data.detail || "Unauthorized"}`;
    }
  } catch (err) {
    statusBox.textContent = `Transmission error: ${err}`;
  }
}

// -----------------------------------------------------------------------------
// Follow-Ups Queue
// -----------------------------------------------------------------------------
async function loadFollowups() {
  const container = document.getElementById("followupsList");
  try {
    const res = await fetch("/api/followups");
    const data = await res.json();

    if (data.count === 0) {
      container.innerHTML = '<div class="text-center text-muted" style="padding:20px;">No unanswered first-contact emails currently eligible for follow-up review.</div>';
      return;
    }

    container.innerHTML = data.followup_drafts.map(f => `
      <div class="card" style="margin-bottom:16px;">
        <h4>Follow-Up for Prof. ${f.professor_name} (${f.university_name})</h4>
        <div class="draft-text-box" style="margin:12px 0;">
          <pre style="white-space:pre-wrap; font-family:var(--font-sans);">${f.draft_body}</pre>
        </div>
        <button class="btn btn-primary btn-sm" onclick="alert('Follow-up transmission requires human trigger.')">Approve & Dispatch Follow-Up</button>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<div class="text-danger">Failed to load follow-ups: ${err}</div>`;
  }
}

// -----------------------------------------------------------------------------
// Suppressions
// -----------------------------------------------------------------------------
async function loadSuppressions() {
  const tbody = document.getElementById("suppressionTableBody");
  try {
    const res = await fetch("/api/suppressions");
    const data = await res.json();

    tbody.innerHTML = data.suppressions.map(s => `
      <tr>
        <td><strong>${s.email || s.domain || "All"}</strong></td>
        <td><span class="badge badge-danger">${s.reason}</span></td>
        <td><small>${s.notes || "-"}</small></td>
        <td><small class="text-muted">${s.created_at || ""}</small></td>
      </tr>
    `).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" class="text-danger">Error: ${err}</td></tr>`;
  }
}

// -----------------------------------------------------------------------------
// Master Workbook Sync
// -----------------------------------------------------------------------------
function initEventHandlers() {
  document.getElementById("btnReloadProfs").addEventListener("click", loadProfessors);
  document.getElementById("profSearch").addEventListener("input", () => renderProfessorsTable(currentProfessors));
  document.getElementById("countryFilter").addEventListener("change", loadProfessors);
  document.getElementById("statusFilter").addEventListener("change", loadProfessors);

  // Close modal
  document.getElementById("btnCloseModal").addEventListener("click", () => {
    document.getElementById("draftModal").classList.remove("active");
  });
  document.getElementById("btnModalClose").addEventListener("click", () => {
    document.getElementById("draftModal").classList.remove("active");
  });

  // Approve Draft
  document.getElementById("btnModalApprove").addEventListener("click", async () => {
    if (!currentDraftProfId) return;
    try {
      const res = await fetch(`/api/professors/${currentDraftProfId}/approve-draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operator_id: "Harshith N" })
      });
      if (res.ok) {
        alert("Draft successfully approved and added to the regional schedule block queue.");
        document.getElementById("draftModal").classList.remove("active");
        loadProfessors();
        loadMetrics();
      }
    } catch (err) {
      alert(`Approval error: ${err}`);
    }
  });

  // Import Workbook
  document.getElementById("btnImportWorkbook").addEventListener("click", async () => {
    const box = document.getElementById("importResult");
    box.textContent = "Importing authoritative workbook dynamically...";
    try {
      const res = await fetch("/api/sync/import-workbook", { method: "POST" });
      const data = await res.json();
      box.textContent = `SUCCESS: Imported ${data.stats.professors_imported} professors from ${data.stats.sheets_found.join(", ")}. Suppressed: ${data.stats.suppressed_detected}.`;
      loadMetrics();
      loadProfessors();
    } catch (err) {
      box.textContent = `Import failed: ${err}`;
    }
  });

  // Sync Back to Workbook
  document.getElementById("btnSyncBackWorkbook").addEventListener("click", async () => {
    const box = document.getElementById("syncBackResult");
    box.textContent = "Writing atomic sync-back to authoritative Excel with timestamped backup...";
    try {
      const res = await fetch("/api/sync/write-back", { method: "POST" });
      const data = await res.json();
      box.textContent = `SUCCESS: ${data.message}`;
    } catch (err) {
      box.textContent = `Sync-back error: ${err}`;
    }
  });

  // Add Manual Suppression
  document.getElementById("btnAddSuppression").addEventListener("click", async () => {
    const email = document.getElementById("suppEmail").value.trim();
    const domain = document.getElementById("suppDomain").value.trim();
    const reason = document.getElementById("suppReason").value;
    const notes = document.getElementById("suppNotes").value.trim();

    if (!email && !domain) {
      alert("Provide at least an email or a domain to suppress.");
      return;
    }

    try {
      const res = await fetch("/api/suppressions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, domain, reason, notes })
      });
      if (res.ok) {
        alert("Suppression successfully added.");
        document.getElementById("suppEmail").value = "";
        document.getElementById("suppDomain").value = "";
        document.getElementById("suppNotes").value = "";
        loadSuppressions();
        loadMetrics();
      }
    } catch (err) {
      alert(`Error: ${err}`);
    }
  });

  // Test Mail
  document.getElementById("btnTestMail").addEventListener("click", async () => {
    const box = document.getElementById("mailTestResult");
    box.textContent = "Testing IMAP (993) and SMTP (465) SSL/TLS endpoints...";
    try {
      const res = await fetch("/api/diagnostics/test-mail", { method: "POST" });
      const data = await res.json();
      box.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      box.textContent = `Diagnostic check error: ${err}`;
    }
  });

  // Toggle Emergency Stop
  document.getElementById("btnToggleEmergencyStop").addEventListener("click", async () => {
    const confirmed = confirm("Are you sure you want to toggle the global Emergency Stop killswitch?");
    if (!confirmed) return;

    try {
      const res = await fetch("/api/safety/emergency-stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: true, operator_id: "Harshith N" })
      });
      const data = await res.json();
      alert(`Emergency Stop is now ${data.emergency_stop ? "ACTIVE" : "INACTIVE"}`);
      loadMetrics();
    } catch (err) {
      alert(`Error: ${err}`);
    }
  });
}

// -----------------------------------------------------------------------------
// Audit Logs Stream
// -----------------------------------------------------------------------------
async function loadAuditLogs() {
  const container = document.getElementById("auditLogStream");
  try {
    const res = await fetch("/api/audit-logs");
    const data = await res.json();

    if (data.events.length === 0) {
      container.innerHTML = '<div class="text-center text-muted">No audit events recorded yet.</div>';
      return;
    }

    container.innerHTML = data.events.map(ev => `
      <div style="border-bottom:1px solid #1E293B; padding:4px 0;">
        <span style="color:#60A5FA;">[${ev.timestamp.slice(11, 19)}]</span>
        <strong style="color:#34D399;">${ev.event_type}</strong>
        <span style="color:#FBBF24;">(${ev.actor}${ev.operator_id ? " - " + ev.operator_id : ""})</span>:
        <span style="color:#D1D5DB;">${ev.entity_type} ${ev.entity_id}</span>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<div class="text-danger">Error loading audit logs: ${err}</div>`;
  }
}
