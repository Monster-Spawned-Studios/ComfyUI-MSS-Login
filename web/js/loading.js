(function () {
    "use strict";

    // Only run on the loading page. If ComfyUI loads this script on the main app, do nothing to avoid redirect loop.
    var pathname = typeof window !== "undefined" && window.location && window.location.pathname;
    if (pathname !== "/loading") {
        return;
    }

    // #region agent log
    try {
        fetch("http://127.0.0.1:7242/ingest/bdf8b85f-87b3-445e-aa3d-4ace3a22d3ae", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "9c3a72" }, body: JSON.stringify({ sessionId: "9c3a72", location: "loading.js:entry", message: "loading_script_run", data: { pathname: pathname }, hypothesisId: "A", timestamp: Date.now() }) }).catch(function () {});
    } catch (e) {}
    // #endregion

    // Same-origin tips (served by GET /mss-login/loading-tips.json; data dir or bundled)
    const TIPS_URL = "/mss-login/loading-tips.json";
    const TIP_INTERVAL_MS = 5000;
    const _metaTimeout = document.querySelector('meta[name="mss-loading-timeout-ms"]');
    const AUTO_REDIRECT_MS = _metaTimeout ? (parseInt(_metaTimeout.content, 10) || 15000) : 15000;

    const tipEl = document.getElementById("loading-tip");
    const bannerEl = document.getElementById("loading-update-banner");
    const continueBtn = document.getElementById("loading-continue-btn");

    let tips = [];
    let tipIndex = 0;
    let tipTimer = null;
    let autoRedirectTimer = null;

    function setTip(text) {
        if (tipEl) tipEl.textContent = text || "Preparing ComfyUI…";
    }

    function nextTip() {
        if (tips.length === 0) return;
        tipIndex = (tipIndex + 1) % tips.length;
        setTip(tips[tipIndex]);
    }

    function startTipRotation() {
        if (tipTimer) clearInterval(tipTimer);
        if (tips.length <= 1) return;
        tipTimer = setInterval(nextTip, TIP_INTERVAL_MS);
    }

    function fetchTips() {
        fetch(TIPS_URL, { method: "GET" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (Array.isArray(data)) {
                    tips = data.filter(function (s) { return typeof s === "string" && s.trim(); });
                } else if (data && Array.isArray(data.messages)) {
                    tips = data.messages.filter(function (s) { return typeof s === "string" && s.trim(); });
                }
                if (tips.length > 0) {
                    tipIndex = 0;
                    setTip(tips[0]);
                    startTipRotation();
                }
            })
            .catch(function () { /* keep default tip */ });
    }

    function escapeHtml(text) {
        if (!text) return "";
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    function showUpdateBanner(status) {
        if (!status || !status.update_available || !bannerEl) return;
        if (autoRedirectTimer) {
            clearTimeout(autoRedirectTimer);
            autoRedirectTimer = null;
        }
        var url = status.release_url || status.changelog_url || "https://github.com/Monster-Spawned-Studios/ComfyUI-MSS-Login/releases";
        var ver = status.latest_version ? " (" + status.latest_version + ")" : "";
        var html = "An update is available" + ver + ". <a href=\"" + url + "\" target=\"_blank\" rel=\"noopener noreferrer\">View release</a>";
        if (status.changelog_body && status.changelog_body.trim()) {
            html += " <button type=\"button\" class=\"loading-changelog-toggle\" id=\"loading-changelog-toggle\" aria-expanded=\"false\">Show changelog</button>";
            html += "<div id=\"loading-changelog-body\" class=\"loading-changelog-body\" style=\"display:none;\" role=\"region\" aria-label=\"Changelog\"></div>";
        }
        bannerEl.innerHTML = html;
        bannerEl.style.display = "block";
        if (status.changelog_body && status.changelog_body.trim()) {
            var toggle = document.getElementById("loading-changelog-toggle");
            var bodyEl = document.getElementById("loading-changelog-body");
            if (toggle && bodyEl) {
                bodyEl.innerHTML = "<pre style=\"white-space:pre-wrap;word-break:break-word;margin:0.5rem 0 0;font-size:0.85em;max-height:12rem;overflow:auto;\">" + escapeHtml(status.changelog_body) + "</pre>";
                toggle.addEventListener("click", function () {
                    var open = bodyEl.style.display !== "none";
                    bodyEl.style.display = open ? "none" : "block";
                    toggle.setAttribute("aria-expanded", open ? "false" : "true");
                    toggle.textContent = open ? "Show changelog" : "Hide changelog";
                });
            }
        }
    }

    function checkAdminUpdateBanner() {
        fetch("/mss-login/api/me", { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (me) {
                if (me && me.is_admin) {
                    return fetch("/mss-login/api/update-status", { credentials: "same-origin" })
                        .then(function (r) { return r.ok ? r.json() : null; })
                        .then(showUpdateBanner);
                }
            })
            .catch(function () {});
    }

    function goToApp() {
        // #region agent log
        try {
            fetch("http://127.0.0.1:7242/ingest/bdf8b85f-87b3-445e-aa3d-4ace3a22d3ae", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "9c3a72" }, body: JSON.stringify({ sessionId: "9c3a72", location: "loading.js:goToApp", message: "goToApp_called", data: { pathname: pathname }, hypothesisId: "A", timestamp: Date.now() }) }).catch(function () {});
        } catch (e) {}
        // #endregion
        if (autoRedirectTimer) {
            clearTimeout(autoRedirectTimer);
            autoRedirectTimer = null;
        }
        window.location.href = "/";
    }

    if (continueBtn) continueBtn.addEventListener("click", goToApp);

    fetchTips();
    checkAdminUpdateBanner();

    // Auto-redirect to main ComfyUI after a short display time; Continue button still works for immediate navigation
    autoRedirectTimer = setTimeout(goToApp, AUTO_REDIRECT_MS);
})();
