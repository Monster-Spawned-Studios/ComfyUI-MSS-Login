(function () {
    "use strict";

    // Only run on the loading page. If ComfyUI loads this script on the main app, do nothing to avoid redirect loop.
    var pathname = typeof window !== "undefined" && window.location && window.location.pathname;
    if (pathname !== "/loading") {
        return;
    }

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

    function applyUpdateNow(btn) {
        if (!btn) return;
        btn.disabled = true;
        btn.textContent = "Updating…";
        fetch("/mss-login/api/update-apply", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: "{}",
        })
            .then(function (r) {
                return r.json().then(function (j) {
                    return { ok: r.ok, j: j };
                });
            })
            .then(function (res) {
                if (res.ok && res.j && res.j.success) {
                    btn.textContent = "Restart required";
                    if (tipEl) {
                        setTip(res.j.message || "Update applied. Restart ComfyUI to finish.");
                    }
                } else {
                    btn.disabled = false;
                    btn.textContent = "Update now";
                    if (tipEl) {
                        setTip((res.j && (res.j.error || res.j.message)) || "Update failed");
                    }
                }
            })
            .catch(function (e) {
                btn.disabled = false;
                btn.textContent = "Update now";
                if (tipEl) setTip(e.message || "Update failed");
            });
    }

    function showUpdateBanner(status, canUpdate) {
        if (!status || !status.update_available || !bannerEl) return;
        // Do not cancel the auto-redirect: the banner is informational unless Update now is used.
        // The user can still click Continue or wait for the normal timeout.
        var fallbackUrl = "https://github.com/Monster-Spawned-Studios/ComfyUI-MSS-Login/releases";
        var rawUrl = status.release_url || status.changelog_url || fallbackUrl;
        var url = /^https:\/\//i.test(String(rawUrl)) ? String(rawUrl) : fallbackUrl;
        var ver = status.latest_version ? " (" + escapeHtml(String(status.latest_version)) + ")" : "";
        bannerEl.textContent = "";
        if (canUpdate) {
            bannerEl.appendChild(
                document.createTextNode("An update is available" + ver + ". ")
            );
        } else {
            bannerEl.appendChild(
                document.createTextNode(
                    "An update is available" +
                        ver +
                        ". Please notify your administrator to update MSS-Login. "
                )
            );
        }
        var link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "View release";
        bannerEl.appendChild(link);
        if (canUpdate) {
            var applyBtn = document.createElement("button");
            applyBtn.type = "button";
            applyBtn.className = "loading-update-apply";
            applyBtn.textContent = "Update now";
            applyBtn.addEventListener("click", function () {
                applyUpdateNow(applyBtn);
            });
            bannerEl.appendChild(applyBtn);
        }
        if (status.changelog_body && status.changelog_body.trim()) {
            bannerEl.appendChild(document.createTextNode(" "));
            var toggle = document.createElement("button");
            toggle.type = "button";
            toggle.className = "loading-changelog-toggle";
            toggle.id = "loading-changelog-toggle";
            toggle.setAttribute("aria-expanded", "false");
            toggle.textContent = "Show changelog";
            bannerEl.appendChild(toggle);
            var bodyEl = document.createElement("div");
            bodyEl.id = "loading-changelog-body";
            bodyEl.className = "loading-changelog-body";
            bodyEl.style.display = "none";
            bodyEl.setAttribute("role", "region");
            bodyEl.setAttribute("aria-label", "Changelog");
            var pre = document.createElement("pre");
            pre.style.cssText = "white-space:pre-wrap;word-break:break-word;margin:0.5rem 0 0;font-size:0.85em;max-height:12rem;overflow:auto;";
            pre.textContent = status.changelog_body;
            bodyEl.appendChild(pre);
            bannerEl.appendChild(bodyEl);
            toggle.addEventListener("click", function () {
                var open = bodyEl.style.display !== "none";
                bodyEl.style.display = open ? "none" : "block";
                toggle.setAttribute("aria-expanded", open ? "false" : "true");
                toggle.textContent = open ? "Show changelog" : "Hide changelog";
            });
        }
        bannerEl.style.display = "block";
    }

    function checkUpdateBanner() {
        // MSS /loading interstitial only (pathname guard above). Does not touch ComfyUI splash at /.
        fetch("/mss-login/api/me", { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (me) {
                var canUpdate = !!(me && me.can_update_mss_login);
                var statusUrl =
                    me && me.is_admin
                        ? "/mss-login/api/update-status"
                        : "/mss-login/api/update-notice";
                return fetch(statusUrl, { credentials: "same-origin" })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (status) {
                        showUpdateBanner(status, canUpdate);
                    });
            })
            .catch(function () {
                fetch("/mss-login/api/update-notice", { credentials: "same-origin" })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (status) {
                        showUpdateBanner(status, false);
                    })
                    .catch(function () {});
            });
    }

    function goToApp() {
        if (autoRedirectTimer) {
            clearTimeout(autoRedirectTimer);
            autoRedirectTimer = null;
        }
        window.location.href = "/";
    }

    if (continueBtn) continueBtn.addEventListener("click", goToApp);

    fetchTips();
    checkUpdateBanner();

    // Auto-redirect to main ComfyUI after a short display time; Continue button still works for immediate navigation
    autoRedirectTimer = setTimeout(goToApp, AUTO_REDIRECT_MS);
})();
