/**
 * SRE Hub - Liquid Glass Dashboard JavaScript
 * Fast, interactive, RTL & AI-driven task management
 */

// Application State
const state = {
    activeTab: 'myTasks',
    user: null,
    myTasksData: null,
    sreTasksData: null,
    analyticsData: null,
    chartInstances: {},
    currentModalTaskId: null,
    filters: {
        search: '',
        form: 'ALL',
        urgency: 'ALL',
        state: 'ALL'
    },
    sort: {
        field: null,      // 'duration', 'assignee', 'state'
        direction: 'asc'  // 'asc' or 'desc'
    }
};

// DOM Content Loaded Entrypoint
document.addEventListener('DOMContentLoaded', async () => {
    initLucide();
    await loadUserProfile();
    await refreshData(false);

    // Keyboard shortcut: Esc to close modal
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeTaskModal();
        }
    });
});

function initLucide() {
    if (window.lucide) {
        window.lucide.createIcons();
    }
}

// ==============================================================================
// Tab Navigation
// ==============================================================================

function switchTab(tabName) {
    if (state.activeTab === tabName) return;
    state.activeTab = tabName;

    const myTasksBtn = document.getElementById('tabMyTasksBtn');
    const sreFormsBtn = document.getElementById('tabSreFormsBtn');
    const analyticsBtn = document.getElementById('tabAnalyticsBtn');
    const viewMyTasks = document.getElementById('viewMyTasks');
    const viewSreForms = document.getElementById('viewSreForms');
    const viewAnalytics = document.getElementById('viewAnalytics');
    const filterToolbar = document.querySelector('.filter-toolbar');
    const formFilterGroup = document.getElementById('formFilterGroup');

    // Reset button states
    [myTasksBtn, sreFormsBtn, analyticsBtn].forEach(b => b?.classList.remove('active'));
    [viewMyTasks, viewSreForms, viewAnalytics].forEach(v => v?.classList.remove('active'));

    if (tabName === 'myTasks') {
        myTasksBtn?.classList.add('active');
        viewMyTasks?.classList.add('active');
        if (filterToolbar) filterToolbar.style.display = 'flex';
        if (formFilterGroup) formFilterGroup.style.display = 'none';
        updateStateFilterOptions();
        applyFilters();
    } else if (tabName === 'sreForms') {
        sreFormsBtn?.classList.add('active');
        viewSreForms?.classList.add('active');
        if (filterToolbar) filterToolbar.style.display = 'flex';
        if (formFilterGroup) formFilterGroup.style.display = 'flex';
        updateStateFilterOptions();
        applyFilters();
    } else if (tabName === 'analytics') {
        analyticsBtn?.classList.add('active');
        viewAnalytics?.classList.add('active');
        // Filter toolbar is not needed on Analytics tab
        if (filterToolbar) filterToolbar.style.display = 'none';
        if (!state.analyticsData) {
            loadAnalyticsData();
        } else {
            renderAllCharts(state.analyticsData);
        }
    }
    initLucide();
}

// ==============================================================================
// Data Loading & API Calls
// ==============================================================================

async function loadUserProfile() {
    try {
        const res = await fetch('/api/me');
        if (res.ok) {
            state.user = await res.json();
            renderUserProfile(state.user);
        }
    } catch (err) {
        console.error('Error loading user profile:', err);
    }
}

function renderUserProfile(user) {
    const avatarEl = document.getElementById('userAvatar');
    const nameEl = document.getElementById('userName');

    if (nameEl) nameEl.textContent = user.username || 'مهندس SRE';
    if (avatarEl) {
        if (user.profile_picture) {
            avatarEl.innerHTML = `<img src="${escapeHtml(user.profile_picture)}" alt="${escapeHtml(user.username)}">`;
        } else {
            avatarEl.textContent = user.initials || 'SRE';
            if (user.color) avatarEl.style.backgroundColor = user.color;
        }
    }
}

async function refreshData(force = false) {
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) refreshBtn.classList.add('spinning');

    try {
        // Fetch all endpoints concurrently
        const [myTasksRes, sreTasksRes, analyticsRes] = await Promise.all([
            fetch(`/api/tasks/my${force ? '?refresh=true' : ''}`),
            fetch(`/api/tasks/sre-forms${force ? '?refresh=true' : ''}`),
            fetch(`/api/analytics/sre${force ? '?refresh=true' : ''}`)
        ]);

        if (myTasksRes.ok) {
            state.myTasksData = await myTasksRes.json();
            updateMyTasksStats(state.myTasksData);
            if (state.activeTab === 'myTasks') renderMyTasks();
        }

        if (sreTasksRes.ok) {
            state.sreTasksData = await sreTasksRes.json();
            updateSreTasksStats(state.sreTasksData);
            populateFormFilterOptions(state.sreTasksData.form_names);
            if (state.activeTab === 'sreForms') renderSreTasks();
        }

        if (analyticsRes.ok) {
            state.analyticsData = await analyticsRes.json();
            if (state.activeTab === 'analytics') renderAllCharts(state.analyticsData);
        }

        updateStateFilterOptions();
        updateGlobalStats();
        if (force) showToast('اطلاعات با موفقیت بروزرسانی شد', 'success');
    } catch (err) {
        console.error('Error refreshing data:', err);
        showToast('خطا در دریافت اطلاعات از سرور', 'error');
    } finally {
        if (refreshBtn) refreshBtn.classList.remove('spinning');
    }
}

async function loadAnalyticsData() {
    try {
        const res = await fetch('/api/analytics/sre');
        if (res.ok) {
            state.analyticsData = await res.json();
            renderAllCharts(state.analyticsData);
        }
    } catch (err) {
        console.error('Error loading analytics:', err);
    }
}

function updateMyTasksStats(data) {
    const badge = document.getElementById('myTasksCountBadge');
    const countTag = document.getElementById('myTasksSectionCount');
    if (badge) badge.textContent = toPersianNumber(data.total_tasks || 0);
    if (countTag) countTag.textContent = `${toPersianNumber(data.total_tasks || 0)} تسک`;
}

function updateSreTasksStats(data) {
    const badge = document.getElementById('sreTasksCountBadge');
    const countTag = document.getElementById('sreSectionCount');
    if (badge) badge.textContent = toPersianNumber(data.total_tasks || 0);
    if (countTag) countTag.textContent = `${toPersianNumber(data.total_tasks || 0)} درخواست`;
}

function updateGlobalStats() {
    const totalEl = document.getElementById('statTotalTasks');
    const critEl = document.getElementById('statCriticalTasks');
    const highEl = document.getElementById('statHighTasks');

    const total = (state.myTasksData?.total_tasks || 0) + (state.sreTasksData?.total_tasks || 0);
    const critical = (state.myTasksData?.critical_count || 0) + (state.sreTasksData?.critical_count || 0);
    const high = (state.myTasksData?.high_count || 0) + (state.sreTasksData?.high_count || 0);

    if (totalEl) totalEl.textContent = toPersianNumber(total);
    if (critEl) critEl.textContent = toPersianNumber(critical);
    if (highEl) highEl.textContent = toPersianNumber(high);
}

function populateFormFilterOptions(formNames) {
    const select = document.getElementById('formSelect');
    if (!select || !formNames) return;

    const currentVal = select.value;
    select.innerHTML = '<option value="ALL">همه فرم‌ها</option>';
    formNames.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        select.appendChild(opt);
    });
    if (formNames.includes(currentVal)) select.value = currentVal;
}

