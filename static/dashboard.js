/**
 * Gmail Triage Dashboard - Frontend Logic
 *
 * Refresh strategy:
 *   1. Poll every 5s until initial triage data arrives
 *   2. Sleep for (REFRESH_INTERVAL - 1 minute)
 *   3. Trigger a new triage (POST /api/triage/refresh)
 *   4. Poll every 5s until new data is detected (timestamp changed)
 *   5. Repeat from step 2
 */

let gmailUser = '0'; // populated from /api/config (GMAIL_USER env var)

let REFRESH_INTERVAL = (parseInt(localStorage.getItem('refreshInterval') || '5', 10)) * 60 * 1000;
const POLL_INTERVAL = 5000;               // 5 seconds
const EARLY_WAKE = 60 * 1000;             // wake 1 minute early

let triageData = null;
let lastTimestamp = null; // track when data last changed
let loadingTimerInterval = null;
let loadingStartTime = localStorage.getItem('loadingStartTime') ? parseInt(localStorage.getItem('loadingStartTime'), 10) : null;
let currentSummaryGroup = null;
let manuallyArchived = JSON.parse(sessionStorage.getItem('manuallyArchived') || '[]'); // {id, subject, sender}
let manuallyDeleted = JSON.parse(sessionStorage.getItem('manuallyDeleted') || '[]'); // {id, subject, sender}
let sessionAutoArchived = JSON.parse(sessionStorage.getItem('sessionAutoArchived') || '[]'); // accumulated auto-archived strings across triage runs
let sessionAutoDeleted = JSON.parse(sessionStorage.getItem('sessionAutoDeleted') || '[]');  // accumulated auto-deleted strings across triage runs
let refreshCycleSleepTimer = null; // track the sleep timer so we can cancel it on page visibility change
let quickLinksUpdateInFlight = false; // prevent concurrent updateQuickLinks calls
let previousLiveUnreadTotal = parseInt(localStorage.getItem('lastKnownUnreadTotal') ?? '-1', 10);
let currentUnreadCount = 0;

// DOM Elements
const refreshBtn = document.getElementById('refreshBtn');
const lastSyncEl = document.getElementById('lastSync');
const nextSyncEl = document.getElementById('nextSync');
const deletedItemsEl = document.getElementById('deletedItems');
const quickLinksContainer = document.getElementById('quickLinksContainer');
const triageSpinner = document.getElementById('triageSpinner');
const headerSpinner = document.getElementById('headerSpinner');
const headerTitle = document.querySelector('.header-content h1');
const summaryContainer = document.getElementById('summaryContainer');
const emailBodyContainer = document.getElementById('emailBodyContainer');
const unreadOnlyToggle = document.getElementById('unreadOnlyToggle');
const splitBtnArrow = document.getElementById('splitBtnArrow');
const refreshDropdown = document.getElementById('refreshDropdown');
const modelSelectEl = document.getElementById('modelSelect');

const undoToastContainer = document.getElementById('undoToastContainer');

// Mobile overlay elements
const mobileEmailOverlay = document.getElementById('mobileEmailOverlay');
const mobileEmailList = document.getElementById('mobileEmailList');
const mobileOverlayTitle = document.getElementById('mobileOverlayTitle');
const mobileOverlayGmail = document.getElementById('mobileOverlayGmail');
const mobileOverlayBack = document.getElementById('mobileOverlayBack');

// ─── Mobile helpers ───────────────────────────────────────────

function isMobile() { return window.innerWidth <= 768; }



// Icon SVGs (Heroicon outlines, 20×20 viewBox)
const ICON_ARCHIVE = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z"/></svg>`;
const ICON_TRASH = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/></svg>`;
const ICON_ENVELOPE_UNREAD = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75"/><circle cx="18.5" cy="5.5" r="3.5" fill="#3b82f6" stroke="white" stroke-width="1"/></svg>`;
const ICON_ENVELOPE_READ = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="20" height="20"><path stroke-linecap="round" stroke-linejoin="round" d="M21.75 9v.906a2.25 2.25 0 01-1.183 1.981l-6.478 3.488M2.25 9v.906a2.25 2.25 0 001.183 1.981l6.478 3.488m8.839 2.51l-4.66-2.51m0 0l-1.023-.55a2.25 2.25 0 00-2.134 0l-1.022.55m0 0l-4.661 2.51m16.5 1.615a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V8.844a2.25 2.25 0 011.183-1.98l7.5-4.04a2.25 2.25 0 012.134 0l7.5 4.04a2.25 2.25 0 011.183 1.98V19.5z"/></svg>`;

// ─── Favicon Guard ────────────────────────────────────────────
// Chrome PWA windows can steal the favicon from iframes (e.g. Gmail preview).
// Watch <head> and re-assert our static favicon if it gets replaced.

const STATIC_FAVICON = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📧</text></svg>";

function assertFavicon() {
    const link = document.getElementById('staticFavicon');
    // Use getAttribute (raw value) not .href (normalized URL) to avoid infinite loop
    if (link && link.getAttribute('href') !== STATIC_FAVICON) {
        link.setAttribute('href', STATIC_FAVICON);
    }
}

// Watch only the favicon element's href — not the whole <head> — to avoid
// triggering on document.title changes (which fire childList mutations in <head>
// and can cause an infinite assertFavicon loop when flashTitle is running).
const _faviconEl = document.getElementById('staticFavicon');
if (_faviconEl) {
    new MutationObserver(assertFavicon).observe(_faviconEl, {
        attributes: true,
        attributeFilter: ['href'],
    });
}

// ─── Favicon Badge ────────────────────────────────────────────

function updateFaviconBadge(count) {
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');

    // Draw base emoji
    ctx.font = '52px serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('📧', 32, 34);

    if (count > 0) {
        const label = count > 99 ? '99+' : String(count);
        const isLong = label.length > 2;
        const radius = 14; // ~10% smaller than original 1/4-quadrant size
        const bx = 48, by = 16;

        // White border then blue circle
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(bx, by, radius + 2, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#0a84ff';
        ctx.beginPath();
        ctx.arc(bx, by, radius, 0, Math.PI * 2);
        ctx.fill();

        // White count text
        ctx.fillStyle = '#ffffff';
        ctx.font = `bold ${isLong ? 14 : 20}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, bx, by);
    }

    const headerIcon = document.getElementById('unreadBadgeIcon');
    if (headerIcon) headerIcon.src = canvas.toDataURL('image/png');
}

function setUnreadCount(n) {
    currentUnreadCount = n;
    updateFaviconBadge(n);
    if ('setAppBadge' in navigator) {
        if (n > 0) navigator.setAppBadge(n);
        else navigator.clearAppBadge();
    }
}

// ─── Initialization ───────────────────────────────────────────

