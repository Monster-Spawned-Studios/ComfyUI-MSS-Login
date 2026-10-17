import { $el } from "/scripts/ui.js";

$el("style", {
  textContent: `
  .mss-login-logout {
    color: var(--p-red-600) !important;
  }
  
  .mss-login-logout:hover {
    background: var(--p-red-600) !important;
    color: var(--p-red-300) !important;
  }

  #logout-menu-button {
    background-color: var(--p-red-600) !important;
  }

  #logout-menu-button {
    background-color: var(--p-red-500) !important;
  }

  #logout-menu-button .logout-icon {
    margin: 8px 0 8px 10px;
    font-size: 15px;
  }

  .mss-login-profile-btn {
    position: relative;
  }

  .mss-login-avatar-img {
    width: 22px;
    height: 22px;
    border-radius: 999px;
    object-fit: cover;
    display: block;
  }

  .mss-login-profile-backdrop {
    position: fixed;
    inset: 0;
    z-index: 12040;
    background: rgba(0, 0, 0, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
  }

  .mss-login-profile-menu {
    position: relative;
    z-index: 12050;
    width: min(320px, 92vw);
    min-width: 220px;
    background: rgba(18, 20, 28, 0.98);
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 14px;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.55);
    padding: 12px 14px 16px;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
    text-align: center;
  }

  .mss-login-profile-header {
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    padding: 4px 36px 10px;
    gap: 8px;
  }

  .mss-login-profile-name {
    flex: 1;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.9;
    text-align: center;
  }

  .mss-login-profile-close {
    position: absolute;
    right: 0;
    top: 0;
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: #f5f5f7;
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .mss-login-profile-close:hover {
    background: rgba(255, 255, 255, 0.1);
  }

  .mss-login-profile-item {
    display: block;
    width: 100%;
    text-align: center;
    background: transparent;
    border: none;
    color: #f5f5f7;
    padding: 10px 12px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
  }

  .mss-login-profile-item:hover {
    background: rgba(255, 255, 255, 0.08);
  }

  .mss-login-profile-logout {
    color: #fca5a5;
  }
  `,
  parent: document.head,
});
