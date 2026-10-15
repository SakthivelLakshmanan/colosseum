/**
 * Colosseum — Decision Intelligence Command Room (BUILD_GUIDE 8.1)
 * Vanilla JavaScript implementation: theme toggle, saved run loader,
 * brief form management, live arena SVG updates, progress step polling,
 * and "Why these advisers" routing alignment.
 */

(function () {
  'use strict';

  // Constants & Domain Definitions
  const ALL_ADVISERS = [
    { id: 'ops', name: 'Ops', icon: '⚙️', lens: 'Maintainability & Migration' },
    { id: 'finance', name: 'Finance', icon: '💰', lens: 'Cost, CAPEX/OPEX & Payback' },
    { id: 'business', name: 'Business', icon: '📈', lens: 'Growth, Strategy & Deadlines' },
    { id: 'customer', name: 'Customer', icon: '👥', lens: 'Merchant/Borrower Experience' },
    { id: 'legal', name: 'Legal', icon: '⚖️', lens: 'Contracts, Liability & Regulation' },
    { id: 'infosec', name: 'InfoSec', icon: '🛡️', lens: 'Data Safety & Mandatory Rules' }
  ];

  const PIPELINE_STAGES = [
    'routing',
    'advisers',
    'moderating',
    'scoring',
    'writing',
    'checking',
    'done'
  ];

  // Application State
  const state = {
    currentRunId: null,
    pollTimer: null,
    elapsedTimer: null,
    startTime: null,
    currentStage: 'idle',
    briefData: null,
    routingData: null,
    positionsData: {}
  };

  // DOM Elements
  const dom = {
    themeToggle: document.getElementById('theme-toggle'),
    themeIcon: document.getElementById('theme-icon'),
    themeText: document.getElementById('theme-text'),
    savedRunsSelect: document.getElementById('saved-runs-select'),
    elapsedTimer: document.getElementById('elapsed-timer'),
    pipelineStatusLabel: document.getElementById('pipeline-status-label'),
    stepProgressLine: document.getElementById('step-progress-line'),
    stepItems: document.querySelectorAll('.step-item'),
    pipelineError: document.getElementById('pipeline-error'),
    pipelineErrorStage: document.getElementById('pipeline-error-stage'),
    pipelineErrorDetail: document.getElementById('pipeline-error-detail'),
    arenaStatusBadge: document.getElementById('arena-status-badge'),
    arenaCenterQuestion: document.getElementById('arena-center-question'),
    routingRowsContainer: document.getElementById('routing-rows-container'),
    routingSummaryBadge: document.getElementById('routing-summary-badge'),
    routingQueryBox: document.getElementById('routing-query-box'),
    weightIndicator: document.getElementById('weight-indicator'),
    btnLoadSample: document.getElementById('btn-load-sample'),
    btnRunDebate: document.getElementById('btn-run-debate'),
    briefQuestion: document.getElementById('brief-question'),
    briefContext: document.getElementById('brief-context'),
    optionAText: document.getElementById('option-a-text'),
    optionBText: document.getElementById('option-b-text'),
    constraintsList: document.getElementById('constraints-list'),
    btnAddConstraint: document.getElementById('btn-add-constraint'),
    criteriaList: document.getElementById('criteria-list'),
    toastContainer: document.getElementById('toast-container')
  };

  // =========================================================================
  // 1. Theme Management (Light / Dark)
  // =========================================================================

  function initTheme() {
    const savedTheme = localStorage.getItem('colosseum_theme') || 'dark';
    applyTheme(savedTheme);

    if (dom.themeToggle) {
      dom.themeToggle.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
        const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
        applyTheme(nextTheme);
        localStorage.setItem('colosseum_theme', nextTheme);
      });
    }
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    if (dom.themeIcon && dom.themeText) {
      if (theme === 'light') {
        dom.themeIcon.textContent = '🌙';
        dom.themeText.textContent = 'Dark Mode';
      } else {
        dom.themeIcon.textContent = '☀️';
        dom.themeText.textContent = 'Light Mode';
      }
    }
  }

  // =========================================================================
  // 2. Toast Notifications
  // =========================================================================

  function showToast(title, message, type = 'info') {
    if (!dom.toastContainer) return;
    const toast = document.createElement('div');
    toast.setAttribute('class', `toast toast-${type}`);
    toast.innerHTML = `
      <div class="toast-content">
        <div class="toast-title">${escapeHtml(title)}</div>
        <div class="toast-message">${escapeHtml(message)}</div>
      </div>
      <button class="toast-close" aria-label="Close">&times;</button>
    `;

    toast.querySelector('.toast-close').addEventListener('click', () => {
      toast.remove();
    });

    dom.toastContainer.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
      }
    }, 4500);
  }

  // =========================================================================
  // 3. Saved Runs Management (GET /runs)
  // =========================================================================

  async function loadSavedRuns() {
    try {
      const response = await fetch('/runs');
      if (!response.ok) return;
      const runs = await response.json();

      if (!Array.isArray(runs) || !dom.savedRunsSelect) return;

      // Reset options except the first placeholder
      dom.savedRunsSelect.innerHTML = '<option value="">Open a saved run...</option>';

      runs.forEach(run => {
        const opt = document.createElement('option');
        opt.value = run.run_id;

        // Format Date
        let dateStr = '';
        if (run.created_at) {
          const d = new Date(run.created_at);
          if (!isNaN(d.getTime())) {
            dateStr = d.toLocaleDateString('en-GB', {
              day: 'numeric',
              month: 'short',
              hour: '2-digit',
              minute: '2-digit'
            });
          }
        }

        // Short ID & Verdict
        const shortId = run.run_id.length > 20 ? `${run.run_id.slice(0, 18)}…` : run.run_id;
        const verdictVal = (run.final_verdict && typeof run.final_verdict === 'object')
          ? run.final_verdict.verdict
          : run.final_verdict;
        const verdictTag = verdictVal ? `[${verdictVal}]` : (run.status ? `[${run.status.toUpperCase()}]` : '');

        opt.textContent = `${dateStr ? dateStr + ' — ' : ''}${shortId} ${verdictTag}`;
        dom.savedRunsSelect.appendChild(opt);
      });
    } catch (err) {
      console.warn('Could not load saved runs list:', err);
    }
  }

  function setupSavedRunsListener() {
    if (!dom.savedRunsSelect) return;
    dom.savedRunsSelect.addEventListener('change', async (e) => {
      const runId = e.target.value;
      if (!runId) return;

      stopPolling();
      stopTimer();

      try {
        resetPipelineView();
      } catch (resetErr) {
        console.error('Error resetting pipeline view for run', runId, resetErr);
      }

      let runDoc = null;
      try {
        const resp = await fetch(`/runs/${encodeURIComponent(runId)}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        runDoc = await resp.json();
      } catch (fetchErr) {
        console.error('Failed to fetch saved run', runId, fetchErr);
        showToast('Error', `Failed to open run ${runId}: ${fetchErr.message}`, 'strike');
        return;
      }

      if (runDoc) {
        try {
          // Render straight away with no polling
          renderRunDocument(runDoc, /* isHistorical */ true);
          showToast('Run Loaded', `Viewing saved run ${runId}`, 'pass');
        } catch (renderErr) {
          console.error('Error during saved run rendering for', runId, renderErr);
          showToast('Warning', `Loaded run with partial display: ${renderErr.message}`, 'flag');
        }
      }
    });
  }

  // =========================================================================
  // 4. Brief Form & Weight Calculations
  // =========================================================================

  function renderConstraints(constraints = []) {
    if (!dom.constraintsList) return;
    dom.constraintsList.innerHTML = '';
    constraints.forEach((cText, idx) => {
      const item = document.createElement('div');
      item.setAttribute('class', 'constraint-item');
      item.innerHTML = `
        <span class="mono-num text-muted" style="font-size: 0.75rem;">${idx + 1}.</span>
        <input type="text" class="input-control constraint-input" value="${escapeHtml(cText)}" placeholder="Constraint description" />
        <button class="btn btn-ghost btn-sm btn-remove-constraint" type="button" title="Remove constraint" style="color: var(--color-strike); padding: 0.4rem 0.6rem;">&times;</button>
      `;

      item.querySelector('.btn-remove-constraint').addEventListener('click', () => {
        item.remove();
      });

      dom.constraintsList.appendChild(item);
    });
  }

  function addConstraintRow(initialText = '') {
    if (!dom.constraintsList) return;
    const idx = dom.constraintsList.querySelectorAll('.constraint-item').length;
    const item = document.createElement('div');
    item.setAttribute('class', 'constraint-item');
    item.innerHTML = `
      <span class="mono-num text-muted" style="font-size: 0.75rem;">${idx + 1}.</span>
      <input type="text" class="input-control constraint-input" value="${escapeHtml(initialText)}" placeholder="Constraint description" />
      <button class="btn btn-ghost btn-sm btn-remove-constraint" type="button" title="Remove constraint" style="color: var(--color-strike); padding: 0.4rem 0.6rem;">&times;</button>
    `;

    item.querySelector('.btn-remove-constraint').addEventListener('click', () => {
      item.remove();
    });

    dom.constraintsList.appendChild(item);
    const inp = item.querySelector('input');
    if (inp) inp.focus();
  }

  function renderCriteria(criteria = []) {
    if (!dom.criteriaList) return;
    dom.criteriaList.innerHTML = '';
    criteria.forEach(crit => {
      const card = document.createElement('div');
      card.setAttribute('class', 'criterion-card');
      const weightPct = Math.round((crit.weight || 0) * 100);
      const ownersStr = Array.isArray(crit.owners) ? crit.owners.join(', ') : (crit.owners || '');

      card.innerHTML = `
        <div class="criterion-meta">
          <div class="criterion-name-text">${escapeHtml(crit.name)}</div>
          <div class="criterion-owners-text">Owners: ${escapeHtml(ownersStr)} · ${escapeHtml(crit.good_looks_like || '')}</div>
        </div>
        <div style="display: flex; align-items: center; gap: 0.25rem;">
          <input type="number" class="input-control criterion-weight-input" min="0" max="100" step="1" value="${weightPct}" data-name="${escapeHtml(crit.name)}" data-owners="${escapeHtml(ownersStr)}" data-gll="${escapeHtml(crit.good_looks_like || '')}" />
          <span class="mono-num" style="font-size: 0.75rem; color: var(--text-muted);">%</span>
        </div>
      `;

      card.querySelector('.criterion-weight-input').addEventListener('input', calculateTotalWeights);
      dom.criteriaList.appendChild(card);
    });

    calculateTotalWeights();
  }

  function calculateTotalWeights() {
    const inputs = document.querySelectorAll('.criterion-weight-input');
    let total = 0;
    inputs.forEach(inp => {
      const v = parseFloat(inp.value) || 0;
      total += v;
    });

    total = Math.round(total);

    if (dom.weightIndicator) {
      if (total === 100) {
        dom.weightIndicator.setAttribute('class', 'badge badge-pass');
        dom.weightIndicator.textContent = 'Weights: 100%';
        if (dom.btnRunDebate && state.currentStage === 'idle') {
          dom.btnRunDebate.disabled = false;
        }
      } else {
        dom.weightIndicator.setAttribute('class', 'badge badge-flag');
        dom.weightIndicator.textContent = `Weights: ${total}% (Must equal 100%)`;
        if (dom.btnRunDebate) {
          dom.btnRunDebate.disabled = true;
        }
      }
    }
  }

  function populateBriefForm(brief) {
    if (!brief) return;
    state.briefData = brief;
    if (dom.briefQuestion && brief.question) dom.briefQuestion.value = brief.question;
    if (dom.briefContext && brief.context) dom.briefContext.value = brief.context;
    if (dom.optionAText && brief.options) dom.optionAText.value = brief.options.A || '';
    if (dom.optionBText && brief.options) dom.optionBText.value = brief.options.B || '';
    if (Array.isArray(brief.constraints)) renderConstraints(brief.constraints);
    if (Array.isArray(brief.criteria)) renderCriteria(brief.criteria);
  }

  async function loadSampleBrief() {
    try {
      const resp = await fetch('/briefs/sample');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      state.briefData = data;

      if (dom.briefQuestion) dom.briefQuestion.value = data.question || '';
      if (dom.briefContext) dom.briefContext.value = data.context || '';
      if (dom.optionAText) dom.optionAText.value = (data.options && data.options.A) || '';
      if (dom.optionBText) dom.optionBText.value = (data.options && data.options.B) || '';

      renderConstraints(data.constraints || []);
      renderCriteria(data.criteria || []);

      updateArenaCenterText(data.question || '');

      showToast('Sample Brief Loaded', 'DEC-001 (Legion Capital payment gateway migration)', 'cyan');
    } catch (err) {
      showToast('Error', `Could not load sample brief: ${err.message}`, 'strike');
    }
  }

  function updateArenaCenterText(question) {
    if (!dom.arenaCenterQuestion) return;
    if (!question) {
      dom.arenaCenterQuestion.innerHTML = `
        <tspan x="400" dy="0">Load brief or run</tspan>
        <tspan x="400" dy="18">to start debate</tspan>
      `;
      return;
    }

    // Shorten question to 2 compact lines
    const words = question.split(/\s+/);
    let line1 = [];
    let line2 = [];
    let currentLength = 0;

    for (const w of words) {
      if (currentLength + w.length <= 26 && line2.length === 0) {
        line1.push(w);
        currentLength += w.length + 1;
      } else if (line2.join(' ').length + w.length <= 28) {
        line2.push(w);
      } else {
        break;
      }
    }

    const t1 = line1.join(' ');
    const t2 = line2.length > 0 ? (line2.join(' ') + (words.length > (line1.length + line2.length) ? '…' : '')) : '';

    dom.arenaCenterQuestion.innerHTML = `
      <tspan x="400" dy="0">${escapeHtml(t1)}</tspan>
      ${t2 ? `<tspan x="400" dy="18">${escapeHtml(t2)}</tspan>` : ''}
    `;
  }

  function collectBriefFormData() {
    const question = (dom.briefQuestion ? dom.briefQuestion.value : '').trim();
    const context = (dom.briefContext ? dom.briefContext.value : '').trim();
    const optA = (dom.optionAText ? dom.optionAText.value : '').trim() || 'Option A';
    const optB = (dom.optionBText ? dom.optionBText.value : '').trim() || 'Option B';

    const constraints = [];
    document.querySelectorAll('.constraint-input').forEach(inp => {
      const val = inp.value.trim();
      if (val) constraints.push(val);
    });

    const criteria = [];
    document.querySelectorAll('.criterion-weight-input').forEach(inp => {
      const name = inp.getAttribute('data-name') || 'Criterion';
      const weightPct = parseFloat(inp.value) || 0;
      const ownersStr = inp.getAttribute('data-owners') || '';
      const owners = ownersStr.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
      const gll = inp.getAttribute('data-gll') || '';

      criteria.push({
        name: name,
        weight: weightPct / 100.0,
        owners: owners.length ? owners : ['finance'],
        good_looks_like: gll
      });
    });

    const decisionId = (state.briefData && state.briefData.decision_id) ? state.briefData.decision_id : `DEC-${Date.now().toString(36).toUpperCase()}`;

    return {
      decision_id: decisionId,
      question: question,
      context: context,
      options: {
        A: optA,
        B: optB
      },
      constraints: constraints,
      criteria: criteria
    };
  }

  // =========================================================================
  // 5. Run Execution & Live Polling (POST /decisions & GET /runs/{id})
  // =========================================================================

  async function handleRunDebate() {
    const briefPayload = collectBriefFormData();

    if (!briefPayload.question) {
      showToast('Validation Error', 'Please enter a decision question.', 'flag');
      return;
    }

    // Check weights
    let weightSum = 0;
    briefPayload.criteria.forEach(c => weightSum += c.weight);
    if (Math.abs(weightSum - 1.0) > 0.01) {
      showToast('Weights Error', 'Criteria weights must sum to 100%.', 'flag');
      return;
    }

    // Set UI state to Running
    if (dom.btnRunDebate) {
      dom.btnRunDebate.disabled = true;
      dom.btnRunDebate.innerHTML = `<span>⏳</span><span>Debate running…</span>`;
    }

    stopPolling();
    resetPipelineView();
    startTimer();
    updatePipelineStep('routing');
    updateArenaCenterText(briefPayload.question);

    try {
      const response = await fetch('/decisions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(briefPayload)
      });

      if (!response.ok) {
        throw new Error(`Failed to start debate: HTTP ${response.status}`);
      }

      const result = await response.json();
      const runId = result.run_id;
      state.currentRunId = runId;

      showToast('Debate Dispatched', `Run ID: ${runId}. Streaming pipeline steps…`, 'cyan');

      // Start Polling every 3 seconds
      startPolling(runId);
    } catch (err) {
      stopTimer();
      if (dom.btnRunDebate) {
        dom.btnRunDebate.disabled = false;
        dom.btnRunDebate.innerHTML = `<span>⚡</span><span>Run debate</span>`;
      }
      showPipelineError('Dispatch Failure', err.message);
      showToast('Dispatch Error', err.message, 'strike');
    }
  }

  function startPolling(runId) {
    stopPolling();
    // Immediate initial poll
    pollRunDetails(runId);

    state.pollTimer = setInterval(() => {
      pollRunDetails(runId);
    }, 3000);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollRunDetails(runId) {
    try {
      const resp = await fetch(`/runs/${encodeURIComponent(runId)}`);
      if (!resp.ok) return;
      const runDoc = await resp.json();

      renderRunDocument(runDoc, /* isHistorical */ false);

      const status = (runDoc.status || '').toLowerCase();
      if (status === 'done' || status === 'incomplete' || status === 'failed') {
        stopPolling();
        stopTimer();

        if (dom.btnRunDebate) {
          dom.btnRunDebate.disabled = false;
          dom.btnRunDebate.innerHTML = `<span>⚡</span><span>Run debate</span>`;
        }

        if (status === 'failed') {
          showPipelineError(
            runDoc.failed_stage ? `Stage: ${runDoc.failed_stage}` : 'Execution Failure',
            runDoc.error || 'Pipeline run aborted with an error.'
          );
          showToast('Pipeline Failed', runDoc.error || 'Run failed', 'strike');
        } else {
          loadSavedRuns(); // Refresh saved runs list to include this new run
          const verdictStr = (runDoc.final_verdict && runDoc.final_verdict.verdict) || status.toUpperCase();
          showToast('Debate Completed', `Final verdict: ${verdictStr}`, status === 'done' ? 'pass' : 'flag');
        }
      }
    } catch (err) {
      console.warn('Polling error:', err);
    }
  }

  // =========================================================================
  // 6. View Rendering (Arena, Routing Panel, Step Bar)
  // =========================================================================

  function renderRunDocument(runDoc, isHistorical = false) {
    if (!runDoc) return;

    const status = (runDoc.status || 'idle').toLowerCase();
    state.currentStage = status;

    // 1. Pipeline Status Label
    try {
      if (dom.pipelineStatusLabel) {
        dom.pipelineStatusLabel.textContent = `PIPELINE STATUS: ${status.toUpperCase()}`;
      }
    } catch (err) {
      console.warn('Error updating pipeline status label:', err);
    }

    // 2. Step Progress Bar & Error Banner
    try {
      if (status === 'failed') {
        showPipelineError(
          runDoc.failed_stage ? `Stage: ${runDoc.failed_stage}` : 'Pipeline Failure',
          runDoc.error || 'Execution encountered an error.'
        );
      } else {
        hidePipelineError();
        updatePipelineStep(status);
      }
    } catch (err) {
      console.warn('Error updating pipeline step progress:', err);
    }

    // 3. Question on Center Arena Node and Brief Form Population
    try {
      if (runDoc.brief) {
        if (runDoc.brief.question) {
          updateArenaCenterText(runDoc.brief.question);
        }
        if (isHistorical) {
          populateBriefForm(runDoc.brief);
        }
      }
    } catch (err) {
      console.warn('Error updating brief question or form:', err);
    }

    // 4. Update Routing Data and "Why these advisers" panel
    try {
      if (runDoc.routing) {
        state.routingData = runDoc.routing;
        renderRoutingPanel(runDoc.routing);
      }
    } catch (err) {
      console.warn('Error rendering routing panel:', err);
    }

    // 5. Update Arena Seats based on stage and stances
    try {
      renderArenaSeats(runDoc);
    } catch (err) {
      console.warn('Error rendering arena seats:', err);
    }

    // 6. Update Arena Status Badge
    try {
      updateArenaStatusBadge(runDoc);
    } catch (err) {
      console.warn('Error updating arena status badge:', err);
    }
  }

  function updateArenaStatusBadge(runDoc) {
    if (!dom.arenaStatusBadge) return;
    const status = (runDoc.status || 'idle').toLowerCase();
    const verdict = (runDoc.final_verdict && typeof runDoc.final_verdict === 'object')
      ? (runDoc.final_verdict.verdict || '').toUpperCase()
      : (typeof runDoc.final_verdict === 'string' ? runDoc.final_verdict.toUpperCase() : '');

    if (status === 'done') {
      if (verdict === 'INCOMPLETE') {
        dom.arenaStatusBadge.setAttribute('class', 'badge badge-flag');
        dom.arenaStatusBadge.textContent = 'Completed (INCOMPLETE)';
      } else if (verdict === 'WRONG') {
        dom.arenaStatusBadge.setAttribute('class', 'badge badge-strike');
        dom.arenaStatusBadge.textContent = 'Completed (WRONG)';
      } else {
        dom.arenaStatusBadge.setAttribute('class', 'badge badge-pass');
        dom.arenaStatusBadge.textContent = verdict ? `Completed (${verdict})` : 'Completed (PASS)';
      }
    } else if (status === 'incomplete') {
      dom.arenaStatusBadge.setAttribute('class', 'badge badge-flag');
      dom.arenaStatusBadge.textContent = 'Completed (INCOMPLETE)';
    } else if (status === 'failed') {
      dom.arenaStatusBadge.setAttribute('class', 'badge badge-strike');
      dom.arenaStatusBadge.textContent = 'Failed';
    } else if (status === 'queued') {
      dom.arenaStatusBadge.setAttribute('class', 'badge badge-neutral');
      dom.arenaStatusBadge.textContent = 'Queued';
    } else {
      dom.arenaStatusBadge.setAttribute('class', 'badge badge-cyan');
      dom.arenaStatusBadge.textContent = `Working: ${status.toUpperCase()}`;
    }
  }

  function setSeatState(seatEl, stateClass) {
    if (!seatEl) return;
    seatEl.setAttribute('class', `arena-seat ${stateClass}`);
  }

  function updatePipelineStep(status) {
    let activeIdx = 0;
    const s = status.toLowerCase();

    if (s === 'routing') activeIdx = 0;
    else if (s === 'evidence_packs' || s === 'advisers') activeIdx = 1;
    else if (s === 'moderating') activeIdx = 2;
    else if (s === 'scoring') activeIdx = 3;
    else if (s === 'writing') activeIdx = 4;
    else if (s === 'checking') activeIdx = 5;
    else if (s === 'done' || s === 'incomplete') activeIdx = 6;
    else activeIdx = 0;

    if (dom.stepItems) {
      dom.stepItems.forEach((item, idx) => {
        item.classList.remove('is-active', 'is-completed');
        if (s === 'done' || s === 'incomplete') {
          item.classList.add('is-completed');
        } else if (idx < activeIdx) {
          item.classList.add('is-completed');
        } else if (idx === activeIdx) {
          item.classList.add('is-active');
        }
      });
    }

    if (dom.stepProgressLine) {
      const pct = (s === 'done' || s === 'incomplete') ? 100 : Math.round((activeIdx / 6) * 100);
      dom.stepProgressLine.style.width = `${pct}%`;
    }
  }

  function renderArenaSeats(runDoc) {
    const routing = runDoc.routing;
    const selectedAdvisers = (routing && Array.isArray(routing.selected))
      ? routing.selected.map(s => String(s).toLowerCase())
      : [];
    const scoresMap = {};

    if (routing && Array.isArray(routing.scores)) {
      routing.scores.forEach(s => {
        if (s && s.adviser_id) {
          scoresMap[String(s.adviser_id).toLowerCase()] = s;
        }
      });
    }

    // Build positions stance and blocking lookup
    const positions = Array.isArray(runDoc.positions) ? runDoc.positions : [];
    const positionsMap = {};
    positions.forEach(p => {
      if (p && p.adviser_id) {
        positionsMap[String(p.adviser_id).toLowerCase()] = p;
      }
    });

    const isStageAdvisersWorking = (runDoc.status || '').toLowerCase() === 'advisers';

    ALL_ADVISERS.forEach(adv => {
      const aid = adv.id;
      const seatEl = document.getElementById(`seat-${aid}`);
      const connectorEl = document.getElementById(`connector-${aid}`);
      const scoreBadgeEl = document.getElementById(`seat-score-${aid}`);

      if (!seatEl) return;

      const isSelected = selectedAdvisers.includes(aid);
      const scoreObj = scoresMap[aid];
      const pos = positionsMap[aid];

      // Update score badge
      if (scoreBadgeEl) {
        if (scoreObj && typeof scoreObj.similarity === 'number') {
          scoreBadgeEl.textContent = scoreObj.similarity.toFixed(2);
        } else {
          scoreBadgeEl.textContent = '0.00';
        }
      }

      // Update connector active line (SVG line element)
      if (connectorEl) {
        if (isSelected) {
          connectorEl.setAttribute('class', 'arena-connector is-active');
        } else {
          connectorEl.setAttribute('class', 'arena-connector');
        }
      }

      // 1. If Adviser Position is Available (Done or Blocking state)
      if (pos) {
        const hasBlocking = Array.isArray(pos.blocking_findings) && pos.blocking_findings.length > 0;
        if (hasBlocking) {
          setSeatState(seatEl, 'state-blocking');
        } else {
          const stance = String(pos.stance || '').toLowerCase();
          if (stance === 'favours_a' || stance === 'favours_b' || stance === 'a' || stance === 'b') {
            setSeatState(seatEl, 'state-done-pass');
          } else if (stance === 'conditional') {
            setSeatState(seatEl, 'state-done-flag');
          } else {
            setSeatState(seatEl, 'state-done-strike');
          }
        }
      }
      // 2. If Advisers are actively working
      else if (isStageAdvisersWorking && isSelected) {
        setSeatState(seatEl, 'state-working');
      }
      // 3. If Selected by router
      else if (isSelected) {
        setSeatState(seatEl, 'state-selected');
      }
      // 4. If Scored by router
      else if (scoreObj) {
        setSeatState(seatEl, 'state-scored');
      }
      // 5. Default Idle
      else {
        setSeatState(seatEl, 'state-idle');
      }
    });
  }

  function renderRoutingPanel(routing) {
    if (!dom.routingRowsContainer) return;
    dom.routingRowsContainer.innerHTML = '';

    if (!routing || !Array.isArray(routing.scores)) {
      dom.routingRowsContainer.innerHTML = `
        <div class="empty-state" style="padding: 1.5rem 0;">
          <p class="text-small text-muted">Awaiting brief routing...</p>
        </div>
      `;
      return;
    }

    const selectedList = Array.isArray(routing.selected) ? routing.selected.map(s => String(s).toLowerCase()) : [];
    if (dom.routingSummaryBadge) {
      dom.routingSummaryBadge.textContent = `${selectedList.length} / 6 Selected`;
    }

    if (dom.routingQueryBox) {
      dom.routingQueryBox.textContent = routing.routing_query ? `"${routing.routing_query}"` : 'No routing query generated.';
    }

    // Sort scores descending
    const sortedScores = [...routing.scores].sort((a, b) => (b.similarity || 0) - (a.similarity || 0));

    sortedScores.forEach(scoreItem => {
      const aid = String(scoreItem.adviser_id || '').toLowerCase();
      const advInfo = ALL_ADVISERS.find(a => a.id === aid) || { name: scoreItem.adviser_id, icon: '🏛️', lens: '' };
      const isSelected = selectedList.includes(aid) || !!scoreItem.selected;
      const sim = Math.max(0, Math.min(1, scoreItem.similarity || 0));
      const fillPct = Math.round(sim * 100);

      // Check always-include rule
      const reasonLower = (scoreItem.reason || '').toLowerCase();
      const isAlwaysInclude = reasonLower.includes('always-include');

      const row = document.createElement('div');
      row.setAttribute('class', 'routing-row');
      row.id = `routing-row-${aid}`;
      row.setAttribute('data-adviser', aid);

      row.innerHTML = `
        <div class="routing-row-header">
          <div class="routing-row-name">
            <span>${advInfo.icon}</span>
            <span>${escapeHtml(advInfo.name)}</span>
            ${isAlwaysInclude ? `<span class="badge badge-human" style="font-size: 0.625rem; padding: 0.1rem 0.4rem;">Always-include</span>` : ''}
          </div>
          <div style="display: flex; align-items: center; gap: 0.5rem;">
            <span class="mono-num" style="font-size: 0.8125rem; font-weight: 600; color: var(--text-primary);">${sim.toFixed(2)}</span>
            ${isSelected ? `<span class="badge badge-cyan"><span class="badge-dot"></span>Selected</span>` : `<span class="badge badge-neutral">Not selected</span>`}
          </div>
        </div>

        <div class="routing-track-wrapper">
          <div class="routing-track-fill ${isSelected ? '' : 'not-selected'}" style="width: ${fillPct}%;"></div>
          <!-- Dashed relative margin threshold indicator at ~60% -->
          <div class="threshold-marker" style="left: 60%;" title="Floor threshold (0.60)"></div>
        </div>

        <div class="routing-row-footer">
          <span>${escapeHtml(scoreItem.best_matching_capability || advInfo.lens)}</span>
          <span class="text-muted">${escapeHtml(scoreItem.reason || (isSelected ? 'Selected' : 'Below floor'))}</span>
        </div>
      `;

      // Hover linkage between row and Arena seat
      row.addEventListener('mouseenter', () => highlightAdviser(aid, true));
      row.addEventListener('mouseleave', () => highlightAdviser(aid, false));

      // Click scrolls to debate section
      row.addEventListener('click', () => scrollToDebate(aid));

      dom.routingRowsContainer.appendChild(row);
    });
  }

  // Bidirectional Hover & Click-to-Scroll Linkage
  function highlightAdviser(aid, active) {
    const seat = document.getElementById(`seat-${aid}`);
    const row = document.getElementById(`routing-row-${aid}`);

    if (seat) {
      if (active) {
        seat.style.transform = 'translateY(-4px) scale(1.05)';
        seat.style.transition = 'all 0.2s var(--ease-command)';
      } else {
        seat.style.transform = '';
      }
    }

    if (row) {
      if (active) {
        row.classList.add('is-highlighted');
      } else {
        row.classList.remove('is-highlighted');
      }
    }
  }

  function setupArenaSeatListeners() {
    ALL_ADVISERS.forEach(adv => {
      const seat = document.getElementById(`seat-${adv.id}`);
      if (!seat) return;

      seat.addEventListener('mouseenter', () => highlightAdviser(adv.id, true));
      seat.addEventListener('mouseleave', () => highlightAdviser(adv.id, false));
      seat.addEventListener('click', () => scrollToDebate(adv.id));
    });
  }

  function scrollToDebate(aid) {
    const debateSec = document.getElementById('debate');
    if (debateSec) {
      debateSec.scrollIntoView({ behavior: 'smooth' });
      showToast('Adviser Selected', `Navigated to ${aid.toUpperCase()} adviser section`, 'info');
    }
  }

  // =========================================================================
  // 7. Timer & Pipeline View Utilities
  // =========================================================================

  function startTimer() {
    stopTimer();
    state.startTime = Date.now();
    updateTimerDisplay();
    state.elapsedTimer = setInterval(updateTimerDisplay, 1000);
  }

  function stopTimer() {
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
  }

  function updateTimerDisplay() {
    if (!state.startTime || !dom.elapsedTimer) return;
    const diff = Math.floor((Date.now() - state.startTime) / 1000);
    const mins = Math.floor(diff / 60).toString().padStart(2, '0');
    const secs = (diff % 60).toString().padStart(2, '0');
    dom.elapsedTimer.textContent = `${mins}:${secs}`;
  }

  function resetPipelineView() {
    hidePipelineError();
    if (dom.pipelineStatusLabel) dom.pipelineStatusLabel.textContent = 'PIPELINE STATUS: READY';
    if (dom.stepProgressLine) dom.stepProgressLine.style.width = '0%';
    if (dom.stepItems) {
      dom.stepItems.forEach((item, idx) => {
        item.classList.remove('is-active', 'is-completed');
        if (idx === 0) item.classList.add('is-active');
      });
    }

    // Reset arena seats and connectors (all SVG elements)
    ALL_ADVISERS.forEach(adv => {
      const seat = document.getElementById(`seat-${adv.id}`);
      const connector = document.getElementById(`connector-${adv.id}`);
      const scoreBadge = document.getElementById(`seat-score-${adv.id}`);
      if (seat) {
        setSeatState(seat, 'state-idle');
      }
      if (connector) {
        connector.setAttribute('class', 'arena-connector');
      }
      if (scoreBadge) {
        scoreBadge.textContent = '0.00';
      }
    });

    if (dom.arenaStatusBadge) {
      dom.arenaStatusBadge.setAttribute('class', 'badge badge-neutral');
      dom.arenaStatusBadge.textContent = 'Idle';
    }
  }

  function showPipelineError(stage, detail) {
    if (!dom.pipelineError) return;
    if (dom.pipelineErrorStage) dom.pipelineErrorStage.textContent = stage;
    if (dom.pipelineErrorDetail) dom.pipelineErrorDetail.textContent = detail;
    dom.pipelineError.style.display = 'flex';
  }

  function hidePipelineError() {
    if (dom.pipelineError) {
      dom.pipelineError.style.display = 'none';
    }
  }

  function escapeHtml(str) {
    if (typeof str !== 'string') return String(str || '');
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // =========================================================================
  // 8. Initialization
  // =========================================================================

  function init() {
    initTheme();
    loadSavedRuns();
    setupSavedRunsListener();
    setupArenaSeatListeners();

    if (dom.btnLoadSample) {
      dom.btnLoadSample.addEventListener('click', loadSampleBrief);
    }

    if (dom.btnAddConstraint) {
      dom.btnAddConstraint.addEventListener('click', () => addConstraintRow(''));
    }

    if (dom.btnRunDebate) {
      dom.btnRunDebate.addEventListener('click', handleRunDebate);
    }

    // Auto-load sample brief on initial visit if form is empty
    if (dom.briefQuestion && !dom.briefQuestion.value) {
      loadSampleBrief();
    }
  }

  // Start on DOMContentLoaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