function startApp() {
    // Render the header icon immediately so it doesn't show as broken during first sync
    updateFaviconBadge(0);

    // Request notification permission on first load
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    refreshBtn.addEventListener('click', handleManualRefresh);

    // Refresh interval split-button dropdown
    const savedInterval = localStorage.getItem('refreshInterval') || '5';
    REFRESH_INTERVAL = parseInt(savedInterval, 10) * 60 * 1000;
    refreshDropdown.querySelectorAll('.split-btn-option').forEach(opt => {
        if (opt.dataset.value === savedInterval) opt.classList.add('selected');
        else opt.classList.remove('selected');
    });

    splitBtnArrow.addEventListener('click', (e) => {
        e.stopPropagation();
        refreshDropdown.classList.toggle('hidden');
    });

    refreshDropdown.querySelectorAll('.split-btn-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const mins = opt.dataset.value;
            localStorage.setItem('refreshInterval', mins);
            REFRESH_INTERVAL = parseInt(mins, 10) * 60 * 1000;
            refreshDropdown.querySelectorAll('.split-btn-option').forEach(o => o.classList.remove('selected'));
            opt.classList.add('selected');
            refreshDropdown.classList.add('hidden');
            // Recompute next sync display from last triage time + new interval
            const remaining = lastTimestamp
                ? Math.max(0, new Date(lastTimestamp).getTime() + REFRESH_INTERVAL - Date.now())
                : REFRESH_INTERVAL;
            updateNextSync(remaining);
        });
    });

    document.addEventListener('click', () => refreshDropdown.classList.add('hidden'));

    // Restore unread-only toggle from localStorage (default: on)
    const savedUnreadOnly = localStorage.getItem('unreadOnly');
    if (savedUnreadOnly !== null) {
        unreadOnlyToggle.checked = savedUnreadOnly === 'true';
    }
    unreadOnlyToggle.addEventListener('change', () => {
        localStorage.setItem('unreadOnly', unreadOnlyToggle.checked);
        updateQuickLinks();
    });

    // Restore saved model from localStorage, sync to backend
    const savedModel = localStorage.getItem('triageModel');
    if (savedModel && modelSelectEl) {
        modelSelectEl.value = savedModel;
        fetch('/api/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: savedModel })
        }).catch(() => {});
    } else {
        fetch('/api/model')
            .then(r => r.json())
            .then(data => { if (data.model && modelSelectEl) modelSelectEl.value = data.model; })
            .catch(() => {});
    }

    modelSelectEl.addEventListener('change', async () => {
        const model = modelSelectEl.value;
        localStorage.setItem('triageModel', model);
        try {
            await fetch('/api/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model })
            });
        } catch (e) {
            console.error('Failed to set model:', e);
        }
    });

    if (mobileOverlayBack) {
        mobileOverlayBack.addEventListener('click', () => {
            mobileEmailOverlay.classList.add('hidden');
        });
    }

    // Load Gmail account config (GMAIL_USER for URL routing)
    fetch('/api/config')
        .then(r => r.json())
        .then(cfg => { if (cfg.gmail_user) gmailUser = cfg.gmail_user; })
        .catch(() => {});

    // Load quick links immediately from live Gmail labels, in parallel with triage
    updateQuickLinks();
    startRefreshCycle();
}

document.addEventListener('DOMContentLoaded', checkPinAuth);

// ─── PIN Auth ─────────────────────────────────────────────────

async function checkPinAuth() {
    try {
        const res = await fetch('/api/pin/status');
        const data = await res.json();
        if (data.configured) {
            document.querySelector('.btn-logout').style.display = 'inline-flex';
        }
        if (!data.configured || data.authenticated) {
            startApp();
        } else {
            document.getElementById('pinOverlay').classList.remove('hidden');
            initPinPad();
        }
    } catch(e) {
        startApp(); // fail open if status check fails
    }
}

let pinDigits = [];

function initPinPad() {
    document.querySelectorAll('.pin-btn[data-digit]').forEach(btn => {
        btn.addEventListener('click', () => pinInput(btn.dataset.digit));
    });
    document.getElementById('pinBack').addEventListener('click', pinBackspace);
    document.getElementById('pinClear').addEventListener('click', pinClearAll);
    document.getElementById('pinSubmit').addEventListener('click', submitPin);
    document.addEventListener('keydown', e => {
        if (/^[0-9]$/.test(e.key)) pinInput(e.key);
        else if (e.key === 'Backspace') pinBackspace();
        else if (e.key === 'Enter') submitPin();
    });
}

function pinInput(digit) {
    pinDigits.push(digit);
    updatePinDots();
}

function pinBackspace() {
    pinDigits.pop();
    updatePinDots();
}

function pinClearAll() {
    pinDigits = [];
    updatePinDots();
}

function updatePinDots() {
    const container = document.getElementById('pinDots');
    container.innerHTML = '';
    const count = Math.max(pinDigits.length, 1);
    for (let i = 0; i < count; i++) {
        const dot = document.createElement('span');
        dot.className = 'pin-dot' + (i < pinDigits.length ? ' filled' : '');
        container.appendChild(dot);
    }
}

async function submitPin() {
    if (pinDigits.length === 0) return;
    const pin = pinDigits.join('');
    const res = await fetch('/api/pin/verify', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pin})
    });
    if (res.ok) {
        document.getElementById('pinOverlay').classList.add('hidden');
        startApp();
    } else {
        const card = document.getElementById('pinCard');
        card.classList.add('shake');
        document.getElementById('pinError').textContent = 'Incorrect PIN';
        setTimeout(() => { card.classList.remove('shake'); }, 400);
        pinDigits = [];
        updatePinDots();
    }
}

// ─── Core refresh cycle ───────────────────────────────────────

/**
 * Main refresh loop:
 *   poll for initial data → sleep → trigger refresh → poll for new data → repeat
 */
async function startRefreshCycle() {
    // Phase 1: try to load cached data immediately
    const cached = await fetchTriage();
    if (cached?.error?.type === 'auth') {
        showAuthError(cached.error.message);
        return; // Don't start the cycle — auth must be fixed first
    }
    if (cached && cached.data) {
        // Cached data exists — render immediately, no loading message
        lastTimestamp = cached.timestamp;
        triageData = Object.assign(cached.data, { cost_usd: cached.cost_usd, model: cached.model });
        updateUI();
        updateSyncTimes(cached);
    } else {
        // Sync loading timer with server-side elapsed time before showing spinner.
        // This prevents the timer resetting to 0 on page refresh mid-triage.
        try {
            const runRes = await fetch('/api/triage/running');
            const runData = await runRes.json();
            if (runData.running && runData.elapsed_seconds > 0) {
                loadingStartTime = Date.now() - Math.floor(runData.elapsed_seconds * 1000);
                localStorage.setItem('loadingStartTime', loadingStartTime);
            }
        } catch (e) { /* non-fatal */ }
        showSpinner();
        await pollUntilData();
    }

    // Phase 2: loop forever — sleep until next sync, trigger, poll
    while (true) {
        // Calculate remaining time from actual last triage, not from "now"
        const lastTriageTime = new Date(lastTimestamp).getTime();
        const nextTriageTime = lastTriageTime + REFRESH_INTERVAL;
        const remainingMs = Math.max(0, nextTriageTime - Date.now());
        const sleepMs = Math.max(0, remainingMs - EARLY_WAKE);

        // Sleep but check progress at shorter intervals to detect stale sleep
        // Wake early if ~30s has passed since last check to recalculate
        await sleepWithRecalc(sleepMs);

        // Recalculate in case time drifted while in background
        const nowLastTriageTime = new Date(lastTimestamp).getTime();
        const nowNextTriageTime = nowLastTriageTime + REFRESH_INTERVAL;
        const nowRemainingMs = Math.max(0, nowNextTriageTime - Date.now());

        // Only trigger if we're actually close to the scheduled time (within 30s)
        if (nowRemainingMs <= 30000) {
            // Wake up early — trigger new triage
            const triageResult = await triggerTriage();

            if (triageResult?.skipped) {
                console.log('[cycle] Triage skipped:', triageResult.reason);
                hideSpinner();
                // Advance lastTimestamp so the cycle sleeps a full interval before retrying
                lastTimestamp = new Date().toISOString();
                updateNextSync(REFRESH_INTERVAL);
            } else {
                // Poll every 5s until fresh data arrives
                await pollUntilData();
            }
        }
    }
}

