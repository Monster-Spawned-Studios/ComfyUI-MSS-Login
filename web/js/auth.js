let failedAttempts = 0;
let timeoutEndTime = null;
let mfaTempToken = null;

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
        body: formData,
      });

      const result = await response.json();

      if (response.ok) {
        // MFA required: redirect to dedicated MFA page
        if (result.mfa_required && result.mfa_temp_token) {
          sessionStorage.setItem("mfa_temp_token", result.mfa_temp_token);
          sessionStorage.setItem("mfa_mode", "verify");
          button.disabled = false;
          button.textContent = "Login";
          window.location.href = "/mfa";
          return;
        }
        // MFA setup required: redirect to dedicated MFA page
        if (result.mfa_setup_required && result.mfa_temp_token) {
          sessionStorage.setItem("mfa_temp_token", result.mfa_temp_token);
          sessionStorage.setItem("mfa_mode", "setup");
          button.disabled = false;
          button.textContent = "Login";
          window.location.href = "/mfa";
          return;
        }
        // Normal login: backend returned { message, token } (and optionally jwt_token)
        const token = result.token || result.jwt_token;
        if (!token) {
          addToast("Login succeeded but no token was returned", "error");
        } else {
          let cookieString = `jwt_token=${token}; path=/; HttpOnly; SameSite=Strict`;

          if (window.location.protocol === "https:") {
            cookieString += "; Secure";
          }

          document.cookie = cookieString;
        }

        addToast(result.message || "Login successful", "success");
        window.location.href = "/";
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

  if (isTimedOut()) {
    return;
  }

  const form = document.getElementById("login-form");
  const guestFlag = document.getElementById("guest_login_flag");
  const loginButton = form.querySelector("button[type='submit']");
  const guestButton = event.target;

  // ensure flag exists / set to true
  if (guestFlag) {
    guestFlag.value = "true";
  }

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
      body: formData,
    });

    const result = await response.json();

    if (response.ok) {
      const token = result.token || result.jwt_token;
      if (token) {
        let cookieString = `jwt_token=${token}; path=/; HttpOnly; SameSite=Strict`;
        if (window.location.protocol === "https:") {
          cookieString += "; Secure";
        }
        document.cookie = cookieString;
      }

      addToast(result.message || "Guest login successful", "success");
      window.location.href = "/";
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
    if (guestFlag) guestFlag.value = "false";
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
  const button = document.querySelector("#mfa-verify-form button[type='submit']");
  button.disabled = true;
  button.textContent = "Verifying...";
  try {
    const response = await fetch("/mss-login/api/mfa/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (response.ok && result.jwt_token) {
      let cookieString = `jwt_token=${result.jwt_token}; path=/; HttpOnly; SameSite=Strict`;
      if (window.location.protocol === "https:") cookieString += "; Secure";
      document.cookie = cookieString;
      addToast(result.message || "Login successful", "success");
      window.location.href = "/";
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
      body: JSON.stringify({ mfa_temp_token: mfaTempToken, code }),
    });
    const verifyData = await verifyResp.json();
    if (verifyResp.ok && verifyData.jwt_token) {
      let cookieString = `jwt_token=${verifyData.jwt_token}; path=/; HttpOnly; SameSite=Strict`;
      if (window.location.protocol === "https:") cookieString += "; Secure";
      document.cookie = cookieString;
      addToast(verifyData.message || "MFA enabled. Login successful.", "success");
      window.location.href = "/";
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

loadTimeoutFromStorage(window.location.pathname.replace("/", "").split("_")[0])
