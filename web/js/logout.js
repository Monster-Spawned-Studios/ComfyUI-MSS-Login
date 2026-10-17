import { app } from "/scripts/app.js";

const AVATAR_URL = "/mss-login/api/me/avatar";
const LOGOUT_URL = "/logout";
const DEFAULT_ICON = "pi pi-user";
const PROFILE_CSS = `
.mss-login-profile-btn { position: relative; }
.mss-login-avatar-img { width: 22px; height: 22px; border-radius: 999px; object-fit: cover; display: block; }
.mss-login-profile-backdrop {
  position: fixed; inset: 0; z-index: 12040;
  background: rgba(0, 0, 0, 0.45);
  display: flex; align-items: center; justify-content: center;
  padding: 16px;
}
.mss-login-profile-menu {
  position: relative; z-index: 12050;
  width: min(320px, 92vw); min-width: 220px;
  background: rgba(18,20,28,0.98); color: #f5f5f7;
  border: 1px solid rgba(255,255,255,0.16); border-radius: 14px;
  box-shadow: 0 16px 48px rgba(0,0,0,0.55);
  padding: 12px 14px 16px;
  display: flex; flex-direction: column; align-items: stretch; gap: 4px;
  text-align: center;
}
.mss-login-profile-header {
  display: flex; align-items: center; justify-content: center;
  position: relative; padding: 4px 36px 10px; gap: 8px;
}
.mss-login-profile-name {
  flex: 1; font-size: 13px; font-weight: 700; letter-spacing: 0.04em;
  text-transform: uppercase; opacity: 0.9; text-align: center;
}
.mss-login-profile-close {
  position: absolute; right: 0; top: 0;
  width: 32px; height: 32px; border: none; border-radius: 8px;
  background: transparent; color: #f5f5f7; cursor: pointer;
  font-size: 18px; line-height: 1; display: flex; align-items: center; justify-content: center;
}
.mss-login-profile-close:hover { background: rgba(255,255,255,0.1); }
.mss-login-profile-item {
  display: block; width: 100%; text-align: center;
  background: transparent; border: none; color: #f5f5f7;
  padding: 10px 12px; border-radius: 8px; cursor: pointer; font-size: 14px;
}
.mss-login-profile-item:hover { background: rgba(255,255,255,0.08); }
.mss-login-profile-logout { color: #fca5a5; }
`;

let _profileDismissCleanup = null;

function ensureProfileStyles() {
  if (document.getElementById("mss-login-profile-css")) return;
  const style = document.createElement("style");
  style.id = "mss-login-profile-css";
  style.textContent = PROFILE_CSS;
  document.head.appendChild(style);
}

/** Browser UI session keys only — never wipe manually generated API tokens. */
const BROWSER_SESSION_STORAGE_KEYS = [
  "jwt_token",
  "mss-login-jwt",
  "mss_login_jwt",
  "mfa_temp_token",
  "mfa_mode",
  "mss_login_remember_me",
];

/** Cookie names for the interactive ComfyUI login session (not API tokens). */
const BROWSER_SESSION_COOKIE_NAMES = new Set([
  "jwt_token",
  "mss-login-jwt",
  "mss_login_jwt",
]);

