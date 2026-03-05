(function () {
    "use strict";

    const TIPS_URL = "https://monsterspawned.studio/data/mss-login/loading.json";
    const TIP_INTERVAL_MS = 10000;

    const tipEl = document.getElementById("loading-tip");
    const bannerEl = document.getElementById("loading-update-banner");
    const continueBtn = document.getElementById("loading-continue-btn");

    let tips = [];
    let tipIndex = 0;
    let tipTimer = null;

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
        window.location.href = "/";
    }

    if (continueBtn) continueBtn.addEventListener("click", goToApp);

    fetchTips();
    checkAdminUpdateBanner();
})();
