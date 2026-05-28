/* ══════════════════════════════════════════════════════════════
   Plantex Sales Dashboard — Frontend Rendering Layer
   All calculations and table rendering are done via SSR (Django).
   This file handles Chart.js rendering, modals, and lightweight UI.
   ══════════════════════════════════════════════════════════════ */

var charts = {};
var modalResponseCache = {};
var MODAL_RESPONSE_CACHE_TTL_MS = 3 * 60 * 1000;
var MODAL_RESPONSE_CACHE_MAX_ITEMS = 120;

function fmtNum(n) { return new Intl.NumberFormat('en-IN').format(n); }
function fmtShort(n) {
    if (n >= 10000000) return '₹' + (n / 10000000).toFixed(2) + ' Cr';
    if (n >= 100000)   return '₹' + (n / 100000).toFixed(1) + ' L';
    if (n >= 1000)     return '₹' + (n / 1000).toFixed(0) + ' K';
    return '₹' + n.toFixed(0);
}
function fmtDateLabel(d) {
    var p = d.split('-');
    if (p.length === 3) {
        var m = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return parseInt(p[2]) + ' ' + m[parseInt(p[1])];
    }
    return d;
}

/* ── Chart Defaults ── */
Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.color = '#94a3b8';

function cOpts(extra) {
    var base = {
        responsive: true, maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: '#0f172a',
                titleColor: '#f0f0ff',
                bodyColor: '#94a3b8',
                borderColor: '#1e293b',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 10,
                titleFont: { weight: 600 },
                bodyFont: { size: 11 }
            }
        },
        scales: {
            x: {
                grid: { display: false },
                ticks: { font: { size: 10 }, maxRotation: 0 },
                border: { display: false }
            },
            y: {
                grid: { color: 'rgba(148,163,184,0.08)', drawBorder: false },
                ticks: {
                    font: { size: 10 },
                    callback: function (v) { return fmtShort(v); }
                },
                border: { display: false }
            }
        }
    };
    if (extra) {
        for (var k in extra) {
            if (k === 'plugins' || k === 'scales') {
                for (var sk in extra[k]) {
                    base[k][sk] = Object.assign(base[k][sk] || {}, extra[k][sk]);
                }
            } else {
                base[k] = extra[k];
            }
        }
    }
    return base;
}

function destroyChart(id) { if (charts[id]) { charts[id].destroy(); delete charts[id]; } }

function _buildModalDataUrl(modalEl, extraParams) {
    var lazyUrl = modalEl ? modalEl.getAttribute('data-lazy-url') : '';
    if (!lazyUrl) return '';
    var params = new URLSearchParams(window.location.search || '');
    if (extraParams) {
        Object.keys(extraParams).forEach(function (k) {
            var val = extraParams[k];
            if (val === null || typeof val === 'undefined' || val === '') params.delete(k);
            else params.set(k, String(val));
        });
    }
    var qs = params.toString();
    return qs ? (lazyUrl + '?' + qs) : lazyUrl;
}

function _getModalResponseFromCache(cacheKey) {
    var record = modalResponseCache[cacheKey];
    if (!record) return null;
    if (Date.now() > Number(record.expiresAt || 0)) {
        delete modalResponseCache[cacheKey];
        return null;
    }
    return record.payload || null;
}

function _setModalResponseCache(cacheKey, payload) {
    if (!cacheKey) return;
    modalResponseCache[cacheKey] = {
        expiresAt: Date.now() + MODAL_RESPONSE_CACHE_TTL_MS,
        payload: payload
    };
    var keys = Object.keys(modalResponseCache);
    if (keys.length <= MODAL_RESPONSE_CACHE_MAX_ITEMS) return;
    keys.sort(function (a, b) {
        return (modalResponseCache[a].expiresAt || 0) - (modalResponseCache[b].expiresAt || 0);
    });
    var trimCount = keys.length - MODAL_RESPONSE_CACHE_MAX_ITEMS;
    for (var i = 0; i < trimCount; i++) {
        delete modalResponseCache[keys[i]];
    }
}