function updateStateFilterOptions() {
    const select = document.getElementById('stateSelect');
    if (!select) return;

    const currentVal = select.value;
    let statesList = [];

    if (state.activeTab === 'myTasks' && state.myTasksData?.state_groups) {
        statesList = state.myTasksData.state_groups.map(g => g.state);
    } else if (state.sreTasksData?.states) {
        statesList = state.sreTasksData.states;
    }

    select.innerHTML = '<option value="ALL">همه وضعیت‌ها</option>';
    statesList.forEach(st => {
        const opt = document.createElement('option');
        opt.value = st;
        opt.textContent = st;
        select.appendChild(opt);
    });
    if (statesList.includes(currentVal)) select.value = currentVal;
}

// ==============================================================================
// Search & Filter Handling
// ==============================================================================

function handleSearch() {
    const input = document.getElementById('searchInput');
    const clearBtn = document.getElementById('clearSearchBtn');
    state.filters.search = (input.value || '').trim().toLowerCase();

    if (clearBtn) {
        clearBtn.style.display = state.filters.search ? 'flex' : 'none';
    }
    applyFilters();
}

function clearSearch() {
    const input = document.getElementById('searchInput');
    if (input) input.value = '';
    handleSearch();
}

function applyFilters() {
    const formSelect = document.getElementById('formSelect');
    const urgencySelect = document.getElementById('urgencySelect');
    const stateSelect = document.getElementById('stateSelect');

    state.filters.form = formSelect ? formSelect.value : 'ALL';
    state.filters.urgency = urgencySelect ? urgencySelect.value : 'ALL';
    state.filters.state = stateSelect ? stateSelect.value : 'ALL';

    if (state.activeTab === 'myTasks') {
        renderMyTasks();
    } else {
        renderSreTasks();
    }
}

function matchesFilter(task) {
    // Immediately exclude 'ready to test' tasks
    const st = (task.status || '').toLowerCase().trim();
    if (st === 'ready to test' || st.includes('ready to test')) {
        return false;
    }

    // 1. Search Query
    if (state.filters.search) {
        const q = state.filters.search;
        const nameMatch = (task.name || '').toLowerCase().includes(q);
        const descMatch = (task.description || '').toLowerCase().includes(q);
        const repoMatch = (task.repo_url || '').toLowerCase().includes(q);
        const formMatch = (task.form_name || '').toLowerCase().includes(q);
        const aiMatch = (task.ai_reasoning || '').toLowerCase().includes(q);
        const assigneeMatch = (task.assignees || []).some(a => (a.username || '').toLowerCase().includes(q));

        if (!nameMatch && !descMatch && !repoMatch && !formMatch && !aiMatch && !assigneeMatch) {
            return false;
        }
    }

    // 2. Form Filter
    if (state.filters.form !== 'ALL') {
        if ((task.form_name || '').toLowerCase() !== state.filters.form.toLowerCase()) {
            return false;
        }
    }

    // 3. Urgency Filter
    if (state.filters.urgency !== 'ALL') {
        if ((task.urgency_level || '').toUpperCase() !== state.filters.urgency.toUpperCase()) {
            return false;
        }
    }

    // 4. State Filter
    if (state.filters.state !== 'ALL') {
        if ((task.status || '').toLowerCase() !== state.filters.state.toLowerCase()) {
            return false;
        }
    }

    return true;
}

// ==============================================================================
// Rendering: View 1 (My Tasks - Grouped by State)
// ==============================================================================

function renderMyTasks() {
    const container = document.getElementById('stateGroupsContainer');
    if (!container) return;

    if (!state.myTasksData || !state.myTasksData.state_groups) {
        container.innerHTML = `
            <div class="loading-state">
                <div class="liquid-spinner"></div>
                <p>در حال بارگذاری کارهای شما...</p>
            </div>
        `;
        return;
    }

    const stateGroups = state.myTasksData.state_groups;
    let html = '';
    let visibleTasksCount = 0;

    stateGroups.forEach((group, index) => {
        const filteredTasks = group.tasks.filter(matchesFilter);
        if (filteredTasks.length === 0) return;

        visibleTasksCount += filteredTasks.length;
        const stateColor = group.state_color || '#6366f1';
        const groupId = `stateGroup_${index}`;

        html += `
            <div class="state-group-card" id="${groupId}">
                <div class="state-group-header" onclick="toggleStateGroup('${groupId}')">
                    <div class="state-title-wrap">
                        <span class="state-color-indicator" style="background-color: ${escapeHtml(stateColor)}; color: ${escapeHtml(stateColor)}"></span>
                        <span class="state-name">${escapeHtml(group.state)}</span>
                        <span class="state-counter">${toPersianNumber(filteredTasks.length)} تسک</span>
                    </div>
                    <div class="state-toggle-icon">
                        <i data-lucide="chevron-down"></i>
                    </div>
                </div>
                <div class="state-group-body">
                    <div class="data-grid-header">
                        <div class="col col-priority">اولویت AI</div>
                        <div class="col col-title">عنوان تسک</div>
                        <div class="col col-duration">مدت در این وضعیت</div>
                        <div class="col col-form-env">محیط / فرم</div>
                        <div class="col col-reporter">ثبت‌کننده</div>
                        <div class="col col-assignee">مسئولین</div>
                        <div class="col col-action"></div>
                    </div>
                    <div class="data-grid-rows">
                        ${filteredTasks.map(t => renderTaskRow(t)).join('')}
                    </div>
                </div>
            </div>
        `;
    });

    if (visibleTasksCount === 0) {
        container.innerHTML = `
            <div class="empty-state glass-panel">
                <i data-lucide="clipboard-check"></i>
                <p>هیچ تسکی با فیلترهای انتخابی مطابقت ندارد.</p>
            </div>
        `;
    } else {
        container.innerHTML = html;
    }

    initLucide();
}

function toggleStateGroup(groupId) {
    const card = document.getElementById(groupId);
    if (card) {
        card.classList.toggle('collapsed');
    }
}

function renderTaskRow(task) {
    const urgencyClass = task.urgency_level || 'MEDIUM';
    const urgencyText = getUrgencyLabel(urgencyClass);
    const score = toPersianNumber(task.priority_score || 50);

    const formBadge = task.form_name ? `
        <span class="badge-pill badge-form">
            <i data-lucide="folder"></i>
            <span>${escapeHtml(task.form_name)}</span>
        </span>
    ` : '';

    const envBadge = task.environment ? `
        <span class="badge-pill badge-env">
            <i data-lucide="server"></i>
            <span>${escapeHtml(task.environment)}</span>
        </span>
    ` : '';

    const repoBadge = task.repo_url ? `
        <span class="badge-pill badge-repo" title="${escapeHtml(task.repo_url)}">
            <i data-lucide="git-branch"></i>
            <span>${escapeHtml(extractRepoName(task.repo_url))}</span>
        </span>
    ` : '';

    const reporter = task.creator || {};
    const reporterName = reporter.username || 'ناشناس';
    const reporterAvatarHtml = reporter.profile_picture
        ? `<img src="${escapeHtml(reporter.profile_picture)}" alt="${escapeHtml(reporterName)}">`
        : escapeHtml(reporter.initials || (reporterName.length >= 2 ? reporterName.substring(0, 2).toUpperCase() : 'U'));

    const reporterHtml = `
        <div class="reporter-cell-wrap" title="ثبت‌کننده: ${escapeHtml(reporterName)}">
            <div class="assignee-avatar-sm" style="background-color: ${escapeHtml(reporter.color || '#6366f1')}">
                ${reporterAvatarHtml}
            </div>
            <span class="reporter-name-text">${escapeHtml(reporterName)}</span>
        </div>
    `;

    const assigneesHtml = renderAssigneesStack(task.assignees);

    return `
        <div class="data-grid-row" onclick="openTaskModal('${escapeHtml(task.id)}')">
            <!-- 1. Priority Column -->
            <div class="col col-priority">
                <div class="ai-priority-badge ${urgencyClass}" title="امتیاز هوش مصنوعی: ${score} از ۱۰۰">
                    <i data-lucide="zap"></i>
                    <span>${urgencyText}</span>
                </div>
            </div>

            <!-- 2. Task Title Column (Clean, bold, prominent) -->
            <div class="col col-title">
                <span class="grid-title-main" dir="auto" title="${escapeHtml(task.name)}">
                    ${escapeHtml(task.name)}
                </span>
            </div>

            <!-- 3. Duration in State Column -->
            <div class="col col-duration">
                ${formatDaysSinceUpdate(task.date_updated, task.date_created)}
            </div>

            <!-- 4. Environment & Form / Repo Column -->
            <div class="col col-form-env">
                <div class="grid-badges-cell">
                    ${envBadge}
                    ${formBadge}
                    ${repoBadge}
                </div>
            </div>

            <!-- 5. Reporter Column -->
            <div class="col col-reporter">
                ${reporterHtml}
            </div>

            <!-- 6. Assignees Column -->
            <div class="col col-assignee">
                ${assigneesHtml}
            </div>

            <!-- 7. Action Arrow -->
            <div class="col col-action">
                <i data-lucide="chevron-left" class="row-action-arrow"></i>
            </div>
        </div>
    `;
}

