/* ══════════════════════════════════════════════════════════════
   Plantex Sales Dashboard — Filter Visibility & UI Helpers
   Handles show/hide logic for platform-dependent filter controls.
   ══════════════════════════════════════════════════════════════ */

/**
 * syncFilterVisibility()
 * Called on page load and whenever the Platform dropdown changes.
 * - Platform = "Flipkart" → show FSN filter, hide ASIN filter.
 * - Platform = "Amazon"   → show ASIN filter, hide FSN filter.
 * - Platform = "All" (empty) → show BOTH filters (Amazon ASIN + Flipkart FSN).
 */
function syncFilterVisibility() {
    var platformEl = document.getElementById('platformSelect');
    var platform = platformEl ? platformEl.value : '';

    var asinGroup = document.getElementById('asinSelect')
        ? document.getElementById('asinSelect').closest('.topbar-filter-group')
        : null;
    var fsnGroup = document.getElementById('fsnFilterGroup');

    if (platform === 'Flipkart') {
        // Only Flipkart: hide ASIN, show FSN
        if (asinGroup) asinGroup.style.display = 'none';
        if (fsnGroup)  fsnGroup.style.display  = '';
    } else if (platform === 'Amazon') {
        // Only Amazon: show ASIN, hide FSN
        if (asinGroup) asinGroup.style.display = '';
        if (fsnGroup)  fsnGroup.style.display  = 'none';
    } else {
        // "All" — show BOTH so user can filter by either platform's product IDs
        if (asinGroup) asinGroup.style.display = '';
        if (fsnGroup)  fsnGroup.style.display  = '';
    }
}