function _ensureModalExportButtons(modalEl) {
    if (!modalEl || modalEl.dataset.exportButtonsInit === '1') return;
    if (!modalEl.getAttribute('data-lazy-url')) return;
    if (modalEl.querySelector('.inv-health-download')) return; // inventory already has export buttons

    var header = modalEl.querySelector('.tbl-overlay-header');
    if (!header) return;

    var closeBtn = header.querySelector('.tbl-overlay-close');
    var actions = document.createElement('div');
    actions.style.display = 'flex';
    actions.style.gap = '8px';
    actions.style.marginLeft = 'auto';
    actions.style.marginRight = '12px';

    ['csv', 'excel'].forEach(function (format) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ceo-view-all-btn';
        btn.textContent = format === 'csv' ? 'Download CSV' : 'Download Excel';
        btn.onclick = function () {
            var url = _buildModalDataUrl(modalEl, { export: format });
            if (url) window.location.href = url;
        };
        actions.appendChild(btn);
    });

    if (closeBtn) header.insertBefore(actions, closeBtn);
    else header.appendChild(actions);

    modalEl.dataset.exportButtonsInit = '1';
}

function _ensureModalPreviewNotice(modalEl) {
    // Modal rows are server-paginated so large detail tables do not block the page.
}

/** Destroy every known chart instance to prevent canvas-reuse errors */
function destroyAllCharts() {
    var allIds = ['salesTrendChart', 'platformSplitChart', 'forecastChart'];
    allIds.forEach(function(id) { destroyChart(id); });
    // Also destroy any leftover keys
    Object.keys(charts).forEach(function(id) { destroyChart(id); });
}

/* ── Modal Logic ── */
function _renderModalPagination(modalEl, pagination) {
    if (!modalEl) return;
    var container = modalEl.querySelector('.modal-pagination-controls');
    if (!container) {
        var scroll = modalEl.querySelector('.tbl-overlay-scroll');
        if (!scroll || !scroll.parentNode) return;
        container = document.createElement('div');
        container.className = 'modal-pagination-controls';
        container.style.display = 'flex';
        container.style.alignItems = 'center';
        container.style.justifyContent = 'flex-end';
        container.style.gap = '8px';
        container.style.padding = '10px 0 0';
        scroll.parentNode.appendChild(container);
    }

    container.style.display = 'flex';

    if (!pagination || !pagination.total_pages || pagination.total_pages <= 1) {
        container.innerHTML = '';
        return;
    }

    var currentPage = Number(pagination.page || 1);
    var totalPages = Number(pagination.total_pages || 1);

    container.innerHTML = '';
    var info = document.createElement('span');
    info.style.fontSize = '12px';
    info.style.color = 'var(--muted)';
    info.textContent = 'Page ' + currentPage + ' of ' + totalPages;
    container.appendChild(info);

    var prevBtn = document.createElement('button');
    prevBtn.type = 'button';
    prevBtn.className = 'btn-page';
    prevBtn.textContent = 'Prev';
    prevBtn.disabled = !pagination.has_prev;
    prevBtn.style.opacity = pagination.has_prev ? '1' : '0.5';
    prevBtn.style.cursor = pagination.has_prev ? 'pointer' : 'not-allowed';
    prevBtn.onclick = function () {
        if (!pagination.has_prev) return;
        _loadModalRowsOnDemand(modalEl, { page: currentPage - 1, force: true });
    };
    container.appendChild(prevBtn);

    var nextBtn = document.createElement('button');
    nextBtn.type = 'button';
    nextBtn.className = 'btn-page';
    nextBtn.textContent = 'Next';
    nextBtn.disabled = !pagination.has_next;
    nextBtn.style.opacity = pagination.has_next ? '1' : '0.5';
    nextBtn.style.cursor = pagination.has_next ? 'pointer' : 'not-allowed';
    nextBtn.onclick = function () {
        if (!pagination.has_next) return;
        _loadModalRowsOnDemand(modalEl, { page: currentPage + 1, force: true });
    };
    container.appendChild(nextBtn);
}