function handleTableSort(field) {
    if (state.sort.field === field) {
        // Toggle direction or reset
        if (state.sort.direction === 'asc') {
            state.sort.direction = 'desc';
        } else {
            // Reset to default AI priority ordering
            state.sort.field = null;
            state.sort.direction = 'asc';
        }
    } else {
        state.sort.field = field;
        state.sort.direction = 'asc';
    }
    updateSortHeaderIcons();
    renderSreTasks();
}

function updateSortHeaderIcons() {
    const fields = ['duration', 'assignee', 'state'];
    fields.forEach(f => {
        const headerEl = document.querySelector(`.forms-table-header .sortable-header[data-sort="${f}"]`);
        const iconWrap = document.getElementById(`sortIcon_${f}`);
        if (!headerEl || !iconWrap) return;

        if (state.sort.field === f) {
            headerEl.classList.add('sort-active');
            if (state.sort.direction === 'asc') {
                iconWrap.innerHTML = '<i data-lucide="arrow-up"></i>';
            } else {
                iconWrap.innerHTML = '<i data-lucide="arrow-down"></i>';
            }
        } else {
            headerEl.classList.remove('sort-active');
            iconWrap.innerHTML = '<i data-lucide="chevrons-up-down"></i>';
        }
    });
    initLucide();
}

function getTaskDurationMs(task) {
    const rawTs = task.date_updated || task.date_created;
    if (!rawTs) return 0;
    try {
        const ts = parseInt(rawTs);
        return Date.now() - ts;
    } catch {
        return 0;
    }
}

function getTaskPrimaryAssigneeName(task) {
    const assignees = task.assignees || [];
    if (!assignees.length) return '';
    return (assignees[0].username || '').trim().toLowerCase();
}

function sortTasks(tasks) {
    if (!state.sort.field) return tasks;

    const sorted = [...tasks];
    const { field, direction } = state.sort;
    const factor = direction === 'asc' ? 1 : -1;

    sorted.sort((a, b) => {
        if (field === 'duration') {
            const durA = getTaskDurationMs(a);
            const durB = getTaskDurationMs(b);
            return (durA - durB) * factor;
        }

        if (field === 'assignee') {
            const nameA = getTaskPrimaryAssigneeName(a);
            const nameB = getTaskPrimaryAssigneeName(b);
            // Place unassigned at the end
            if (!nameA && nameB) return 1;
            if (nameA && !nameB) return -1;
            if (!nameA && !nameB) return 0;
            return nameA.localeCompare(nameB, 'fa', { sensitivity: 'base' }) * factor;
        }

        if (field === 'state') {
            const stateA = (a.status || '').trim().toLowerCase();
            const stateB = (b.status || '').trim().toLowerCase();
            return stateA.localeCompare(stateB, 'fa', { sensitivity: 'base' }) * factor;
        }

        return 0;
    });

    return sorted;
}