/**
 * Poll GET /api/triage every 5s until data with groups arrives
 * (or until timestamp changes from what we already have).
 */
function pollUntilData() {
    return new Promise((resolve) => {
        const timer = setInterval(async () => {
            try {
                const result = await fetchTriage();
                if (!result) return;

                // Auth error stored in cache — surface it immediately
                if (result.error?.type === 'auth') {
                    clearInterval(timer);
                    hideSpinner();
                    showAuthError(result.error.message);
                    resolve();
                    return;
                }

                // Other error types (timeout, etc.) — only surface if triage is not currently running
                // (a new triage clears the error; stale errors linger until a fresh run completes)
                if (result.error?.type === 'other' && !result.running) {
                    clearInterval(timer);
                    hideSpinner();
                    showError(result.error.message);
                    resolve();
                    return;
                }

                if (!result.data) return;

                const isNew = result.timestamp && result.timestamp !== lastTimestamp;

                if (isNew || !lastTimestamp) {
                    clearInterval(timer);
                    lastTimestamp = result.timestamp;

                    // If server timestamp is much newer than our loading start time,
                    // the server was restarted — reset the timer to show correct elapsed time
                    if (loadingStartTime && result.timestamp) {
                        const serverTime = new Date(result.timestamp).getTime();
                        const timeDiff = serverTime - loadingStartTime;
                        // If more than 10 seconds difference, assume server restarted
                        if (timeDiff > 10000) {
                            loadingStartTime = serverTime;
                        }
                    }

                    triageData = Object.assign(result.data, { model: result.model });
                    updateUI();
                    updateSyncTimes(result);
                    hideSpinner();
                    resolve();
                }
            } catch (error) {
                console.error('[poll] Error in pollUntilData callback:', error);
            }
        }, POLL_INTERVAL);
    });
}

/**
 * Trigger a new triage run on the backend (POST).
 * If firstLoad is true, show loading state in quick links.
 * Otherwise, keep existing data visible and only show header spinner.
 */
async function triggerTriage(firstLoad = false) {
    showSpinner();

    try {
        const res = await fetch('/api/triage/refresh', { method: 'POST' });
        return await res.json();
    } catch (error) {
        console.error('Error triggering triage:', error);
        return null;
    }
}

// ─── Manual refresh ───────────────────────────────────────────

async function handleManualRefresh() {
    refreshBtn.disabled = true;
    refreshBtn.textContent = '⟳ Refreshing...';

    // Reset baseline so fetchTotalCounts fires a notification after this refresh
    previousLiveUnreadTotal = -1;

    const triageResult = await triggerTriage();

    if (triageResult?.auth_error) {
        hideSpinner();
        showAuthError(triageResult.error);
        refreshBtn.disabled = false;
        refreshBtn.textContent = '⟳ Refresh Now';
        return;
    }

    if (triageResult?.skipped) {
        hideSpinner();
        updateNextSync(REFRESH_INTERVAL);
        refreshBtn.textContent = '— No new emails';
    } else {
        await pollUntilData();
        refreshBtn.textContent = '✓ Refreshed';
    }

    refreshBtn.disabled = false;
    setTimeout(() => {
        refreshBtn.textContent = '⟳ Refresh Now';
    }, 2000);
}

// ─── API helpers ──────────────────────────────────────────────

async function fetchTriage() {
    try {
        const response = await fetch('/api/triage');
        return await response.json();
    } catch (error) {
        console.error('Error fetching triage:', error);
        return null;
    }
}

// ─── Spinners ─────────────────────────────────────────────────

function showSpinner() {
    headerSpinner.classList.remove('hidden');
    // Don't restart the timer if it's already running (e.g., page refresh during triage)
    if (!loadingTimerInterval) {
        startLoadingTimer();
    }
}

function hideSpinner() {
    headerSpinner.classList.add('hidden');
    stopLoadingTimer();
    loadingStartTime = null;
    localStorage.removeItem('loadingStartTime');
}

// ─── Loading elapsed timer ───────────────────────────────────

function startLoadingTimer() {
    stopLoadingTimer();
    // If loading start time wasn't set (fresh triage), set it now
    // Otherwise keep the existing one (from page refresh during triage)
    if (!loadingStartTime) {
        loadingStartTime = Date.now();
        localStorage.setItem('loadingStartTime', loadingStartTime);
    }
    updateLoadingTimerDisplay(0);
    loadingTimerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - loadingStartTime) / 1000);
        updateLoadingTimerDisplay(elapsed);
    }, 1000);
}

function stopLoadingTimer() {
    if (loadingTimerInterval) {
        clearInterval(loadingTimerInterval);
        loadingTimerInterval = null;
    }
}

function updateLoadingTimerDisplay(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    const timeStr = mins > 0
        ? `${mins}:${secs.toString().padStart(2, '0')}`
        : `${secs}s`;
    const el = document.getElementById('loadingTimer');
    if (el) el.textContent = `(${timeStr})`;
}

// ─── UI updates ───────────────────────────────────────────────

function updateSyncTimes(result) {
    if (result.timestamp) {
        lastSyncEl.textContent = formatTime(result.timestamp);
        // Compute next sync from the actual last triage time + interval (not from now)
        const next = new Date(new Date(result.timestamp).getTime() + REFRESH_INTERVAL);
        nextSyncEl.textContent = formatTimestamp(next.toISOString());
    }
    if (result.model && modelSelectEl) {
        modelSelectEl.value = result.model;
    }
}

function updateNextSync(ms) {
    const next = new Date(Date.now() + ms);
    nextSyncEl.textContent = formatTimestamp(next.toISOString());
}

