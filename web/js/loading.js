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

    function showUpdateBanner(status) {
        if (!status || !status.update_available || !bannerEl) return;
        var url = status.release_url || status.changelog_url || "https://github.com/Monster-Spawned-Studios/ComfyUI-MSS-Login/releases";
        var ver = status.latest_version ? " (" + status.latest_version + ")" : "";
        bannerEl.innerHTML = "An update is available" + ver + ". <a href=\"" + url + "\" target=\"_blank\" rel=\"noopener noreferrer\">View release</a>";
        bannerEl.style.display = "block";
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