function _initializeDataTable(modalEl) {
    if (!window.jQuery || !$.fn.DataTable) return;
    var table = modalEl.querySelector('table');
    if (!table) return;

    // Don't initialize on empty/loading state — a colspan placeholder row causes
    // DataTables tn/18 "Incorrect column count" because it sees 1 column vs N in thead.
    var bodyRows = table.querySelectorAll('tbody tr');
    if (!bodyRows.length || (bodyRows.length === 1 && bodyRows[0].querySelector('td[colspan]'))) return;

    // Destroy existing DataTable instance if any
    if ($.fn.DataTable.isDataTable(table)) {
        $(table).DataTable().destroy();
    }

    // Hide the inv-health-search since DataTables has its own search
    var invSearch = modalEl.querySelector('.inv-health-search');
    if (invSearch) invSearch.style.display = 'none';

    // Hide custom pagination controls since DataTables handles it
    var paginationBox = modalEl.querySelector('.modal-pagination-controls');
    if (paginationBox) paginationBox.style.display = 'none';

    var modalId = modalEl.id || '';
    var headerCount = table.querySelectorAll('thead th').length;
    var defaultOrder = [];
    if (modalId === 'catGrowthModal') {
        defaultOrder = [[2, 'desc'], [1, 'desc']];
    } else if (modalId === 'winningModal') {
        defaultOrder = [[1, 'desc']];
    } else if (modalId === 'topProductsModal') {
        defaultOrder = [[2, 'desc']];
    } else if (modalId === 'bizDecliningModal' || modalId === 'decliningModal') {
        defaultOrder = [[headerCount - 1, 'asc']];
    } else if (modalId === 'clusterModal') {
        defaultOrder = [[1, 'desc']];
    }

    $(table).DataTable({
        pageLength: 25,
        lengthChange: true,
        lengthMenu: [10, 25, 50, 100],
        searching: true,
        ordering: true,
        order: defaultOrder,
        info: true,
        autoWidth: false,
        responsive: true,
        dom: '<"dt-top"lf>rt<"dt-bottom"ip>',
        language: {
            search: "",
            searchPlaceholder: "Search records...",
            lengthMenu: "Show _MENU_ entries",
            info: "Showing _START_ to _END_ of _TOTAL_ entries",
            paginate: {
                previous: "Prev",
                next: "Next"
            }
        }
    });
}

function _loadModalRowsOnDemand(modalEl, opts) {
    if (!modalEl) return;
    opts = opts || {};
    var force = !!opts.force;
    var lazyUrl = modalEl.getAttribute('data-lazy-url');
    if (!lazyUrl) return;
    if (modalEl.dataset.lazyLoading === '1') return;

    var tbody = modalEl.querySelector('tbody[data-modal-lazy="1"]');
    if (!tbody) return;
    
    if (!force && modalEl.dataset.lazyLoaded === '1') return;

    var thCount = 3;
    var ths = modalEl.querySelectorAll('thead th');
    if (ths && ths.length) thCount = ths.length;

    modalEl.dataset.lazyLoading = '1';

    // Destroy existing DataTable before modifying innerHTML
    if (window.jQuery && $.fn.DataTable && modalEl.querySelector('table')) {
        var existingTable = modalEl.querySelector('table');
        if ($.fn.DataTable.isDataTable(existingTable)) {
            $(existingTable).DataTable().destroy();
        }
    }

    tbody.innerHTML = '<tr><td colspan="' + thCount + '" class="empty">Loading data...</td></tr>';

    // Non-inventory modals use DataTables (client-side) — request all rows at once.
    var isInventoryModal = modalEl.classList.contains('inventory-health-modal');
    var useDataTable = !isInventoryModal && window.jQuery && $.fn.DataTable;

    var extraParams;
    if (useDataTable && !opts.page) {
        // DataTables mode: load all rows in one shot; DataTables handles pagination/search.
        extraParams = { all: '1' };
    } else {
        var page = Number(opts.page || modalEl.dataset.currentPage || 1) || 1;
        var pageSize = Number(opts.pageSize || modalEl.dataset.pageSize || 50) || 50;
        modalEl.dataset.pageSize = String(pageSize);
        extraParams = { page: page, page_size: pageSize };
    }
    var searchInput = modalEl.querySelector('.inv-health-search');
    var searchTerm = searchInput ? String(searchInput.value || '').trim() : '';
    if (searchTerm) extraParams.q = searchTerm;
    else extraParams.q = null;

    var url = _buildModalDataUrl(modalEl, extraParams);
    var cacheKey = modalEl.id + "::" + url;
    var cachedPayload = _getModalResponseFromCache(cacheKey);
    
    function applyLoadedHTML(html, pagination, useDatatable) {
        tbody.innerHTML = html || '<tr><td colspan="' + thCount + '" class="empty">No data available.</td></tr>';
        modalEl.dataset.lazyLoaded = '1';
        if (pagination && pagination.page) {
            modalEl.dataset.currentPage = String(pagination.page);
        }
        if (useDatatable) {
            _initializeDataTable(modalEl);
        } else {
            _renderModalPagination(modalEl, pagination || null);
        }
    }
    
    if (cachedPayload) {
        applyLoadedHTML(cachedPayload.html, cachedPayload.pagination, cachedPayload.use_datatable);
        modalEl.dataset.lazyLoading = '0';
        return;
    }
    
    if (modalEl._modalFetchController) {
        try { modalEl._modalFetchController.abort(); } catch (e) {}
    }
    modalEl._modalFetchController = new AbortController();
    fetch(url, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        signal: modalEl._modalFetchController.signal
    })
        .then(function (resp) {
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var contentType = String(resp.headers.get('content-type') || '').toLowerCase();
            if (contentType.indexOf('application/json') !== -1) {
                return resp.json();
            }
            return resp.text().then(function (html) {
                return { html: html };
            });
        })
        .then(function (payload) {
            applyLoadedHTML(
                payload && payload.html ? payload.html : null,
                payload ? payload.pagination : null,
                !!(payload && payload.use_datatable)
            );
            _setModalResponseCache(cacheKey, payload || {});
        })
        .catch(function (e) {
            if (modalEl._modalFetchController && modalEl._modalFetchController.signal.aborted) {
                return;
            }
            tbody.innerHTML = '<tr><td colspan="' + thCount + '" class="empty">Failed to load data. Please retry.</td></tr>';
        })
        .finally(function () {
            modalEl.dataset.lazyLoading = '0';
        });
}