function updateUI() {
    if (!triageData) return;

    // Show load time
    if (loadingStartTime) {
        const elapsed = Math.floor((Date.now() - loadingStartTime) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = `${mins}:${secs.toString().padStart(2, '0')}`;
        const loadTimeEl = document.getElementById('loadTime');
        if (loadTimeEl) loadTimeEl.textContent = `Load: ${timeStr}`;
    }

    const { summary, labeled_groups } = triageData;

    if (summary) {
        // Compute "new emails" as sum of labeled group unread counts
        const newTotal = (labeled_groups || []).reduce((sum, g) => sum + (g.count || 0), 0);
        setUnreadCount(newTotal);
    }

    // Fire notification for newly auto-cleaned items (auto-deleted or auto-archived emails
    // that never appear in labeled groups — the only signal that new mail was handled)
    const prevDeletedSet = new Set(sessionAutoDeleted);
    const prevArchivedSet = new Set(sessionAutoArchived);
    const newAutoCleanedCount = [
        ...(triageData.auto_cleaned?.deleted || []).filter(i => !prevDeletedSet.has(i)),
        ...(triageData.auto_cleaned?.archived || []).filter(i => !prevArchivedSet.has(i)),
    ].length;
    if (newAutoCleanedCount > 0) {
        sendNewEmailNotification(newAutoCleanedCount, []);
    }

    // Accumulate auto-cleaned items for the session (survive triage refreshes)
    (triageData.auto_cleaned?.deleted || []).forEach(item => {
        if (!sessionAutoDeleted.includes(item)) sessionAutoDeleted.push(item);
    });
    sessionStorage.setItem('sessionAutoDeleted', JSON.stringify(sessionAutoDeleted));
    (triageData.auto_cleaned?.archived || []).forEach(item => {
        if (!sessionAutoArchived.includes(item)) sessionAutoArchived.push(item);
    });
    sessionStorage.setItem('sessionAutoArchived', JSON.stringify(sessionAutoArchived));

    renderDeletedItems();
    renderArchivedItems();
    updateQuickLinks();
}

function renderDeletedItems() {
    deletedItemsEl.innerHTML = '';

    const autoDeleted = sessionAutoDeleted;

    if (autoDeleted.length > 0) {
        const header = document.createElement('div');
        header.className = 'archived-section-label';
        header.textContent = `Auto-deleted (${autoDeleted.length})`;
        deletedItemsEl.appendChild(header);

        autoDeleted.forEach(item => {
            const jiraMatch = item.match(/^([A-Z]+-\d+)/);
            const searchTerm = jiraMatch ? jiraMatch[1] : item.split('—')[0].trim();
            const gmailUrl = `https://mail.google.com/mail/u/${gmailUser}/#search/in:trash+${encodeURIComponent(searchTerm)}`;

            const parts = item.split('—');
            const subject = parts[0].trim();
            const detail = parts.slice(1).join('—').trim();

            const a = document.createElement('a');
            a.className = 'archived-email-item';
            a.href = gmailUrl;
            a.target = '_blank';
            a.innerHTML = `
                <div class="archived-email-text">
                    <div class="archived-email-subject">${escapeHtml(subject)}</div>
                    ${detail ? `<div class="archived-email-sender">${escapeHtml(detail)}</div>` : ''}
                </div>
                <span class="archived-email-launch"><img src="/static/gmail-logo.png" height="14" alt="Gmail"> <span class="btn-gmail-arrow">↗</span></span>
            `;
            deletedItemsEl.appendChild(a);
        });
    }

    if (manuallyDeleted.length > 0) {
        const header = document.createElement('div');
        header.className = 'archived-section-label';
        header.textContent = `Manually deleted (${manuallyDeleted.length})`;
        deletedItemsEl.appendChild(header);

        manuallyDeleted.forEach(email => {
            const gmailUrl = `https://mail.google.com/mail/u/${gmailUser}/#trash/${email.id}`;
            const a = document.createElement('a');
            a.className = 'archived-email-item';
            a.href = gmailUrl;
            a.target = '_blank';
            a.innerHTML = `
                <div class="archived-email-text">
                    <div class="archived-email-subject">${escapeHtml(email.subject)}</div>
                    <div class="archived-email-sender">${escapeHtml(email.sender)}</div>
                </div>
                <span class="archived-email-launch"><img src="/static/gmail-logo.png" height="14" alt="Gmail"> <span class="btn-gmail-arrow">↗</span></span>
            `;
            deletedItemsEl.appendChild(a);
        });
    }

    if (autoDeleted.length === 0 && manuallyDeleted.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'archived-empty';
        empty.textContent = 'No deleted emails this session';
        deletedItemsEl.appendChild(empty);
    }
}

function renderArchivedItems() {
    const container = document.getElementById('archivedItems');
    if (!container) return;

    container.innerHTML = '';

    const autoArchivedItems = sessionAutoArchived;

    // Auto-archived from triage
    if (autoArchivedItems.length > 0) {
        const header = document.createElement('div');
        header.className = 'archived-section-label';
        header.textContent = `Auto-archived (${autoArchivedItems.length})`;
        container.appendChild(header);

        autoArchivedItems.forEach(item => {
            const parts = item.split('←');
            const subject = parts[0].trim();
            const sender = parts[1]?.trim() || '';
            const searchQuery = `in:all subject:"${subject}"`;
            const gmailUrl = `https://mail.google.com/mail/u/${gmailUser}/#search/${encodeURIComponent(searchQuery)}`;

            const a = document.createElement('a');
            a.className = 'archived-email-item';
            a.href = gmailUrl;
            a.target = '_blank';
            a.innerHTML = `
                <div class="archived-email-text">
                    <div class="archived-email-subject">${escapeHtml(subject)}</div>
                    ${sender ? `<div class="archived-email-sender">${escapeHtml(sender)}</div>` : ''}
                </div>
                <span class="archived-email-launch"><img src="/static/gmail-logo.png" height="14" alt="Gmail"> <span class="btn-gmail-arrow">↗</span></span>
            `;
            container.appendChild(a);
        });
    }

    // Manually archived from dashboard
    if (manuallyArchived.length > 0) {
        const header = document.createElement('div');
        header.className = 'archived-section-label';
        header.textContent = `Manually archived (${manuallyArchived.length})`;
        container.appendChild(header);

        manuallyArchived.forEach(email => {
            const gmailUrl = `https://mail.google.com/mail/u/${gmailUser}/#inbox/${email.id}`;
            const a = document.createElement('a');
            a.className = 'archived-email-item';
            a.href = gmailUrl;
            a.target = '_blank';
            a.innerHTML = `
                <div class="archived-email-text">
                    <div class="archived-email-subject">${escapeHtml(email.subject)}</div>
                    <div class="archived-email-sender">${escapeHtml(email.sender)}</div>
                </div>
                <span class="archived-email-launch"><img src="/static/gmail-logo.png" height="14" alt="Gmail"> <span class="btn-gmail-arrow">↗</span></span>
            `;
            container.appendChild(a);
        });
    }

    if (autoArchivedItems.length === 0 && manuallyArchived.length === 0) {
        container.innerHTML = '<p class="archived-empty">No archived emails this session</p>';
    }
}

function showAuthError(message) {
    quickLinksContainer.innerHTML = `
        <div class="auth-error-banner">
            <div class="auth-error-icon">🔐</div>
            <div class="auth-error-title">Authentication Error</div>
            <div class="auth-error-message">${message || 'Gmail or AI assistant CLI authentication failed. Please re-authenticate and restart the server.'}</div>
            <div class="auth-error-hint">Check the server console for details, then restart <code>make run</code>.</div>
        </div>
    `;
    currentSummaryGroup = null;
    summaryContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔐</div>Authentication required</div>';
    emailBodyContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔑</div>Re-authenticate to continue</div>';
}

function showError(message) {
    quickLinksContainer.innerHTML = `
        <div class="auth-error-banner">
            <div class="auth-error-icon">⚠️</div>
            <div class="auth-error-title">Triage Error</div>
            <div class="auth-error-message">${message || 'Triage failed. Check the server console for details.'}</div>
            <div class="auth-error-hint">Press <strong>Refresh Now</strong> to try again.</div>
        </div>
    `;
    currentSummaryGroup = null;
    summaryContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">⚠️</div>Triage failed</div>';
    emailBodyContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔄</div>Try refreshing</div>';
}

function showEmptyInbox() {
    const scenes = ['🏖️', '🌅', '🏔️', '🌴', '🏕️', '🌈'];
    const icon = scenes[Math.floor(Math.random() * scenes.length)];
    quickLinksContainer.innerHTML = `
        <div class="empty-inbox">
            <div class="empty-inbox-icon">${icon}</div>
            <div>Inbox clear — nothing to triage. Enjoy the calm!</div>
        </div>
    `;
    currentSummaryGroup = null;
    summaryContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔗</div>Click a quick link to see email summaries</div>';
    emailBodyContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔍</div>Click an email to view its contents</div>';
}

async function updateQuickLinks() {
    // Drop concurrent calls — the next triage cycle will refresh anyway
    if (quickLinksUpdateInFlight) return;
    quickLinksUpdateInFlight = true;

    try {
        await _updateQuickLinksInner();
    } finally {
        quickLinksUpdateInFlight = false;
    }
}

async function _updateQuickLinksInner() {
    const unreadOnly = unreadOnlyToggle.checked;

    // Build candidate groups: current triage data groups + all Gmail Triage/* labels
    let groups = [...(triageData?.labeled_groups || [])];
    const triageNames = new Set(groups.map(g => g.name));

    try {
        const res = await fetch('/api/labels/triage');
        const data = await res.json();
        (data.labels || []).forEach(name => {
            if (!triageNames.has(name)) {
                groups.push({ name, count: 0, items: [], priority: 'Info' });
            }
        });
    } catch (e) {
        console.error('Error fetching triage labels:', e);
    }

    if (groups.length === 0) {
        showEmptyInbox();
        return;
    }

    // Fetch live counts before rendering so we only create cards for non-empty labels
    let counts = {};
    try {
        const params = groups.map(g => `label=${encodeURIComponent(g.name)}`).join('&');
        const res = await fetch(`/api/emails/counts?${params}`);
        const data = await res.json();
        counts = data.counts || {};
    } catch (e) {
        console.error('Error fetching email counts:', e);
    }

    // Filter to only groups with live emails (keep on API error so real emails aren't hidden)
    const visibleGroups = groups.filter(group => {
        const entry = counts[group.name];
        if (entry === null) return true; // API error — keep it
        const total = entry?.total ?? group.count;
        const unread = entry?.unread ?? 0;
        if (total === 0) return false;
        if (unreadOnly && unread === 0) return false;
        return true;
    });

    // All fetches done — now atomically replace the container contents
    quickLinksContainer.innerHTML = '';

    if (visibleGroups.length === 0) {
        showEmptyInbox();
        return;
    }

    // Sort by live unread count descending
    visibleGroups.sort((a, b) => {
        const aUnread = counts[a.name]?.unread ?? a.count;
        const bUnread = counts[b.name]?.unread ?? b.count;
        return bUnread - aUnread;
    });

    visibleGroups.forEach(group => {
        const entry = counts[group.name];
        const liveUnread = entry?.unread ?? group.count;
        const liveTotal = entry?.total ?? group.count;
        const liveRead = Math.max(0, liveTotal - liveUnread);
        const hasUnread = liveUnread > 0;

        const link = document.createElement('div');
        link.className = hasUnread ? 'quick-link' : 'quick-link quick-link-read-only';
        link.dataset.label = group.name;

        const groupName = group.name.replace('Triage/', '');
        const gmailUrl = buildGmailSearchUrl(group.name);

        link.addEventListener('click', (e) => {
            if (e.target.closest('.quick-link-icon')) return;
            quickLinksContainer.querySelectorAll('.quick-link').forEach(el => el.classList.remove('active'));
            link.classList.add('active');
            if (isMobile()) {
                showMobileEmailList(group, gmailUrl);
            } else {
                showSummary(group);
            }
        });

        const arrow = document.createElement('div');
        arrow.className = 'quick-link-icon';
        arrow.title = 'Open in Gmail';
        arrow.innerHTML = '<img src="/static/gmail-logo.png" height="14" alt="Gmail"> <span class="btn-gmail-arrow">↗</span>';
        arrow.addEventListener('click', (e) => {
            e.stopPropagation();
            openGmailUrl(gmailUrl);
        });

        const info = document.createElement('div');
        info.className = 'quick-link-info';
        info.innerHTML = hasUnread
            ? `<div class="quick-link-title">${escapeHtml(groupName)}</div>
               <span class="quick-link-badge badge-unread" title="${liveUnread} unread">${liveUnread}</span>
               <span class="quick-link-read">${liveRead > 0 ? `+${liveRead} read` : ''}</span>`
            : `<div class="quick-link-title">${escapeHtml(groupName)}</div>
               <span class="quick-link-badge badge-read" title="All read">✓</span>
               <span class="quick-link-read">${liveRead > 0 ? `+${liveRead} read` : ''}</span>`;

        link.appendChild(info);
        link.appendChild(arrow);
        quickLinksContainer.appendChild(link);
    });

    const allLinks = quickLinksContainer.querySelectorAll('.quick-link');
    if (allLinks.length === 0) {
        showEmptyInbox();
    } else if (!currentSummaryGroup) {
        // Auto-select the first quick link on initial load
        allLinks[0].click();
    } else {
        // Preserve selection: mark the previously selected group active
        const activeEl = Array.from(allLinks).find(el => el.dataset.label === currentSummaryGroup.name);
        if (activeEl) {
            activeEl.classList.add('active');
            // Update group reference to pick up fresh triage summaries if available
            const updatedGroup = visibleGroups.find(g => g.name === currentSummaryGroup.name);
            if (updatedGroup) currentSummaryGroup = updatedGroup;
        } else {
            currentSummaryGroup = null;
            summaryContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔗</div>Click a quick link to see email summaries</div>';
            emailBodyContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔍</div>Click an email to view its contents</div>';
        }
    }

    // Notification check using live counts
    const liveUnreadTotal = Object.values(counts).reduce((sum, entry) => sum + (entry?.unread ?? 0), 0);
    if (liveUnreadTotal > previousLiveUnreadTotal) {
        const added = previousLiveUnreadTotal === -1 ? liveUnreadTotal : liveUnreadTotal - previousLiveUnreadTotal;
        const notifGroups = visibleGroups.map(g => ({
            name: g.name,
            count: counts[g.name]?.unread ?? 0,
        })).filter(g => g.count > 0);
        sendNewEmailNotification(added, notifGroups);
    }
    previousLiveUnreadTotal = liveUnreadTotal;
    localStorage.setItem('lastKnownUnreadTotal', liveUnreadTotal);
    setUnreadCount(liveUnreadTotal);
}

// ─── Summary pane ─────────────────────────────────────────────

async function showSummary(group) {
    currentSummaryGroup = group;
    summaryContainer.innerHTML = '';
    emailBodyContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔍</div>Click an email to view its contents</div>';

    const title = document.createElement('h3');
    title.textContent = group.name.replace('Triage/', '');
    title.style.marginBottom = '12px';
    summaryContainer.appendChild(title);

    // Show triage items as placeholder while fetching live data
    if (group.items && group.items.length > 0) {
        group.items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'summary-item';
            div.innerHTML = `<div class="summary-item-subject">${escapeHtml(item)}</div>`;
            summaryContainer.appendChild(div);
        });
    }

    // Fetch live email data with message IDs for archive/delete
    try {
        const response = await fetch(`/api/emails?label=${encodeURIComponent(group.name)}`);
        const data = await response.json();
        console.log(`[summary] Fetched ${data.emails?.length ?? 0} emails for ${group.name}`, data.error || '');

        // Always replace placeholders — even when empty
        summaryContainer.innerHTML = '';
        summaryContainer.appendChild(title);

        if (data.emails && data.emails.length > 0) {
            data.emails.forEach(email => {
                const div = document.createElement('div');
                div.className = email.isUnread ? 'summary-item summary-item-unread' : 'summary-item summary-item-read';
                div.id = `email-${email.id}`;
                div.innerHTML = `
                    <div class="summary-item-row">
                        <button class="unread-dot ${email.isUnread ? 'unread-dot-active' : ''}" title="${email.isUnread ? 'Mark as read' : 'Mark as unread'}" data-id="${email.id}"></button>
                        <div class="summary-item-text">
                            <div class="summary-item-subject">${escapeHtml(email.subject)}</div>
                            <div class="summary-item-meta">${escapeHtml(email.sender)}</div>
                        </div>
                        <div class="summary-item-actions">
                            <a class="btn-gmail" href="https://mail.google.com/mail/u/${gmailUser}/#inbox/${email.id}" target="_blank" title="Open in Gmail"><img src="/static/gmail-logo.png" height="16" alt="Gmail"> <span class="btn-gmail-arrow">↗</span></a>
                            <button class="btn-archive" title="Archive" data-id="${email.id}">Archive</button>
                            <button class="btn-delete" title="Delete" data-id="${email.id}">Delete</button>
                        </div>
                    </div>
                `;
                summaryContainer.appendChild(div);

                // Click email card to show body in right pane
                div.addEventListener('click', (e) => {
                    if (e.target.closest('.summary-item-actions') || e.target.closest('.unread-dot')) return;
                    summaryContainer.querySelectorAll('.summary-item').forEach(el => el.classList.remove('selected'));
                    div.classList.add('selected');
                    showEmailBody(email);
                });

                div.querySelector('.unread-dot').addEventListener('click', (e) => {
                    e.stopPropagation();
                    toggleReadState(email, div);
                });
                div.querySelector('.btn-archive').addEventListener('click', (e) => {
                    e.stopPropagation();
                    emailAction('archive', email, div);
                });
                div.querySelector('.btn-delete').addEventListener('click', (e) => {
                    e.stopPropagation();
                    emailAction('delete', email, div);
                });
            });
        } else {
            const empty = document.createElement('p');
            empty.className = 'summary-hint';
            empty.innerHTML = '<div class="summary-hint-icon">🔗</div>No emails in this group — all clear!';
            summaryContainer.appendChild(empty);
            emailBodyContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔍</div>Click an email to view its contents</div>';

            // Group is empty — remove its quick link
            const ql = Array.from(quickLinksContainer.querySelectorAll('.quick-link'))
                .find(el => el.dataset.label === group.name);
            if (ql) {
                const allLinks = Array.from(quickLinksContainer.querySelectorAll('.quick-link'));
                const idx = allLinks.indexOf(ql);
                const nextLink = idx > 0 ? allLinks[idx - 1] : allLinks[idx + 1] || null;
                ql.remove();
                currentSummaryGroup = null;
                if (quickLinksContainer.querySelectorAll('.quick-link').length === 0) {
                    showEmptyInbox();
                } else if (nextLink) {
                    nextLink.click();
                }
            }
        }
    } catch (error) {
        console.error('Error fetching emails for summary:', error);
    }
}

