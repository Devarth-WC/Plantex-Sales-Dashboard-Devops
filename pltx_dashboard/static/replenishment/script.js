document.addEventListener('DOMContentLoaded', () => {
    // -- CSRF Token Helper --
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    const csrftoken = getCookie('csrftoken');

    const generatorForm = document.getElementById('generatorForm');
    const validateBtn = document.getElementById('validateBtn');
    const valSpinner = document.getElementById('valSpinner');
    const btnLabel = document.getElementById('btnLabel');

    // Status and UI state
    const statusBadge = document.getElementById('statusBadge');
    const statusText = document.getElementById('statusText');
    const idleState = document.getElementById('idleState');
    const resultState = document.getElementById('resultState');
    const errorCountBadge = document.getElementById('errorCountBadge');
    const outputStatusMessage = document.getElementById('outputStatusMessage');

    const reportsContainer = document.getElementById('reportsContainer');

    // Toast
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMessage');

    // Master Generator
    const generateBtn = document.getElementById('generateBtn');
    const genSpinner = document.getElementById('genSpinner');
    const genBtnLabel = document.getElementById('genBtnLabel');
    const masterActions = document.getElementById('masterActions');
    const dlMasterCsv = document.getElementById('dlMasterCsv');
    const dlMasterExcel = document.getElementById('dlMasterExcel');

    // Track if master report is ready for download
    let masterReportReady = false;

    function setStatus(state, label) {
        statusBadge.className = `pfa-status-badge ${state}`;
        statusText.textContent = label;
    }

    function showToast(message, type = 'info') {
        toastMsg.textContent = message;
        toast.className = `pfa-toast ${type}`;
        clearTimeout(toast._t);
        toast._t = setTimeout(() => toast.classList.add('hidden'), 5500);
    }

    function getFormData() {
        const formData = new FormData();
        const files = {
            Sales: document.getElementById('fileSales').files[0],
            Stock: document.getElementById('fileStock').files[0],
            LIS: document.getElementById('fileLIS').files[0],
            Shipment: document.getElementById('fileShipment').files[0],
            Assortment: document.getElementById('fileAssortment').files[0],
            FC_Cluster: document.getElementById('fileFCCluster').files[0],
            Pincode_Cluster: document.getElementById('filePincode').files[0],
            Input_Sheet: document.getElementById('fileInputSheet').files[0],
            Business_Report: document.getElementById('fileBusiness').files[0],
            Flex_Qty: document.getElementById('fileFlexQty').files[0] || null
        };

        for (const [key, file] of Object.entries(files)) {
            if (file) formData.append(key, file);
        }
        return formData;
    }

    const inputIds = [
        'fileSales', 'fileStock', 'fileLIS', 'fileShipment', 
        'fileAssortment', 'fileFCCluster', 'filePincode', 'fileInputSheet', 'fileBusiness', 'fileFlexQty'
    ];
    const demoTemplateMap = {
        fileSales: 'repl_sales',
        fileStock: 'repl_stock',
        fileLIS: 'repl_lis',
        fileShipment: 'repl_shipment',
        fileAssortment: 'repl_assortment',
        fileFCCluster: 'repl_fc_cluster',
        filePincode: 'repl_pincode_cluster',
        fileInputSheet: 'repl_input_sheet',
        fileBusiness: 'repl_business_report',
        fileFlexQty: 'repl_flex_qty',
    };

    function initDemoTemplateLinks() {
        Object.entries(demoTemplateMap).forEach(([inputId, templateKey]) => {
            const input = document.getElementById(inputId);
            if (!input) return;
            const field = input.closest('.field');
            if (!field || field.querySelector(`.demo-template-link[data-template="${templateKey}"]`)) return;

            const link = document.createElement('a');
            link.href = `/api/demo-template/?template=${encodeURIComponent(templateKey)}`;
            link.className = 'demo-template-link';
            link.setAttribute('data-template', templateKey);
            link.textContent = 'Download Demo File';
            link.style.cssText = 'display:inline-flex;align-items:center;gap:6px;margin:0 0 8px;padding:6px 10px;border-radius:8px;border:1px solid #dbeafe;background:#eff6ff;color:#1d4ed8;font-size:12px;font-weight:600;text-decoration:none;';

            const label = field.querySelector('.field-label');
            if (label) {
                label.insertAdjacentElement('afterend', link);
            } else {
                field.prepend(link);
            }
        });
    }
    initDemoTemplateLinks();

    // --- Custom File Upload UI Handling ---
    function updateFileUploadUI(input) {
        const wrapper = input.closest('.file-upload-wrapper');
        const display = wrapper.querySelector('.file-upload-display');
        const statusText = wrapper.querySelector('.file-status-text');
        
        if (input.files && input.files.length > 0) {
            const fileName = input.files[0].name;
            statusText.textContent = `Selected: ${fileName}`;
            display.classList.add('has-file');
        } else {
            const originalText = 'Click to upload CSV or Excel file';
            statusText.textContent = originalText;
            display.classList.remove('has-file');
        }
    }

    inputIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', () => updateFileUploadUI(el));
        }
    });

    // Removed saveInputs/loadInputs as browser security prevents restoring file inputs.
    
    // Polling helper (max 10 minutes, with gradual back-off)
    async function pollStatus(taskId) {
        return new Promise((resolve, reject) => {
            let attempts = 0;
            const MAX_ATTEMPTS = 240; // 240 × 2.5s ≈ 10 minutes hard limit
            let delay = 2500;

            const check = async () => {
                if (attempts >= MAX_ATTEMPTS) {
                    return reject(new Error('Task timed out after 10 minutes. It may still be processing — please check back later.'));
                }
                attempts++;
                try {
                    const res = await fetch(`/api/replenishment/status/${taskId}/`);
                    if (res.status === 404) {
                        return reject(new Error('Task expired or failed to start.'));
                    }
                    if (!res.ok) {
                        return reject(new Error(`Server error: HTTP ${res.status}`));
                    }

                    const contentType = res.headers.get("content-type");
                    if (contentType && contentType.indexOf("application/json") !== -1) {
                        const data = await res.json();
                        if (data.status === 'success') {
                            resolve(data);
                        } else if (data.status === 'error') {
                            reject(new Error(data.message || 'Error occurred during processing.'));
                        } else {
                            // Still processing — back off gradually (cap at 5s)
                            delay = Math.min(delay * 1.05, 5000);
                            setTimeout(check, delay);
                        }
                    } else {
                        const text = await res.text();
                        console.error("Non-JSON status response:", text);
                        reject(new Error('Server returned an invalid status response.'));
                    }
                } catch (err) {
                    reject(err);
                }
            };
            setTimeout(check, delay);
        });
    }

    function handleValidationSuccess(data) {
        localStorage.removeItem('replenishment_task');
        const totalErrors = data.total_errors || 0;

        if (totalErrors === 0) {
            errorCountBadge.textContent = '✓  No Errors Found';
            errorCountBadge.className = 'result-pill success';
            outputStatusMessage.innerHTML = `All reports passed validation checks successfully. You can proceed to generate the Master Report.`;
            setStatus('success', 'Passed');
            showToast('Validation complete — no errors.', 'success');
        } else {
            errorCountBadge.textContent = `${totalErrors} Total Error${totalErrors > 1 ? 's' : ''}`;
            errorCountBadge.className = 'result-pill error';
            outputStatusMessage.innerHTML = `Validation completed with errors. See the breakdown below.`;
            setStatus('error', 'Failed');
            showToast(`${totalErrors} error(s) found across reports.`, 'error');
        }

        // Build independent reports UI
        reportsContainer.innerHTML = '';
        if (data.reports) {
            reportsContainer.classList.remove('hidden');

            Object.keys(data.reports).forEach(rType => {
                const rData = data.reports[rType];
                const hasErrors = rData.error_count > 0;

                const row = document.createElement('div');
                row.style.cssText = "display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--border); padding: 12px; border-radius: var(--radius-md); background: var(--surface);";

                let buttonsHtml = '';
                if (hasErrors) {
                    buttonsHtml = `
                        <div style="display: flex; gap: 8px;">
                            <button type="button" class="btn-action dl-csv" data-report-type="${rType}" data-format="csv">CSV</button>
                            <button type="button" class="btn-action dl-excel" data-report-type="${rType}" data-format="excel">Excel</button>
                        </div>
                    `;
                }

                row.innerHTML = `
                    <div>
                        <div style="font-weight: 600; font-size: 0.9rem; color: var(--text-1);">${rType} Validation</div>
                        <div style="font-size: 0.78rem; font-weight: 600; color: ${hasErrors ? 'var(--red)' : 'var(--green)'};">${rData.error_count} Error${rData.error_count !== 1 ? 's' : ''}</div>
                    </div>
                    ${buttonsHtml}
                `;

                reportsContainer.appendChild(row);
            });

            // Bind dynamic download buttons for validation errors
            reportsContainer.querySelectorAll('.dl-csv, .dl-excel').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const reportType = e.target.getAttribute('data-report-type');
                    const format = e.target.getAttribute('data-format');
                    if (reportType && format) {
                        handleValidationDownload(reportType, format);
                    }
                });
            });
        }

        idleState.classList.add('hidden');
        resultState.classList.remove('hidden');

        validateBtn.disabled = false;
        btnLabel.textContent = 'Run Validation';
        valSpinner.classList.add('hidden');
    }

    function handleValidationCatch(err) {
        localStorage.removeItem('replenishment_task');
        console.error(err);
        setStatus('error', 'Error');
        showToast(err.message, 'error');
        validateBtn.disabled = false;
        btnLabel.textContent = 'Run Validation';
        valSpinner.classList.add('hidden');
    }

    function handleGenerationSuccess(data) {
        localStorage.removeItem('replenishment_task');
        masterReportReady = true;
        showToast('Master Report generated successfully! Click download buttons to save files.', 'success');
        setStatus('success', 'Generated');

        idleState.classList.add('hidden');
        resultState.classList.remove('hidden');
        masterActions.classList.remove('hidden');

        generateBtn.disabled = false;
        genBtnLabel.textContent = 'Generate Master';
        genSpinner.classList.add('hidden');
    }

    function handleGenerationCatch(err) {
        localStorage.removeItem('replenishment_task');
        console.error(err);
        setStatus('error', 'Error Generation');
        showToast(err.message, 'error');
        generateBtn.disabled = false;
        genBtnLabel.textContent = 'Generate Master';
        genSpinner.classList.add('hidden');
    }

    // ── Run Validation ──
    generatorForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        // Loading
        validateBtn.disabled = true;
        btnLabel.textContent = 'Validating…';
        valSpinner.classList.remove('hidden');
        setStatus('running', 'Running Validation…');

        reportsContainer.classList.add('hidden');
        reportsContainer.innerHTML = '';
        masterActions.classList.add('hidden');

        try {
            const response = await fetch('/api/replenishment/validate/', {
                method: 'POST',
                headers: csrftoken ? { 'X-CSRFToken': csrftoken } : {},
                body: getFormData()
            });

            const contentType = response.headers.get("content-type");
            if (contentType && contentType.indexOf("application/json") !== -1) {
                const initialData = await response.json();
                if (!response.ok) throw new Error(initialData.error || 'Server error occurred.');
                if (!initialData.task_id) throw new Error('Task failed to start.');

                localStorage.setItem('replenishment_task', JSON.stringify({ type: 'validation', id: initialData.task_id }));

                // Poll for completion
                const data = await pollStatus(initialData.task_id);
                handleValidationSuccess(data);
            } else {
                const text = await response.text();
                console.error("Non-JSON validation response:", text);
                throw new Error('Server error: Received invalid response format.');
            }
        } catch (err) {
            handleValidationCatch(err);
        }
    });

    // ── Generate Master ──
    generateBtn.addEventListener('click', async () => {
        generateBtn.disabled = true;
        genBtnLabel.textContent = 'Generating…';
        genSpinner.classList.remove('hidden');
        setStatus('running', 'Generating Master…');
        masterActions.classList.add('hidden');
        masterReportReady = false;

        try {
            const response = await fetch('/api/replenishment/generate_master/', {
                method: 'POST',
                headers: csrftoken ? { 'X-CSRFToken': csrftoken } : {},
                body: getFormData()
            });

            const contentType = response.headers.get("content-type");
            if (contentType && contentType.indexOf("application/json") !== -1) {
                const initialData = await response.json();
                if (!response.ok) throw new Error(initialData.error || 'Failed to start master generation.');
                if (!initialData.task_id) throw new Error('Task failed to start.');

                localStorage.setItem('replenishment_task', JSON.stringify({ type: 'generation', id: initialData.task_id }));

                // Poll for completion
                const data = await pollStatus(initialData.task_id);
                handleGenerationSuccess(data);
            } else {
                const text = await response.text();
                console.error("Non-JSON generation response (HTTP " + response.status + "):", text.substring(0, 1000));
                throw new Error('Server error (HTTP ' + response.status + '): The server returned an invalid response. Please check server logs.');
            }
        } catch (err) {
            handleGenerationCatch(err);
        }
    });

    // Handle Active Background Tasks on Page Reload
    const activeTaskRaw = localStorage.getItem('replenishment_task');
    if (activeTaskRaw) {
        try {
            const activeTask = JSON.parse(activeTaskRaw);
            if (activeTask.type === 'validation') {
                validateBtn.disabled = true;
                btnLabel.textContent = 'Resume…';
                valSpinner.classList.remove('hidden');
                setStatus('running', 'Resuming Validation…');
                reportsContainer.classList.add('hidden');
                masterActions.classList.add('hidden');
                pollStatus(activeTask.id).then(handleValidationSuccess).catch(handleValidationCatch);
            } else if (activeTask.type === 'generation') {
                // To display 'Generate' button we need the result state panel to be open
                idleState.classList.add('hidden');
                resultState.classList.remove('hidden');

                generateBtn.disabled = true;
                genBtnLabel.textContent = 'Resume…';
                genSpinner.classList.remove('hidden');
                setStatus('running', 'Resuming Generation…');
                masterActions.classList.add('hidden');
                pollStatus(activeTask.id).then(handleGenerationSuccess).catch(handleGenerationCatch);
            }
        } catch (e) {
            localStorage.removeItem('replenishment_task');
        }
    }

    // ── Downloads ──
    const handleValidationDownload = (reportType, format) => {
        if (!reportType || !format) return;
        window.location.href = `/api/replenishment/download/validation/${reportType}/${format}/`;
    };

    const handleMasterDownload = (format) => {
        if (!masterReportReady || !format) {
            showToast('Master report not ready for download', 'error');
            return;
        }
        window.location.href = `/api/replenishment/download/master/${format}/`;
    };

    dlMasterCsv.addEventListener('click', () => handleMasterDownload('csv'));
    dlMasterExcel.addEventListener('click', () => handleMasterDownload('excel'));
});