function openModal(id) {
    var el = document.getElementById(id);
    if (el) {
        _ensureModalExportButtons(el);
        _ensureModalPreviewNotice(el);
        _loadModalRowsOnDemand(el);
        el.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}
function closeModal(id) {
    var el = document.getElementById(id);
    if (el) { el.classList.remove('active'); document.body.style.overflow = ''; }
}
// Close on clicking overlay background
document.addEventListener('click', function (e) {
    if (e.target.classList.contains('tbl-overlay') && e.target.classList.contains('active')) {
        e.target.classList.remove('active');
        document.body.style.overflow = '';
    }
});
// Close on Escape key
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.tbl-overlay.active').forEach(function (el) {
            el.classList.remove('active');
        });
        document.body.style.overflow = '';
    }
});

function initInventoryHealthModals() {
    var modals = document.querySelectorAll('.inventory-health-modal');
    modals.forEach(function (modal) {
        if (modal.dataset.invHealthInit === '1') return;
        modal.dataset.invHealthInit = '1';

        var table = modal.querySelector('.inv-health-table');
        var tbody = table ? table.querySelector('tbody') : null;
        if (!table || !tbody) return;

        var searchInput = modal.querySelector('.inv-health-search');
        var debounceId = null;

        modal.querySelectorAll('.inv-health-download').forEach(function (btn) {
            btn.onclick = function () {
                var format = btn.getAttribute('data-format');
                var q = searchInput ? String(searchInput.value || '').trim() : '';
                var extra = { export: format };
                if (q) extra.q = q;
                var url = _buildModalDataUrl(modal, extra);
                if (url) {
                    window.location.href = url;
                    return;
                }
            };
        });

        // Server-side search with pagination
        if (searchInput) {
            searchInput.oninput = function () {
                if (debounceId) clearTimeout(debounceId);
                debounceId = setTimeout(function () {
                    _loadModalRowsOnDemand(modal, { page: 1, force: true });
                }, 250);
            };
        }
    });
}

/* ══════════════════════════════════════════════════════════════
   INIT CHARTS
   ══════════════════════════════════════════════════════════════ */
function initCharts() {
    var data = window.DASHBOARD_PAYLOAD;
    if (!data) return;

    // Always destroy all existing charts first to prevent canvas-reuse errors
    destroyAllCharts();

    var viewType = window.DASHBOARD_VIEW_TYPE;

    /* ── Business Dashboard Charts ── */
    if (viewType === 'business') {
        initCEOCharts(data);
    }

    /* ── CEO Dashboard Charts ── */
    if (viewType === 'ceo') {
        initCEOCharts(data);
    }

    /* ── Category Dashboard Charts ── */
    if (viewType === 'category') {
        initCEOCharts(data);
    }
}

/* ══════════════════════════════════════════════════════════════
   CEO / SHARED DASHBOARD CHARTS
   ══════════════════════════════════════════════════════════════ */