// ─── Mobile email list overlay ────────────────────────────────

async function showMobileEmailList(group, gmailUrl) {
    currentSummaryGroup = group;
    mobileOverlayTitle.textContent = group.name.replace('Triage/', '');
    mobileOverlayGmail.href = gmailUrl;
    mobileOverlayGmail.target = '_blank';
    mobileEmailList.innerHTML = '<div class="loading">Loading...</div>';
    mobileEmailOverlay.classList.remove('hidden');

    try {
        const response = await fetch(`/api/emails?label=${encodeURIComponent(group.name)}`);
        const data = await response.json();

        mobileEmailList.innerHTML = '';

        if (!data.emails || data.emails.length === 0) {
            mobileEmailList.innerHTML = '<div class="loading">No emails in this group — all clear!</div>';
            return;
        }

        data.emails.forEach(email => {
            const row = document.createElement('div');
            row.className = `mobile-email-row ${email.isUnread ? 'mobile-row-unread' : 'mobile-row-read'}`;
            row.id = `mobile-email-${email.id}`;
            row.innerHTML = `
                <button class="btn-icon-action btn-icon-readstate unread-dot ${email.isUnread ? 'unread-dot-active' : ''}" title="${email.isUnread ? 'Mark as read' : 'Mark as unread'}" data-id="${escapeHtml(email.id)}">${email.isUnread ? ICON_ENVELOPE_UNREAD : ICON_ENVELOPE_READ}</button>
                <div class="mobile-row-text">
                    <div class="mobile-row-subject">${escapeHtml(email.subject)}</div>
                    <div class="mobile-row-sender">${escapeHtml(email.sender)}</div>
                </div>
                <div class="mobile-row-actions">
                    <a class="btn-icon-action" href="${isMobile() ? `https://mail.google.com/mail/mu/mp/#cv/Inbox/${escapeHtml(email.threadId || email.id)}` : `https://mail.google.com/mail/u/${gmailUser}/#inbox/${escapeHtml(email.threadId || email.id)}`}" target="_blank" title="Open in Gmail"><img src="/static/gmail-m.png" height="16" alt="Gmail"></a>
                    <button class="btn-icon-action btn-mobile-archive" title="Archive" data-id="${escapeHtml(email.id)}">${ICON_ARCHIVE}</button>
                    <button class="btn-icon-action btn-mobile-delete" title="Delete" data-id="${escapeHtml(email.id)}">${ICON_TRASH}</button>
                </div>
            `;
            mobileEmailList.appendChild(row);

            row.querySelector('.btn-icon-readstate').addEventListener('click', async (e) => {
                e.stopPropagation();
                const btn = row.querySelector('.btn-icon-readstate');
                await toggleReadState(email, row);
                btn.innerHTML = email.isUnread ? ICON_ENVELOPE_UNREAD : ICON_ENVELOPE_READ;
                row.className = `mobile-email-row ${email.isUnread ? 'mobile-row-unread' : 'mobile-row-read'}`;
            });

            row.querySelector('.btn-mobile-archive').addEventListener('click', (e) => {
                e.stopPropagation();
                emailAction('archive', email, row);
            });

            row.querySelector('.btn-mobile-delete').addEventListener('click', (e) => {
                e.stopPropagation();
                emailAction('delete', email, row);
            });
        });
    } catch (error) {
        console.error('Error fetching emails for mobile overlay:', error);
        mobileEmailList.innerHTML = '<div class="loading">Error loading emails</div>';
    }
}

function showEmailBody(email) {
    const gmailUrl = `https://mail.google.com/mail/u/${gmailUser}/#inbox/${email.id}`;
    emailBodyContainer.innerHTML = `
        <div class="email-body-header">
            <div class="email-body-title-row">
                <div class="email-body-subject">${escapeHtml(email.subject)}</div>
                <a class="email-open-btn" href="${gmailUrl}" target="_blank" title="Open in Gmail">Open in Gmail ↗</a>
            </div>
            <div class="email-body-meta">From: ${escapeHtml(email.sender)}</div>
            <div class="email-body-meta">Date: ${escapeHtml(email.date)}</div>
        </div>
        <div class="email-body-content" id="emailBodyContent"></div>
    `;

    const bodyContent = document.getElementById('emailBodyContent');
    const body = email.body || 'No content available';
    const isHtml = /<[a-z][\s\S]*>/i.test(body);

    if (isHtml) {
        const sanitized = sanitizeEmailHtml(body);
        const iframe = document.createElement('iframe');
        iframe.className = 'email-iframe';
        iframe.sandbox = '';  // No special permissions for iframe
        iframe.srcdoc = `
            <html><head><style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                       font-size: 14px; line-height: 1.6; color: #111827; margin: 0; padding: 0; }
                a { color: #3b82f6; }
                img { max-width: 100%; height: auto; }
            </style></head><body>${sanitized}</body></html>
        `;
        iframe.onload = () => {
            try {
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                iframe.style.height = doc.body.scrollHeight + 'px';
            } catch (e) { /* cross-origin fallback */ }
        };
        bodyContent.appendChild(iframe);
    } else {
        bodyContent.innerHTML = `<pre class="email-plain-text">${linkifyUrls(escapeHtml(body))}</pre>`;
    }
}

async function toggleReadState(email, itemEl) {
    const btn = itemEl.querySelector('.unread-dot');
    if (btn) btn.disabled = true;

    const newUnread = !email.isUnread;
    try {
        const response = await fetch('/api/emails/readstate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message_id: email.id, unread: newUnread })
        });
        const result = await response.json();

        if (result.success) {
            email.isUnread = newUnread;

            if (btn) {
                btn.classList.toggle('unread-dot-active', newUnread);
                btn.title = newUnread ? 'Mark as read' : 'Mark as unread';
                btn.disabled = false;
            }

            itemEl.classList.toggle('summary-item-unread', newUnread);
            itemEl.classList.toggle('summary-item-read', !newUnread);

            // Update quick link unread badge
            const groupName = currentSummaryGroup?.name;
            if (groupName) {
                const ql = Array.from(quickLinksContainer.querySelectorAll('.quick-link'))
                    .find(el => el.dataset.label === groupName);
                if (ql) {
                    const badge = ql.querySelector('.quick-link-badge');
                    if (badge) {
                        const current = parseInt(badge.textContent, 10) || 0;
                        const next = newUnread ? current + 1 : Math.max(0, current - 1);
                        badge.textContent = next;
                        badge.title = `${next} unread`;
                        badge.style.display = next === 0 ? 'none' : '';
                        badge.className = `quick-link-badge ${next > 0 ? 'badge-unread' : 'badge-read'}`;
                    }
                }
            }

            // Update header unread count
            setUnreadCount(Math.max(0, currentUnreadCount + (newUnread ? 1 : -1)));
        } else {
            if (btn) btn.disabled = false;
            console.error('Failed to toggle read state:', result.error);
        }
    } catch (err) {
        if (btn) btn.disabled = false;
        console.error('Error toggling read state:', err);
    }
}

function showUndoToast(action, email, groupName) {
    const label = action === 'archive' ? 'Archived' : 'Deleted';
    const subject = email.subject.length > 45 ? email.subject.slice(0, 45) + '…' : email.subject;

    const toast = document.createElement('div');
    toast.className = 'undo-toast';
    toast.innerHTML = `
        <div class="undo-toast-message">${label}: <strong>${escapeHtml(subject)}</strong></div>
        <button class="undo-toast-btn">Undo</button>
        <div class="undo-toast-progress"></div>
    `;
    undoToastContainer.appendChild(toast);

    let dismissed = false;

    const dismiss = () => {
        if (dismissed) return;
        dismissed = true;
        toast.classList.add('toast-out');
        setTimeout(() => toast.remove(), 280);
    };

    const timer = setTimeout(dismiss, 10000);

    toast.querySelector('.undo-toast-btn').addEventListener('click', async () => {
        if (dismissed) return;
        dismissed = true;
        clearTimeout(timer);
        toast.remove();

        const endpoint = action === 'archive' ? '/api/emails/unarchive' : '/api/emails/undelete';
        try {
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message_id: email.id })
            });
            const result = await res.json();
            if (result.success) {
                // Remove from manual tracking arrays
                if (action === 'archive') {
                    const idx = manuallyArchived.findIndex(e => e.id === email.id);
                    if (idx !== -1) {
                        manuallyArchived.splice(idx, 1);
                        sessionStorage.setItem('manuallyArchived', JSON.stringify(manuallyArchived));
                        renderArchivedItems();
                    }
                } else {
                    const idx = manuallyDeleted.findIndex(e => e.id === email.id);
                    if (idx !== -1) {
                        manuallyDeleted.splice(idx, 1);
                        sessionStorage.setItem('manuallyDeleted', JSON.stringify(manuallyDeleted));
                        renderDeletedItems();
                    }
                }
                // Refresh group summary if it's currently selected
                if (currentSummaryGroup && currentSummaryGroup.name === groupName) {
                    showSummary(currentSummaryGroup);
                }
                updateQuickLinks();
            } else {
                console.error('Undo failed:', result.error);
            }
        } catch (e) {
            console.error('Undo error:', e);
        }
    });
}