function renderSreTasks() {
    const container = document.getElementById('sreTasksContainer');
    if (!container) return;

    if (!state.sreTasksData || !state.sreTasksData.tasks) {
        container.innerHTML = `
            <div class="loading-state">
                <div class="liquid-spinner"></div>
                <p>در حال بارگذاری تسک‌های فرم‌های SRE...</p>
            </div>
        `;
        return;
    }

    const filtered = state.sreTasksData.tasks.filter(matchesFilter);
    const sorted = sortTasks(filtered);

    if (sorted.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i data-lucide="inbox"></i>
                <p>هیچ پاسخی با این مشخصات یافت نشد.</p>
            </div>
        `;
        return;
    }

    const rowsHtml = sorted.map(task => {
        const urgencyClass = task.urgency_level || 'MEDIUM';
        const urgencyText = getUrgencyLabel(urgencyClass);
        const score = toPersianNumber(task.priority_score || 50);

        const formName = task.form_name || 'فرم عمومی';
        const statusColor = task.status_color || '#6366f1';
        const statusText = task.status || 'open';

        const assigneesHtml = renderAssigneesWithNames(task.assignees);

        const reporter = task.creator || {};
        const reporterName = reporter.username || 'ناشناس';
        const reporterAvatarHtml = reporter.profile_picture
            ? `<img src="${escapeHtml(reporter.profile_picture)}" alt="${escapeHtml(reporterName)}">`
            : escapeHtml(reporter.initials || (reporterName.length >= 2 ? reporterName.substring(0, 2).toUpperCase() : 'U'));

        const reporterHtml = `
            <div style="display: flex; align-items: center; gap: 0.5rem;" title="ثبت‌کننده: ${escapeHtml(reporterName)}">
                <div class="assignee-avatar-sm" style="background-color: ${escapeHtml(reporter.color || '#6366f1')}">
                    ${reporterAvatarHtml}
                </div>
                <span style="font-size: 0.82rem; font-weight: 500; color: #e2e8f0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 120px;">
                    ${escapeHtml(reporterName)}
                </span>
            </div>
        `;

        return `
            <div class="sre-task-row" onclick="openTaskModal('${escapeHtml(task.id)}')">
                <div class="col col-priority">
                    <div class="ai-priority-badge ${urgencyClass}" title="امتیاز هوش مصنوعی: ${score} از ۱۰۰">
                        <i data-lucide="zap"></i>
                        <span>${urgencyText}</span>
                    </div>
                </div>
                <div class="col col-title">
                    <span class="grid-title-main" dir="auto" title="${escapeHtml(task.name)}">
                        ${escapeHtml(task.name)}
                    </span>
                </div>
                <div class="col col-duration">
                    ${formatDaysSinceUpdate(task.date_updated, task.date_created)}
                </div>
                <div class="col col-form-env">
                    <div class="grid-badges-cell">
                        ${task.environment ? `<span class="badge-pill badge-env">${escapeHtml(task.environment)}</span>` : ''}
                        <span class="badge-pill badge-form">
                            <i data-lucide="folder"></i>
                            <span>${escapeHtml(formName)}</span>
                        </span>
                    </div>
                </div>
                <div class="col col-reporter">
                    ${reporterHtml}
                </div>
                <div class="col col-assignee">
                    ${assigneesHtml}
                </div>
                <div class="col col-state">
                    <span class="task-status-pill" style="border-color: ${escapeHtml(statusColor)}; color: #fff; background: rgba(255, 255, 255, 0.05);">
                        <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${escapeHtml(statusColor)}; margin-left:6px;"></span>
                        ${escapeHtml(statusText)}
                    </span>
                </div>
                <div class="col col-action">
                    <i data-lucide="chevron-left" class="row-action-arrow"></i>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = rowsHtml;
    updateSortHeaderIcons();
    initLucide();
}

function renderAssigneesStack(assignees) {
    if (!assignees || assignees.length === 0) {
        return '<span class="unassigned-label">بدون مسئول</span>';
    }
    return `
        <div class="assignees-stack">
            ${assignees.slice(0, 3).map(a => `
                <div class="assignee-avatar-sm" title="${escapeHtml(a.username)}">
                    ${a.profile_picture ? `<img src="${escapeHtml(a.profile_picture)}" alt="${escapeHtml(a.username)}">` : escapeHtml(a.initials)}
                </div>
            `).join('')}
            ${assignees.length > 3 ? `<span class="assignee-avatar-sm">+${toPersianNumber(assignees.length - 3)}</span>` : ''}
        </div>
    `;
}

function renderAssigneesWithNames(assignees) {
    if (!assignees || assignees.length === 0) {
        return '<span class="unassigned-label">بدون مسئول</span>';
    }
    const first = assignees[0];
    return `
        <div style="display: flex; align-items: center; gap: 0.5rem;">
            <div class="assignee-avatar-sm" title="${escapeHtml(first.username)}">
                ${first.profile_picture ? `<img src="${escapeHtml(first.profile_picture)}" alt="${escapeHtml(first.username)}">` : escapeHtml(first.initials)}
            </div>
            <span style="font-size: 0.82rem; font-weight: 500; color: #e2e8f0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 120px;">
                ${escapeHtml(first.username)}
            </span>
            ${assignees.length > 1 ? `<span style="font-size:0.72rem; color:#94a3b8;">+${toPersianNumber(assignees.length - 1)}</span>` : ''}
        </div>
    `;
}

// ==============================================================================
// ClickUp-Style Liquid Glass Modal
// ==============================================================================

async function openTaskModal(taskId) {
    state.currentModalTaskId = taskId;
    const overlay = document.getElementById('taskModalOverlay');
    const modalBody = document.getElementById('modalBody');
    const statusPill = document.getElementById('modalTaskStatus');
    const taskIdBadge = document.getElementById('modalTaskId');
    const formPill = document.getElementById('modalTaskForm');
    const clickUpLink = document.getElementById('modalClickUpLink');

    overlay.classList.add('active');
    modalBody.innerHTML = `
        <div class="loading-state">
            <div class="liquid-spinner"></div>
            <p>در حال واکشی اطلاعات کامل تسک، کامنت‌ها و تحلیل هوش مصنوعی...</p>
        </div>
    `;

    try {
        const res = await fetch(`/api/tasks/${taskId}/details`);
        if (!res.ok) throw new Error('Task not found');
        const data = await res.json();

        // Update Header
        if (taskIdBadge) taskIdBadge.textContent = `#${data.id}`;
        if (statusPill) {
            statusPill.textContent = data.status || 'open';
            statusPill.style.borderColor = data.status_color || '#6366f1';
        }
        if (formPill) formPill.textContent = data.form_name || 'فرم';
        if (clickUpLink) clickUpLink.href = data.url || `https://app.clickup.com/t/${data.id}`;

        // Render Modal Body
        renderModalDetails(data);
    } catch (err) {
        console.error('Error fetching task details:', err);
        modalBody.innerHTML = `
            <div class="empty-state">
                <i data-lucide="alert-triangle"></i>
                <p>خطا در بارگذاری جزئیات تسک.</p>
            </div>
        `;
        initLucide();
    }
}

function renderModalDetails(task) {
    const modalBody = document.getElementById('modalBody');
    if (!modalBody) return;

    const urgencyClass = task.urgency_level || 'MEDIUM';
    const urgencyLabel = getUrgencyLabel(urgencyClass);
    const score = toPersianNumber(task.priority_score || 50);

    // Format Dates
    const createdStr = formatDate(task.date_created);
    const updatedStr = formatDate(task.date_updated);
    const dueStr = task.due_date ? formatDate(task.due_date) : 'تعیین نشده';

    // Assignees List
    const assigneesText = (task.assignees && task.assignees.length > 0)
        ? task.assignees.map(a => escapeHtml(a.username)).join('، ')
        : 'بدون مسئول';

    // Custom Fields Cards
    let cfHtml = '';
    if (task.custom_fields && task.custom_fields.length > 0) {
        const validCfs = task.custom_fields.filter(cf => cf.display_value !== null && cf.display_value !== undefined && cf.display_value !== '');
        if (validCfs.length > 0) {
            cfHtml = `
                <div class="modal-section">
                    <h4 class="modal-section-title">
                        <i data-lucide="sliders"></i>
                        <span>فیلدهای سفارشی (Custom Fields)</span>
                    </h4>
                    <div class="custom-fields-grid">
                        ${validCfs.map(cf => `
                            <div class="custom-field-card">
                                <span class="cf-name">${escapeHtml(cf.name)}</span>
                                <span class="cf-val">${formatCustomFieldValue(cf.display_value)}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }
    }

    // Comments Timeline
    const comments = task.comments || [];
    const commentsHtml = comments.map(c => `
        <div class="comment-bubble">
            <div class="comment-user-avatar">
                ${c.user?.profile_picture ? `<img src="${escapeHtml(c.user.profile_picture)}" alt="${escapeHtml(c.user.username)}">` : escapeHtml(c.user?.initials || 'U')}
            </div>
            <div class="comment-content-wrap">
                <div class="comment-header">
                    <span class="comment-author">${escapeHtml(c.user?.username || 'کاربر')}</span>
                    <span class="comment-date">${formatDate(c.date)}</span>
                </div>
                <div class="comment-text">${escapeHtml(c.text)}</div>
            </div>
        </div>
    `).join('');

    modalBody.innerHTML = `
        <h2 class="modal-task-title">${escapeHtml(task.name)}</h2>

        <!-- AI Executive Summary Card -->
        <div class="ai-summary-card">
            <div class="ai-summary-header">
                <div class="ai-title-wrap">
                    <i data-lucide="cpu"></i>
                    <span>تحلیل و رتبه‌بندی هوش مصنوعی</span>
                </div>
                <div class="ai-priority-badge ${urgencyClass}">
                    <i data-lucide="zap"></i>
                    <span>اولویت ${urgencyLabel}</span>
                </div>
            </div>
            <p class="ai-reasoning-text">${escapeHtml(task.ai_reasoning || 'تحلیل انجام شد')}</p>
            ${task.suggested_action ? `
                <div class="ai-action-suggestion">
                    <i data-lucide="compass"></i>
                    <span><strong>اقدام پیشنهادی:</strong> ${escapeHtml(task.suggested_action)}</span>
                </div>
            ` : ''}
        </div>

        <!-- Metadata Grid -->
        <div class="metadata-grid">
            <div class="metadata-item">
                <span class="meta-label">فضای کاری و فرم</span>
                <span class="meta-value">${escapeHtml(task.space_name || 'SRE')} / ${escapeHtml(task.form_name || '-')}</span>
            </div>
            <div class="metadata-item">
                <span class="meta-label">ایجادکننده (Reporter)</span>
                <span class="meta-value">${escapeHtml(task.creator?.username || 'ناشناس')}</span>
            </div>
            <div class="metadata-item">
                <span class="meta-label">مسئولین (Assignees)</span>
                <span class="meta-value">${assigneesText}</span>
            </div>
            <div class="metadata-item">
                <span class="meta-label">تاریخ ایجاد</span>
                <span class="meta-value">${createdStr}</span>
            </div>
            <div class="metadata-item">
                <span class="meta-label">مهلت سررسید (Due Date)</span>
                <span class="meta-value">${dueStr}</span>
            </div>
            ${task.environment ? `
                <div class="metadata-item">
                    <span class="meta-label">محیط استقرار</span>
                    <span class="meta-value"><span class="badge-pill badge-env">${escapeHtml(task.environment)}</span></span>
                </div>
            ` : ''}
        </div>

        <!-- Description Box -->
        <div class="modal-section">
            <h4 class="modal-section-title">
                <i data-lucide="align-right"></i>
                <span>توضیحات تسک (Description)</span>
            </h4>
            <div class="task-description-box">
                ${task.description ? renderFormattedText(task.description) : '<span style="color:var(--text-muted);">توضیحاتی برای این تسک ثبت نشده است.</span>'}
            </div>
        </div>

        <!-- Custom Fields -->
        ${cfHtml}

        <!-- Comments Section -->
        <div class="modal-section">
            <h4 class="modal-section-title">
                <i data-lucide="message-square"></i>
                <span>تاریخچه کامنت‌ها و گفتگو (${toPersianNumber(comments.length)})</span>
            </h4>

            <div class="comments-thread" id="commentsThread">
                ${comments.length > 0 ? commentsHtml : '<p style="font-size:0.85rem; color:var(--text-muted); padding:0.5rem 0;">هنوز کامنتی در کلیک‌آپ برای این تسک ارسال نشده است.</p>'}
            </div>

            <!-- Add Comment Box -->
            <div class="add-comment-box">
                <textarea id="newCommentInput" placeholder="ارسال کامنت جدید به این تسک در کلیک‌آپ..."></textarea>
                <div class="add-comment-actions">
                    <label class="notify-checkbox-label">
                        <input type="checkbox" id="notifyAllCheckbox" checked>
                        <span>اطلاع‌رسانی به همه (Notify all)</span>
                    </label>
                    <button class="submit-comment-btn" id="submitCommentBtn" onclick="submitNewComment('${escapeHtml(task.id)}')">
                        <i data-lucide="send"></i>
                        <span>ارسال کامنت</span>
                    </button>
                </div>
            </div>
        </div>
    `;

    initLucide();
}

async function submitNewComment(taskId) {
    const input = document.getElementById('newCommentInput');
    const notifyBox = document.getElementById('notifyAllCheckbox');
    const btn = document.getElementById('submitCommentBtn');
    if (!input || !input.value.trim()) {
        showToast('لطفاً متن کامنت را وارد نمایید', 'error');
        return;
    }

    const commentText = input.value.trim();
    const notifyAll = notifyBox ? notifyBox.checked : true;

    btn.disabled = true;
    btn.innerHTML = `<div class="liquid-spinner" style="width:16px; height:16px; border-width:2px;"></div> در حال ارسال...`;

    try {
        const res = await fetch(`/api/tasks/${taskId}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comment_text: commentText, notify_all: notifyAll })
        });

        if (res.ok) {
            showToast('کامنت با موفقیت به کلیک‌آپ ارسال شد', 'success');
            input.value = '';
            // Refresh modal details
            await openTaskModal(taskId);
        } else {
            const errData = await res.json();
            showToast(errData.detail || 'خطا در ارسال کامنت', 'error');
        }
    } catch (err) {
        console.error('Error submitting comment:', err);
        showToast('خطا در برقراری ارتباط با سرور', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<i data-lucide="send"></i><span>ارسال کامنت</span>`;
        initLucide();
    }
}

function closeTaskModal() {
    const overlay = document.getElementById('taskModalOverlay');
    if (overlay) overlay.classList.remove('active');
    state.currentModalTaskId = null;
}

function handleOverlayClick(event) {
    if (event.target.id === 'taskModalOverlay') {
        closeTaskModal();
    }
}

// ==============================================================================
// Utilities & Formatters
// ==============================================================================

function getUrgencyLabel(urgency) {
    switch ((urgency || '').toUpperCase()) {
        case 'CRITICAL': return 'بحرانی';
        case 'HIGH': return 'بالا';
        case 'MEDIUM': return 'متوسط';
        case 'LOW': return 'عادی';
        default: return 'متوسط';
    }
}

function toPersianNumber(n) {
    if (n === null || n === undefined) return '';
    const farsiDigits = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];
    return n.toString().replace(/[0-9]/g, x => farsiDigits[x]);
}

function formatDaysSinceUpdate(dateUpdated, dateCreated) {
    const rawTs = dateUpdated || dateCreated;
    if (!rawTs) return '<span class="staleness-pill stale-recent">نامشخص</span>';
    try {
        const ts = parseInt(rawTs);
        const diffMs = Date.now() - ts;
        const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));

        let text = '';
        let badgeClass = '';

        if (days <= 0) {
            text = 'امروز';
            badgeClass = 'stale-recent';
        } else if (days === 1) {
            text = '۱ روز پیش';
            badgeClass = 'stale-recent';
        } else if (days < 7) {
            text = `${toPersianNumber(days)} روز پیش`;
            badgeClass = 'stale-normal';
        } else if (days < 30) {
            text = `${toPersianNumber(days)} روز پیش`;
            badgeClass = 'stale-warning';
        } else if (days < 365) {
            const months = Math.floor(days / 30);
            text = `${toPersianNumber(days)} روز (${toPersianNumber(months)} ماه)`;
            badgeClass = 'stale-danger';
        } else {
            const years = (days / 365).toFixed(1);
            text = `${toPersianNumber(days)} روز (${toPersianNumber(years)} سال)`;
            badgeClass = 'stale-extreme';
        }

        const exactDate = formatDate(rawTs);
        return `<span class="staleness-pill ${badgeClass}" title="آخرین تغییر وضعیت: ${exactDate} (${toPersianNumber(days)} روز قبل)">
            <i data-lucide="clock"></i>
            <span>${text}</span>
        </span>`;
    } catch {
        return '<span class="staleness-pill stale-recent">-</span>';
    }
}

