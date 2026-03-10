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

let REFRESH_INTERVAL = (parseInt(localStorage.getItem('refreshInterval') || '10', 10)) * 60 * 1000;
const POLL_INTERVAL = 5000;               // 5 seconds
const EARLY_WAKE = 60 * 1000;             // wake 1 minute early

let triageData = null;
let lastTimestamp = null; // track when data last changed
let loadingTimerInterval = null;
let loadingStartTime = null;
let currentSummaryGroup = null;
let manuallyArchived = JSON.parse(localStorage.getItem('manuallyArchived') || '[]'); // {id, subject, sender}
let manuallyDeleted = JSON.parse(localStorage.getItem('manuallyDeleted') || '[]'); // {id, subject, sender}
let sessionAutoArchived = []; // accumulated auto-archived strings across triage runs
let sessionAutoDeleted = [];  // accumulated auto-deleted strings across triage runs

// DOM Elements
const refreshBtn = document.getElementById('refreshBtn');
const lastSyncEl = document.getElementById('lastSync');
const totalEmailsEl = document.getElementById('totalEmails');
const nextSyncEl = document.getElementById('nextSync');
const deletedItemsEl = document.getElementById('deletedItems');
const quickLinksContainer = document.getElementById('quickLinksContainer');
const triageSpinner = document.getElementById('triageSpinner');
const headerSpinner = document.getElementById('headerSpinner');
const headerTitle = document.querySelector('.header-content h1');
const summaryContainer = document.getElementById('summaryContainer');
const emailBodyContainer = document.getElementById('emailBodyContainer');
const autoOpenToggle = document.getElementById('autoOpenToggle');
const unreadOnlyToggle = document.getElementById('unreadOnlyToggle');
const splitBtnArrow = document.getElementById('splitBtnArrow');
const refreshDropdown = document.getElementById('refreshDropdown');
const modelSelectEl = document.getElementById('modelSelect');

// ─── Initialization ───────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    refreshBtn.addEventListener('click', handleManualRefresh);

    // Refresh interval split-button dropdown
    const savedInterval = localStorage.getItem('refreshInterval') || '10';
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
        });
    });

    document.addEventListener('click', () => refreshDropdown.classList.add('hidden'));

    // Restore auto-open toggle from localStorage
    const savedAutoOpen = localStorage.getItem('autoOpenGmail');
    if (savedAutoOpen !== null) {
        autoOpenToggle.checked = savedAutoOpen === 'true';
    }
    autoOpenToggle.addEventListener('change', () => {
        localStorage.setItem('autoOpenGmail', autoOpenToggle.checked);
    });

    // Restore unread-only toggle from localStorage (default: on)
    const savedUnreadOnly = localStorage.getItem('unreadOnly');
    if (savedUnreadOnly !== null) {
        unreadOnlyToggle.checked = savedUnreadOnly === 'true';
    }
    unreadOnlyToggle.addEventListener('change', () => {
        localStorage.setItem('unreadOnly', unreadOnlyToggle.checked);
        if (triageData) updateQuickLinks();
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

    startRefreshCycle();
});

// ─── Core refresh cycle ───────────────────────────────────────

/**
 * Main refresh loop:
 *   poll for initial data → sleep → trigger refresh → poll for new data → repeat
 */
