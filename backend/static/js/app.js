(function () {
    window.history.pushState(null, null, window.location.href);

    window.onpopstate = function () {
        window.history.pushState(null, null, window.location.href);
    };

    window.addEventListener('pageshow', function(event) {
        const navEntries = (window.performance && window.performance.getEntriesByType)
            ? window.performance.getEntriesByType('navigation')
            : [];
        const isBackForward = navEntries.length > 0 && navEntries[0].type === 'back_forward';
        if (event.persisted || isBackForward) {
            window.location.reload();
        }
    });
    if (window.location.hash === "#no-back") {
        window.location.hash = "no-back-again";
    }
})();

let monitoringTable;
let socket;
let currentUserRole = 'staff';
const MAX_LIVE_ROWS = 10000;
const LIVE_BATCH_WINDOW_MS = 180;
const LIVE_BATCH_MAX = 20;
let tagLookupTimer = null;
let lastAutoFilledTag = '';
const LAST_ACTIVE_PAGE_KEY = 'rfid_last_active_page';
let liveRowQueue = [];
let liveRowFlushTimer = null;
let statsRenderTimer = null;
const liveStatsState = { total: 0, in: 0, out: 0 };

function restoreActivePageAfterReload() {
    try {
        const pageId = sessionStorage.getItem(LAST_ACTIVE_PAGE_KEY);
        if (!pageId) return false;
        sessionStorage.removeItem(LAST_ACTIVE_PAGE_KEY);

        if (pageId === 'user-management-page' && currentUserRole !== 'admin') {
            showPage('dashboard-page');
            return true;
        }

        if (['dashboard-page', 'registration-page', 'user-management-page'].includes(pageId)) {
            showPage(pageId);
            return true;
        }
    } catch (err) {
        console.warn('Unable to restore active page:', err);
    }
    return false;
}

function normalizeTagId(rawTag) {
    const cleaned = String(rawTag || "")
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, "");

    if (!cleaned) return "";
    if (cleaned.length === 48 && cleaned.slice(0, 24) === cleaned.slice(24, 48)) {
        return cleaned.slice(0, 24);
    }
    return cleaned.slice(0, 24);
}

function animateSection(sectionId) {
    const nodes = document.querySelectorAll(`#${sectionId} .reveal`);
    nodes.forEach((node, idx) => {
        node.style.opacity = '0';
        node.style.animation = 'none';
        void node.offsetWidth;
        node.style.animation = `fadeUp 0.58s ease ${(idx * 55)}ms forwards`;
    });
}

function setupUiMotion() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return;
    }

    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
        return;
    }

    const tiltTargets = document.querySelectorAll('.hero-dock, .spotlight, .kpi, .summary, .scanner, .panel');
    tiltTargets.forEach((node) => {
        node.classList.add('motion-tilt');

        node.addEventListener('mousemove', (event) => {
            const rect = node.getBoundingClientRect();
            const offsetX = (event.clientX - rect.left) / rect.width;
            const offsetY = (event.clientY - rect.top) / rect.height;
            const tiltY = (offsetX - 0.5) * 4;
            const tiltX = (0.5 - offsetY) * 3.2;

            node.style.setProperty('--tilt-x', `${tiltX.toFixed(2)}deg`);
            node.style.setProperty('--tilt-y', `${tiltY.toFixed(2)}deg`);
        });

        node.addEventListener('mouseleave', () => {
            node.style.setProperty('--tilt-x', '0deg');
            node.style.setProperty('--tilt-y', '0deg');
        });
    });
}

function scheduleStatsRender() {
    if (statsRenderTimer) return;
    statsRenderTimer = setTimeout(() => {
        statsRenderTimer = null;
        animateMetricValue('#stat-total', liveStatsState.total);
        animateMetricValue('#stat-in', liveStatsState.in);
        animateMetricValue('#stat-out', liveStatsState.out);
    }, 120);
}

