let failedAttempts = 0;
let timeoutEndTime = null;
let mfaTempToken = null;

/** DOMPurify for sanitizing the authentication forms (loaded by the HTML page when used standalone). */
Object.defineProperty(String.prototype, 'capitalize', {
  value: function() {
    return this.charAt(0).toUpperCase() + this.slice(1);
  },
  enumerable: false
});

if (window.location.pathname === "/register") {
  document.addEventListener("DOMContentLoaded", () => {
    const adminFields = document.getElementById("admin-fields");
    const registerLink = document.getElementById("register-link");
    const verticalDivider = document.getElementById("vertical-divider");
    const isAdminUser = document.body.dataset.adminUser === "true";

    adminFields.style.display = isAdminUser ? "none" : "block";
    registerLink.style.display = isAdminUser ? "none" : "block";
    verticalDivider.style.display = isAdminUser ? "none" : "block";
  });
}

if (window.location.pathname === "/login") {
  document.addEventListener("DOMContentLoaded", () => {
    function applyLoginBackground(data) {
      const root = document.getElementById("mss-login-bg");
      const img = document.getElementById("mss-login-bg-image");
      const video = document.getElementById("mss-login-bg-video");
      if (!root || !img || !video) return;
      if (!data || !data.enabled || !data.src) {
        root.hidden = true;
        img.hidden = true;
        video.hidden = true;
        img.removeAttribute("src");
        video.removeAttribute("src");
        video.pause();
        return;
      }
      const kind = data.media_kind === "video" ? "video" : "image";
      root.hidden = false;
      if (kind === "video") {
        img.hidden = true;
        img.removeAttribute("src");
        video.hidden = false;
        video.src = data.src;
        video.play().catch(function () {});
      } else {
        video.hidden = true;
        video.removeAttribute("src");
        video.pause();
        img.hidden = false;
        img.src = data.src;
      }
    }

    try {
      const cfgEl = document.getElementById("mss-login-bg-config");
      if (cfgEl && cfgEl.textContent) {
        applyLoginBackground(JSON.parse(cfgEl.textContent));
      }
    } catch (_) {}

    fetch("/mss-login/api/login-background", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) applyLoginBackground(data); })
      .catch(function () {});

    // Update notice (public); session-aware Update now when can_update_mss_login
    (function initLoginUpdateNotice() {
      const noticeEl = document.getElementById("login-update-notice");
      if (!noticeEl) return;
      const DISMISS_KEY = "mss_login_update_notice_dismissed";
      const fallbackUrl = "https://github.com/Monster-Spawned-Studios/ComfyUI-MSS-Login/releases";

      function httpsUrl(raw) {
        return /^https:\/\//i.test(String(raw || "")) ? String(raw) : fallbackUrl;
      }

      function applyUpdate(btn) {
        if (!btn) return;
        btn.disabled = true;
        btn.textContent = "Updating…";
        fetch("/mss-login/api/update-apply", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (res) {
            if (res.ok && res.j && res.j.success) {
              if (typeof addToast === "function") {
                addToast(res.j.message || "Update applied. Restart ComfyUI to finish.", "success");
              }
              btn.textContent = "Restart required";
            } else {
              if (typeof addToast === "function") {
                addToast((res.j && (res.j.error || res.j.message)) || "Update failed", "error");
              }
              btn.disabled = false;
              btn.textContent = "Update now";
            }
          })
          .catch(function (e) {
            if (typeof addToast === "function") {
              addToast(e.message || "Update failed", "error");
            }
            btn.disabled = false;
            btn.textContent = "Update now";
          });
      }

      function renderNotice(status, canUpdate) {
        if (!status || !status.update_available) return;
        const latest = status.latest_version ? String(status.latest_version) : "";
        if (latest && sessionStorage.getItem(DISMISS_KEY) === latest) return;

        noticeEl.textContent = "";
        const header = document.createElement("div");
        header.className = "login-update-notice-header";
        const title = document.createElement("span");
        title.textContent = latest
          ? "MSS-Login update available (v" + latest + ")"
          : "MSS-Login update available";
        const dismiss = document.createElement("button");
        dismiss.type = "button";
        dismiss.className = "login-update-dismiss";
        dismiss.setAttribute("aria-label", "Dismiss update notice");
        dismiss.textContent = "×";
        dismiss.addEventListener("click", function () {
          if (latest) sessionStorage.setItem(DISMISS_KEY, latest);
          noticeEl.style.display = "none";
        });
        header.appendChild(title);
        header.appendChild(dismiss);

        const body = document.createElement("p");
        body.className = "login-update-notice-body";
        body.textContent = canUpdate
          ? "A newer version is available. You can apply the update now; restart ComfyUI afterward."
          : "A newer version is available. Please notify your administrator so they can update MSS-Login.";

        const actions = document.createElement("div");
        actions.className = "login-update-notice-actions";
        const link = document.createElement("a");
        link.href = httpsUrl(status.release_url);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "View release";
        actions.appendChild(link);

        if (canUpdate) {
          const applyBtn = document.createElement("button");
          applyBtn.type = "button";
          applyBtn.className = "login-update-apply";
          applyBtn.textContent = "Update now";
          applyBtn.addEventListener("click", function () { applyUpdate(applyBtn); });
          actions.appendChild(applyBtn);
        }

        noticeEl.appendChild(header);
        noticeEl.appendChild(body);
        noticeEl.appendChild(actions);
        noticeEl.style.display = "block";
      }

      fetch("/mss-login/api/update-notice", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (status) {
          if (!status || !status.update_available) return null;
          return fetch("/mss-login/api/me", { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (me) {
              renderNotice(status, !!(me && me.can_update_mss_login));
            })
            .catch(function () { renderNotice(status, false); });
        })
        .catch(function () {});
    })();

    const section = document.getElementById("login-news-section");
    const feedEl = document.getElementById("login-news-feed");
    if (section && feedEl) {
    fetch("/mss-login/api/news/feed.xml", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) return null;
        return r.text();
      })
      .then(function (xmlText) {
        if (!xmlText) return;
        const parser = new DOMParser();
        const doc = parser.parseFromString(xmlText, "text/xml");
        const items = doc.querySelectorAll("channel > item");
        if (!items.length) return;
        feedEl.innerHTML = "";
        for (let i = 0; i < items.length; i++) {
          const item = items[i];
          const title = (item.querySelector("title") && item.querySelector("title").textContent) || "";
          const desc = (item.querySelector("description") && item.querySelector("description").textContent) || "";
          const pubDate = (item.querySelector("pubDate") && item.querySelector("pubDate").textContent) || "";
          const div = document.createElement("div");
          div.className = "login-news-item";
          const strong = document.createElement("strong");
          strong.textContent = title;
          const timeEl = document.createElement("time");
          timeEl.textContent = pubDate ? " " + pubDate : "";
          div.appendChild(strong);
          div.appendChild(timeEl);
          if (desc) {
            const p = document.createElement("p");
            p.style.margin = "0.25rem 0 0 0";
            p.textContent = desc;
            div.appendChild(p);
          }
          feedEl.appendChild(div);
        }
        section.style.display = "block";
      })
      .catch(function () {});
    }

    // Check Tailscale / Local Network authentication status (experimental)
    const localSection = document.getElementById("local-login-section");
    const localLabel = document.getElementById("local-net-label");
    const localIp = document.getElementById("local-client-ip");
    const localActions = document.getElementById("local-login-actions");

    if (localSection && localActions) {
      fetch("/mss-login/api/auth/local-login-status", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || !data.enabled || !data.is_local) return;

          localSection.style.display = "block";
          if (localIp) localIp.textContent = data.client_ip ? "(" + data.client_ip + ")" : "";
          if (localLabel) {
            localLabel.textContent = data.network_type === "tailscale"
              ? "Tailscale Network Detected"
              : "Local Network Detected";
          }

          localActions.innerHTML = "";
          const eligible = data.eligible_users || [];
          if (eligible.length > 0) {
            eligible.forEach(function (username) {
              const btn = document.createElement("button");
              btn.type = "button";
              btn.className = "local-login-quick-btn";
              btn.textContent = "Quick Login: " + username;
              btn.onclick = async function () {
                btn.disabled = true;
                btn.textContent = "Logging in...";
                try {
                  const res = await fetch("/mss-login/api/auth/local-login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    credentials: "same-origin",
                    body: JSON.stringify({ username: username })
                  });
                  const json = await res.json();
                  if (res.ok && json.redirect_url) {
                    window.location.href = json.redirect_url;
                  } else {
                    addToast(json.error || "Local login failed", "error");
                    btn.disabled = false;
                    btn.textContent = "Quick Login: " + username;
                  }
                } catch (e) {
                  addToast(e.message || "Network error", "error");
                  btn.disabled = false;
                  btn.textContent = "Quick Login: " + username;
                }
              };
              localActions.appendChild(btn);
            });
          }
          // When there are no quick-login users, keep #local-login-body as the
          // single copy source (do not duplicate the trusted-network sentence).
        })
        .catch(function () {});
    }
  });
}

