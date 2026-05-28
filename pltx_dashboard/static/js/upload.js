// =============================================================================
// Two-Phase Upload Flow
// Phase 1: Files upload to tmp/ with per-file progress bars (button disabled)
// Phase 2: User clicks "Process" → files move from tmp/ to uploads/ → Celery
// =============================================================================

// ---------------------------------------------------------------------------
// CSRF Helper
// ---------------------------------------------------------------------------
function getCookie(name) {
    let v = null;
    document.cookie.split(';').forEach(c => {
        c = c.trim();
        if (c.startsWith(name + '=')) v = decodeURIComponent(c.substring(name.length + 1));
    });
    return v;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let uploadFlowPlatform = null;
let uploadFlowStarted = false;
let uploadFlowCompleted = false;
let isProcessingBatch = false;

// Staged files registry: { uniqueId: { staged_path, file_type, date_str, original_name, inputId } }
const stagedFiles = new Map();
// Track files currently uploading (to disable button)
let activeUploads = 0;

// ---------------------------------------------------------------------------
// WebSocket / Global Progress Integration
// ---------------------------------------------------------------------------
function syncInlineUploadStatusFromState(state) {
    const statusEl = document.getElementById('statusMsg');
    if (!statusEl) return;
    const message = String((state && state.message) || "");
    if (!message) return;
    statusEl.textContent = `⚙️ ${message}`;
}

function handleGlobalUploadProgressEvent(event) {
    const data = (event && event.detail) || {};
    const statusEl = document.getElementById('statusMsg');
    if (!statusEl) return;

    const status = String(data.status || "").toLowerCase();
    const message = String(data.message || "");
    if (message) {
        statusEl.textContent = `⚙️ ${message}`;
    }

    if (status === "complete" || status === "success") {
        uploadFlowCompleted = true;
        statusEl.textContent = "✅ All files processed successfully!";
        if (uploadFlowStarted && uploadFlowPlatform) {
            if (uploadFlowPlatform === 'amazon') {
                setTimeout(() => { window.location.href = '/dashboard/business/'; }, 500);
            } else if (uploadFlowPlatform === 'flipkart') {
                setTimeout(() => { window.location.href = '/dashboard/business/?platform=Flipkart'; }, 500);
            }
        }
    } else if (status === "error") {
        uploadFlowCompleted = true;
        isProcessingBatch = false;
        setUploadControlsDisabled(false);
        updateProcessButton();
    }
}

// ---------------------------------------------------------------------------
// Platform Switching
// ---------------------------------------------------------------------------
function getSelectedPlatform() {
    return document.querySelector('input[name="platform"]:checked')?.value || 'amazon';
}

document.querySelectorAll('input[name="platform"]').forEach(radio => {
    radio.addEventListener('change', () => {
        const platform = getSelectedPlatform();
        document.getElementById('amazonSection').classList.toggle('active', platform === 'amazon');
        document.getElementById('flipkartSection').classList.toggle('active', platform === 'flipkart');
        clearAllFiles();
        updateProcessButton();
    });
});

// ---------------------------------------------------------------------------
// File input IDs and list IDs
// ---------------------------------------------------------------------------
const AMAZON_IDS = ['csvInput', 'catFile', 'spendFile', 'priceFile', 'fbaStockFile', 'flexStockFile'];
const AMAZON_LISTS = ['fileList', 'catFileList', 'spendFileList', 'priceFileList', 'fbaStockFileList', 'flexStockFileList'];

const FK_IDS = [
    'fkSearchTrafficFile', 'fkCategoryFile', 'fkPriceFile',
    'fkPlaNewFile', 'fkFbaStockFile', 'fkInventoryFile'
];
const FK_LISTS = [
    'fkSearchTrafficFileList', 'fkCategoryFileList', 'fkPriceFileList',
    'fkPlaNewFileList', 'fkFbaStockFileList', 'fkInventoryFileList'
];

// Map input IDs to file_type values for the API
const INPUT_TO_FILE_TYPE = {
    csvInput: 'sales',
    catFile: 'category',
    spendFile: 'spend',
    priceFile: 'price',
    fbaStockFile: 'fba_stock',
    flexStockFile: 'flex_stock',
    fkSearchTrafficFile: 'fk_search_traffic',
    fkCategoryFile: 'fk_category',
    fkPriceFile: 'fk_price',
    fkPlaNewFile: 'fk_pla',
    fkFbaStockFile: 'fk_fba_stock',
    fkInventoryFile: 'fk_inventory',
};

// Map input IDs to their file list container IDs
const INPUT_TO_LIST = {
    csvInput: 'fileList',
    catFile: 'catFileList',
    spendFile: 'spendFileList',
    priceFile: 'priceFileList',
    fbaStockFile: 'fbaStockFileList',
    flexStockFile: 'flexStockFileList',
    fkSearchTrafficFile: 'fkSearchTrafficFileList',
    fkCategoryFile: 'fkCategoryFileList',
    fkPriceFile: 'fkPriceFileList',
    fkPlaNewFile: 'fkPlaNewFileList',
    fkFbaStockFile: 'fkFbaStockFileList',
    fkInventoryFile: 'fkInventoryFileList',
};

const DEMO_TEMPLATE_MAP = {
    csvInput: 'upload_sales',
    catFile: 'upload_category',
    spendFile: 'upload_spend',
    priceFile: 'upload_price',
    fbaStockFile: 'upload_fba_stock',
    flexStockFile: 'upload_flex_stock',
    fkSearchTrafficFile: 'fk_search_traffic',
    fkCategoryFile: 'fk_category',
    fkPriceFile: 'fk_price',
    fkPlaNewFile: 'fk_pla',
    fkFbaStockFile: 'fk_fba_stock',
    fkInventoryFile: 'fk_inventory',
};

// ---------------------------------------------------------------------------
// Utility Helpers
// ---------------------------------------------------------------------------
function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function generateUniqueId() {
    return `file_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function generateBatchId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID().replace(/-/g, '');
    }
    return `batch_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`;
}

// ---------------------------------------------------------------------------
// Clear All
// ---------------------------------------------------------------------------
function clearAllFiles() {
    AMAZON_IDS.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    AMAZON_LISTS.forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
    FK_IDS.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    FK_LISTS.forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
    stagedFiles.clear();
    activeUploads = 0;
    isProcessingBatch = false;
    setUploadControlsDisabled(false);
    updateProcessButton();
}

// ---------------------------------------------------------------------------
// Process Button State
// ---------------------------------------------------------------------------
function updateProcessButton() {
    const btn = document.getElementById('loadBtn');
    const badge = document.getElementById('stagedCountBadge');
    if (!btn) return;

    const stagedCount = stagedFiles.size;
    const isUploading = activeUploads > 0 || isProcessingBatch;

    // Disable during uploads OR if no staged files
    btn.disabled = isUploading || stagedCount === 0;

    if (badge) {
        if (stagedCount > 0) {
            badge.textContent = `${stagedCount} file${stagedCount > 1 ? 's' : ''} ready`;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }
}

function setUploadControlsDisabled(disabled) {
    Object.keys(INPUT_TO_LIST).forEach((inputId) => {
        const inputEl = document.getElementById(inputId);
        if (inputEl) inputEl.disabled = !!disabled;
    });
    document.querySelectorAll('input[name="platform"]').forEach((radio) => {
        radio.disabled = !!disabled;
    });
}

// ---------------------------------------------------------------------------
// Create File Item UI Element
// ---------------------------------------------------------------------------
function createFileItemElement(file, uniqueId) {
    const div = document.createElement('div');
    div.className = 'file-item';
    div.id = `file-item-${uniqueId}`;
    div.innerHTML = `
        <span class="material-icons-round file-icon">description</span>
        <div class="file-info">
            <div class="file-name">${file.name}</div>
            <div class="file-size">${formatFileSize(file.size)}</div>
            <div class="file-progress-bar">
                <div class="file-progress-fill" id="progress-${uniqueId}"></div>
            </div>
        </div>
        <span class="material-icons-round file-status-icon" id="status-icon-${uniqueId}" style="display:none;"></span>
        <button class="file-remove-btn" id="remove-${uniqueId}" title="Remove file" style="display:none;">
            <span class="material-icons-round">close</span>
        </button>
    `;
    return div;
}

function updateFileItemStatus(uniqueId, status, errorMsg) {
    const item = document.getElementById(`file-item-${uniqueId}`);
    const progressFill = document.getElementById(`progress-${uniqueId}`);
    const statusIcon = document.getElementById(`status-icon-${uniqueId}`);
    const removeBtn = document.getElementById(`remove-${uniqueId}`);
    if (!item) return;

    item.className = `file-item ${status}`;

    if (status === 'uploading') {
        if (statusIcon) { statusIcon.style.display = 'none'; }
        if (removeBtn) { removeBtn.style.display = 'none'; }
    } else if (status === 'staged') {
        if (progressFill) { progressFill.style.width = '100%'; }
        if (statusIcon) {
            statusIcon.textContent = 'check_circle';
            statusIcon.style.color = '#22c55e';
            statusIcon.style.display = 'inline';
        }
        if (removeBtn) {
            removeBtn.style.display = 'inline-flex';
            removeBtn.onclick = () => removeStagedFile(uniqueId);
        }
    } else if (status === 'error') {
        if (progressFill) {
            progressFill.style.width = '100%';
            progressFill.style.background = '#ef4444';
        }
        if (statusIcon) {
            statusIcon.textContent = 'error';
            statusIcon.style.color = '#ef4444';
            statusIcon.style.display = 'inline';
        }
        if (removeBtn) {
            removeBtn.style.display = 'inline-flex';
            removeBtn.onclick = () => {
                item.remove();
            };
        }
        // Show error tooltip
        const fileInfo = item.querySelector('.file-info');
        if (fileInfo && errorMsg) {
            const errEl = document.createElement('div');
            errEl.style.cssText = 'font-size:11px;color:#ef4444;margin-top:4px;';
            errEl.textContent = errorMsg;
            fileInfo.appendChild(errEl);
        }
    }
}

async function removeStagedFile(uniqueId) {
    const fileInfo = stagedFiles.get(uniqueId);
    stagedFiles.delete(uniqueId);
    const item = document.getElementById(`file-item-${uniqueId}`);
    if (item) item.remove();
    updateProcessButton();

    if (!fileInfo || !fileInfo.staged_path) return;
    try {
        const form = new FormData();
        form.append('staged_path', fileInfo.staged_path);
        await fetch('/api/upload/stage/delete/', {
            method: 'POST',
            body: form,
            headers: { 'X-CSRFToken': getCookie('csrftoken') },
            credentials: 'same-origin',
        });
    } catch (err) {
        console.warn('Failed to delete staged file from tmp:', err);
    }
}

// ---------------------------------------------------------------------------
// Phase 1: Upload file to tmp/ with XHR progress
// ---------------------------------------------------------------------------
function stageFileUpload(file, inputId) {
    const uniqueId = generateUniqueId();
    const listId = INPUT_TO_LIST[inputId];
    const fileType = INPUT_TO_FILE_TYPE[inputId];
    const listEl = document.getElementById(listId);
    if (!listEl) return;

    // Create the file item UI
    const fileItem = createFileItemElement(file, uniqueId);
    listEl.appendChild(fileItem);

    // Start uploading
    updateFileItemStatus(uniqueId, 'uploading');
    activeUploads++;
    updateProcessButton();

    const form = new FormData();
    form.append('file', file);
    form.append('file_type', fileType);

    // For sales files, extract date from filename
    if (fileType === 'sales') {
        const dateStr = file.name.replace(/\.(csv|xlsx|xls|xlsm)$/i, '').substring(0, 10);
        form.append('date', dateStr);
    }

    const xhr = new XMLHttpRequest();

    // Progress handler
    xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            const progressFill = document.getElementById(`progress-${uniqueId}`);
            if (progressFill) {
                progressFill.style.width = `${pct}%`;
            }
        }
    });

    // Load handler (upload complete, waiting for server response)
    xhr.addEventListener('load', () => {
        activeUploads--;
        try {
            const resp = JSON.parse(xhr.responseText);
            if (xhr.status >= 200 && xhr.status < 300 && resp.staged_path) {
                // Success — file staged in tmp/
                stagedFiles.set(uniqueId, {
                    staged_path: resp.staged_path,
                    file_type: fileType,
                    date_str: resp.date_str || '',
                    original_name: resp.original_name || file.name,
                    inputId: inputId,
                });
                updateFileItemStatus(uniqueId, 'staged');
            } else {
                updateFileItemStatus(uniqueId, 'error', resp.error || 'Upload failed');
            }
        } catch (e) {
            updateFileItemStatus(uniqueId, 'error', 'Server error');
        }
        updateProcessButton();
    });

    // Error handler
    xhr.addEventListener('error', () => {
        activeUploads--;
        updateFileItemStatus(uniqueId, 'error', 'Network error');
        updateProcessButton();
    });

    // Abort handler
    xhr.addEventListener('abort', () => {
        activeUploads--;
        updateFileItemStatus(uniqueId, 'error', 'Upload cancelled');
        updateProcessButton();
    });

    xhr.open('POST', '/api/upload/stage/');
    xhr.setRequestHeader('X-CSRFToken', getCookie('csrftoken'));
    xhr.withCredentials = true;
    xhr.send(form);
}

// ---------------------------------------------------------------------------
// File Input Change Handlers — trigger Phase 1 upload immediately
// ---------------------------------------------------------------------------
Object.entries(INPUT_TO_LIST).forEach(([inputId, listId]) => {
    const el = document.getElementById(inputId);
    if (!el) return;
    el.addEventListener('change', (e) => {
        const files = Array.from(e.target.files);
        files.forEach(file => {
            stageFileUpload(file, inputId);
        });
        // Reset the input so the same file can be re-selected
        e.target.value = '';
    });
});

// ---------------------------------------------------------------------------
// Phase 2: Process staged files (move from tmp/ to uploads/ + Celery)
// ---------------------------------------------------------------------------
async function processAndUpload() {
    const btn = document.getElementById('loadBtn');
    const status = document.getElementById('statusMsg');
    const platform = getSelectedPlatform();
    uploadFlowPlatform = platform;
    uploadFlowStarted = true;
    uploadFlowCompleted = false;

    if (stagedFiles.size === 0) {
        alert('No files staged for processing. Please upload files first.');
        return;
    }
    if (activeUploads > 0) {
        alert('Please wait until all files finish uploading to tmp.');
        return;
    }

    isProcessingBatch = true;
    setUploadControlsDisabled(true);
    updateProcessButton();
    status.textContent = '⏳ Processing files...';

    if (typeof window.setGlobalUploadProgress === "function") {
        window.setGlobalUploadProgress({
            status: "processing",
            message: "Files are being processed.",
            active: true,
        });
    }

    try {
        const totalFiles = stagedFiles.size;
        const batchId = generateBatchId();
        const payload = {
            batch_id: batchId,
            staged_files: Array.from(stagedFiles.values()).map((fileInfo) => ({
                staged_path: fileInfo.staged_path,
                file_type: fileInfo.file_type,
                original_name: fileInfo.original_name,
                date_str: fileInfo.date_str || '',
            })),
        };

        status.textContent = `⏳ Queuing ${totalFiles} file(s) for background processing...`;
        const resp = await fetch('/api/upload/process-batch/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
        });

        const respData = await resp.json();

        if (!resp.ok) {
            status.textContent = `❌ Processing failed: ${respData.error || 'Unknown error'}`;
            if (typeof window.setGlobalUploadProgress === "function") {
                window.setGlobalUploadProgress({
                    status: "error",
                    message: `Processing failed: ${respData.error || 'Unknown error'}`,
                    active: false,
                    visible_until: Date.now() + 12000,
                });
            }
            isProcessingBatch = false;
            setUploadControlsDisabled(false);
            updateProcessButton();
            return;
        }

        // All files sent to processing
        stagedFiles.clear();
        isProcessingBatch = false;
        setUploadControlsDisabled(false);
        updateProcessButton();
        if (respData.failed_count > 0) {
            status.textContent = `⚠️ ${respData.queued_count} queued, ${respData.failed_count} failed. Check Upload Log Notes.`;
        } else {
            status.textContent = "✅ All files queued. Dashboard update continues in background.";
        }

        if (typeof window.setGlobalUploadProgress === "function") {
            window.setGlobalUploadProgress({
                status: "processing",
                message: "All files uploaded. Processing and dashboard update continue in background.",
                active: true,
            });
        }

    } catch (err) {
        uploadFlowCompleted = true;
        isProcessingBatch = false;
        setUploadControlsDisabled(false);
        status.textContent = "❌ " + err.message;
        if (typeof window.setGlobalUploadProgress === "function") {
            window.setGlobalUploadProgress({
                status: "error",
                message: err.message || "Processing failed.",
                active: false,
                visible_until: Date.now() + 12000,
            });
        }
        updateProcessButton();
    }
}

// ---------------------------------------------------------------------------
// Demo Template Buttons
// ---------------------------------------------------------------------------
function initDemoTemplateButtons() {
    Object.entries(DEMO_TEMPLATE_MAP).forEach(([inputId, templateKey]) => {
        const input = document.getElementById(inputId);
        if (!input) return;
        const card = input.closest('.upload-card');
        if (!card || card.querySelector(`.demo-template-link[data-template="${templateKey}"]`)) return;

        const link = document.createElement('a');
        link.href = `/api/demo-template/?template=${encodeURIComponent(templateKey)}`;
        link.className = 'demo-template-link';
        link.setAttribute('data-template', templateKey);
        link.textContent = 'Download Demo File';
        link.style.cssText = 'display:inline-flex;align-items:center;gap:6px;margin:-8px 0 16px;padding:8px 12px;border-radius:8px;border:1px solid #dbeafe;background:#eff6ff;color:#1d4ed8;font-size:12px;font-weight:600;text-decoration:none;';

        const title = card.querySelector('.upload-card-title');
        if (title && title.nextSibling) {
            card.insertBefore(link, title.nextSibling);
        } else if (title) {
            title.insertAdjacentElement('afterend', link);
        } else {
            card.prepend(link);
        }
    });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
    initDemoTemplateButtons();
    updateProcessButton();

    if (typeof window.readUploadProgressState === "function") {
        syncInlineUploadStatusFromState(window.readUploadProgressState());
    }
    window.addEventListener('pltx-upload-progress', handleGlobalUploadProgressEvent);
});