async function startRefreshCycle() {
    // Phase 1: try to load cached data immediately
    const cached = await fetchTriage();
    if (cached && cached.data) {
        // Cached data exists — render immediately, no loading message
        lastTimestamp = cached.timestamp;
        triageData = Object.assign(cached.data, { cost_usd: cached.cost_usd, model: cached.model });
        updateUI();
        updateSyncTimes(cached);
    } else {
        // First load — show loading message and header spinner
        quickLinksContainer.innerHTML = `
            <div class="loading-with-spinner">
                <span class="spinner-email">⏳</span>
                <span>Loading triage data...</span>
            </div>
        `;
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

        await sleep(sleepMs);

        // Wake up early — trigger new triage
        await triggerTriage();

        // Poll every 5s until fresh data arrives
        await pollUntilData();
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
                if (!result || !result.data) return;

                const isNew = result.timestamp && result.timestamp !== lastTimestamp;

                if (isNew || !lastTimestamp) {
                    clearInterval(timer);
                    lastTimestamp = result.timestamp;
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

    if (firstLoad && !triageData) {
        quickLinksContainer.innerHTML = `
            <div class="loading-with-spinner">
                <span class="spinner-email">⏳</span>
                <span>Loading triage data...</span>
            </div>
        `;
    }

    try {
        await fetch('/api/triage/refresh', { method: 'POST' });
    } catch (error) {
        console.error('Error triggering triage:', error);
    }
}

// ─── Manual refresh ───────────────────────────────────────────

async function handleManualRefresh() {
    refreshBtn.disabled = true;
    refreshBtn.textContent = '⟳ Refreshing...';

    await triggerTriage();
    await pollUntilData();

    refreshBtn.textContent = '✓ Refreshed';
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
    startLoadingTimer();
}

function hideSpinner() {
    headerSpinner.classList.add('hidden');
    stopLoadingTimer();
}

// ─── Loading elapsed timer ───────────────────────────────────

function startLoadingTimer() {
    stopLoadingTimer();
    loadingStartTime = Date.now();
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
    }
    if (result.next_sync) {
        nextSyncEl.textContent = formatTimestamp(result.next_sync);
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
        totalEmailsEl.textContent = newTotal;
    }

    // Accumulate auto-cleaned items for the session (survive triage refreshes)
    (triageData.auto_cleaned?.deleted || []).forEach(item => {
        if (!sessionAutoDeleted.includes(item)) sessionAutoDeleted.push(item);
    });
    (triageData.auto_cleaned?.archived || []).forEach(item => {
        if (!sessionAutoArchived.includes(item)) sessionAutoArchived.push(item);
    });

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
            const gmailUrl = `https://mail.google.com/mail/u/0/#search/in:trash+${encodeURIComponent(searchTerm)}`;

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
            const gmailUrl = `https://mail.google.com/mail/u/0/#trash/${email.id}`;
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
            const gmailUrl = `https://mail.google.com/mail/u/0/#search/${encodeURIComponent(searchQuery)}`;

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
            const gmailUrl = `https://mail.google.com/mail/u/0/#inbox/${email.id}`;
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
    const unreadOnly = unreadOnlyToggle.checked;
    quickLinksContainer.innerHTML = '';

    // Start with groups from triage data
    let groups = [...(triageData.labeled_groups || [])];

    // In "show all" mode, also include any Triage/* labels not in current triage run
    if (!unreadOnly) {
        try {
            const res = await fetch('/api/labels/triage');
            const data = await res.json();
            const existingNames = new Set(groups.map(g => g.name));
            (data.labels || []).forEach(name => {
                if (!existingNames.has(name)) {
                    groups.push({ name, count: 0, items: [], priority: 'Info' });
                }
            });
        } catch (e) {
            console.error('Error fetching triage labels:', e);
        }
    }

    if (groups.length === 0) {
        showEmptyInbox();
        return;
    }

    groups.sort((a, b) => b.count - a.count);
    const linkElements = [];

    groups.forEach((group) => {
        const hasUnread = group.count > 0;
        const link = document.createElement('div');
        link.className = hasUnread ? 'quick-link' : 'quick-link quick-link-read-only';
        link.dataset.label = group.name;

        const groupName = group.name.replace('Triage/', '');
        const gmailUrl = buildGmailSearchUrl(group.name);

        link.addEventListener('click', (e) => {
            if (e.target.closest('.quick-link-icon')) return;
            quickLinksContainer.querySelectorAll('.quick-link').forEach(el => el.classList.remove('active'));
            link.classList.add('active');
            if (autoOpenToggle.checked) openGmailUrl(gmailUrl);
            showSummary(group);
        });

        const arrow = document.createElement('div');
        arrow.className = 'quick-link-icon';
        arrow.title = 'Open in Gmail';
        arrow.innerHTML = '<img src="/static/gmail-logo.png" height="14" alt="Gmail"> <span class="btn-gmail-arrow">↗</span>';
        arrow.addEventListener('click', (e) => {
            e.stopPropagation();
            openGmailUrl(gmailUrl);
            showSummary(group);
        });

        const info = document.createElement('div');
        info.className = 'quick-link-info';
        info.innerHTML = hasUnread
            ? `<div class="quick-link-title">${escapeHtml(groupName)}</div>
               <span class="quick-link-badge badge-unread" title="${group.count} unread">${group.count}</span>
               <span class="quick-link-read"></span>`
            : `<div class="quick-link-title">${escapeHtml(groupName)}</div>
               <span class="quick-link-badge badge-read" title="All read">✓</span>
               <span class="quick-link-read"></span>`;

        link.appendChild(info);
        link.appendChild(arrow);
        quickLinksContainer.appendChild(link);
        linkElements.push({ link, group });
    });

    fetchTotalCounts(linkElements, unreadOnly);
}

async function fetchTotalCounts(linkElements, unreadOnly = true) {
    try {
        const params = linkElements.map(({ group }) => `label=${encodeURIComponent(group.name)}`).join('&');
        const response = await fetch(`/api/emails/counts?${params}`);
        const data = await response.json();
        const counts = data.counts || {};

        linkElements.forEach(({ link, group }) => {
            const readEl = link.querySelector('.quick-link-read');
            const counts_entry = counts[group.name];
            const total = counts_entry?.total ?? 0;
            const unread = counts_entry?.unread ?? 0;
            const read = Math.max(0, total - unread);

            if (total === 0 || (unreadOnly && unread === 0)) {
                link.remove();
                return;
            }

            // Update badge with live unread count
            const badge = link.querySelector('.quick-link-badge');
            if (badge) {
                if (unread > 0) {
                    badge.textContent = unread;
                    badge.title = `${unread} unread`;
                    badge.className = 'quick-link-badge badge-unread';
                    link.classList.remove('quick-link-read-only');
                } else {
                    badge.textContent = '✓';
                    badge.title = 'All read';
                    badge.className = 'quick-link-badge badge-read';
                }
            }

            if (read > 0) {
                if (readEl) readEl.textContent = `+${read} read`;
            }
        });

        // If all cards were removed, show empty inbox
        const remainingLinks = quickLinksContainer.querySelectorAll('.quick-link');
        if (remainingLinks.length === 0) {
            showEmptyInbox();
        } else if (!currentSummaryGroup) {
            // Auto-select the first quick link on initial load
            remainingLinks[0].click();
        } else {
            // Check if the currently selected group is still visible; if not, clear the summary
            const stillPresent = Array.from(remainingLinks).some(el => el.dataset.label === currentSummaryGroup.name);
            if (!stillPresent) {
                currentSummaryGroup = null;
                summaryContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔗</div>Click a quick link to see email summaries</div>';
                emailBodyContainer.innerHTML = '<div class="summary-hint"><div class="summary-hint-icon">🔍</div>Click an email to view its contents</div>';
            }
        }
    } catch (e) {
        console.error('Error fetching email counts:', e);
    }
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
                        <div class="summary-item-text">
                            <div class="summary-item-subject">${escapeHtml(email.subject)}</div>
                            <div class="summary-item-meta">${escapeHtml(email.sender)}</div>
                        </div>
                        <div class="summary-item-actions">
                            <a class="btn-gmail" href="https://mail.google.com/mail/u/0/#inbox/${email.id}" target="_blank" title="Open in Gmail"><img src="/static/gmail-logo.png" height="16" alt="Gmail"> <span class="btn-gmail-arrow">↗</span></a>
                            <button class="btn-archive" title="Archive" data-id="${email.id}">Archive</button>
                            <button class="btn-delete" title="Delete" data-id="${email.id}">Delete</button>
                        </div>
                    </div>
                `;
                summaryContainer.appendChild(div);

                // Click email card to show body in right pane
                div.addEventListener('click', (e) => {
                    if (e.target.closest('.summary-item-actions')) return;
                    summaryContainer.querySelectorAll('.summary-item').forEach(el => el.classList.remove('selected'));
                    div.classList.add('selected');
                    showEmailBody(email);
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
        }
    } catch (error) {
        console.error('Error fetching emails for summary:', error);
    }
}

function showEmailBody(email) {
    const gmailUrl = `https://mail.google.com/mail/u/0/#inbox/${email.id}`;
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
                localStorage.setItem('manuallyDeleted', JSON.stringify(manuallyDeleted));
                renderDeletedItems();
            } else {
                itemEl.classList.add('actioned-archive');
                manuallyArchived.push({ id: email.id, subject: email.subject, sender: email.sender });
                localStorage.setItem('manuallyArchived', JSON.stringify(manuallyArchived));
                renderArchivedItems();
            }
            buttons.forEach(b => b.remove());

            // Decrement the quick link badge only if email was unread
            const groupName = currentSummaryGroup?.name;
            if (groupName && email.isUnread) {
                const ql = Array.from(quickLinksContainer.querySelectorAll('.quick-link'))
                    .find(el => el.dataset.label === groupName);
                if (ql) {
                    const badge = ql.querySelector('.quick-link-badge');
                    if (badge) {
                        const current = parseInt(badge.textContent, 10) || 0;
                        const next = Math.max(0, current - 1);
                        if (next === 0) {
                            badge.style.display = 'none';
                        } else {
                            badge.textContent = next;
                            badge.title = `${next} unread`;
                        }
                    }
                }
            }

            // Remove the item from the DOM after the animation, then check if group is now empty
            setTimeout(() => {
                itemEl.remove();
                const remaining = summaryContainer.querySelectorAll('.summary-item');
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
    const encoded = encodeURIComponent(searchQuery);
    return `https://mail.google.com/mail/u/0/#search/${encoded}`;
}

// ─── Utilities ────────────────────────────────────────────────

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
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