function initCEOCharts(data) {
    if (data.charts && data.charts.trend && document.getElementById('salesTrendChart')) {
        renderSalesTrend(data, 'daily');
    }
    if (data.platforms && document.getElementById('platformSplitChart')) {
        renderPlatformDonut(data);
    }
    if (data.forecast && document.getElementById('forecastChart')) {
        renderForecastChart(data);
    }
    initTrendTabs(data);
}

/* ── Aggregate helpers ── */
function aggregateData(labels, current, prev, chunkSize) {
    var newLabels = [], newCurrent = [], newPrev = [];
    for (var i = 0; i < labels.length; i += chunkSize) {
        var end = Math.min(i + chunkSize, labels.length);
        newLabels.push(labels[i] + ' - ' + labels[end - 1]);
        var sumC = 0, sumP = 0;
        for (var j = i; j < end; j++) { sumC += (current[j] || 0); sumP += (prev[j] || 0); }
        newCurrent.push(sumC); newPrev.push(sumP);
    }
    return { labels: newLabels, current: newCurrent, prev: newPrev };
}

function aggregateMultiData(labels, rawSets, chunkSize) {
    var newLabels = [];
    var newSets = {};
    var keys = Object.keys(rawSets);
    keys.forEach(function (k) { newSets[k] = []; });
    for (var i = 0; i < labels.length; i += chunkSize) {
        var end = Math.min(i + chunkSize, labels.length);
        newLabels.push(labels[i] + ' - ' + labels[end - 1]);
        keys.forEach(function (k) {
            var sum = 0;
            for (var j = i; j < end; j++) { sum += (rawSets[k][j] || 0); }
            newSets[k].push(sum);
        });
    }
    return Object.assign({ labels: newLabels }, newSets);
}

/* ── Sales Trend Chart ── */
function renderSalesTrend(data, period) {
    destroyChart('salesTrendChart');
    var trend = data.charts.trend;
    var labels = trend.labels.map(fmtDateLabel);

    var platformEl = document.getElementById('platformSelect');
    var platform   = platformEl ? platformEl.value : '';  // '' = All
    var dateEl     = document.getElementById('dateRangeSelect');
    var isFiltered = !!(dateEl && dateEl.value);
    var prevLabel  = isFiltered ? 'Selected Date Range' : 'Last Period';

    // Check if per-platform data is meaningful (all-zeros means only one platform active)
    var hasAmazon   = trend.amazon_revenue   && trend.amazon_revenue.some(function(v)   { return v > 0; });
    var hasFlipkart = trend.flipkart_revenue && trend.flipkart_revenue.some(function(v) { return v > 0; });
    var showDualLines = (!platform || platform === '') && hasAmazon && hasFlipkart;

    var rawSets = { 'current': trend.revenue };
    if (showDualLines) {
        rawSets['amazon']   = trend.amazon_revenue;
        rawSets['flipkart'] = trend.flipkart_revenue;
    }
    if (trend.prev_revenue && trend.prev_revenue.length > 0) {
        rawSets['prev'] = trend.prev_revenue;
    }

    var processed = Object.assign({ labels: labels }, rawSets);
    if (period === 'weekly')       processed = aggregateMultiData(labels, rawSets, 7);
    else if (period === 'monthly') processed = aggregateMultiData(labels, rawSets, 30);

    var datasets = [];

    if (platform === 'Amazon') {
        datasets.push({ label: 'Amazon Revenue', data: processed.current, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.08)', fill: true, tension: 0.4, borderWidth: 2.5, pointRadius: 0 });
    } else if (platform === 'Flipkart') {
        datasets.push({ label: 'Flipkart Revenue', data: processed.current, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.08)', fill: true, tension: 0.4, borderWidth: 2.5, pointRadius: 0 });
    } else if (showDualLines) {
        // "All" with both platforms — show two separate lines
        datasets.push({ label: 'Amazon',   data: processed.amazon,   borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.05)', fill: true, tension: 0.4, borderWidth: 2, pointRadius: 0 });
        datasets.push({ label: 'Flipkart', data: processed.flipkart, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.05)', fill: true, tension: 0.4, borderWidth: 2, pointRadius: 0 });
    } else {
        // "All" but only one platform has data — single combined line
        var cBorder = '#0891b2';
        var cBg     = 'rgba(8,145,178,0.08)';
        var cLabel  = 'Total Revenue';

        if (hasFlipkart && !hasAmazon) {
            cBorder = '#f59e0b'; // Flipkart Orange
            cBg     = 'rgba(245,158,11,0.08)';
            cLabel  = 'Flipkart Revenue';
        } else if (hasAmazon && !hasFlipkart) {
            cBorder = '#3b82f6'; // Amazon Blue
            cBg     = 'rgba(59,130,246,0.08)';
            cLabel  = 'Amazon Revenue';
        }

        datasets.push({ label: cLabel, data: processed.current, borderColor: cBorder, backgroundColor: cBg, fill: true, tension: 0.4, borderWidth: 2.5, pointRadius: 0 });
    }

    if (processed.prev && processed.prev.length > 0) {
        datasets.push({ label: prevLabel, data: processed.prev, borderColor: '#94a3b8', borderDash: [5, 5], fill: false, tension: 0.4, borderWidth: 1.5, pointRadius: 0 });
    }

    // Show legend when multiple datasets are displayed
    var showLegend = datasets.length > 1;

    charts.salesTrendChart = new Chart(document.getElementById('salesTrendChart'), {
        type: 'line',
        data: { labels: processed.labels, datasets: datasets },
        options: cOpts({
            plugins: {
                legend: { display: showLegend, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
                tooltip: { mode: 'index', intersect: false }
            },
            interaction: { mode: 'index', intersect: false }
        })
    });
}

function initTrendTabs(data) {
    var tabs = document.querySelectorAll('.ceo-trend-tab');
    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            tabs.forEach(function (t) { t.classList.remove('active'); });
            tab.classList.add('active');
            renderSalesTrend(data, tab.getAttribute('data-period'));
        });
    });
}

/* ── Platform Split Donut ── */
function renderPlatformDonut(data) {
    var el = document.getElementById('platformSplitChart');
    if (!el) return;
    destroyChart('platformSplitChart');
    var pLabels = Object.keys(data.platforms);
    if (!pLabels.length) return;
    var pVals   = pLabels.map(function (k) { return data.platforms[k].revenue; });
    var colors  = pLabels.map(function (name) {
        return name === 'Amazon' ? '#3b82f6' : (name === 'Flipkart' ? '#f59e0b' : '#10b981');
    });
    charts.platformSplitChart = new Chart(el, {
        type: 'doughnut',
        data: { labels: pLabels, datasets: [{ data: pVals, backgroundColor: colors, borderWidth: 0 }] },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '72%',
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: function (ctx) { return ctx.label + ': ' + fmtShort(ctx.raw) + ' (' + data.platforms[ctx.label].pct + '%)'; } } }
            }
        }
    });
}

