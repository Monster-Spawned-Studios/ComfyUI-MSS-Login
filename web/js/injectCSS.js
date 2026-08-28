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

  .mss-login-profile-menu {
    position: fixed;
    z-index: 12050;
    min-width: 180px;
    background: rgba(18, 20, 28, 0.96);
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 10px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
    padding: 6px;
  }

  .mss-login-profile-name {
    padding: 8px 10px 6px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.85;
  }

  .mss-login-profile-item {
    display: block;
    width: 100%;
    text-align: left;
    background: transparent;
    border: none;
    color: #f5f5f7;
    padding: 8px 10px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 13px;
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