function setLiveStats(totalValue, inValue, outValue) {
    liveStatsState.total = Number(totalValue) || 0;
    liveStatsState.in = Number(inValue) || 0;
    liveStatsState.out = Number(outValue) || 0;
    scheduleStatsRender();
}

function bumpLiveStats(direction) {
    const normalized = String(direction || '').toUpperCase();
    if (normalized === 'IN') {
        liveStatsState.in += 1;
    } else if (normalized === 'OUT') {
        liveStatsState.out += 1;
    }
    scheduleStatsRender();
}

function flushLiveRowQueue() {
    if (!monitoringTable) {
        liveRowQueue = [];
        liveRowFlushTimer = null;
        return;
    }

    const batch = liveRowQueue.splice(0, LIVE_BATCH_MAX);
    if (batch.length === 0) {
        liveRowFlushTimer = null;
        return;
    }

    batch.forEach((entry) => addRow(entry, false));
    monitoringTable.draw(false);

    const firstVisible = $('#monitoringTable tbody tr').first();
    firstVisible.addClass('live-row-enter');
    setTimeout(() => firstVisible.removeClass('live-row-enter'), 900);

    if (liveRowQueue.length > 0) {
        liveRowFlushTimer = setTimeout(flushLiveRowQueue, LIVE_BATCH_WINDOW_MS);
    } else {
        liveRowFlushTimer = null;
    }
}

function queueLiveRow(entry) {
    liveRowQueue.push(entry);
    if (!liveRowFlushTimer) {
        liveRowFlushTimer = setTimeout(flushLiveRowQueue, LIVE_BATCH_WINDOW_MS);
    }
}

function animateMetricValue(selector, nextValue, duration = 560) {
    const node = document.querySelector(selector);
    if (!node) return;

    const parsedNext = Number(nextValue);
    if (!Number.isFinite(parsedNext)) {
        node.textContent = String(nextValue ?? '0');
        return;
    }

    const current = Number(String(node.textContent || '0').replace(/,/g, ''));
    const start = Number.isFinite(current) ? current : 0;
    const delta = parsedNext - start;
    const startedAt = performance.now();

    node.classList.remove('metric-updating');
    void node.offsetWidth;
    node.classList.add('metric-updating');

    function tick(now) {
        const elapsed = now - startedAt;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const value = Math.round(start + (delta * eased));
        node.textContent = value.toLocaleString('en-IN');
        if (progress < 1) {
            requestAnimationFrame(tick);
        }
    }

    requestAnimationFrame(tick);
}

function setupRippleEffects() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return;
    }

    const selectors = '.action-btn, .nav-link, .live-page-btn, .logout-link';
    document.querySelectorAll(selectors).forEach((node) => {
        node.classList.add('ripple-host');

        node.addEventListener('click', (event) => {
            const rect = node.getBoundingClientRect();
            const ripple = document.createElement('span');
            const diameter = Math.max(rect.width, rect.height) * 1.22;
            ripple.className = 'tap-ripple';
            ripple.style.width = `${diameter}px`;
            ripple.style.height = `${diameter}px`;
            ripple.style.left = `${event.clientX - rect.left}px`;
            ripple.style.top = `${event.clientY - rect.top}px`;
            node.appendChild(ripple);
            setTimeout(() => ripple.remove(), 700);
        });
    });
}