function formatDate(timestamp) {
    if (!timestamp) return '-';
    try {
        const ts = parseInt(timestamp);
        const date = new Date(ts);
        return date.toLocaleDateString('fa-IR', {
            month: 'long',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch {
        return timestamp;
    }
}

function extractRepoName(url) {
    if (!url) return '';
    try {
        const parts = url.split('/');
        return parts[parts.length - 1].replace('.git', '');
    } catch {
        return url;
    }
}

function formatCustomFieldValue(val) {
    if (Array.isArray(val)) {
        return val.map(v => `<span class="badge-pill badge-repo" style="margin:2px;">${escapeHtml(v)}</span>`).join(' ');
    }
    if (typeof val === 'string' && (val.startsWith('http://') || val.startsWith('https://'))) {
        return `<a href="${escapeHtml(val)}" target="_blank" style="color:var(--text-accent); text-decoration:none;">${escapeHtml(val)}</a>`;
    }
    return escapeHtml(String(val));
}

function renderFormattedText(text) {
    if (!text) return '';
    const escaped = escapeHtml(text);
    // Linkify URLs
    return escaped.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" style="color:var(--text-accent); text-decoration:underline;">$1</a>');
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function showToast(message, type = 'success') {
    const toast = document.getElementById('toastNotification');
    const msgEl = document.getElementById('toastMessage');
    const iconEl = document.getElementById('toastIcon');

    if (!toast) return;
    toast.className = `toast-notification show ${type}`;
    if (msgEl) msgEl.textContent = message;
    if (iconEl && window.lucide) {
        iconEl.setAttribute('data-lucide', type === 'success' ? 'check-circle' : 'alert-circle');
        window.lucide.createIcons();
    }

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3500);
}

// ==============================================================================
// Analytics & Charts Rendering
// ==============================================================================

async function loadAnalyticsData() {
    try {
        const res = await fetch('/api/analytics/sre');
        if (res.ok) {
            state.analyticsData = await res.json();
            renderAllCharts(state.analyticsData);
        }
    } catch (err) {
        console.error('Failed to load analytics data:', err);
    }
}

function destroyChart(name) {
    if (state.chartInstances[name]) {
        state.chartInstances[name].destroy();
        delete state.chartInstances[name];
    }
}

function renderAllCharts(data) {
    if (!data) return;
    const timeEl = document.getElementById('analyticsUpdatedTime');
    if (timeEl) {
        const now = new Date();
        timeEl.textContent = `به‌روزرسانی: ${now.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })}`;
    }

    if (window.Chart) {
        renderAssigneeDonut(data.assignee_donut);
        if (data.sre_forms) {
            populateStateCountFormSelect(data.sre_forms);
        }
        renderStateCountChart(data.state_counts);
        renderAvgDaysNewChart(data.avg_days_new_by_form);
        renderAvgDaysInProgressChart(data.avg_days_in_progress_by_form);
        renderWeeklyThroughputChart(data.weekly_throughput);
    }
    renderActivityHeatmap(data.activity_heatmap);
}

function renderAssigneeDonut(donutList) {
    destroyChart('assigneeDonut');
    const canvas = document.getElementById('assigneeDonutChart');
    if (!canvas || !donutList || !donutList.length) return;

    const labels = donutList.map(item => item.name);
    const values = donutList.map(item => item.count);

    // Highly distinct, vibrant neon palette tailored for dark UI
    // Mapping distinct vibrant colors so each person pops out with high contrast
    const vibrantPalette = [
        '#06b6d4', // Cyan / Teal
        '#8b5cf6', // Violet / Purple
        '#ec4899', // Pink / Rose
        '#10b981', // Emerald / Green
        '#f59e0b', // Amber / Orange
        '#3b82f6', // Electric Blue
        '#64748b'  // Slate for unassigned
    ];

    // Fallback/Neutral mapping
    const specialColors = {
        'بدون مسئول': '#64748b',
        'unassigned': '#64748b'
    };

    const colors = donutList.map((item, idx) => {
        const norm = (item.name || '').toLowerCase().trim();
        if (specialColors[norm]) return specialColors[norm];
        if (item.color && item.color !== '#3b82f6') return item.color;
        return vibrantPalette[idx % vibrantPalette.length];
    });

    const ctx = canvas.getContext('2d');
    state.chartInstances['assigneeDonut'] = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderColor: 'rgba(15, 23, 42, 0.8)',
                borderWidth: 2,
                hoverOffset: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            plugins: {
                legend: {
                    position: 'bottom',
                    rtl: true,
                    labels: {
                        color: '#94a3b8',
                        font: { family: 'Vazirmatn, sans-serif', size: 11 },
                        padding: 12,
                        boxWidth: 12
                    }
                },
                tooltip: {
                    rtl: true,
                    callbacks: {
                        label: function(context) {
                            const val = context.parsed;
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const pct = total > 0 ? ((val / total) * 100).toFixed(1) : 0;
                            return ` ${context.label}: ${val} تسک (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

function populateStateCountFormSelect(forms) {
    const select = document.getElementById('stateCountFormSelect');
    if (!select || !forms) return;
    const currentVal = select.value || 'all';
    select.innerHTML = '<option value="all">همه فرم‌ها</option>';
    forms.forEach(fn => {
        const opt = document.createElement('option');
        opt.value = fn;
        opt.textContent = fn;
        select.appendChild(opt);
    });
    if (Array.from(select.options).some(o => o.value === currentVal)) {
        select.value = currentVal;
    } else {
        select.value = 'all';
    }
}

function onStateCountFormChange() {
    if (!state.analyticsData) return;
    const select = document.getElementById('stateCountFormSelect');
    const selectedForm = select ? select.value : 'all';
    
    if (selectedForm === 'all') {
        renderStateCountChart(state.analyticsData.state_counts);
    } else {
        const byForm = state.analyticsData.state_counts_by_form || {};
        const formStates = byForm[selectedForm] || [];
        renderStateCountChart(formStates);
    }
}

function renderStateCountChart(stateList) {
    destroyChart('stateCount');
    const canvas = document.getElementById('stateCountChart');
    if (!canvas) return;

    // Fallback: if not provided or called initially, check current select value
    if (!stateList || !stateList.length) {
        const select = document.getElementById('stateCountFormSelect');
        const selectedForm = select ? select.value : 'all';
        if (selectedForm !== 'all' && state.analyticsData && state.analyticsData.state_counts_by_form) {
            stateList = state.analyticsData.state_counts_by_form[selectedForm] || [];
        } else if (state.analyticsData) {
            stateList = state.analyticsData.state_counts;
        }
    }

    if (!stateList || !stateList.length) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    // Filter out 'prioritize' and 'wait for customer' as requested
    const excluded = ['prioritize', 'wait for customer'];
    const filteredList = (stateList || []).filter(s => !excluded.includes((s.state || '').toLowerCase().trim()));
    if (!filteredList.length) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    const labels = filteredList.map(s => s.state);
    const values = filteredList.map(s => s.count);

    // Modern neon palette matching state status or sleek gradient colors
    const colors = filteredList.map(s => s.color || '#a855f7');

    const ctx = canvas.getContext('2d');
    state.chartInstances['stateCount'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'تعداد تسک‌ها',
                data: values,
                backgroundColor: colors.map(c => {
                    // Give slight transparency for neon glass feel
                    return c.startsWith('#') && c.length === 7 ? c + 'cc' : c;
                }),
                borderColor: colors,
                borderWidth: 1.5,
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    rtl: true,
                    callbacks: {
                        label: (context) => ` تعداد: ${context.parsed.y} تسک`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { family: 'Vazirmatn, sans-serif', size: 10 },
                        maxRotation: 25,
                        minRotation: 0
                    }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        precision: 0,
                        font: { size: 10 }
                    }
                }
            }
        }
    });
}

function onAvgDaysRangeChange() {
    if (!state.analyticsData) return;
    const select = document.getElementById('avgDaysRangeSelect');
    const range = select ? select.value : 'all';
    const rangesData = state.analyticsData.avg_days_ranges;
    if (rangesData && rangesData[range]) {
        renderAvgDaysNewChart(rangesData[range]);
    } else {
        renderAvgDaysNewChart(state.analyticsData.avg_days_new_by_form);
    }
}

function renderAvgDaysNewChart(formList) {
    destroyChart('avgDaysNew');
    const canvas = document.getElementById('avgDaysNewChart');
    if (!canvas) return;

    if (!formList || !formList.length) {
        const select = document.getElementById('avgDaysRangeSelect');
        const range = select ? select.value : 'all';
        if (state.analyticsData && state.analyticsData.avg_days_ranges && state.analyticsData.avg_days_ranges[range]) {
            formList = state.analyticsData.avg_days_ranges[range];
        } else if (state.analyticsData) {
            formList = state.analyticsData.avg_days_new_by_form;
        }
    }

    // Filter out 'Merchant multi deploy' and 'IT Equipment Requests'
    const filteredList = (formList || []).filter(f => {
        const name = (f.form_name || '').toLowerCase();
        return !name.includes('merchant multi deploy') && !name.includes('it equipment');
    });
    if (!filteredList.length) {
        // Show empty message or clear canvas
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    const fullLabels = filteredList.map(f => f.form_name);
    const shortLabels = fullLabels.map(l => l.length > 18 ? l.substring(0, 16) + '...' : l);
    const values = filteredList.map(f => f.avg_days);

    const ctx = canvas.getContext('2d');
    state.chartInstances['avgDaysNew'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: shortLabels,
            datasets: [{
                label: 'میانگین روز ماندگاری در New',
                data: values,
                backgroundColor: 'rgba(245, 158, 11, 0.8)',
                borderColor: '#f59e0b',
                borderWidth: 1.5,
                borderRadius: 5
            }]
        },
        options: {
            indexAxis: 'x', // Standard vertical columns: categories at the bottom, numbers on vertical axis
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    rtl: true,
                    callbacks: {
                        title: (items) => fullLabels[items[0].dataIndex],
                        label: (context) => ` میانگین: ${context.parsed.y} روز`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#cbd5e1',
                        font: { family: 'Vazirmatn, sans-serif', size: 10 },
                        maxRotation: 30,
                        minRotation: 0
                    }
                },
                y: {
                    position: 'right', // Place values / numbers on the right side
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 10 }
                    },
                    title: {
                        display: true,
                        text: 'تعداد روز',
                        color: '#64748b',
                        font: { family: 'Vazirmatn, sans-serif', size: 10 }
                    }
                }
            }
        }
    });
}

function renderAvgDaysInProgressChart(formList) {
    destroyChart('avgDaysInProgress');
    const canvas = document.getElementById('avgDaysInProgressChart');
    if (!canvas) return;

    // Fallback: use range data if formList is empty
    if (!formList || !formList.length) {
        const select = document.getElementById('avgInprogressRangeSelect');
        const range = select ? select.value : 'all';
        if (state.analyticsData && state.analyticsData.avg_inprogress_ranges && state.analyticsData.avg_inprogress_ranges[range]) {
            formList = state.analyticsData.avg_inprogress_ranges[range];
        } else if (state.analyticsData) {
            formList = state.analyticsData.avg_days_in_progress_by_form;
        }
    }

    const filteredList = (formList || []).filter(f => {
        const name = (f.form_name || '').toLowerCase();
        return !name.includes('merchant multi deploy') && !name.includes('it equipment');
    });
    if (!filteredList.length) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    const fullLabels = filteredList.map(f => f.form_name);
    const shortLabels = fullLabels.map(l => l.length > 20 ? l.substring(0, 18) + '…' : l);
    const values = filteredList.map(f => parseFloat(f.avg_days.toFixed(1)));
    const counts = filteredList.map(f => f.task_count);

    // Uniform vibrant orange color for all bars
    const BAR_COLOR = 'rgba(249, 115, 22, 0.78)';
    const BAR_BORDER = '#f97316';

    const ctx = canvas.getContext('2d');
    state.chartInstances['avgDaysInProgress'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: shortLabels,
            datasets: [{
                label: 'میانگین روزهای انجام',
                data: values,
                backgroundColor: BAR_COLOR,
                borderColor: BAR_BORDER,
                borderWidth: 1.5,
                borderRadius: 5
            }]
        },
        options: {
            indexAxis: 'x',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    rtl: true,
                    callbacks: {
                        title: (items) => fullLabels[items[0].dataIndex],
                        label: (context) => ` میانگین: ${context.parsed.y} روز  (${counts[context.dataIndex]} تسک)`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#cbd5e1',
                        font: { family: 'Vazirmatn, sans-serif', size: 10 },
                        maxRotation: 30,
                        minRotation: 0
                    }
                },
                y: {
                    position: 'right',
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 10 }
                    },
                    title: {
                        display: true,
                        text: 'تعداد روز',
                        color: '#64748b',
                        font: { family: 'Vazirmatn, sans-serif', size: 10 }
                    }
                }
            }
        }
    });
}

function onAvgInprogressRangeChange() {
    if (!state.analyticsData) return;
    const select = document.getElementById('avgInprogressRangeSelect');
    const range = select ? select.value : 'all';
    const rangesData = state.analyticsData.avg_inprogress_ranges;
    if (rangesData && rangesData[range]) {
        renderAvgDaysInProgressChart(rangesData[range]);
    } else {
        renderAvgDaysInProgressChart(state.analyticsData.avg_days_in_progress_by_form);
    }
}

function renderWeeklyThroughputChart(throughputList) {
    destroyChart('weeklyThroughput');
    const canvas = document.getElementById('weeklyThroughputChart');
    if (!canvas || !throughputList || !throughputList.length) return;

    const faDays = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه'];
    const localTodayStr = new Date().toISOString().split('T')[0];

    const labels = throughputList.map(t => {
        let label = t.label || t.date;
        try {
            const dt = new Date(t.date + 'T12:00:00Z');
            const satDayIdx = (dt.getUTCDay() + 1) % 7;
            const dayName = faDays[satDayIdx] || '';
            const jFmt = new Intl.DateTimeFormat('fa-IR-u-ca-persian', {
                month: 'numeric',
                day: 'numeric'
            });
            const jDate = jFmt.format(dt);
            const isToday = !!t.is_today || t.date === localTodayStr;
            label = isToday ? `${dayName} (${jDate}) [امروز]` : `${dayName} (${jDate})`;
        } catch (e) {
            // fallback
        }
        return label;
    });

    const closedValues = throughputList.map(t => t.closed || 0);
    const readyValues = throughputList.map(t => t.ready_to_test || 0);

    const ctx = canvas.getContext('2d');
    state.chartInstances['weeklyThroughput'] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'بسته شده (Closed / Done)',
                    data: closedValues,
                    backgroundColor: 'rgba(16, 185, 129, 0.85)',
                    borderColor: '#10b981',
                    borderWidth: 1.5,
                    borderRadius: 4
                },
                {
                    label: 'آماده تست (Ready to Test)',
                    data: readyValues,
                    backgroundColor: 'rgba(6, 182, 212, 0.85)',
                    borderColor: '#06b6d4',
                    borderWidth: 1.5,
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    rtl: true,
                    labels: {
                        color: '#94a3b8',
                        font: { family: 'Vazirmatn, sans-serif', size: 11 },
                        boxWidth: 12
                    }
                },
                tooltip: {
                    rtl: true,
                    callbacks: {
                        label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y} تسک`,
                        footer: (tooltipItems) => {
                            let sum = 0;
                            tooltipItems.forEach(item => {
                                sum += item.parsed.y;
                            });
                            return `مجموع کل روز: ${sum} تسک`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    stacked: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { family: 'Vazirmatn, sans-serif', size: 10 }
                    }
                },
                y: {
                    stacked: true,
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        precision: 0,
                        font: { size: 10 }
                    }
                }
            }
        }
    });
}

function renderActivityHeatmap(heatmapData) {
    if (!heatmapData && state.analyticsData) {
        heatmapData = state.analyticsData.activity_heatmap;
    }
    const container = document.getElementById('heatmapContainer');
    const select = document.getElementById('heatmapMemberSelect');
    if (!container || !heatmapData) return;

    // Populate member select if only "ALL" is present
    const members = heatmapData.members || heatmapData.all_members || [];
    if (select && select.options.length <= 1 && members.length > 0) {
        members.forEach(m => {
            const opt = document.createElement('option');
            opt.value = String(m.id);
            opt.textContent = m.name || m.username;
            select.appendChild(opt);
        });
    }

    const selectedUserId = select ? select.value : 'ALL';
    const days = heatmapData.days || [];

    // Calculate sorted positive counts to determine percentile thresholds
    const positiveCounts = [];
    days.forEach(d => {
        if (!d.is_future) {
            const count = selectedUserId === 'ALL'
                ? d.total_activity
                : (d.user_activity ? (d.user_activity[selectedUserId] || 0) : 0);
            if (count > 0) positiveCounts.push(count);
        }
    });
    positiveCounts.sort((a, b) => a - b);

    // Calculate quartiles for clear, high-contrast distribution
    let q1 = 1, q2 = 2, q3 = 4;
    if (positiveCounts.length >= 4) {
        q1 = positiveCounts[Math.floor(positiveCounts.length * 0.25)];
        q2 = positiveCounts[Math.floor(positiveCounts.length * 0.50)];
        q3 = positiveCounts[Math.floor(positiveCounts.length * 0.75)];
        // Ensure strictly increasing thresholds
        if (q2 <= q1) q2 = q1 + 1;
        if (q3 <= q2) q3 = q2 + 1;
    } else if (positiveCounts.length > 0) {
        const maxC = positiveCounts[positiveCounts.length - 1];
        q1 = Math.max(1, Math.round(maxC * 0.25));
        q2 = Math.max(q1 + 1, Math.round(maxC * 0.50));
        q3 = Math.max(q2 + 1, Math.round(maxC * 0.75));
    }

    // Day of week labels in Persian: Saturday (0) to Friday (6)
    const faDays = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه'];

    // Group into week columns (7 days per column, Saturday to Friday)
    const weeks = [];
    let currentWeek = [];
    days.forEach(d => {
        currentWeek.push(d);
        if (currentWeek.length === 7) {
            weeks.push(currentWeek);
            currentWeek = [];
        }
    });
    if (currentWeek.length > 0) {
        weeks.push(currentWeek);
    }

    // Helper function to extract Jalali month name from Gregorian YYYY-MM-DD
    const jMonthNames = [
        'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
        'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'
    ];

    function getJalaliMonth(dateStr) {
        try {
            const dt = new Date(dateStr + 'T12:00:00Z');
            // Try standard Persian calendar month name
            const fmt = new Intl.DateTimeFormat('fa-IR-u-ca-persian', { month: 'long' });
            const mName = fmt.format(dt).trim();
            const idx = jMonthNames.indexOf(mName);
            if (idx !== -1) {
                return { index: idx, name: mName };
            }
            // Fallback with numeric month using English digits (nu-latn)
            const fmtNum = new Intl.DateTimeFormat('en-US-u-ca-persian-nu-latn', { month: 'numeric' });
            const num = parseInt(fmtNum.format(dt), 10);
            if (!isNaN(num) && num >= 1 && num <= 12) {
                return { index: num - 1, name: jMonthNames[num - 1] };
            }
        } catch (e) {
            console.error('Error resolving Jalali month for date:', dateStr, e);
        }
        return null;
    }
    
    // Find month boundaries across weeks (cell width 12px + gap 3px = 15px per column)
    const columnWidth = 15;
    const monthMarkers = [];
    let lastJMonth = -1;

    weeks.forEach((w, wIdx) => {
        const firstDay = w[0];
        if (firstDay && firstDay.date) {
            const jm = getJalaliMonth(firstDay.date);
            if (jm && jm.index !== lastJMonth) {
                monthMarkers.push({
                    monthName: jm.name,
                    colIndex: wIdx,
                    leftPx: wIdx * columnWidth
                });
                lastJMonth = jm.index;
            }
        }
    });

    // Build Month Header HTML
    let monthsHtml = `<div class="heatmap-months-row">`;
    // Filter out markers too close to each other (< 34px apart) to prevent overlapping Persian month names
    let prevLeft = -60;
    monthMarkers.forEach(m => {
        if (m.leftPx - prevLeft >= 34) {
            monthsHtml += `<span class="heatmap-month-label" style="left: ${m.leftPx}px">${m.monthName}</span>`;
            prevLeft = m.leftPx;
        }
    });
    monthsHtml += `</div>`;

    // Weekday labels on left in Persian:
    // شن برای شنبه
    // دو برای دوشنبه
    // چه برای چهارشنبه
    // جم برای جمعه
    // Weekdays in matrix: 0=Saturday (شنبه), 1=Sunday (یکشنبه), 2=Monday (دوشنبه), 3=Tuesday (سه‌شنبه), 4=Wednesday (چهارشنبه), 5=Thursday (پنج‌شنبه), 6=Friday (جمعه)
    const weekdayLabels = [
        { row: 0, text: 'شنبه' },
        { row: 1, text: '' },
        { row: 2, text: 'دوشنبه' },
        { row: 3, text: '' },
        { row: 4, text: 'چهارشنبه' },
        { row: 5, text: '' },
        { row: 6, text: 'جمعه' }
    ];

    let weekdaysHtml = `<div class="heatmap-days-axis">`;
    weekdayLabels.forEach(wl => {
        weekdaysHtml += `<div class="heatmap-day-label">${wl.text}</div>`;
    });
    weekdaysHtml += `</div>`;

    // Matrix columns
    let matrixHtml = `<div class="heatmap-matrix-grid">`;
    weeks.forEach(week => {
        matrixHtml += `<div class="heatmap-column">`;
        week.forEach(day => {
            const isFuture = !!day.is_future;
            // Check if day is today (from backend flag or comparing date string)
            const localTodayStr = new Date().toISOString().split('T')[0];
            const isToday = !!day.is_today || day.date === localTodayStr;
            const count = isFuture ? 0 : (selectedUserId === 'ALL'
                ? day.total_activity
                : (day.user_activity ? (day.user_activity[selectedUserId] || 0) : 0));

            // Determine level 0 to 4 based on quantile thresholds
            let lvl = 0;
            if (count > 0) {
                if (count <= q1) lvl = 1;
                else if (count <= q2) lvl = 2;
                else if (count <= q3) lvl = 3;
                else lvl = 4;
            }

            const dayName = (day.day_of_week !== undefined && day.day_of_week >= 0 && day.day_of_week < 7)
                ? faDays[day.day_of_week]
                : '';

            // Format date to Jalali (e.g. ۵ مهر ۱۴۰۵)
            let jalaliDateStr = day.date;
            try {
                const dt = new Date(day.date + 'T12:00:00Z');
                const jFmt = new Intl.DateTimeFormat('fa-IR-u-ca-persian', {
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric'
                });
                jalaliDateStr = jFmt.format(dt);
            } catch (e) {
                // fallback to raw date
            }

            let titlePrefix = `${dayName}، ${jalaliDateStr}`;
            if (isToday) {
                titlePrefix += ' (امروز)';
            }
            const title = isFuture
                ? titlePrefix
                : `${titlePrefix} : ${count} فعالیت ثبت شده`;
            const futureClass = isFuture ? ' is-future' : '';
            const todayClass = isToday ? ' is-today' : '';

            matrixHtml += `<div class="heatmap-cell lvl-${lvl}${futureClass}${todayClass}" title="${escapeHtml(title)}" data-count="${count}" data-date="${day.date}"></div>`;
        });
        matrixHtml += `</div>`;
    });
    matrixHtml += `</div>`;

    let outerHtml = `
        <div class="heatmap-outer-wrapper">
            ${monthsHtml}
            <div class="heatmap-body-wrapper">
                ${weekdaysHtml}
                ${matrixHtml}
            </div>
        </div>
    `;

    container.innerHTML = outerHtml;
}