// Clear token display on load so refresh/navigation removes it entirely (generate token page only)
document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("token-display-container");
  if (container) {
    container.style.display = "none";
    const val = document.getElementById("token-display-value");
    if (val) val.textContent = "";
  }
});

function showTokenOnPage(token) {
  const container = document.getElementById("token-display-container");
  const val = document.getElementById("token-display-value");
  const copyBtn = document.getElementById("token-copy-btn");
  if (!container || !val) return;
  val.textContent = token;
  container.style.display = "block";
  if (copyBtn) {
    copyBtn.onclick = () => {
      navigator.clipboard.writeText(token).then(() => addToast("Copied to clipboard", "success")).catch(() => addToast("Copy failed", "error"));
    };
  }
}

function addToast(message, type) {
  const toasts = document.getElementById("toasts");
  const toast = document.createElement("div");

  toast.classList.add("toast", "hide", type);
  toast.textContent = message;

  toasts.appendChild(toast);

  setTimeout(() => {
    toast.classList.replace("hide", "show");
  }, 500);

  setTimeout(() => {
    toast.classList.replace("show", "hide");
  }, 4500);

  setTimeout(() => {
    toast.remove();
  }, 5500);
}

function validateRegisterForm() {
  const usernameField = document.getElementById("new_user_username");
  const passwordField = document.getElementById("new_user_password");
  const newUsername = usernameField.value;
  const newPassword = passwordField.value;

  usernameField.classList.remove("error");
  passwordField.classList.remove("error");

  if (/[^a-zA-Z0-9_]/.test(newUsername) || /\s/.test(newUsername)) {
    addToast(
      "Username can only contain letters, numbers, and underscores",
      "error"
    );
    usernameField.classList.add("error");
    return false;
  }

  if (!newUsername.trim() || newUsername.trim().length < 3) {
    addToast("Username must be at least 3 characters", "error");
    usernameField.classList.add("error");
    return false;
  }

  if (!newPassword.trim() || /\s/.test(newPassword)) {
    addToast("Password cannot contain spaces", "error");
    passwordField.classList.add("error");
    return false;
  }

  if (
    newPassword.trim().length < 8 ||
    !/\d/.test(newPassword) ||
    !/[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>?/`~]/.test(newPassword)
  ) {
    addToast(
      "Password must be at least 8 characters, include a number, and a special character",
      "error"
    );
    passwordField.classList.add("error");
    return false;
  }

  return true;
}

function validateGenerateForm() {
  const usernameField = document.getElementById("username");
  const passwordField = document.getElementById("password");
  const expireField = document.getElementById("expire_hours");
  const expire_hours = expireField.value;

  usernameField.classList.remove("error");
  passwordField.classList.remove("error");
  expireField.classList.remove("error");

  if (/[^0-9]/.test(expire_hours) || /\s/.test(expire_hours)) {
    addToast(
      "Expiration can only contain numbers",
      "error"
    );
    expireField.classList.add("error");
    return false;
  }

  return true;
}

function disableForm(duration, action) {
  const form = document.getElementById(`${action}-form`);
  const button = form.querySelector("button[type='submit']");
  button.disabled = true;

  // const fields = form.querySelectorAll("input, button");
  // fields.forEach((field) => (field.disabled = true));

  let remainingTime = duration;

  if (remainingTime > 0) {
    const countdownInterval = setInterval(() => {
      const minutes = Math.floor(remainingTime / 60);
      const seconds = remainingTime % 60;
      const remainingTimeMessage =
        remainingTime > 60 ? `${minutes}min ${seconds}s` : `${remainingTime}s`;

      button.textContent = `Wait ${remainingTimeMessage}`;
      remainingTime--;

      if (remainingTime <= 0) {
        clearInterval(countdownInterval);
        button.disabled = false;
        button.textContent = action.capitalize();
        // fields.forEach((field) => (field.disabled = false));
      }
    }, 1000);
  } else {
    button.disabled = false;
    button.textContent = action.capitalize();
  }
}

function loadTimeoutFromStorage(action) {
  const savedFailedAttempts =
    parseInt(localStorage.getItem("failedAttempts"), 10) || 0;
  const savedLockoutEndTime =
    parseInt(localStorage.getItem("timeoutEndTime"), 10) || null;

  failedAttempts = savedFailedAttempts;
  timeoutEndTime = savedLockoutEndTime;

  const currentTime = Date.now();
  const remainingTime = Math.round(
    timeoutEndTime ? Math.max(0, (timeoutEndTime - currentTime) / 1000) : 0
  );

  if (remainingTime !== 0) {
    disableForm(remainingTime, action);
  }
}

function setTimeoutFromServer(
  serverFailedAttempts,
  serverRemainingSeconds,
  action
) {
  const currentTime = Date.now();

  localStorage.setItem("failedAttempts", serverFailedAttempts);
  localStorage.setItem(
    "timeoutEndTime",
    currentTime + serverRemainingSeconds * 1000
  );

  loadTimeoutFromStorage(action);
}

function updateFailedAttempts(responseStatus, result, action) {
  if (![200, 400, 401, 403].includes(responseStatus)) {
    return;
  }

  if (result.failed_attempts && result.remaining_seconds) {
    setTimeoutFromServer(
      result.failed_attempts,
      result.remaining_seconds,
      action
    );
    return;
  }

  if (responseStatus === 200) {
    localStorage.removeItem("failedAttempts");
    localStorage.removeItem("timeoutEndTime");
    failedAttempts = 0;
    timeoutEndTime = null;
  }

  if (![200, 400].includes(responseStatus)) {
    failedAttempts++;
  }

  localStorage.setItem("failedAttempts", failedAttempts);

  let timeoutDuration = 0;
  if (failedAttempts >= 9) {
    timeoutDuration = 300;
  } else if (failedAttempts >= 6) {
    timeoutDuration = 90;
  } else if (failedAttempts >= 3) {
    timeoutDuration = 60;
  }

  const currentTime = Date.now();
  timeoutEndTime = currentTime + timeoutDuration * 1000;
  localStorage.setItem("timeoutEndTime", timeoutEndTime);

  disableForm(timeoutDuration, action);
}

function isTimedOut() {
  const currentTime = Date.now();
  if (timeoutEndTime && currentTime < timeoutEndTime) {
    const remainingTimeInSeconds = Math.round(
      (timeoutEndTime - currentTime) / 1000
    );
    const minutes = Math.floor(remainingTimeInSeconds / 60);
    const seconds = remainingTimeInSeconds % 60;
    const remainingTimeMessage =
      remainingTimeInSeconds > 60
        ? `${minutes} minute${minutes > 1 ? "s" : ""} and ${seconds} second${
            seconds > 1 ? "s" : ""
          }`
        : `${remainingTimeInSeconds} second${
            remainingTimeInSeconds > 1 ? "s" : ""
          }`;

    addToast(
      `Too many failed attempts. Please wait ${remainingTimeMessage}`,
      "error"
    );
    return true;
  }
  return false;
}

async function login(event) {
  event.preventDefault();

  if (!isTimedOut()) {
    const button = event.submitter || document.querySelector("#login-form button[type='submit']");
    const form = document.getElementById("login-form");
    const formData = new FormData(form);
    const usernameField = document.getElementById("username");
    const passwordField = document.getElementById("password");

    try {
      usernameField.classList.remove("error");
      passwordField.classList.remove("error");
      button.disabled = true;
      button.textContent = "Sending...";

      const response = await fetch("/login", {
        method: "POST",
        credentials: "same-origin",
        body: formData,
      });

      const result = await response.json();

      if (response.ok) {
        // MFA required: redirect to dedicated MFA page
        if (result.mfa_required && result.mfa_temp_token) {
          sessionStorage.setItem("mfa_temp_token", result.mfa_temp_token);
          sessionStorage.setItem("mfa_mode", "verify");
          if (result.remember_me) {
            sessionStorage.setItem("mss_login_remember_me", "1");
          } else {
            sessionStorage.removeItem("mss_login_remember_me");
          }
          button.disabled = false;
          button.textContent = "Login";
          window.location.href = "/mfa";
          return;
        }
        // MFA setup required: redirect to dedicated MFA page
        if (result.mfa_setup_required && result.mfa_temp_token) {
          sessionStorage.setItem("mfa_temp_token", result.mfa_temp_token);
          sessionStorage.setItem("mfa_mode", "setup");
          if (result.remember_me) {
            sessionStorage.setItem("mss_login_remember_me", "1");
          } else {
            sessionStorage.removeItem("mss_login_remember_me");
          }
          button.disabled = false;
          button.textContent = "Login";
          window.location.href = "/mfa";
          return;
        }
        // Normal login: HttpOnly cookie is set by the server Set-Cookie header.
        // Do not write jwt_token from JS (cannot set HttpOnly; would duplicate
        // a non-HttpOnly cookie). Long-lived API tokens are never stored here.
        sessionStorage.removeItem("mss_login_remember_me");

        addToast(result.message || "Login successful", "success");
        // Browser-only redirect: use redirect_url from server when loading screen is enabled, else /.
        window.location.href = result.redirect_url || "/";
      } else {
        usernameField.classList.add("error");
        passwordField.classList.add("error");
        addToast(result.error || result.message || "Login failed", "error");
      }
      updateFailedAttempts(response.status, result, "login");
    } catch (error) {
      addToast("An error occurred: " + error.message, "error");
      button.disabled = false;
      button.textContent = "Login";
    }
  }
}

async function guestLogin(event) {
  event.preventDefault();

  // Guest UI omitted from HTML when ALLOW_GUEST_JWT is false.
  const guestFlag = document.getElementById("guest_login_flag");
  const guestButton = document.getElementById("guest-login-btn");
  if (!guestFlag || !guestButton) {
    return;
  }

  if (isTimedOut()) {
    return;
  }

  const form = document.getElementById("login-form");
  if (!form) {
    return;
  }
  const loginButton = form.querySelector("button[type='submit']");

  // ensure flag exists / set to true
  guestFlag.value = "true";

  // clear username/password; backend ignores them on guest path anyway
  const usernameField = document.getElementById("username");
  const passwordField = document.getElementById("password");
  if (usernameField) usernameField.value = "";
  if (passwordField) passwordField.value = "";

  try {
    guestButton.disabled = true;
    guestButton.textContent = "Signing in as guest...";
    if (loginButton) loginButton.disabled = true;

    const formData = new FormData(form);

    const response = await fetch("/login", {
      method: "POST",
      credentials: "same-origin",
      body: formData,
    });

    const result = await response.json();

    if (response.ok) {
      // Server sets HttpOnly session cookie; do not mirror jwt_token in JS.
      addToast(result.message || "Guest login successful", "success");
      window.location.href = result.redirect_url || "/";
    } else {
      addToast(result.error || result.message || "Guest login failed", "error");
    }

    updateFailedAttempts(response.status, result, "login");
  } catch (error) {
    addToast("An error occurred: " + error.message, "error");
  } finally {
    guestButton.disabled = false;
    guestButton.textContent = "Guest Login";
    if (loginButton) {
      loginButton.disabled = false;
      loginButton.textContent = "Login";
    }
    // reset flag so normal login stays normal
    guestFlag.value = "false";
  }
}

async function register(event) {
  event.preventDefault();

  if (validateRegisterForm() && !isTimedOut()) {
    const button = event.submitter;
    const form = document.getElementById("register-form");
    const formData = new FormData(form);

    try {
      button.disabled = true;
      button.textContent = "Sending...";

      const response = await fetch("/register", {
        method: "POST",
        body: formData,
      });

      const result = await response.json();

      if (response.ok) {
        addToast(result.message, "success");
        updateFailedAttempts(response.status, result, "register");

        const isAdminUser = document.body.dataset.adminUser === "true";
        if (isAdminUser) {
          window.location.href = "/login";
        }

        form.reset();
      } else {
        addToast(
          result.error || result.message || "Registration failed",
          "error"
        );
      }
      updateFailedAttempts(response.status, result, "register");
    } catch (error) {
      addToast("An error occurred: " + error.message, "error");
      button.disabled = false;
      button.textContent = "Register";
    }
  }
}

async function generate(event) {
  event.preventDefault();

  if (validateGenerateForm() && !isTimedOut()) {
    const button = event.submitter;
    const form = document.getElementById("generate-form");
    const formData = new FormData(form);

    try {
      button.disabled = true;
      button.textContent = "Sending...";

      const response = await fetch("/mss-login/generate_token", {
        method: "POST",
        body: formData,
      });

      const result = await response.json();

      if (response.ok) {
        if (result.mfa_required && result.mfa_temp_token) {
          mfaTempToken = result.mfa_temp_token;
          document.getElementById("mfa-expire-hours").value = result.expire_hours || 720;
          const mfaLabelEl = document.getElementById("mfa-label");
          if (mfaLabelEl) mfaLabelEl.value = (document.getElementById("label") || {}).value || "";
          document.getElementById("generate-form").style.display = "none";
          const mfaSection = document.getElementById("mfa-verify-section");
          if (mfaSection) mfaSection.style.display = "block";
          addToast(result.message || "Enter your verification code", "success");
          document.getElementById("mfa-code").focus();
        } else if (result.jwt_token) {
          addToast(result.message, "success");
          updateFailedAttempts(response.status, result, "generate");
          form.reset();
          showTokenOnPage(result.jwt_token);
          loadMyTokens();
        } else {
          addToast(result.message || "Token created", "success");
        }
        button.textContent = "Generate";
        button.disabled = false;
      } else {
        addToast(
          result.error || result.message || "Generation failed",
          "error"
        );
        button.textContent = "Generate";
        button.disabled = false;
      }
      updateFailedAttempts(response.status, result, "generate");
    } catch (error) {
      addToast("An error occurred: " + error.message, "error");
      button.disabled = false;
      button.textContent = "Generate";
    }
  }
}

async function generateMfaVerify(event) {
  event.preventDefault();
  const code = (document.getElementById("mfa-code").value || "").replace(/\s/g, "");
  const backupCode = (document.getElementById("mfa-backup").value || "").replace(/\s/g, "").replace(/-/g, "").toUpperCase();
  if (!mfaTempToken) {
    addToast("Session expired. Please try again.", "error");
    backToGenerateForm();
    return;
  }
  if (!backupCode && !code) {
    addToast("Enter verification code or backup code", "error");
    return;
  }
  const formData = new FormData();
  formData.append("mfa_temp_token", mfaTempToken);
  formData.append("expire_hours", document.getElementById("mfa-expire-hours").value || "720");
  const mfaLabelEl = document.getElementById("mfa-label");
  if (mfaLabelEl && mfaLabelEl.value) formData.append("label", mfaLabelEl.value);
  if (backupCode) formData.append("backup_code", backupCode);
  else formData.append("code", code);
  const button = document.querySelector("#mfa-verify-form button[type='submit']");
  button.disabled = true;
  button.textContent = "Verifying...";
  try {
    const response = await fetch("/mss-login/generate_token", {
      method: "POST",
      body: formData,
    });
    const result = await response.json();
    if (response.ok && result.jwt_token) {
      addToast(result.message, "success");
      backToGenerateForm();
      document.getElementById("generate-form").reset();
      showTokenOnPage(result.jwt_token);
      loadMyTokens();
    } else {
      addToast(result.error || "Invalid code", "error");
    }
  } catch (err) {
    addToast("Error: " + err.message, "error");
  }
  button.disabled = false;
  button.textContent = "Verify and Generate";
}

function backToGenerateForm() {
  const form = document.getElementById("generate-form");
  const mfaSection = document.getElementById("mfa-verify-section");
  if (form) form.style.display = "block";
  if (mfaSection) mfaSection.style.display = "none";
  mfaTempToken = null;
  const mfaCode = document.getElementById("mfa-code");
  const mfaBackup = document.getElementById("mfa-backup");
  if (mfaCode) mfaCode.value = "";
  if (mfaBackup) mfaBackup.value = "";
}

function backToLogin() {
  document.getElementById("login-form").style.display = "block";
  document.getElementById("mfa-verify-section").style.display = "none";
  document.getElementById("mfa-setup-section").style.display = "none";
  mfaTempToken = null;
  document.getElementById("mfa-code").value = "";
  document.getElementById("mfa-backup").value = "";
  document.getElementById("mfa-setup-code").value = "";
}

async function submitMfaVerify(event) {
  event.preventDefault();
  const code = (document.getElementById("mfa-code").value || "").replace(/\s/g, "");
  const backupCode = (document.getElementById("mfa-backup").value || "").replace(/\s/g, "").replace(/-/g, "").toUpperCase();
  if (!mfaTempToken) {
    addToast("Session expired. Please log in again.", "error");
    backToLogin();
    return;
  }
  const body = { mfa_temp_token: mfaTempToken };
  if (backupCode) body.backup_code = backupCode;
  else if (code) body.code = code;
  else {
    addToast("Enter verification code or backup code", "error");
    return;
  }
  if (sessionStorage.getItem("mss_login_remember_me") === "1") {
    body.remember_me = true;
  }
  const button = document.querySelector("#mfa-verify-form button[type='submit']");
  button.disabled = true;
  button.textContent = "Verifying...";
  try {
    const response = await fetch("/mss-login/api/mfa/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (response.ok) {
      // Cookie is set by the server; do not duplicate jwt_token from JS.
      sessionStorage.removeItem("mss_login_remember_me");
      sessionStorage.removeItem("mfa_temp_token");
      sessionStorage.removeItem("mfa_mode");
      addToast(result.message || "Login successful", "success");
      window.location.href = result.redirect_url || "/";
    } else {
      addToast(result.error || "Invalid code", "error");
      button.disabled = false;
      button.textContent = "Verify";
    }
  } catch (err) {
    addToast("Error: " + err.message, "error");
    button.disabled = false;
    button.textContent = "Verify";
  }
}

async function submitMfaSetup(event) {
  event.preventDefault();
  const code = (document.getElementById("mfa-setup-code").value || "").replace(/\s/g, "");
  if (!code || code.length !== 6) {
    addToast("Enter a 6-digit code from your authenticator app", "error");
    return;
  }
  if (!mfaTempToken) {
    addToast("Session expired. Please log in again.", "error");
    backToLogin();
    return;
  }
  const button = document.querySelector("#mfa-setup-form button[type='submit']");
  button.disabled = true;
  button.textContent = "Verifying...";
  try {
    const verifySetupResp = await fetch("/mss-login/api/mfa/verify-setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mfa_temp_token: mfaTempToken, code }),
    });
    const verifySetupData = await verifySetupResp.json();
    if (!verifySetupResp.ok) {
      addToast(verifySetupData.error || "Invalid code", "error");
      button.disabled = false;
      button.textContent = "Complete Setup";
      return;
    }
    const verifyResp = await fetch("/mss-login/api/mfa/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        mfa_temp_token: mfaTempToken,
        code,
        remember_me: sessionStorage.getItem("mss_login_remember_me") === "1",
      }),
    });
    const verifyData = await verifyResp.json();
    if (verifyResp.ok) {
      sessionStorage.removeItem("mss_login_remember_me");
      sessionStorage.removeItem("mfa_temp_token");
      sessionStorage.removeItem("mfa_mode");
      addToast(verifyData.message || "MFA enabled. Login successful.", "success");
      window.location.href = verifyData.redirect_url || "/";
    } else {
      addToast(verifyData.error || "Verification failed", "error");
      button.disabled = false;
      button.textContent = "Complete Setup";
    }
  } catch (err) {
    addToast("Error: " + err.message, "error");
    button.disabled = false;
    button.textContent = "Complete Setup";
  }
}

// ---------------------------------------------------------------------------
// Token management (generate_token page only)
// ---------------------------------------------------------------------------

async function loadMyTokens() {
  const container = document.getElementById("my-tokens-list");
  if (!container) return;
  try {
    const response = await fetch("/mss-login/api/tokens", { credentials: "same-origin" });
    if (!response.ok) {
      container.textContent = "";
      const p = document.createElement("p");
      p.style.color = "#888";
      p.textContent = "Log in to view your tokens.";
      container.appendChild(p);
      return;
    }
    const data = await response.json();
    const tokens = data.tokens || [];
    if (tokens.length === 0) {
      container.textContent = "";
      const p = document.createElement("p");
      p.style.color = "#888";
      p.textContent = "No API tokens found.";
      container.appendChild(p);
      return;
    }
    container.textContent = "";
    const table = document.createElement("table");
    table.style.cssText = "width:100%; border-collapse:collapse; font-size:0.9rem;";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.style.borderBottom = "1px solid #444";
    ["Label", "Hash Prefix", "Created", "Last used", "Expires", "Revoke"].forEach((label, i) => {
      const th = document.createElement("th");
      th.style.cssText = i === 5
        ? "text-align:center; padding:6px;"
        : "text-align:left; padding:6px;";
      th.textContent = label;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (const t of tokens) {
      const created = t.created_at_iso ? new Date(t.created_at_iso).toLocaleString() : "N/A";
      const lastUsed = t.last_used_at_iso ? new Date(t.last_used_at_iso).toLocaleString() : "Never";
      const neverExpires = t.expires_iso === "9999-12-31T23:59:59+00:00";
      const expires = neverExpires ? "Never" : (t.expires_iso ? new Date(t.expires_iso).toLocaleString() : "N/A");
      const prefix = String(t.token_hash_prefix || "");
      const tr = document.createElement("tr");
      tr.style.borderBottom = "1px solid #333";
      const cells = [
        t.label || "Unlabeled",
        prefix,
        created,
        lastUsed,
        expires,
      ];
      cells.forEach((text, i) => {
        const td = document.createElement("td");
        td.style.padding = "6px";
        if (i === 1) {
          td.style.fontFamily = "monospace";
          td.style.fontSize = "0.8rem";
        }
        if (i === 0 && !t.label) {
          td.style.color = "#666";
        }
        td.textContent = text;
        tr.appendChild(td);
      });
      const revokeTd = document.createElement("td");
      revokeTd.style.cssText = "padding:6px; text-align:center;";
      const revokeBtn = document.createElement("button");
      revokeBtn.className = "btn";
      revokeBtn.style.cssText = "padding:2px 10px; font-size:0.8rem;";
      revokeBtn.textContent = "Revoke";
      revokeBtn.addEventListener("click", () => revokeToken(prefix));
      revokeTd.appendChild(revokeBtn);
      tr.appendChild(revokeTd);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    container.appendChild(table);
  } catch {
    container.textContent = "";
    const p = document.createElement("p");
    p.style.color = "#888";
    p.textContent = "Could not load tokens.";
    container.appendChild(p);
  }
}

async function revokeToken(hashPrefix) {
  if (!confirm("Revoke this API token? This cannot be undone.")) return;
  const prefix = hashPrefix.replace(/\.+$/, "");
  try {
    const response = await fetch("/mss-login/api/tokens", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token_hash_prefix: prefix }),
      credentials: "same-origin",
    });
    const result = await response.json();
    if (response.ok) {
      addToast(result.message || "Token revoked", "success");
    } else {
      addToast(result.error || "Failed to revoke token", "error");
    }
  } catch (err) {
    addToast("Error: " + err.message, "error");
  }
  loadMyTokens();
}

// Load token list on the generate_token page
document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("my-tokens-list")) {
    loadMyTokens();
  }
});

loadTimeoutFromStorage(window.location.pathname.replace("/", "").split("_")[0])