function clearClientAuthState() {
  // Clear tab-scoped login/MFA state only. Do not remove arbitrary localStorage
  // keys — users may keep long-lived API JWTs there for other apps.
  try {
    for (const key of BROWSER_SESSION_STORAGE_KEYS) {
      sessionStorage.removeItem(key);
    }
  } catch (_) {}
  try {
    // Only the known browser-session aliases; leave other localStorage alone.
    for (const key of ["jwt_token", "mss-login-jwt", "mss_login_jwt", "mss_login_remember_me"]) {
      localStorage.removeItem(key);
    }
  } catch (_) {}
  try {
    const paths = ["/", "/mss-login", "/login", "/logout", "/mfa"];
    document.cookie.split(";").forEach((cookie) => {
      const name = cookie.split("=")[0].trim();
      if (!name || !BROWSER_SESSION_COOKIE_NAMES.has(name)) return;
      for (const path of paths) {
        document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=${path}; samesite=strict`;
      }
    });
  } catch (_) {}
}

async function logoutAction() {
  try {
    clearClientAuthState();
    await fetch(LOGOUT_URL, { method: "POST", credentials: "same-origin" });
  } catch (error) {
    console.error("[mss-login] Logout request failed:", error);
  }
  // Go straight to login; a second GET /logout is unnecessary after POST revoke.
  window.location.href = "/login";
}

function closeProfileMenu() {
  if (typeof _profileDismissCleanup === "function") {
    _profileDismissCleanup();
    _profileDismissCleanup = null;
  }
  const backdrop = document.getElementById("mss-login-profile-backdrop");
  if (backdrop) backdrop.remove();
  const menu = document.getElementById("mss-login-profile-menu");
  if (menu) menu.remove();
}

function isGuestUser(me) {
  const name = (me?.username || "").trim().toLowerCase();
  return !name || name === "guest" || me?.role === "guest";
}

async function fetchMe() {
  try {
    const res = await fetch("/mss-login/api/me", { credentials: "same-origin" });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

function applyAvatarToButton(btn) {
  if (!btn) return;
  fetch(AVATAR_URL, { credentials: "same-origin" })
    .then((res) => {
      if (!res.ok) throw new Error("no avatar");
      return res.blob();
    })
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      let img = btn.querySelector("img.mss-login-avatar-img");
      if (!img) {
        img = document.createElement("img");
        img.className = "mss-login-avatar-img";
        img.alt = "";
        const icon = btn.querySelector("i");
        if (icon) icon.style.display = "none";
        btn.prepend(img);
      }
      img.src = url;
    })
    .catch(() => {
      const img = btn.querySelector("img.mss-login-avatar-img");
      if (img) img.remove();
      const icon = btn.querySelector("i");
      if (icon) icon.style.display = "";
    });
}

function tryOpenMssLoginDialog() {
  const tryOpen = () => {
    if (
      window._mss_loginDialogInstance &&
      window._mss_loginDialogInstance.overlay &&
      document.body.contains(window._mss_loginDialogInstance.overlay)
    ) {
      window._mss_loginDialogInstance.overlay.style.zIndex = "999999";
      return true;
    }
    if (window.mss_loginDialog && typeof window.mss_loginDialog === "function") {
      try {
        const dialog = new window.mss_loginDialog();
        dialog.show().catch((err) => {
          console.error("[mss-login] Error in dialog.show():", err);
        });
        return true;
      } catch (err) {
        console.error("[mss-login] Error creating dialog:", err);
        return false;
      }
    }
    return false;
  };
  if (tryOpen()) return;
  let retries = 0;
  const maxRetries = 20;
  const retryInterval = setInterval(() => {
    retries += 1;
    if (tryOpen() || retries >= maxRetries) {
      clearInterval(retryInterval);
    }
  }, 100);
}

function openProfileMenu(anchor, me) {
  closeProfileMenu();
  const guest = isGuestUser(me);

  const backdrop = document.createElement("div");
  backdrop.id = "mss-login-profile-backdrop";
  backdrop.className = "mss-login-profile-backdrop";
  backdrop.setAttribute("aria-hidden", "false");

  const menu = document.createElement("div");
  menu.id = "mss-login-profile-menu";
  menu.className = "mss-login-profile-menu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "Account menu");

  const header = document.createElement("div");
  header.className = "mss-login-profile-header";

  const nameRow = document.createElement("div");
  nameRow.className = "mss-login-profile-name";
  nameRow.textContent = me?.username || "Account";
  header.appendChild(nameRow);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "mss-login-profile-close";
  closeBtn.setAttribute("aria-label", "Close account menu");
  closeBtn.textContent = "✕";
  closeBtn.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    closeProfileMenu();
  });
  header.appendChild(closeBtn);
  menu.appendChild(header);

  if (!guest) {
    const changeBtn = document.createElement("button");
    changeBtn.type = "button";
    changeBtn.className = "mss-login-profile-item";
    changeBtn.textContent = "Change avatar";
    changeBtn.setAttribute("role", "menuitem");
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "image/png,image/jpeg,image/webp";
    fileInput.hidden = true;
    changeBtn.appendChild(fileInput);
    changeBtn.addEventListener("click", (ev) => {
      if (ev.target === fileInput) return;
      fileInput.click();
    });
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = "";
      if (!file) return;
      if (file.size > 2 * 1024 * 1024) {
        if (app.extensionManager?.toast?.add) {
          app.extensionManager.toast.add({
            severity: "error",
            summary: "Avatar too large",
            detail: "Maximum size is 2 MB.",
            life: 4000,
          });
        }
        return;
      }
      const body = new FormData();
      body.append("avatar", file, file.name);
      try {
        const res = await fetch("/mss-login/api/me/avatar", {
          method: "POST",
          credentials: "same-origin",
          body,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data.error || `HTTP ${res.status}`);
        }
        const btn = document.querySelector("[data-mss-login-profile='1']");
        applyAvatarToButton(btn);
        if (app.extensionManager?.toast?.add) {
          app.extensionManager.toast.add({
            severity: "success",
            summary: "Avatar updated",
            life: 2500,
          });
        }
      } catch (err) {
        if (app.extensionManager?.toast?.add) {
          app.extensionManager.toast.add({
            severity: "error",
            summary: "Avatar rejected",
            detail: err.message || "Could not use that image.",
            life: 5000,
          });
        }
      }
      closeProfileMenu();
    });
    menu.appendChild(changeBtn);
  }

  if (me?.is_admin && !guest) {
    const settingsBtn = document.createElement("button");
    settingsBtn.type = "button";
    settingsBtn.className = "mss-login-profile-item";
    settingsBtn.textContent = "MSS-Login Settings";
    settingsBtn.setAttribute("role", "menuitem");
    settingsBtn.addEventListener("click", () => {
      closeProfileMenu();
      tryOpenMssLoginDialog();
    });
    menu.appendChild(settingsBtn);
  }

  const isOwner =
    Array.isArray(me?.groups) &&
    me.groups.map((g) => String(g).toLowerCase()).includes("owner");
  if (isOwner && !guest) {
    const registerBtn = document.createElement("button");
    registerBtn.type = "button";
    registerBtn.className = "mss-login-profile-item";
    registerBtn.textContent = "Register user";
    registerBtn.setAttribute("role", "menuitem");
    registerBtn.addEventListener("click", () => {
      closeProfileMenu();
      window._mss_loginPreferUsersTab = true;
      tryOpenMssLoginDialog();
    });
    menu.appendChild(registerBtn);
  }

  // Extension radial-menu items (Gallery, etc.) — skip duplicates of settings/logout
  try {
    const extButtons =
      window.mss_loginRadialMenu && typeof window.mss_loginRadialMenu.getAll === "function"
        ? window.mss_loginRadialMenu.getAll()
        : [];
    const skipIds = new Set(["settings", "logout"]);
    for (const ext of extButtons) {
      if (!ext || skipIds.has(ext.id)) continue;
      const extBtn = document.createElement("button");
      extBtn.type = "button";
      extBtn.className = "mss-login-profile-item";
      extBtn.textContent = ext.label || ext.id;
      extBtn.setAttribute("role", "menuitem");
      extBtn.addEventListener("click", () => {
        closeProfileMenu();
        try {
          if (typeof ext.onClick === "function") ext.onClick();
        } catch (err) {
          console.error("[mss-login] Extension menu action failed:", err);
        }
      });
      menu.appendChild(extBtn);
    }
  } catch (_) {}

  const logoutBtn = document.createElement("button");
  logoutBtn.type = "button";
  logoutBtn.className = "mss-login-profile-item mss-login-profile-logout";
  logoutBtn.textContent = "Log out";
  logoutBtn.setAttribute("role", "menuitem");
  logoutBtn.addEventListener("click", () => {
    closeProfileMenu();
    logoutAction();
  });
  menu.appendChild(logoutBtn);

  backdrop.appendChild(menu);
  document.body.appendChild(backdrop);

  const onBackdropClick = (ev) => {
    if (ev.target === backdrop) {
      closeProfileMenu();
    }
  };
  const onKeyDown = (ev) => {
    if (ev.key === "Escape") {
      ev.preventDefault();
      closeProfileMenu();
    }
  };
  const onDocMouseDown = (ev) => {
    if (menu.contains(ev.target)) return;
    if (anchor && anchor.contains && anchor.contains(ev.target)) return;
    if (ev.target === backdrop || backdrop.contains(ev.target)) {
      if (ev.target === backdrop) closeProfileMenu();
      return;
    }
    closeProfileMenu();
  };

  backdrop.addEventListener("click", onBackdropClick);
  document.addEventListener("keydown", onKeyDown, true);
  document.addEventListener("mousedown", onDocMouseDown, true);

  _profileDismissCleanup = () => {
    backdrop.removeEventListener("click", onBackdropClick);
    document.removeEventListener("keydown", onKeyDown, true);
    document.removeEventListener("mousedown", onDocMouseDown, true);
  };
}

async function onProfileClick(event) {
  const existing = document.getElementById("mss-login-profile-backdrop");
  if (existing) {
    closeProfileMenu();
    return;
  }
  const me = await fetchMe();
  openProfileMenu(event.currentTarget, me);
}

const profileButtonSpec = {
  icon: DEFAULT_ICON,
  label: "",
  tooltip: "Account",
  class: "mss-login-profile-btn",
  onClick: () => {},
};

function markProfileButton() {
  const host = document.querySelector("[data-testid='action-bar-buttons']");
  if (!host) return;
  const btn = host.querySelector(".mss-login-profile-btn") || host.querySelector("button:last-of-type");
  if (!btn) return;
  btn.setAttribute("data-mss-login-profile", "1");
  btn.setAttribute("aria-label", "Account menu");
  btn.onclick = onProfileClick;
  applyAvatarToButton(btn);
}

if (typeof app !== "undefined" && app.registerExtension) {
  app.registerExtension({
    name: "mss-login.Logout",
    commands: [
      {
        id: "MSS-Login.Logout",
        label: "Log Out",
        icon: "pi pi-sign-out",
        function: logoutAction,
      },
    ],
    menuCommands: [
      { path: ["File"], commands: ["MSS-Login.Logout"] },
      { path: ["MSS-Login"], commands: ["MSS-Login.Logout"] },
    ],
    actionBarButtons: [profileButtonSpec],
    async setup() {
      ensureProfileStyles();
      window.mssLoginLogout = logoutAction;
      const tryMark = () => markProfileButton();
      setTimeout(tryMark, 400);
      setTimeout(tryMark, 1200);
      const obs = new MutationObserver(tryMark);
      obs.observe(document.body, { childList: true, subtree: true });
      setTimeout(() => obs.disconnect(), 15000);
    },
  });
}

export { logoutAction };