$(document).ready(async function() {
    const isAuthenticated = await applyUserAccess();
    if (!isAuthenticated) {
        return;
    }

    $('#reg-tag-id').on('input', function () {
        this.value = normalizeTagId(this.value);
        scheduleTagLookup(this.value);
    });
    $('#reg-tag-id').on('blur', function () {
        this.value = normalizeTagId(this.value);
        scheduleTagLookup(this.value, true);
    });

    monitoringTable = $('#monitoringTable').DataTable({
        paging: true,
        pagingType: 'simple_numbers',
        pageLength: 25,
        lengthChange: false,
        deferRender: true,
        scrollY: '420px',
        scrollCollapse: true,
        order: [[8, "desc"]],
        columnDefs: [{ targets: 8, visible: false, searchable: false }],
        dom: 'Bfrtp',
        buttons: [
            { extend: 'excel', text: 'Download Excel', className: 'dt-button', exportOptions: { columns: [0,1,2,3,4,5,6,7] } },
            { extend: 'pdf', text: 'Download PDF', className: 'dt-button', exportOptions: { columns: [0,1,2,3,4,5,6,7] } }
        ]
    });

    setupLivePaginationControls();
    initWebSocket();
    refreshStats();
    loadOldLogs();
    setupUiMotion();
    setupRippleEffects();
    if (!restoreActivePageAfterReload()) {
        showPage('dashboard-page');
    }
});

function showPage(pageId) {
    $('.page-section').removeClass('page-active');
    $('#' + pageId).addClass('page-active');
    $('.nav-link').removeClass('nav-link-active');

    if (pageId === 'dashboard-page') {
        $('#btn-live').addClass('nav-link-active');
    } else if (pageId === 'registration-page') {
        $('#btn-setup').addClass('nav-link-active');
    } else if (pageId === 'user-management-page') {
        if (currentUserRole !== 'admin') {
            showPage('dashboard-page');
            return;
        }
        $('#btn-users').addClass('nav-link-active');
    }

    animateSection(pageId);

    const activePage = document.getElementById(pageId);
    if (activePage) {
        activePage.classList.remove('page-enter');
        void activePage.offsetWidth;
        activePage.classList.add('page-enter');
    }
}

async function applyUserAccess() {
    try {
        const response = await fetch('/api/me', { cache: 'no-store' });
        if (!response.ok) {
            window.location.replace('/login');
            return false;
        }
        const me = await response.json();
        currentUserRole = String(me.role || 'staff').toLowerCase();
    } catch (err) {
        console.error('Unable to read current user role:', err);
        window.location.replace('/login');
        return false;
    }

    if (currentUserRole !== 'admin') {
        $('#btn-users')
            .show()
            .addClass('nav-link-locked')
            .attr('title', 'Admin only module');
        $('#update-item-btn').hide();
        if ($('#user-management-page').hasClass('page-active')) {
            showPage('dashboard-page');
        }
    } else {
        $('#btn-users')
            .show()
            .removeClass('nav-link-locked')
            .removeAttr('title');
        $('#update-item-btn').show();
    }
    return true;
}

