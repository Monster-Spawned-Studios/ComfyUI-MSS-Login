import { app } from "/scripts/app.js";

const AVATAR_URL = "/mss-login/api/me/avatar";
const LOGOUT_URL = "/logout";
const DEFAULT_ICON = "pi pi-user";
const PROFILE_CSS = `
.mss-login-profile-btn { position: relative; }
.mss-login-avatar-img { width: 22px; height: 22px; border-radius: 999px; object-fit: cover; display: block; }
.mss-login-profile-menu { position: fixed; z-index: 12050; min-width: 180px; background: rgba(18,20,28,0.96); color: #f5f5f7; border: 1px solid rgba(255,255,255,0.16); border-radius: 10px; box-shadow: 0 12px 32px rgba(0,0,0,0.45); padding: 6px; }
.mss-login-profile-name { padding: 8px 10px 6px; font-size: 12px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; opacity: 0.85; }
.mss-login-profile-item { display: block; width: 100%; text-align: left; background: transparent; border: none; color: #f5f5f7; padding: 8px 10px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.mss-login-profile-item:hover { background: rgba(255,255,255,0.08); }
.mss-login-profile-logout { color: #fca5a5; }
`;

function ensureProfileStyles() {
  if (document.getElementById("mss-login-profile-css")) return;
  const style = document.createElement("style");
  style.id = "mss-login-profile-css";
  style.textContent = PROFILE_CSS;
  document.head.appendChild(style);
}

function clearClientAuthState() {
  try {
    sessionStorage.removeItem("jwt_token");
    sessionStorage.removeItem("mss-login-jwt");
    localStorage.removeItem("jwt_token");
  } catch (_) {}
  try {
    document.cookie.split(";").forEach((cookie) => {
      const name = cookie.split("=")[0].trim();
      if (!name) return;
      const lower = name.toLowerCase();
      if (lower === "jwt_token" || lower.includes("session") || lower === "mss_login_device_id") {
        document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; samesite=strict`;
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
  window.location.href = LOGOUT_URL;
}

function closeProfileMenu() {
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

function openProfileMenu(anchor, me) {
  closeProfileMenu();
  const guest = isGuestUser(me);
  const menu = document.createElement("div");
  menu.id = "mss-login-profile-menu";
  menu.className = "mss-login-profile-menu";
  menu.setAttribute("role", "menu");

  const nameRow = document.createElement("div");
  nameRow.className = "mss-login-profile-name";
  nameRow.textContent = me?.username || "Account";
  menu.appendChild(nameRow);

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

  document.body.appendChild(menu);
  const rect = anchor.getBoundingClientRect();
  menu.style.top = `${Math.round(rect.bottom + 6)}px`;
  menu.style.right = `${Math.round(window.innerWidth - rect.right)}px`;

  const dismiss = (ev) => {
    if (menu.contains(ev.target) || anchor.contains(ev.target)) return;
    closeProfileMenu();
    document.removeEventListener("mousedown", dismiss, true);
  };
  document.addEventListener("mousedown", dismiss, true);
}

async function onProfileClick(event) {
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