/* ── Revenue Forecast Chart ── */
function renderForecastChart(data) {
    var el = document.getElementById('forecastChart');
    if (!el) return;
    destroyChart('forecastChart');
    var fc = data.forecast;

    // Validate required array fields exist
    if (!fc || !fc.labels || !fc.labels.length) return;

    charts.forecastChart = new Chart(el, {
        type: 'line',
        data: {
            labels: fc.labels,
            datasets: [
                {
                    label: 'Actual',
                    data: fc.actual,
                    borderColor: '#0891b2',
                    backgroundColor: 'rgba(8,145,178,0.08)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2.5,
                    pointRadius: 0,
                    spanGaps: false
                },
                {
                    label: 'Forecast',
                    data: fc.forecast,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16,185,129,0.06)',
                    borderDash: [5, 5],
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2,
                    pointRadius: 0,
                    spanGaps: false
                },
                {
                    label: 'Target',
                    data: fc.target_line,
                    borderColor: '#94a3b8',
                    borderDash: [3, 3],
                    fill: false,
                    tension: 0,
                    borderWidth: 1.5,
                    pointRadius: 0,
                    spanGaps: true
                }
            ]
        },
        options: cOpts({
            plugins: {
                legend: {
                    display: true, position: 'top',
                    labels: { boxWidth: 12, font: { size: 10 } }
                }
            }
        })
    });
}

/* ══════════════════════════════════════════════════════════════
   INIT — on page load AND after every HTMX navigation
   ══════════════════════════════════════════════════════════════ */
window.addEventListener('DOMContentLoaded', function () {
    initCharts();
    initInventoryHealthModals();
    syncFilterVisibility();
});

// Re-initialize charts after HTMX content swap (base_htmx.html dispatches this event)
document.addEventListener('dashboardContentLoaded', function () {
    setTimeout(function () {
        initCharts();
        initInventoryHealthModals();
    }, 50);
});