async function emailAction(action, email, itemEl) {
    const buttons = itemEl.querySelectorAll('button');
    buttons.forEach(b => b.disabled = true);

    try {
        const response = await fetch(`/api/emails/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message_id: email.id })
        });
        const result = await response.json();

        if (result.success) {
            // Show visual feedback: strikethrough for delete, fade for archive
            itemEl.classList.add('actioned');
            if (action === 'delete') {
                itemEl.classList.add('actioned-delete');
                manuallyDeleted.push({ id: email.id, subject: email.subject, sender: email.sender });
                sessionStorage.setItem('manuallyDeleted', JSON.stringify(manuallyDeleted));
                renderDeletedItems();
            } else {
                itemEl.classList.add('actioned-archive');
                manuallyArchived.push({ id: email.id, subject: email.subject, sender: email.sender });
                sessionStorage.setItem('manuallyArchived', JSON.stringify(manuallyArchived));
                renderArchivedItems();
            }
            buttons.forEach(b => b.remove());
            showUndoToast(action, email, currentSummaryGroup?.name);

            // Decrement the quick link badge (unread) or read counter (read email)
            const groupName = currentSummaryGroup?.name;
            if (groupName && !email.isUnread) {
                const ql = Array.from(quickLinksContainer.querySelectorAll('.quick-link'))
                    .find(el => el.dataset.label === groupName);
                if (ql) {
                    const readSpan = ql.querySelector('.quick-link-read');
                    if (readSpan) {
                        const match = readSpan.textContent.match(/\+(\d+)/);
                        const current = match ? parseInt(match[1], 10) : 0;
                        const next = Math.max(0, current - 1);
                        readSpan.textContent = next > 0 ? `+${next} read` : '';
                    }
                }
            } else if (groupName && email.isUnread) {
                const ql = Array.from(quickLinksContainer.querySelectorAll('.quick-link'))
                    .find(el => el.dataset.label === groupName);
                if (ql) {
                    const badge = ql.querySelector('.quick-link-badge');
                    if (badge) {
                        const current = parseInt(badge.textContent, 10) || 0;
                        const next = Math.max(0, current - 1);
                        if (next === 0 && unreadOnlyToggle.checked) {
                            // In unread-only mode, remove the pill when no unreads remain
                            const allLinks = Array.from(quickLinksContainer.querySelectorAll('.quick-link'));
                            const idx = allLinks.indexOf(ql);
                            const nextLink = idx > 0 ? allLinks[idx - 1] : allLinks[idx + 1] || null;
                            ql.remove();
                            currentSummaryGroup = null;
                            if (quickLinksContainer.querySelectorAll('.quick-link').length === 0) {
                                showEmptyInbox();
                            } else if (nextLink) {
                                nextLink.click();
                            }
                        } else if (next === 0) {
                            badge.style.display = 'none';
                        } else {
                            badge.textContent = next;
                            badge.title = `${next} unread`;
                        }
                    }
                }
            }

            // Decrement the header unread counter if email was unread
            if (email.isUnread) {
                setUnreadCount(Math.max(0, currentUnreadCount - 1));
            }

            // Remove the item from the DOM after the animation, then check if group is now empty
            setTimeout(() => {
                itemEl.remove();
                const remaining = mobileEmailOverlay.classList.contains('hidden')
                    ? summaryContainer.querySelectorAll('.summary-item')
                    : mobileEmailList.querySelectorAll('.mobile-email-row');
                if (remaining.length === 0) {
                    // Find the card to the left (or right if leftmost) before removing
                    let nextLink = null;
                    if (groupName) {
                        const allLinks = Array.from(quickLinksContainer.querySelectorAll('.quick-link'));
                        const ql = allLinks.find(el => el.dataset.label === groupName);
                        if (ql) {
                            const idx = allLinks.indexOf(ql);
                            nextLink = idx > 0 ? allLinks[idx - 1] : allLinks[idx + 1] || null;
                            ql.remove();
                        }
                    }
                    // If no quick links remain, show empty inbox state
                    if (quickLinksContainer.querySelectorAll('.quick-link').length === 0) {
                        showEmptyInbox();
                    } else if (nextLink) {
                        // Auto-select the adjacent card to the left
                        nextLink.click();
                    }
                }
            }, 600);
        } else {
            console.error(`${action} failed:`, result.error);
            buttons.forEach(b => b.disabled = false);
        }
    } catch (error) {
        console.error(`Error ${action}ing email:`, error);
        buttons.forEach(b => b.disabled = false);
    }
}

// ─── Gmail URL opener ─────────────────────────────────────────

function openGmailUrl(url) {
    const tab = window.open('about:blank', '_blank');
    if (tab) tab.location.href = url;
}

// ─── Gmail URL builder ────────────────────────────────────────

function buildGmailSearchUrl(groupName) {
    const searchQuery = `in:inbox label:${groupName}`;
    if (isMobile()) {
        return `https://mail.google.com/mail/mu/mp/#tl/search/${encodeURIComponent(searchQuery)}`;
    }
    return `https://mail.google.com/mail/u/${gmailUser}/#search/${encodeURIComponent(searchQuery)}`;
}

// ─── Utilities ────────────────────────────────────────────────

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Sleep with periodic recalculation.
 * Wakes up every 30s to check if we're close to the next sync time.
 * This prevents stale sleeps when the page is backgrounded.
 */
function sleepWithRecalc(ms) {
    return new Promise((resolve) => {
        const CHECK_INTERVAL = 30 * 1000; // wake every 30 seconds
        const endTime = Date.now() + ms;

        const checkAndResolve = () => {
            const remaining = Math.max(0, endTime - Date.now());
            if (remaining <= 0) {
                resolve();
            } else if (remaining > CHECK_INTERVAL) {
                // Still have time — sleep for another CHECK_INTERVAL and check again
                setTimeout(checkAndResolve, CHECK_INTERVAL);
            } else {
                // Close to the end — sleep the remaining time
                setTimeout(resolve, remaining);
            }
        };

        checkAndResolve();
    });
}

/**
 * Format a past timestamp as relative time (e.g., "2m ago")
 */
function formatTime(isoString) {
    if (!isoString) return '-';

    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString();
}

/**
 * Format a future timestamp as absolute time (e.g., "5:45 PM")
 */
function formatTimestamp(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function linkifyUrls(text) {
    return text.replace(
        /https?:\/\/[^\s<>&"')\]]+/g,
        url => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`
    );
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

let titleFlashInterval = null;

function flashTitle(message) {
    const original = 'Gmail Dashboard';
    let toggle = true;
    if (titleFlashInterval) clearInterval(titleFlashInterval);
    titleFlashInterval = setInterval(() => {
        document.title = toggle ? `🔔 ${message}` : original;
        toggle = !toggle;
    }, 1000);
    // Stop flashing when user focuses the tab
    const stop = () => {
        clearInterval(titleFlashInterval);
        titleFlashInterval = null;
        document.title = original;
        window.removeEventListener('focus', stop);
    };
    window.addEventListener('focus', stop);
}

/**
 * Send a browser notification when new emails are detected.
 * Supported in Firefox and Chrome.
 */
function sendNewEmailNotification(count, groups) {
    if (!('Notification' in window) || Notification.permission !== 'granted') {
        return;
    }

    // Build group summary for the notification
    let title = count === 1 ? '1 new email' : `${count} new emails`;

    // Get the top groups by count for the notification body
    const topGroups = [...(groups || [])].sort((a, b) => b.count - a.count).slice(0, 3);
    const details = topGroups.map(g => {
        const groupName = g.name.replace('Triage/', '');
        return `${groupName} (${g.count})`;
    }).join(', ');

    const options = {
        icon: '/static/gmail-logo.png',
        body: details || 'Check your inbox',
        tag: 'gmail-triage-notification',
        badge: '/static/gmail-logo.png',
        requireInteraction: false
    };

    // Flash the page title as a fallback for suppressed OS/browser toasts
    flashTitle(title);

    try {
        const notification = new Notification(title, options);
        notification.onclick = () => {
            // Focus the window when notification is clicked
            window.focus();
            notification.close();
        };
        setTimeout(() => notification.close(), 30000);
    } catch (e) {
        console.error('Failed to send notification:', e);
    }
}

/**
 * Sanitize HTML email body to remove dangerous tags and remote resource references.
 * Removes: <script>, <iframe>, <object>, <embed>, <link>, <img>, <source>, <track>, srcset, on* attributes.
 * Rewrites: data:// and mailto: URLs are allowed; http(s)://, //, and other external URLs are removed from src/href.
 * Returns: sanitized HTML string, or null if unsafe content detected after cleaning.
 */
function sanitizeEmailHtml(html) {
    // Check for obviously dangerous tags that we cannot safely allow
    const dangerousTags = /<(script|iframe|object|embed|link|img|source|track|meta|form|input|button|style)\b/gi;
    if (dangerousTags.test(html)) {
        // These tags are inherently dangerous; strip them all
        html = html.replace(/<(script|iframe|object|embed|link|img|source|track)\b[^>]*>[\s\S]*?<\/\1>/gi, '');
        html = html.replace(/<(meta|form|input|button|style)\b[^>]*>/gi, '');
    }

    // Remove srcset, src, href that point to external URLs (http://, https://, //, etc.)
    // But allow data: URLs and mailto: links
    html = html.replace(/\s+(srcset|src|href)=(['"])(?!(?:data:|mailto:))(?:https?:|\/\/)?[^'"]*\2/gi, '');

    // Remove CSS url() references to external resources in inline styles
    html = html.replace(/\burl\s*\(\s*(['"]?)(?!(?:data:))(?:https?:|\/\/)?[^)]*\1\s*\)/gi, '');

    // Remove on* event handlers
    html = html.replace(/\s+on\w+=(['"]).*?\1/gi, '');

    return html;
}