function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/live-monitoring`;
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        $('#hw-status').text("Online").addClass('text-emerald-300').removeClass('text-slate-400');
        $('#hw-dot').addClass('bg-emerald-300').removeClass('bg-amber-500');
    };

    socket.onmessage = (e) => {
        const d = JSON.parse(e.data);
        if (d.source === "REGISTRATION") {
            const normalizedTag = normalizeTagId(d.tag_id);
            $('#reg-tag-id').val(normalizedTag).addClass('scan-highlight');
            setTimeout(() => $('#reg-tag-id').removeClass('scan-highlight'), 500);
            scheduleTagLookup(normalizedTag, true);
        }
        if (d.source === "MONITORING") {
            queueLiveRow(d);
            bumpLiveStats(d.direction);
        }
    };

    socket.onclose = () => {
        $('#hw-status').text("Offline").removeClass('text-emerald-300').addClass('text-slate-400');
        $('#hw-dot').addClass('bg-amber-500').removeClass('bg-emerald-300');
        setTimeout(initWebSocket, 5000);
    };
}

function addRow(d, drawNow = true) {
    const datePart = String(d.date || "").trim();
    const timePart = String(d.timestamp || "").trim();
    let dateLabel = datePart || "-";
    const isoDateMatch = /^\d{4}-\d{2}-\d{2}$/.exec(datePart);
    if (isoDateMatch) {
        const [year, month, day] = datePart.split('-');
        dateLabel = `${day}-${month}-${year}`;
    }
    const timeLabel = timePart || "-";
    const directionClass = d.direction === 'IN' ? 'status-chip status-chip-in' : 'status-chip status-chip-out';
    const badge = `<div class="text-right"><span class="${directionClass}">${d.direction || '-'}</span></div>`;
    const sortKey = Number(d.id || Date.now() / 1000);
    const parsedPiece = Number(d.piece || 0);
    const pieceLabel = Number.isFinite(parsedPiece) ? parsedPiece.toLocaleString('en-IN') : (d.piece || 0);
    if (monitoringTable.rows().count() >= MAX_LIVE_ROWS) {
        const lastIndex = monitoringTable.rows().indexes().toArray().pop();
        if (lastIndex !== undefined) monitoringTable.row(lastIndex).remove();
    }
    monitoringTable.row.add([
        dateLabel,
        timeLabel,
        `<span class="tag-chip">${d.tag_id || '-'}</span>`,
        `<div class="flex flex-col"><span class="item-name">${d.item_name || '-'}</span><span class="item-sub">${d.huid || '-'}</span></div>`,
        `<span class="font-semibold text-slate-300">${d.category || '-'}</span>`,
        `<span class="font-semibold text-slate-100">${d.weight || 0}g</span>`,
        `<span class="font-black piece-cell">${pieceLabel}</span>`,
        badge,
        sortKey
    ]);
    if (drawNow) {
        monitoringTable.draw(false);
        const firstVisible = $('#monitoringTable tbody tr').first();
        firstVisible.addClass('live-row-enter');
        setTimeout(() => firstVisible.removeClass('live-row-enter'), 900);
    }
}

function updateLivePaginationMeta() {
    if (!monitoringTable) return;
    const info = monitoringTable.page.info();
    if (!info) return;

    const totalPages = Math.max(info.pages || 0, 1);
    const currentPage = (info.pages || 0) > 0 ? (info.page + 1) : 1;
    const totalRows = monitoringTable.rows().count();
    $('#live-page-meta').text(`Page ${currentPage} of ${totalPages}`);
    $('#live-row-meta').text(`Total Entries ${totalRows}`);
    $('#live-page-number').attr('max', totalPages);

    const atFirst = info.page <= 0;
    const atLast = info.page >= (info.pages - 1);
    $('#live-first-page').prop('disabled', atFirst);
    $('#live-last-page').prop('disabled', atLast);
}

function goToLivePage() {
    if (!monitoringTable) return;
    const info = monitoringTable.page.info();
    if (!info || !info.pages) return;

    let targetPage = parseInt($('#live-page-number').val(), 10);
    if (Number.isNaN(targetPage)) {
        targetPage = info.page + 1;
    }
    targetPage = Math.max(1, Math.min(targetPage, info.pages));
    $('#live-page-number').val(targetPage);
    monitoringTable.page(targetPage - 1).draw('page');
}

function setupLivePaginationControls() {
    $('#live-page-size').on('change', function () {
        const nextSize = parseInt(this.value, 10);
        if (Number.isNaN(nextSize) || nextSize <= 0) return;
        monitoringTable.page.len(nextSize).draw(false);
    });

    $('#live-first-page').on('click', function () {
        monitoringTable.page('first').draw('page');
    });

    $('#live-last-page').on('click', function () {
        monitoringTable.page('last').draw('page');
    });

    $('#live-go-page').on('click', goToLivePage);
    $('#live-page-number').on('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            goToLivePage();
        }
    });

    monitoringTable.on('draw.dt page.dt length.dt search.dt order.dt', updateLivePaginationMeta);
    updateLivePaginationMeta();
}

async function refreshStats() {
        try {
            const r = await fetch('/api/stats'); 
            const d = await r.json();
            setLiveStats(d.total, d.in, d.out);
        } catch(e) { console.error(e); }
    }

    async function loadOldLogs() {
        try {
            const r = await fetch(`/api/logs?limit=${MAX_LIVE_ROWS}`);
            const logs = await r.json();
            monitoringTable.clear(); 
            logs.forEach(l => addRow(l, false)); 
            monitoringTable.draw(false);
            monitoringTable.page('first').draw('page');
        } catch(e) { console.error(e); }
    }

    function clearRegistrationFields() {
        $('#reg-name').val('');
        $('#reg-category').val('');
        $('#reg-metal').val('');
        $('#reg-purity').val('');
        $('#reg-huid').val('');
        $('#reg-weight').val('');
        $('#reg-piece').val('');
    }

    function fillRegistrationFields(item) {
        $('#reg-name').val(item.item_name || '');
        $('#reg-category').val(item.category || '');
        $('#reg-metal').val(item.metal_type || '');
        $('#reg-purity').val(item.purity || '');
        $('#reg-huid').val(item.huid || '');
        $('#reg-weight').val(item.weight ?? '');
        $('#reg-piece').val(item.piece ?? '');
    }

    async function fetchAndFillItemByTag(tagId, silentNotFound = true) {
        if (!tagId || tagId.length !== 24) return false;

        try {
            const res = await fetch(`/api/item/${encodeURIComponent(tagId)}`, { cache: 'no-store' });
            if (res.status === 401) {
                window.location.replace('/login');
                return false;
            }
            if (res.status === 404) {
                if (!silentNotFound) {
                    alert("No registered item found for this tag.");
                }
                lastAutoFilledTag = '';
                return false;
            }
            if (!res.ok) {
                const err = await res.json();
                alert(err.detail || "Item fetch failed.");
                return false;
            }

            const item = await res.json();
            fillRegistrationFields(item);
            lastAutoFilledTag = tagId;
            return true;
        } catch (err) {
            console.error("Tag lookup failed:", err);
            return false;
        }
    }

    function scheduleTagLookup(tagId, immediate = false) {
        if (tagLookupTimer) {
            clearTimeout(tagLookupTimer);
            tagLookupTimer = null;
        }

        if (!tagId || tagId.length !== 24) {
            lastAutoFilledTag = '';
            return;
        }

        if (tagId === lastAutoFilledTag) {
            return;
        }

        if (immediate) {
            fetchAndFillItemByTag(tagId, true);
            return;
        }

        tagLookupTimer = setTimeout(() => {
            fetchAndFillItemByTag(tagId, true);
        }, 250);
    }

    function getRegistrationPayload() {
        return {
            item_name: $('#reg-name').val(),
            category: $('#reg-category').val(),
            metal_type: $('#reg-metal').val(),
            purity: $('#reg-purity').val(),
            weight: parseFloat($('#reg-weight').val()) || 0,
            huid: $('#reg-huid').val(),
            piece: parseFloat($('#reg-piece').val()) || 0
        };
    }

    function getUpdatePayload() {
        const rawTextFields = {
            item_name: $('#reg-name').val(),
            category: $('#reg-category').val(),
            metal_type: $('#reg-metal').val(),
            purity: $('#reg-purity').val(),
            huid: $('#reg-huid').val()
        };

        const payload = {};
        Object.entries(rawTextFields).forEach(([key, value]) => {
            const cleanValue = String(value || "").trim();
            if (cleanValue !== "") payload[key] = cleanValue;
        });

        const rawWeight = $('#reg-weight').val();
        if (rawWeight !== '') payload.weight = parseFloat(rawWeight);

        const rawPiece = $('#reg-piece').val();
        if (rawPiece !== '') payload.piece = parseFloat(rawPiece);

        return payload;
    }

    function validateTagIdForRegistration() {
        const tagId = normalizeTagId($('#reg-tag-id').val());
        $('#reg-tag-id').val(tagId);
        if (!tagId || tagId === "") {
            alert("RFID Tag ID is required.");
            return "";
        }
        if (tagId.length !== 24) {
            alert("EPC must be exactly 24 characters. Please scan again.");
            return "";
        }
        return tagId;
    }

    $('#registration-form').submit(async (e) => {
        e.preventDefault();
        const tagId = validateTagIdForRegistration();
        if (!tagId) return;
        
        const p = { tag_id: tagId, ...getRegistrationPayload() };
        
        try {
            const res = await fetch('/register-item', { 
                method: 'POST', 
                headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify(p) 
            });
            if(res.ok) { 
                alert("Item registered successfully.");
                clearRegistrationFields();
                $('#reg-tag-id').val('');
                lastAutoFilledTag = '';
                sessionStorage.setItem(LAST_ACTIVE_PAGE_KEY, 'registration-page');
                setTimeout(() => window.location.reload(), 120);
            } else { 
                const err = await res.json();
                alert(err.detail || "Registration failed.");
            }
        } catch (err) { alert("Connection failed."); }
    });

    async function updateRegisteredItem() {
        if (currentUserRole !== 'admin') {
            alert("Only admin can update items.");
            return;
        }

        const tagId = validateTagIdForRegistration();
        if (!tagId) return;

        const payload = getUpdatePayload();
        if (Object.keys(payload).length === 0) {
            alert("Please fill at least one field to update.");
            return;
        }

        try {
            const res = await fetch(`/update-item/${encodeURIComponent(tagId)}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                alert("Item updated successfully.");
                clearRegistrationFields();
                $('#reg-tag-id').val('');
                lastAutoFilledTag = '';
                sessionStorage.setItem(LAST_ACTIVE_PAGE_KEY, 'registration-page');
                setTimeout(() => window.location.reload(), 120);
            } else {
                const err = await res.json();
                alert(err.detail || "Update failed.");
            }
        } catch (err) {
            alert("Connection failed.");
        }
    }

    async function checkDateReport() {
        const selectedDate = $('#report-date').val();
        if(!selectedDate) return alert("Select date!");
        try {
            const res = await fetch(`/api/report-summary?target_date=${selectedDate}`);
            const data = await res.json();
            $('#res-in').text(data.in_count);
            $('#res-out').text(data.out_count);
            $('#res-pending').text(data.pending_count);
        } catch (err) { console.error(err); }
    }

  function buildOrderedCsvRows(data, preferredOrder = []) {
    if (!Array.isArray(data) || data.length === 0) return { headers: [], rows: [] };
    const headerLabelMap = {
      piece: "Piece"
    };
    const firstRowKeys = Object.keys(data[0]);
    const orderedPreferred = preferredOrder.filter(k => firstRowKeys.includes(k));
    const remaining = firstRowKeys.filter(k => !orderedPreferred.includes(k));
    const keys = [...orderedPreferred, ...remaining];
    const headers = keys.map((key) => headerLabelMap[key] || key);
    const rows = data.map((row, idx) => keys.map((key) => {
      if (key === "sr_no") return idx + 1;
      const value = row[key];
      return value === undefined || value === null ? "" : value;
    }));
    return { headers, rows };
  }

  function downloadCsvFallback(fileName, headers, rows) {
    const csv = [headers.join(',')];
    rows.forEach((row) => csv.push(row.map((v) => JSON.stringify(v ?? "")).join(',')));
    const blob = new Blob([csv.join('\r\n')], { type: 'text/csv' });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    const outName = fileName.toLowerCase().endsWith('.xlsx')
      ? fileName.replace(/\.xlsx$/i, '.csv')
      : `${fileName}.csv`;
    link.download = outName;
    link.click();
  }

  function downloadAutoWidthXlsx(fileName, sheetName, headers, rows) {
    if (!headers.length) return;

    // Fallback to CSV if XLSX lib is unavailable.
    if (typeof XLSX === "undefined" || !XLSX.utils) {
      downloadCsvFallback(fileName, headers, rows);
      return;
    }

    const normalizedRows = rows.map((row) => row.map((cell) => {
      if (cell === undefined || cell === null) return "";
      return cell;
    }));

    const aoa = [headers, ...normalizedRows];
    const ws = XLSX.utils.aoa_to_sheet(aoa);

    const colWidths = headers.map((header, colIdx) => {
      let maxLen = String(header ?? "").length;
      normalizedRows.forEach((row) => {
        const cellLen = String(row[colIdx] ?? "").length;
        if (cellLen > maxLen) maxLen = cellLen;
      });
      return { wch: Math.min(52, Math.max(10, maxLen + 2)) };
    });
    ws['!cols'] = colWidths;

    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, sheetName || "Report");

    const wbout = XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
    const blob = new Blob(
      [wbout],
      { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }
    );
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = fileName;
    link.click();
  }

  async function downloadMissingReport() {
    const selectedDate = document.getElementById('report-date').value;
    
    if (!selectedDate) {
        alert("Please select a date first!");
        return;
    }

    try {
        const res = await fetch(`/api/missing-items?target_date=${selectedDate}`);
        const data = await res.json();
        
        if (data.length === 0) { 
            alert(`No items were found pending (IN) for ${selectedDate}`); 
            return; 
        }

        const { headers, rows } = buildOrderedCsvRows(data);
        downloadAutoWidthXlsx(
            `Missing_Items_${selectedDate}.xlsx`,
            "Missing Items",
            headers,
            rows
        );
    } catch (err) { 
        alert("Download failed"); 
        console.error(err);
    }
}

    async function filterReportByRange() {
        const start = $('#feed-start-date').val();
        const end = $('#feed-end-date').val();
        if (!start || !end) {
            alert("Please select both start and end dates.");
            return;
        }
        if (start > end) {
            alert("Start date cannot be after end date.");
            return;
        }
        try {
            const response = await fetch(`/api/logs?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}&limit=${MAX_LIVE_ROWS}`);
            if (!response.ok) {
                const err = await response.json();
                alert(err.detail || "Unable to filter by date range.");
                return;
            }
            const logs = (await response.json()).slice(0, MAX_LIVE_ROWS);
            monitoringTable.clear();
            logs.forEach(log => addRow(log, false));
            monitoringTable.draw(false);
            monitoringTable.page('first').draw('page');
        } catch (err) { console.error(err); }
    }

    async function downloadFullInventory() {
        try {
            const res = await fetch('/api/all-inventory');
            const data = await res.json();
            if (data.length === 0) { alert("Inventory is empty!"); return; }
            const preferredColumns = [
                "tag_id",
                "item_name",
                "category",
                "metal_type",
                "purity",
                "huid",
                "weight",
                "piece",
                "timestamp"
            ];
            const { headers, rows } = buildOrderedCsvRows(data, preferredColumns);
            downloadAutoWidthXlsx(
                `Full_Inventory_Report_${new Date().toISOString().split('T')[0]}.xlsx`,
                "Full Inventory",
                headers,
                rows
            );
        } catch (err) { alert("Download failed!"); }
    }

async function registerNewUser() {
    const msg = document.getElementById('user-action-msg');
    const userData = {
        username: document.getElementById('new_username').value.trim(),
        password: document.getElementById('new_password').value.trim(),
        role: document.getElementById('new_role').value
    };

    if (!userData.username || !userData.password) {
        msg.className = 'text-xs font-semibold mt-4 text-red-700';
        msg.textContent = 'Username and password required.';
        return;
    }

    try {
        const response = await fetch('/api/add-user', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(userData)
        });

        if (response.ok) {
            msg.className = 'text-xs font-semibold mt-4 text-emerald-600';
            msg.textContent = 'User created successfully.';
            document.getElementById('addUserForm').reset();
        } else {
            const error = await response.json();
            msg.className = 'text-xs font-semibold mt-4 text-red-700';
            msg.textContent = error.detail || 'Unable to create user.';
        }
    } catch (err) {
        msg.className = 'text-xs font-semibold mt-4 text-red-700';
        msg.textContent = 'Network error while creating user.';
    }
}
