import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";
import { $el, ComfyDialog } from "../../scripts/ui.js";

const GROUPS = ["owner", "admin", "power", "user", "guest"];
/** Roles assignable via the Users & Roles dropdown (ownership uses Transfer ownership). */
const ASSIGNABLE_GROUPS = ["admin", "power", "user", "guest"];
let currentUser = null;
let groupsConfig = {};

/** DOMPurify (loaded dynamically when run inside ComfyUI). */
const DOMPURIFY_CDN = "https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.2.7/purify.min.js";
function loadDOMPurify() {
  if (typeof window.DOMPurify !== "undefined") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = DOMPURIFY_CDN;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Failed to load DOMPurify"));
    document.head.appendChild(s);
  });
}

// --- Extension Tab Registry API ---
/**
 * Registry for extension tabs in the mss-login admin panel.
 * Extensions can register custom tabs to manage their own permissions or settings.
 */
window.mss_loginAdminTabs = {
    _tabs: [],
    _defaultOrder: ["users", "perms", "ip", "env", "nsfw"],
    
    /**
     * Register a new tab in the admin panel.
     * @param {Object} config - Tab configuration
     * @param {string} config.id - Unique tab identifier (alphanumeric, lowercase, no spaces)
     * @param {string} config.label - Display name for the tab
     * @param {Function} config.render - Async function that renders the tab content
     *   @param {HTMLElement} container - The container element to render into
     *   @param {Object} context - Context object with available data
     *   @param {Array} context.usersList - List of all users
     *   @param {Object} context.groupsConfig - Groups configuration object
     *   @param {Object} context.currentUser - Current logged-in user object
     * @param {number} [config.order] - Optional order/position (lower numbers appear first, default: 100)
     * @param {string} [config.icon] - Optional icon class or text (not currently used, reserved for future)
     * @returns {boolean} - True if registration was successful, false if ID already exists
     * 
     * @example
     * window.mss_loginAdminTabs.register({
     *   id: "myextension",
     *   label: "My Extension",
     *   order: 50,
     *   render: async (container, context) => {
     *     container.innerHTML = `<h3>My Extension Settings</h3>`;
     *     // Render your content here
     *   }
     * });
     */
    register(config) {
        if (!config || !config.id || !config.label || !config.render) {
            console.error("[mss-login] Tab registration failed: missing required fields (id, label, render)");
            return false;
        }
        
        // Validate ID format
        if (!/^[a-z0-9_-]+$/.test(config.id)) {
            console.error("[mss-login] Tab registration failed: id must be lowercase alphanumeric with underscores/hyphens only");
            return false;
        }
        
        // Check for duplicate IDs
        if (this._tabs.some(t => t.id === config.id)) {
            console.warn(`[mss-login] Tab with id "${config.id}" already registered, skipping`);
            return false;
        }
        
        // Check for conflicts with built-in tabs
        if (this._defaultOrder.includes(config.id)) {
            console.error(`[mss-login] Tab registration failed: id "${config.id}" conflicts with built-in tab`);
            return false;
        }
        
        const tab = {
            id: config.id,
            label: config.label,
            render: config.render,
            order: config.order !== undefined ? config.order : 100,
            icon: config.icon || null
        };
        
        this._tabs.push(tab);
        // Sort by order
        this._tabs.sort((a, b) => a.order - b.order);
        
        console.log(`[mss-login] Registered extension tab: "${config.id}" (${config.label})`);
        return true;
    },
    
    /**
     * Unregister a tab by ID.
     * @param {string} id - Tab identifier to remove
     * @returns {boolean} - True if tab was found and removed
     */
    unregister(id) {
        const index = this._tabs.findIndex(t => t.id === id);
        if (index !== -1) {
            this._tabs.splice(index, 1);
            console.log(`[mss-login] Unregistered extension tab: "${id}"`);
            return true;
        }
        return false;
    },
    
    /**
     * Get all registered extension tabs.
     * @returns {Array} - Array of tab configurations
     */
    getAll() {
        return [...this._tabs];
    },
    
    /**
     * Clear all registered extension tabs.
     */
    clear() {
        this._tabs = [];
        console.log("[mss-login] Cleared all extension tabs");
    }
};

// Backend API endpoints (adjust if your backend uses different paths)
const IP_API_ENDPOINT = "/mss-login/api/ip-lists";
const USER_ENV_API_ENDPOINT = "/mss-login/api/user-env";

// --- 1. BLOCKING MAP (The Enforcer) ---
// If a user lacks permission for the Key, these CSS selectors are hidden via !important
const CSS_BLOCK_MAP = {
    // --- Built-in Console / Logs / Terminal (sidebar toggle + bottom panel) ---
    "can_view_console": [
        ".comfy-console",
        "#comfy-console",
        "[data-panel-id='console']",
        "[aria-label='Console']",
        "[aria-label='Logs']",
        ".comfy-log-panel",
        ".comfy-bottom-panel [data-tab='console']",
        ".p-panel-content .comfy-console",
        "button[aria-label='Console']",
        "button[aria-label='Logs']",
        ".comfy-ui-panel-console",
        ".side-bar-button:has(.icon-\\[ph--terminal-bold\\])",
        ".side-bar-button:has([class*='ph--terminal'])",
        ".side-bar-button[aria-label*='Console' i]",
        ".side-bar-button[aria-label*='Logs' i]",
        ".side-bar-button[aria-label*='bottom panel' i]",
        ".side-bar-button[aria-label*='Bottom Panel' i]",
        ".xterm",
        ".xterm-viewport",
        ".xterm-screen"
    ],
    // --- Core UI ---
    "ui_queue_button": ["#queue-button", ".queue-button", "button.queue-button"],
    "ui_batch_widget": [".comfy-menu-queue-batch"],
    "ui_extra_options": [".comfy-menu-queue-extra"],
    
    // --- Sidebar / Left Toolbar ---
    // Core & Common Extensions
    "ui_side_history": ["#comfy-view-history-button", "[title='History']", ".pi-history"], // Often clock icon
    "ui_side_queue": ["#comfy-view-queue-button", "[title='Queue']", ".pi-list"], 
    "ui_side_assets": [
        "[title='Assets']",
        "[aria-label='Assets']",
        ".pi-folder",                 // Common icon for assets
        "#comfyui-browser-button",    // ComfyUI-Browser
        ".comfy-assets-tab",
        "button.assets-tab-button",
        ".assets-tab-button",
        ".assets-tab-button .side-bar-button-label"
    ],
    "ui_side_templates": [
        "[title='Templates']", 
        "[aria-label='Templates']",
        ".pi-copy",                   // Common icon for templates
        "#node-templates-button",     // Node Templates
        ".comfy-templates-tab",
        "button.templates-tab-button",
        ".templates-tab-button",
        ".templates-tab-button .side-bar-button-label"
    ],
    "ui_side_models": [
        "[title='Models']",
        "[aria-label='Models']",
        ".side-bar-button[aria-label='Models']",
        ".side-bar-button[aria-label*='Models' i]",
        ".side-bar-button[title='Models']",
        "[data-panel-id='models']",
        ".comfy-models-tab",
        "button.models-tab-button",
        ".models-tab-button"
    ],
    
    // --- Standard Menus (New Vue/Prime UI + legacy ids) ---
    // NOTE:
    // - We treat "Save", "Save As", "Export", and "Export (API)" all as "ui_menu_save"
    //   because they all modify or export workflows.
    // - "Open" is controlled by ui_menu_load.

    "ui_menu_save": [
        // Old ComfyUI top-bar save button (if still present anywhere)
        "#comfy-save-button",
        // New File menu entries
        "li.p-tieredmenu-item[aria-label='Save']",
        "li.p-tieredmenu-item[aria-label='Save As']",
        "li.p-tieredmenu-item[aria-label='Export']",
        "li.p-tieredmenu-item[aria-label='Export (API)']"
    ],
    "ui_menu_load": [
        // Old ComfyUI load button
        "#comfy-load-button",
        // New "Open" menu entry in the File menu
        "li.p-tieredmenu-item[aria-label='Open']"
    ],
    "ui_menu_refresh": ["#comfy-refresh-button"],

    // --- Workflow breadcrumb (Graph title dropdown) ---
    "ui_workflow_breadcrumb": [".subgraph-breadcrumb"],

    "ui_menu_clipspace": ["#comfy-clipspace-button"],
    "ui_menu_clear": ["#comfy-clear-button"],
    "ui_menu_manager": [
        ".comfyui-manager-menu-btn", 
        "button.comfyui-manager-menu-btn"
    ],
    "ui_menu_extensions": [
        "li.p-tieredmenu-item[aria-label='Manage Extensions']",
        "li.p-tieredmenu-item[aria-label='Manage Extensions'] *"
    ],
    "ui_menu_templates": [
        "li.p-tieredmenu-item[aria-label='Browse Templates']",
        "li.p-tieredmenu-item[aria-label='Browse Templates'] *"
    ],

    // --- Extensions (Hotbars, Overlays, & Settings Menu) ---
    "settings_comfy": [
        "li[aria-label='Comfy']",
        "li.p-listbox-option[aria-label='Comfy']",
        "[data-testid='settings-dialog'] [data-nav-id='Comfy']"
    ],
    "settings_extension": [
        "li[aria-label='Extension']",
        "li.p-listbox-option[aria-label='Extension']",
        "[data-testid='settings-dialog'] [data-nav-id='extension']"
    ],
    "settings_user": [
        "li[aria-label='User']",
        "li.p-listbox-option[aria-label='User']",
        "[data-testid='settings-dialog'] [data-nav-id='user']"
    ],
    "settings_keybinding": [
        "li[aria-label='Keybinding']",
        "li.p-listbox-option[aria-label='Keybinding']",
        "[data-testid='settings-dialog'] [data-nav-id='keybinding']"
    ],
    "settings_appearance": [
        "li[aria-label='Appearance']",
        "li.p-listbox-option[aria-label='Appearance']",
        "[data-testid='settings-dialog'] [data-nav-id='Appearance']"
    ],
    "settings_litegraph": [
        "li[aria-label='Lite Graph']",
        "li.p-listbox-option[aria-label='Lite Graph']",
        "li[aria-label='LiteGraph']",
        "li.p-listbox-option[aria-label='LiteGraph']",
        "[data-testid='settings-dialog'] [data-nav-id='LiteGraph']"
    ],
    "settings_3d": [
        "li[aria-label='3D']",
        "li.p-listbox-option[aria-label='3D']",
        "[data-testid='settings-dialog'] [data-nav-id='3D']"
    ],
    "settings_maskeditor": [
        "li[aria-label='Mask Editor']",
        "li.p-listbox-option[aria-label='Mask Editor']",
        "[data-testid='settings-dialog'] [data-nav-id='Mask Editor']"
    ],
    "settings_about": [
        "li[aria-label='About']",
        "li.p-listbox-option[aria-label='About']",
        "[data-testid='settings-dialog'] [data-nav-id='about']"
    ],
    "settings_mss_loginsettings": [
        "li[aria-label='mss-login']",
        "li.p-listbox-option[aria-label='mss-login']",
        "[data-testid='settings-dialog'] [data-nav-id='mss-login']",
        "[data-testid='settings-dialog'] [data-nav-id='MSS-Login']"
    ],

    // iTools
    "settings_itools": [
        ".itools-floating-bar", 
        ".itools-menu-btn",
        ".itools-panel",
        "[id*='itools']"
    ],
    // Crystools
    "settings_crystools": [
        "#crystools-root",
        ".crystools-nav-bar",
        ".crystools-save-button",
        "[title^='Crystools']"
    ],
    // rgthree
    "settings_rgthree": [
        ".rgthree-menu-btn",
        ".rgthree-context-menu"
    ],
    // Gallery
    "settings_gallery": [
        ".gallery-container",
        "#gallery-button"
    ],
    // Impact Pack
    "settings_impact": [
        "#impact-pack-button" 
    ]
};

// --- 2. MODAL CSS (The Look & Feel) ---
const ADMIN_STYLES = `
/* Overlay Backdrop */
.mss-login-modal-overlay {
    position: fixed;
    inset: 0;
    width: 100vw;
    height: 100vh;
    /* a bit more transparent so the app shows through */
    background: radial-gradient(circle at top, rgba(0,0,0,0.75), rgba(0,0,0,0.92));
    backdrop-filter: blur(6px);
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
}

/* Main Window */
.mss-login-modal {
    position: relative;
    width: 960px;
    max-width: 96vw;
    height: 720px;
    max-height: 92vh;
    /* slightly more transparent card */
    background: rgba(12, 12, 16, 0.92);
    color: #f5f5f7;
    display: flex;
    flex-direction: column;
    border-radius: 12px;
    overflow: hidden;
    box-shadow:
        0 22px 60px rgba(0,0,0,0.95),
        0 0 0 1px rgba(255,255,255,0.08);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

/* Large transparent logo in the background */
.mss-login-modal::before {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    opacity: 0.06;  /* tweak if too bright/dim */
    background-image: url("/mss-login/assets/mss_logo.png");
    background-repeat: no-repeat;
    background-position: center 35%;
    background-size: 420px auto;
    mix-blend-mode: screen;
    z-index: 0;
}

/* Small logo badge in the top-right corner */
.mss-login-modal::after {
    content: "";
    position: absolute;
    top: 10px;
    right: 18px;
    width: 120px;
    height: 40px;
    pointer-events: none;
    background-image: url("/mss-login/assets/mss_logo.png");
    background-repeat: no-repeat;
    background-position: right center;
    background-size: contain;
    opacity: 0.4;
    z-index: 1;
}

/* Header */
.mss-login-modal-header {
    padding: 14px 20px;
    background: linear-gradient(
        to right,
        rgba(255,255,255,0.05),
        rgba(255,255,255,0.02)
    );
    border-bottom: 1px solid rgba(255,255,255,0.14);
    display: flex;
    justify-content: flex-start;
    align-items: center;
    gap: 4px;
    z-index: 2;
    position: relative;
}
.mss-login-modal-title {
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #ffffff;
    flex: 1;
}
.mss-login-modal-subtitle {
    font-size: 12px;
    opacity: 0.9;
    color: #d0d0d0;
}
.mss-login-modal-close {
    cursor: pointer;
    font-size: 20px;
    color: #e0e0e0;
    background: none;
    border: none;
    transition: 0.16s ease;
    padding: 2px 6px;
    border-radius: 999px;
}
.mss-login-modal-close:hover {
    color: #ffffff;
    background: rgba(255,255,255,0.12);
    transform: translateY(-1px);
}
.mss-login-dialog-back {
    background: transparent;
    border: 1px solid rgba(255,255,255,0.2);
    color: #e5e7eb;
    width: 32px;
    height: 32px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
    margin-right: 8px;
    flex-shrink: 0;
}
.mss-login-dialog-back:hover {
    background: rgba(255,255,255,0.12);
    color: #ffffff;
}
.mss-login-dialog-back[hidden] {
    display: none !important;
}
.mss-login-home {
    flex: 1;
    min-height: 0;
}

/* Body & Tabs */
.mss-login-modal-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: radial-gradient(
        circle at top left,
        rgba(255,255,255,0.04),
        rgba(0,0,0,0.96)
    );
    z-index: 2;
    position: relative;
}
.mss-login-tab-menu {
    position: relative;
    padding: 8px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.14);
    background: #181b22;
}
.mss-login-tab-toggle {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(255,255,255,0.08);
    color: #ffffff;
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    cursor: pointer;
    text-transform: uppercase;
}
.mss-login-tab-toggle:hover {
    background: rgba(255,255,255,0.16);
}
.mss-login-tab-toggle-icon {
    font-size: 14px;
    line-height: 1;
}
.mss-login-tab-dropdown {
    position: absolute;
    top: calc(100% - 2px);
    left: 12px;
    min-width: 260px;
    max-width: min(90vw, 380px);
    max-height: 55vh;
    overflow-y: auto;
    background: #151821;
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 10px;
    box-shadow: 0 14px 32px rgba(0,0,0,0.5);
    z-index: 20;
    padding: 6px;
}
.mss-login-tab-menu-item {
    width: 100%;
    text-align: left;
    background: transparent;
    border: none;
    color: #c5c8d3;
    padding: 9px 10px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.mss-login-tab-menu-item:hover {
    color: #ffffff;
    background: rgba(255,255,255,0.09);
}
.mss-login-tab-menu-item.active {
    color: #ffffff;
    background: rgba(59,130,246,0.25);
}

/* Content Area */
.mss-login-content {
    flex: 1;
    padding: 10px 0 0;
    overflow-y: auto;
    overflow-x: hidden;
    display: none;
}
.mss-login-content.active {
    display: block;
}

@media (max-width: 980px) {
    .mss-login-modal {
        width: 98vw;
        height: 94vh;
    }
    .mss-login-modal-header {
        padding: 10px 14px;
    }
    .mss-login-modal-title {
        font-size: 15px;
        letter-spacing: 0.04em;
        text-transform: none;
    }
    .mss-login-tab-menu {
        padding: 8px;
    }
    .mss-login-tab-toggle {
        width: 100%;
        justify-content: space-between;
        font-size: 11px;
    }
    .mss-login-tab-dropdown {
        left: 8px;
        right: 8px;
        max-width: none;
        min-width: 0;
    }
}

/* Tables */
.mss-login-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.mss-login-table th {
    text-align: left;
    padding: 12px 18px;
    border-bottom: 1px solid rgba(255,255,255,0.22);
    position: sticky;
    top: 0;
    z-index: 10;
    background: #171923;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.10em;
    color: #f9fafb;  /* bright */
}
.mss-login-table td {
    padding: 10px 18px;
    border-bottom: 1px solid rgba(255,255,255,0.10);
    vertical-align: middle;
    color: #e5e7f3;  /* brighter row text */
    font-size: 13px;
}
.mss-login-table tr:nth-child(even) td {
    background: rgba(255,255,255,0.02);
}
.mss-login-table tr:hover td {
    background: rgba(59,130,246,0.20);
}

/* Table Sections */
.mss-login-section-row td {
    background: #151821;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    padding: 22px 18px 10px;
    color: #f9fafb;
    font-size: 11px;
    border-bottom: 2px solid rgba(255,255,255,0.24);
}

/* Checkbox cell */
.mss-login-check-cell {
    text-align: center;
    width: 80px;
    border-left: 1px solid rgba(255,255,255,0.15);
}

/* Buttons */
.mss-login-btn {
    background: var(--p-button-primary-bg, #9660fa);
    color: var(--p-button-primary-text, #ffffff);
    border: 1px solid rgba(255,255,255,0.24);
    padding: 7px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 600;
    font-size: 12px;
    transition: 0.16s ease;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
}
.mss-login-btn:hover {
    opacity: 0.97;
    transform: translateY(-1px);
    box-shadow: 0 6px 18px rgba(0,0,0,0.7);
}
.mss-login-btn.secondary {
    background: rgba(255,255,255,0.04);
    color: #e5e7f3;
}
.mss-login-btn.danger {
    background: #7a2525;
    border-color: #aa3a3a;
}
.mss-login-btn.mss-login-btn-danger {
    background: #8b1f2f;
    border: 1px solid #b03a4a;
}

.mss-login-btn.mss-login-btn-danger:hover {
    background: #b03a4a;
    border-color: #d14f5d;
}

/* Launcher button in the Comfy Settings panel */
.mss-login-launch-btn {
    width: 100%;
    padding: 10px;
    font-weight: 600;
    border-radius: 8px;
    margin-top: 6px;

    /* Strong contrast on BOTH light and dark settings panels */
    background: #111827;                 /* dark slate */
    color: #f9fafb;
    border: 1px solid #1f2937;
    cursor: pointer;
    box-shadow: 0 4px 10px rgba(0,0,0,0.35);
    text-align: center;
}

.mss-login-launch-btn:hover {
    background: #7c60fa;                 /* purple on hover */
    border-color: #9660fa;
    color: #ffffff;
    box-shadow: 0 6px 16px rgba(0,0,0,0.45);
}

/* Small info text */
.mss-login-note {
    font-size: 12px;
    opacity: 0.95;
    color: #d3d3dd;
}

/* Flex layouts */
.mss-login-row {
    display: flex;
    gap: 12px;
    align-items: flex-start;
}
.mss-login-row-space {
    display: flex;
    gap: 12px;
    justify-content: space-between;
    align-items: center;
}
.mss-login-col {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

/* Inputs / textareas */
.mss-login-textarea,
.mss-login-input {
    background: #181a23;
    color: #f5f5f7;
    border-radius: 6px;
    border: 1px solid rgba(255,255,255,0.28);
    padding: 6px 8px;
    font-size: 12px;
    resize: vertical;
}
.mss-login-textarea {
    min-height: 140px;
    width: 100%;
    font-family: monospace;
}
.mss-login-select {
    background: #181a23;
    color: #f5f5f7;
    border-radius: 6px;
    border: 1px solid rgba(255,255,255,0.28);
    padding: 5px 8px;
    font-size: 12px;
}

/* Env file list / cards */
.mss-login-card {
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.18);
    background: #13141c;
    padding: 12px 14px;
    margin: 4px 0 10px;
}
.mss-login-card-header {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 4px;
    color: #ffffff;
}
.mss-login-chip {
    display: inline-flex;
    padding: 2px 7px;
    border-radius: 999px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border: 1px solid rgba(255,255,255,0.35);
}
.mss-login-file-list {
    max-height: 240px;
    overflow-y: auto;
    font-family: monospace;
    font-size: 11px;
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid rgba(255,255,255,0.25);
    background: #101119;
    color: #f5f5f7;
}

/* Toast */
.mss-login-toast {
    position: fixed;
    top: 18px;
    left: 50%;
    transform: translateX(-50%);
    padding: 10px 16px;
    background: rgba(0,0,0,0.92);
    color: #f5f5f7;
    border-radius: 999px;
    font-size: 12px;
    z-index: 11000;
    box-shadow: 0 10px 30px rgba(0,0,0,0.8);
    border: 1px solid rgba(255,255,255,0.3);
}

/* Enforcement */
.mss-login-blocked-item {
    display: none !important;
    opacity: 0 !important;
    pointer-events: none !important;
}
`;
// --- DATA HELPERS ---
async function getData(endpoint) {
    try {
        const res = await api.fetchApi(endpoint);
        if (res.status === 200) return await res.json();
    } catch (e) { console.error(e); }
    return null;
}

function getSanitizedId(text) {
    if (!text) return "";
    return "settings_" + text.trim().toLowerCase().replace(/[^a-z0-9]/g, "");
}

// Helper function to escape HTML to prevent XSS
function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// --- 3. ADMIN DIALOG CLASS ---
// Global reference to the current dialog instance
window._mss_loginDialogInstance = null;

function loadMssLoginConfigAssets() {
    return new Promise((resolve, reject) => {
        if (typeof window.mssLoginMountConfig === "function") {
            resolve();
            return;
        }
        if (!document.querySelector('link[data-mss-login-config-css]')) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = "/mss-login/dist/config.css";
            link.setAttribute("data-mss-login-config-css", "1");
            document.head.appendChild(link);
        }
        const loadModule = (src) =>
            new Promise((res, rej) => {
                const existing = document.querySelector(`script[data-mss-login-src="${src}"]`);
                if (existing) {
                    existing.addEventListener("load", () => res());
                    existing.addEventListener("error", () => rej(new Error(src + " failed")));
                    // Already loaded
                    if (existing.dataset.loaded === "1") res();
                    return;
                }
                const script = document.createElement("script");
                script.type = "module";
                script.src = src;
                script.setAttribute("data-mss-login-src", src);
                script.onload = () => {
                    script.dataset.loaded = "1";
                    res();
                };
                script.onerror = () => rej(new Error("Failed to load " + src));
                document.head.appendChild(script);
            });
        // vue-vendor chunk (if present) then config-app entry
        loadModule("/mss-login/dist/vue-vendor.js")
            .catch(() => null)
            .then(() => loadModule("/mss-login/dist/config-app.js"))
            .then(resolve)
            .catch(reject);
    });
}

class mss_loginDialog extends ComfyDialog {
    constructor() {
        super();
        this.overlay = $el("div.mss-login-modal-overlay");
        this.element = $el("div.mss-login-modal");
        this._vueUnmount = null;
    }

    async show() {
        await loadDOMPurify();
        // Prevent multiple dialogs from being open at the same time
        if (window._mss_loginDialogInstance && window._mss_loginDialogInstance.overlay && 
            document.body.contains(window._mss_loginDialogInstance.overlay)) {
            console.log("[mss-login] Dialog is already open, focusing existing dialog");
            // Focus the existing dialog by bringing it to front
            window._mss_loginDialogInstance.overlay.style.zIndex = "999999";
            return;
        }
        
        // Store this instance as the current dialog
        window._mss_loginDialogInstance = this;
        
        this.overlay.appendChild(this.element);
        document.body.appendChild(this.overlay);
        
        // Hide floating button when dialog opens
        if (window._mss_loginFloatingButton && window._mss_loginFloatingButton.button) {
            window._mss_loginFloatingButton.button.style.display = "none";
        }
        this.element.innerHTML = `<div style="padding:50px; text-align:center;">Loading System Configuration...</div>`;
        
        // Fetch fresh data
        const [me, groups, users] = await Promise.all([
            getData("/mss-login/api/me"),
            getData("/mss-login/api/groups"),
            getData("/mss-login/api/users")
        ]);

        currentUser = me;
        groupsConfig = groups?.groups || {};
        const usersList = users?.users || [];
        const preferUsers = !!window._mss_loginPreferUsersTab;
        const preferModelDownload = !!window._mss_loginPreferModelDownloadTab;
        window._mss_loginPreferUsersTab = false;
        window._mss_loginPreferModelDownloadTab = false;
        const canDownloadModels = !!(currentUser && (currentUser.can_download_models || currentUser.is_admin));
        const downloadOnlyAccess = !!(currentUser && !currentUser.is_admin && canDownloadModels);

        // Admin Guard — allow Model download section for non-admins with can_download_models.
        if (!currentUser || (!currentUser.is_admin && !canDownloadModels)) {
            this.element.innerHTML = `
                <div style="padding:40px; text-align:center; color:#ff6b6b;">
                    <h2>Access Denied</h2>
                    <p>Administrative privileges are required to modify system policies.</p>
                    <br><button id='s-close-btn' class='mss-login-btn'>Close</button>
                </div>`;
            this.element.querySelector("#s-close-btn").onclick = () => this.close();
            return;
        }

        const extensionTabs = downloadOnlyAccess ? [] : window.mss_loginAdminTabs.getAll();
        this.overlay.onclick = (e) => { if (e.target === this.overlay) this.close(); };

        // Prefer Vue + Tailwind config UI when dist assets are available.
        try {
            await loadMssLoginConfigAssets();
            if (typeof window.mssLoginMountConfig === "function") {
                const dialog = this;
                const legacyRenderers = {
                    users: async (container) => dialog.renderUsers(usersList, container),
                    perms: async (container) => dialog.renderPerms(container),
                    tokens: async (container) => dialog.renderTokens(container),
                    ip: async (container) => dialog.renderIpRules(container),
                    env: async (container) => dialog.renderUserEnv(container, usersList),
                    nsfw: async (container) => dialog.renderNsfwManagement(container),
                    "token-storage": async (container) => dialog.renderTokenStorage(container),
                    "users-db": async (container) => dialog.renderUsersDbConfig(container),
                    s3: async (container) => dialog.renderS3Settings(container),
                    "shared-models": async (container) => dialog.renderSharedModels(container, usersList),
                    "model-download": async (container) => dialog.renderModelDownload(container),
                    "login-appearance": async (container) => dialog.renderLoginAppearance(container),
                };
                for (const extTab of extensionTabs) {
                    if (!/^[a-z0-9_-]+$/.test(extTab.id)) continue;
                    legacyRenderers[extTab.id] = async (container, ctx) => {
                        await extTab.render(container, ctx);
                    };
                }
                this.element.innerHTML = "";
                this.element.style.cssText = "padding:0;background:transparent;border:none;box-shadow:none;max-width:none;width:auto;";
                if (this._vueUnmount) {
                    try { this._vueUnmount(); } catch (_) {}
                    this._vueUnmount = null;
                }
                let preferTab = "";
                if (preferUsers && currentUser.is_admin) preferTab = "users";
                else if (preferModelDownload || downloadOnlyAccess) preferTab = "model-download";
                this._vueUnmount = window.mssLoginMountConfig(this.element, {
                    currentUser,
                    usersList: downloadOnlyAccess ? [] : usersList,
                    groupsConfig: downloadOnlyAccess ? {} : groupsConfig,
                    extensionTabs,
                    legacyRenderers,
                    preferTab,
                    onClose: () => dialog.close(),
                });
                return;
            }
        } catch (err) {
            console.error("[mss-login] Vue config assets failed, using legacy dialog:", err);
        }

        // Get extension tabs (legacy vanilla fallback)
        // const extensionTabs already set
        
        // Build tabs HTML (built-in tabs first, then extension tabs)
        const isOwner = Array.isArray(currentUser?.groups) && currentUser.groups.map(g => String(g).toLowerCase()).includes("owner");
        const builtInTabs = [
            { id: "users", label: "Users & Roles", order: 0 },
            { id: "perms", label: "Permissions & UI", order: 1 },
            { id: "tokens", label: "Tokens & API Keys", order: 2 },
            { id: "shared-models", label: "Shared Models", order: 3 },
            { id: "model-download", label: "Model download", order: 4 },
            ...(isOwner ? [{ id: "s3", label: "S3 Settings", order: 5 }] : []),
            { id: "ip", label: "IP Rules", order: 6 },
            { id: "env", label: "User Env", order: 7 },
            { id: "nsfw", label: "NSFW Management", order: 8 },
            { id: "token-storage", label: "Token Storage", order: 9 },
            { id: "users-db", label: "Users DB", order: 10 },
            ...(isOwner ? [{ id: "login-appearance", label: "Login Appearance", order: 11 }] : []),
        ];
        
        // Combine and sort all tabs
        const allTabs = [...builtInTabs, ...extensionTabs.map(t => ({ id: t.id, label: t.label, order: t.order }))];
        allTabs.sort((a, b) => a.order - b.order);
        
        const tabsHTML = allTabs.map((tab) => {
            const escapedLabel = escapeHtml(tab.label);
            return `<button type="button" class="mss-login-tab-menu-item" data-tab="${tab.id}">${escapedLabel}</button>`;
        }).join("");
        
        const contentHTML = allTabs.map((tab) => {
            return `<div class="mss-login-content" id="mss-login-tab-${tab.id}" hidden></div>`;
        }).join("");

        const homeButtonsHTML = allTabs.map((tab) => {
            const escapedLabel = escapeHtml(tab.label);
            return `<button type="button" class="mss-login-launch-btn mss-login-home-btn" data-tab="${tab.id}" style="min-width:220px; max-width:320px;">${escapedLabel}</button>`;
        }).join("");
        
        // Render Layout: start on home menu (no section active)
        this.element.innerHTML = `
            <div class="mss-login-modal-header">
                <button type="button" id="mss-login-dialog-back" class="mss-login-dialog-back" aria-label="Back to main menu" hidden>←</button>
                <span class="mss-login-modal-title" id="mss-login-modal-title">MSS-Login Security Policy</span>
                <button class="mss-login-modal-close">✕</button>
            </div>
            <div class="mss-login-modal-body">
                <div class="mss-login-tab-menu" id="mss-login-section-nav" hidden>
                    <button type="button" class="mss-login-tab-toggle" id="mss-login-tab-toggle" aria-haspopup="true" aria-expanded="false">
                        <span class="mss-login-tab-toggle-icon">☰</span>
                        <span class="mss-login-tab-toggle-text">Select section</span>
                    </button>
                    <div class="mss-login-tab-dropdown" id="mss-login-tab-dropdown" hidden>
                        ${tabsHTML}
                    </div>
                </div>
                <div id="mss-login-home" class="mss-login-home" style="padding:24px; overflow:auto; display:flex; flex-direction:column; align-items:center; gap:10px;">
                    <p class="mss-login-note" style="margin-bottom:8px;">Choose a configuration section</p>
                    ${homeButtonsHTML}
                </div>
                ${contentHTML}
            </div>
        `;

        // Bindings
        this.element.querySelector(".mss-login-modal-close").onclick = () => this.close();
        this.overlay.onclick = (e) => { if (e.target === this.overlay) this.close(); };

        const titleEl = this.element.querySelector("#mss-login-modal-title");
        const backBtn = this.element.querySelector("#mss-login-dialog-back");
        const homeEl = this.element.querySelector("#mss-login-home");
        const sectionNav = this.element.querySelector("#mss-login-section-nav");
        const tabMenu = this.element.querySelector(".mss-login-tab-menu");
        const tabToggle = this.element.querySelector("#mss-login-tab-toggle");
        const tabToggleText = this.element.querySelector(".mss-login-tab-toggle-text");
        const tabDropdown = this.element.querySelector("#mss-login-tab-dropdown");
        const tabButtons = this.element.querySelectorAll(".mss-login-tab-menu-item");
        const HOME_TITLE = "MSS-Login Security Policy";

        const closeTabMenu = () => {
            if (!tabDropdown || !tabToggle) return;
            tabDropdown.hidden = true;
            tabToggle.setAttribute("aria-expanded", "false");
        };

        const openTabMenu = () => {
            if (!tabDropdown || !tabToggle) return;
            tabDropdown.hidden = false;
            tabToggle.setAttribute("aria-expanded", "true");
        };

        const showHome = () => {
            if (homeEl) homeEl.style.display = "flex";
            if (sectionNav) sectionNav.hidden = true;
            if (backBtn) backBtn.hidden = true;
            if (titleEl) titleEl.textContent = HOME_TITLE;
            this.element.querySelectorAll(".mss-login-content").forEach(c => {
                c.classList.remove("active");
                c.hidden = true;
            });
            tabButtons.forEach(btn => btn.classList.remove("active"));
            closeTabMenu();
        };

        const setActiveTab = (tabId) => {
            if (!tabId || !/^[a-z0-9_-]+$/.test(tabId)) {
                return;
            }
            const contentEl = this.element.querySelector(`#mss-login-tab-${tabId}`);
            if (!contentEl) {
                return;
            }
            if (homeEl) homeEl.style.display = "none";
            if (sectionNav) sectionNav.hidden = false;
            if (backBtn) backBtn.hidden = false;
            tabButtons.forEach(btn => {
                btn.classList.toggle("active", btn.dataset.tab === tabId);
            });
            this.element.querySelectorAll(".mss-login-content").forEach(c => {
                c.classList.remove("active");
                c.hidden = true;
            });
            contentEl.classList.add("active");
            contentEl.hidden = false;
            const activeTab = allTabs.find(tab => tab.id === tabId);
            if (activeTab) {
                if (tabToggleText) tabToggleText.textContent = activeTab.label;
                if (titleEl) titleEl.textContent = activeTab.label;
            }
        };

        if (backBtn) {
            backBtn.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                showHome();
            };
        }

        this.element.querySelectorAll(".mss-login-home-btn").forEach(btn => {
            btn.onclick = () => setActiveTab(btn.dataset.tab || "");
        });

        if (preferUsers) {
            setActiveTab("users");
        }

        if (tabToggle) {
            tabToggle.onclick = () => {
                if (tabDropdown?.hidden) {
                    openTabMenu();
                } else {
                    closeTabMenu();
                }
            };
        }

        tabButtons.forEach(btn => {
            btn.onclick = () => {
                setActiveTab(btn.dataset.tab || "");
                closeTabMenu();
            };
        });

        this._tabMenuOutsideClickHandler = (event) => {
            if (tabDropdown?.hidden) return;
            if (tabMenu && !tabMenu.contains(event.target)) {
                closeTabMenu();
            }
        };
        document.addEventListener("click", this._tabMenuOutsideClickHandler);
        this._tabMenuEscapeHandler = (event) => {
            if (event.key === "Escape") {
                closeTabMenu();
            }
        };
        document.addEventListener("keydown", this._tabMenuEscapeHandler);

        // Fill Data - Built-in tabs
        this.renderUsers(usersList, this.element.querySelector("#mss-login-tab-users"));
        this.renderPerms(this.element.querySelector("#mss-login-tab-perms"));
        await this.renderIpRules(this.element.querySelector("#mss-login-tab-ip"));
        this.renderUserEnv(this.element.querySelector("#mss-login-tab-env"), usersList);
        this.renderNsfwManagement(this.element.querySelector("#mss-login-tab-nsfw"));
        await this.renderSharedModels(this.element.querySelector("#mss-login-tab-shared-models"), usersList);
        await this.renderTokenStorage(this.element.querySelector("#mss-login-tab-token-storage"));
        await this.renderUsersDbConfig(this.element.querySelector("#mss-login-tab-users-db"));
        await this.renderModelDownload(this.element.querySelector("#mss-login-tab-model-download"));
        if (this.element.querySelector("#mss-login-tab-s3")) {
            await this.renderS3Settings(this.element.querySelector("#mss-login-tab-s3"));
        }
        if (this.element.querySelector("#mss-login-tab-tokens")) {
            await this.renderTokens(this.element.querySelector("#mss-login-tab-tokens"));
        }
        if (this.element.querySelector("#mss-login-tab-login-appearance")) {
            await this.renderLoginAppearance(this.element.querySelector("#mss-login-tab-login-appearance"));
        }
        
        // Fill Data - Extension tabs
        const context = {
            usersList,
            groupsConfig,
            currentUser
        };
        
        for (const extTab of extensionTabs) {
            // Validate tab ID before using in querySelector (double-check, already validated during registration)
            if (!/^[a-z0-9_-]+$/.test(extTab.id)) {
                console.error(`[mss-login] Invalid tab ID format: "${extTab.id}", skipping render`);
                continue;
            }
            
            const container = this.element.querySelector(`#mss-login-tab-${extTab.id}`);
            if (container) {
                try {
                    // Show loading state
                    container.innerHTML = `<div style="padding:20px; text-align:center; color:#c5c8d3;">Loading...</div>`;
                    // Render extension tab content
                    await extTab.render(container, context);
                } catch (error) {
                    console.error(`[mss-login] Error rendering extension tab "${extTab.id}":`, error);
                    // Escape error message to prevent XSS
                    const errorMsg = String(error.message || "Unknown error").replace(/[<>]/g, "");
                    const escapedLabel = escapeHtml(extTab.label);
                    container.innerText = DOMPurify.sanitize(`
                        <div style="padding:20px; text-align:center; color:#ff6b6b;">
                            <h3>Error Loading Tab</h3>
                            <p>Failed to render "${escapedLabel}" tab.</p>
                            <p style="font-size:11px; color:#c5c8d3;">${errorMsg}</p>
                        </div>
                    `);
                }
            } else {
                console.warn(`[mss-login] Container not found for extension tab: "${extTab.id}"`);
            }
        }
    }

    close() { 
        if (this._tabMenuOutsideClickHandler) {
            document.removeEventListener("click", this._tabMenuOutsideClickHandler);
            this._tabMenuOutsideClickHandler = null;
        }
        if (this._tabMenuEscapeHandler) {
            document.removeEventListener("keydown", this._tabMenuEscapeHandler);
            this._tabMenuEscapeHandler = null;
        }
        if (this._vueUnmount) {
            try { this._vueUnmount(); } catch (_) {}
            this._vueUnmount = null;
        }
        this.overlay.remove();
        
        // Clear the global instance if this is the current dialog
        if (window._mss_loginDialogInstance === this) {
            window._mss_loginDialogInstance = null;
        }
        
        // Show floating button when dialog closes
        if (window._mss_loginFloatingButton && window._mss_loginFloatingButton.button) {
            window._mss_loginFloatingButton.button.style.display = "flex";
        }
    }
    
    // Expose dialog class globally for floating button and other extensions
    static expose() {
        window.mss_loginDialog = mss_loginDialog;
        console.log("[mss-login] mss_loginDialog exposed to window.mss_loginDialog");
    }

renderUsers(list, container) {
    const currentName = currentUser?.username || null;
    const self = this;
    const isOwnerCaller = Array.isArray(currentUser?.groups)
        && currentUser.groups.map(g => String(g).toLowerCase()).includes("owner");
    const createRoles = GROUPS.filter(g => g !== "owner");

    let html = "";
    if (isOwnerCaller) {
        html += `
        <div class="mss-login-section" style="margin-bottom:20px;">
            <h3>Register user</h3>
            <p class="mss-login-note">Owner only. Assign a role at creation. To move ownership, use Transfer ownership below (requires confirmation).</p>
            <div class="mss-login-row" style="gap:12px; flex-wrap:wrap; align-items:flex-end;">
                <div>
                    <label class="mss-login-field-label">Username</label>
                    <input type="text" id="mss-login-create-username" class="mss-login-input" autocomplete="off" style="min-width:160px;">
                </div>
                <div>
                    <label class="mss-login-field-label">Password</label>
                    <input type="password" id="mss-login-create-password" class="mss-login-input" autocomplete="new-password" style="min-width:160px;">
                </div>
                <div>
                    <label class="mss-login-field-label">Role</label>
                    <select id="mss-login-create-role" class="mss-login-input" style="min-width:120px;">
                        ${createRoles.map(g => `<option value="${g}" ${g === "user" ? "selected" : ""}>${g.toUpperCase()}</option>`).join("")}
                    </select>
                </div>
                <button type="button" class="mss-login-btn" id="mss-login-create-user-btn">Create user</button>
            </div>
            <p id="mss-login-create-user-status" class="mss-login-note" style="margin-top:8px;"></p>
        </div>`;
    }

    html += `
        <table class="mss-login-table">
            <thead>
                <tr>
                    <th>User Account</th>
                    <th>Assigned Group</th>
                    <th style="text-align:center;width:120px;">SFW Check</th>
                    <th style="text-align:right;width:180px;">Actions</th>
                </tr>
            </thead>
            <tbody>
    `;

    list.forEach(u => {
        const grp = (u.groups && u.groups.length) ? u.groups[0] : "user";
        const uname = u.username || "unknown";
        const isSelf = currentName && uname === currentName;
        const isGuest = uname.toLowerCase() === "guest";
        const isOwner = grp === "owner";

        // NEW: per-user SFW flag; default ON if undefined
        const sfwEnabled = (u.sfw_check !== false);

        let actionsHtml = `
            <button class="mss-login-btn btn-save" data-user="${uname}" data-is-owner="${isOwner}">
                Save Changes
            </button>
        `;

        // Don't allow deleting yourself or the guest account
        if (!isSelf && !isGuest) {
            actionsHtml += `
                <button class="mss-login-btn mss-login-btn-danger btn-delete" data-user="${uname}">
                    Delete
                </button>
            `;
        }

        const groupCell = isOwner
            ? `<span class="mss-login-owner-locked" title="Owner role cannot be changed via Save Changes">Owner (locked)</span>`
            : `<select
                        class="mss-login-role-select"
                        data-user="${uname}"
                        style="background:var(--comfy-input-bg); color:var(--input-text); border:1px solid #555; padding:6px 10px; border-radius:4px; width: 150px;"
                    >
                        ${ASSIGNABLE_GROUPS.map(g => `
                            <option value="${g}" ${g === grp ? "selected" : ""}>
                                ${g.toUpperCase()}
                            </option>
                        `).join("")}
                    </select>`;

        if (!isOwner && isOwnerCaller && !isGuest) {
            actionsHtml += `
                <button class="mss-login-btn btn-transfer-owner" data-user="${uname}" title="Transfer ownership to this user">
                    Transfer ownership
                </button>
            `;
        }

        html += `
            <tr>
                <td><strong>${uname}</strong></td>
                <td>
                    ${groupCell}
                </td>
                <td style="text-align:center">
                    <input
                        type="checkbox"
                        class="mss-login-sfw-toggle"
                        data-user="${uname}"
                        ${sfwEnabled ? "checked" : ""}
                    />
                </td>
                <td style="text-align:right">
                    ${actionsHtml}
                </td>
            </tr>
        `;
    });

    html += `</tbody></table>`;
    container.innerHTML = html;

    const createBtn = container.querySelector("#mss-login-create-user-btn");
    if (createBtn) {
        createBtn.onclick = async () => {
            const statusEl = container.querySelector("#mss-login-create-user-status");
            const username = (container.querySelector("#mss-login-create-username")?.value || "").trim();
            const password = container.querySelector("#mss-login-create-password")?.value || "";
            const role = container.querySelector("#mss-login-create-role")?.value || "user";
            if (statusEl) statusEl.textContent = "Creating...";
            try {
                const res = await api.fetchApi("/mss-login/api/users", {
                    method: "POST",
                    body: JSON.stringify({ username, password, role }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    if (statusEl) statusEl.textContent = data.error || `Error ${res.status}`;
                    return;
                }
                if (statusEl) statusEl.textContent = `Created ${data.username || username} as ${data.role || role}.`;
                const usersData = await getData("/mss-login/api/users");
                self.renderUsers(usersData?.users || [], container);
            } catch (e) {
                if (statusEl) statusEl.textContent = e.message || "Request failed";
            }
        };
    }

    // --- Save handler per user ---
    container.querySelectorAll(".btn-save[data-user]").forEach(btn => {
        btn.onclick = async () => {
            const u = btn.dataset.user;
            const isOwnerUser = btn.dataset.isOwner === "true";
            // Owner must retain admin so privilege checks and registration bootstrap stay correct.
            // Never send "owner" from the role dropdown — ownership uses Transfer ownership.
            let role = container.querySelector(`select[data-user="${u}"]`)?.value || "user";
            if (role === "owner") role = "admin";
            const g = isOwnerUser ? ["owner", "admin"] : [role];

            const sfwCheckbox = container.querySelector(`.mss-login-sfw-toggle[data-user="${u}"]`);
            const sfw = sfwCheckbox ? sfwCheckbox.checked : true;

            btn.innerText = "Saving...";
            try {
                const res = await api.fetchApi(`/mss-login/api/users/${u}`, {
                    method: "PUT",
                    body: JSON.stringify({
                        groups: g,
                        sfw_check: sfw,
                    }),
                });
                if (!res.ok) {
                    btn.innerText = "Error";
                } else {
                    btn.innerText = "Saved";
                }
            } catch (e) {
                console.error("[mss-login] Failed to update user:", e);
                btn.innerText = "Error";
            }
            setTimeout(() => (btn.innerText = "Save Changes"), 1000);
        };
    });

    // --- Explicit ownership transfer (requires typed confirmation + API confirm_transfer) ---
    container.querySelectorAll(".btn-transfer-owner[data-user]").forEach(btn => {
        btn.onclick = async () => {
            const u = btn.dataset.user;
            const typed = window.prompt(
                `Transfer ownership to "${u}"?\n\nYou will become an admin and lose the owner role.\nType the username exactly to confirm:`,
                ""
            );
            if (typed !== u) {
                if (typed !== null) {
                    window.alert("Transfer cancelled: username did not match.");
                }
                return;
            }
            const originalText = btn.innerText;
            btn.disabled = true;
            btn.innerText = "Transferring...";
            try {
                const res = await api.fetchApi(`/mss-login/api/users/${u}`, {
                    method: "PUT",
                    body: JSON.stringify({
                        groups: ["owner", "admin"],
                        confirm_transfer: true,
                    }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    window.alert(data.error || `Transfer failed (${res.status})`);
                    btn.disabled = false;
                    btn.innerText = originalText;
                    return;
                }
                window.alert(`Ownership transferred to "${u}". Reload recommended.`);
                const usersData = await getData("/mss-login/api/users");
                self.renderUsers(usersData?.users || [], container);
            } catch (e) {
                console.error("[mss-login] Ownership transfer failed:", e);
                window.alert(e.message || "Transfer failed");
                btn.disabled = false;
                btn.innerText = originalText;
            }
        };
    });

    // --- Delete handler per user (unchanged logic) ---
    container.querySelectorAll(".btn-delete").forEach(btn => {
        btn.onclick = async () => {
            const u = btn.dataset.user;
            const confirmed = window.confirm(
                `Are you sure you want to delete the user "${u}"?\nThis cannot be undone.`
            );
            if (!confirmed) return;

            btn.disabled = true;
            const originalText = btn.innerText;
            btn.innerText = "Deleting...";

            try {
                const res = await api.fetchApi(`/mss-login/api/users/${u}`, {
                    method: "DELETE",
                });

                if (res.status === 200) {
                    const usersData = await getData("/mss-login/api/users");
                    const usersList = usersData?.users || [];
                    self.renderUsers(usersList, container);
                } else {
                    let msg = "Failed to delete user.";
                    try {
                        const err = await res.json();
                        if (err && err.error) msg = err.error;
                    } catch {}
                    window.alert(msg);
                    btn.disabled = false;
                    btn.innerText = originalText;
                }
            } catch (e) {
                console.error("[mss-login] Failed to delete user:", e);
                window.alert("Unexpected error while deleting user.");
                btn.disabled = false;
                btn.innerText = originalText;
            }
        };
    });
}

        async renderIpRules(container) {
        container.innerHTML = `
            <div class="mss-login-section">
                <h3>IP Whitelist & Blacklist</h3>
                <p>
                    Configure IP-based access rules. Whitelisted IPs are always allowed,
                    blacklisted IPs are always denied (before other checks).
                    Blacklist entries can be permanent or temporary (e.g. 24h); auto-bans from failed logins expire by default.
                </p>
                <div class="mss-login-row">
                    <div>
                        <label class="mss-login-field-label">
                            Whitelist (one IP or CIDR per line)
                        </label>
                        <textarea class="mss-login-textarea" id="mss-login-ip-whitelist"></textarea>
                    </div>
                    <div style="flex:1;">
                        <label class="mss-login-field-label">Blacklist</label>
                        <div id="mss-login-ip-blacklist-entries" class="mss-login-ip-list"></div>
                        <div class="mss-login-row" style="margin-top:8px; gap:8px; align-items:center; flex-wrap:wrap;">
                            <input type="text" id="mss-login-ip-blacklist-add" class="mss-login-input" placeholder="IP or CIDR" style="min-width:140px;">
                            <label class="mss-login-field-label" style="margin:0;">Type</label>
                            <select id="mss-login-ip-blacklist-type" class="mss-login-select" style="width:auto;">
                                <option value="permanent">Permanent ban</option>
                                <option value="24">Temporary (24h)</option>
                                <option value="1">Temporary (1h)</option>
                                <option value="168">Temporary (7 days)</option>
                            </select>
                            <button type="button" class="mss-login-btn secondary" id="mss-login-ip-blacklist-add-btn">Add</button>
                        </div>
                    </div>
                </div>
                <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:8px;">
                    <button class="mss-login-btn secondary" id="mss-login-ip-refresh">Reload</button>
                    <button class="mss-login-btn" id="mss-login-ip-save">Save Rules</button>
                </div>

                <div id="mss-login-owner-local-cidrs-section" style="display:none; margin-top:24px; border-top:1px solid rgba(255,255,255,0.12); padding-top:16px;">
                    <h3>Trusted Local & Tailscale Networks (Owner Only)</h3>
                    <p class="mss-login-note" style="margin-bottom:8px;">
                        Tailscale ranges (<code>100.64.0.0/10</code>, <code>fd7a:115c:a1e0::/48</code>) and private subnets (<code>192.168.0.0/16</code>, <code>10.0.0.0/8</code>, <code>172.16.0.0/12</code>, <code>127.0.0.0/8</code>) are automatically trusted. Owners can specify additional local IP addresses or CIDR blocks below.
                    </p>
                    <label class="mss-login-field-label">Custom Local CIDRs or IPs (one per line)</label>
                    <textarea class="mss-login-textarea" id="mss-login-local-cidrs" placeholder="192.168.2.0/24&#10;10.50.0.0/16" style="min-height:80px;"></textarea>
                    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:8px;">
                        <button type="button" class="mss-login-btn" id="mss-login-local-cidrs-save">Save Local CIDRs</button>
                    </div>
                </div>
            </div>
        `;


        const wlEl = container.querySelector("#mss-login-ip-whitelist");
        const blEntriesEl = container.querySelector("#mss-login-ip-blacklist-entries");
        const blAddEl = container.querySelector("#mss-login-ip-blacklist-add");
        const blTypeEl = container.querySelector("#mss-login-ip-blacklist-type");
        const blAddBtn = container.querySelector("#mss-login-ip-blacklist-add-btn");
        const refreshBtn = container.querySelector("#mss-login-ip-refresh");
        const saveBtn = container.querySelector("#mss-login-ip-save");

        let blacklistEntries = [];

        function formatExpiresAt(entry) {
            if (entry.permanent) return "Permanent";
            if (entry.expires_at != null) {
                const ts = typeof entry.expires_at === "number" ? entry.expires_at : (new Date(entry.expires_at).getTime() / 1000);
                const d = new Date(ts * 1000);
                return "Expires at " + d.toLocaleString();
            }
            if (entry.expires_in_hours != null)
                return "Temporary (" + entry.expires_in_hours + "h)";
            return "Temporary";
        }

        function renderBlacklistEntries() {
            blEntriesEl.innerHTML = blacklistEntries.map((entry, idx) => {
                const label = formatExpiresAt(entry);
                return `<div class="mss-login-row" style="align-items:center; gap:8px; margin-bottom:4px;">
                    <span class="mss-login-ip-entry" data-idx="${idx}" style="flex:1; font-family:monospace;">${escapeHtml(entry.ip)}</span>
                    <span class="mss-login-badge" style="font-size:11px; padding:2px 6px; border-radius:4px; background:var(--input-bg, #333); color:var(--input-text, #eee);">${escapeHtml(label)}</span>
                    <button type="button" class="mss-login-btn secondary" data-idx="${idx}" data-action="remove" style="padding:2px 8px;">Remove</button>
                </div>`;
            }).join("") || "<p class=\"mss-login-note\">No blacklist entries. Add an IP above and click Save.</p>";
            blEntriesEl.querySelectorAll("[data-action=remove]").forEach(btn => {
                btn.onclick = () => {
                    const idx = parseInt(btn.getAttribute("data-idx"), 10);
                    blacklistEntries.splice(idx, 1);
                    renderBlacklistEntries();
                };
            });
        }

        async function loadIpConfig() {
            const data = await getData(IP_API_ENDPOINT);
            const whitelist = (data?.whitelist || []);
            wlEl.value = Array.isArray(whitelist) ? whitelist.join("\n") : String(whitelist);
            const bl = (data?.blacklist || []);
            blacklistEntries = bl.map(item => {
                if (typeof item === "string") return { ip: item, permanent: true };
                return {
                    ip: item.ip || "",
                    permanent: item.permanent !== false && item.expires_at == null,
                    expires_at: item.expires_at ?? null,
                };
            }).filter(e => e.ip);
            renderBlacklistEntries();
        }

        await loadIpConfig();

        refreshBtn.onclick = () => loadIpConfig();

        blAddBtn.onclick = () => {
            const ip = (blAddEl.value || "").trim();
            if (!ip) return;
            const typeVal = blTypeEl.value;
            const permanent = typeVal === "permanent";
            const expiresInHours = permanent ? null : parseFloat(typeVal, 10);
            blacklistEntries.push({
                ip,
                permanent,
                expires_in_hours: expiresInHours,
            });
            blAddEl.value = "";
            renderBlacklistEntries();
        };

        saveBtn.onclick = async () => {
            const whitelist = wlEl.value
                .split(/\r?\n/)
                .map(l => l.trim())
                .filter(l => l.length > 0);
            const nowHours = Date.now() / 3600000;
            const blacklist = blacklistEntries.map(e => {
                if (e.permanent || (e.expires_in_hours == null && e.expires_at == null))
                    return { ip: e.ip, permanent: true };
                let hours = e.expires_in_hours;
                if (hours == null && e.expires_at != null) {
                    const ts = typeof e.expires_at === "number" ? e.expires_at : (new Date(e.expires_at).getTime() / 1000);
                    hours = Math.max(0, (ts - Date.now() / 1000) / 3600);
                }
                return { ip: e.ip, permanent: false, expires_in_hours: hours };
            });

            saveBtn.disabled = true;
            saveBtn.textContent = "Saving...";
            try {
                await api.fetchApi(IP_API_ENDPOINT, {
                    method: "PUT",
                    body: JSON.stringify({ whitelist, blacklist }),
                });
                saveBtn.textContent = "Saved";
                setTimeout(() => (saveBtn.textContent = "Save Rules"), 1200);
                await loadIpConfig();
            } catch (e) {
                console.error("[mss-login] Failed to save IP rules:", e);
                saveBtn.textContent = "Error";
                setTimeout(() => (saveBtn.textContent = "Save Rules"), 1500);
            } finally {
                saveBtn.disabled = false;
            }
        };

        // Owner-only Local CIDRs configuration
        const isOwner = currentUser && (currentUser.role === "owner" || (Array.isArray(currentUser.groups) && currentUser.groups.map(g => String(g).toLowerCase()).includes("owner")));
        const ownerCidrsSection = container.querySelector("#mss-login-owner-local-cidrs-section");
        const localCidrsEl = container.querySelector("#mss-login-local-cidrs");
        const saveLocalCidrsBtn = container.querySelector("#mss-login-local-cidrs-save");

        if (isOwner && ownerCidrsSection && localCidrsEl && saveLocalCidrsBtn) {
            ownerCidrsSection.style.display = "block";
            try {
                const cidrsData = await getData("/mss-login/api/settings/local-cidrs");
                if (cidrsData && Array.isArray(cidrsData.local_network_cidrs)) {
                    localCidrsEl.value = cidrsData.local_network_cidrs.join("\n");
                }
            } catch (_) {}

            saveLocalCidrsBtn.onclick = async () => {
                const lines = localCidrsEl.value
                    .split(/\r?\n/)
                    .map(l => l.trim())
                    .filter(l => l.length > 0);
                saveLocalCidrsBtn.disabled = true;
                saveLocalCidrsBtn.textContent = "Saving...";
                try {
                    const res = await api.fetchApi("/mss-login/api/settings/local-cidrs", {
                        method: "PUT",
                        body: JSON.stringify({ local_network_cidrs: lines })
                    });
                    if (res && res.ok) {
                        saveLocalCidrsBtn.textContent = "Saved";
                        if (window.showToast) window.showToast("Local CIDRs saved.");
                    } else {
                        const err = await res.json().catch(() => ({}));
                        if (window.showToast) window.showToast(err.error || "Save failed");
                        saveLocalCidrsBtn.textContent = "Error";
                    }
                } catch (e) {
                    if (window.showToast) window.showToast("Save failed: " + (e.message || "Error"));
                    saveLocalCidrsBtn.textContent = "Error";
                } finally {
                    setTimeout(() => {
                        saveLocalCidrsBtn.disabled = false;
                        saveLocalCidrsBtn.textContent = "Save Local CIDRs";
                    }, 1500);
                }
            };
        }
    }


renderUserEnv(container, usersList) {
    const users = usersList || [];

    const userOptions = users
        .map(u => {
            const name = u.username || "unknown";
            return `<option value="${name}">${name}</option>`;
        })
        .join("");

    container.innerHTML = `
        <div class="mss-login-section">
            <h3>User Environment & Folders</h3>
            <p>
                Manage per-user environment folders created by <code>user_env.py</code>.
                You can inspect files, purge cached folders, delete individual files,
                and mark a user's folder as the active Gallery root.
            </p>

            <div class="mss-login-row">
                <div>
                    <label class="mss-login-field-label">User</label>
                    <select id="mss-login-env-user" class="mss-login-select">
                        ${userOptions}
                    </select>
                </div>
                <div style="display:flex; align-items:flex-end; gap:8px; justify-content:flex-end;">
                    <button class="mss-login-btn secondary" id="mss-login-env-list">List Files</button>
                    <button class="mss-login-btn danger" id="mss-login-env-purge">Purge Folders</button>
                </div>
            </div>

            <div class="mss-login-row" style="align-items:center; margin-top:4px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <input type="checkbox" id="mss-login-env-gallery-toggle" />
                    <label for="mss-login-env-gallery-toggle">
                        Use this user's folder as Gallery root
                    </label>
                </div>
            </div>

            <div style="margin-top:12px;">
                <label class="mss-login-field-label">Folder Contents / Status</label>
                <textarea id="mss-login-env-output" class="mss-login-textarea" readonly></textarea>
            </div>

            <div class="mss-login-row" style="margin-top:8px; align-items:flex-end; gap:8px;">
                <div style="flex:1;">
                    <label class="mss-login-field-label">Delete Single File</label>
                    <select id="mss-login-env-file" class="mss-login-select">
                        <option value="">(no files loaded yet)</option>
                    </select>
                </div>
                <button class="mss-login-btn danger" id="mss-login-env-delete">Delete File</button>
            </div>
        </div>

        <div class="mss-login-section" style="margin-top:16px;">
            <h3>Workflow Management</h3>
            <p>
                Promote a user's workflow into the global/default workflow list
                so it becomes visible to all users.
            </p>

            <div class="mss-login-row">
                <div>
                    <label class="mss-login-field-label">User</label>
                    <select id="mss-login-wf-user" class="mss-login-select">
                        ${userOptions}
                    </select>
                </div>
                <div style="flex:1;">
                    <label class="mss-login-field-label">Workflow</label>
                    <select id="mss-login-wf-select" class="mss-login-select">
                        <option value="">(load workflows...)</option>
                    </select>
                </div>
                <div style="display:flex; align-items:flex-end; gap:8px;">
                    <button class="mss-login-btn secondary" id="mss-login-wf-load">Load Workflows</button>
                    <button class="mss-login-btn primary" id="mss-login-wf-promote">Promote to Default</button>
                </div>
            </div>
            <div style="margin-top:6px; display:flex; align-items:center; gap:8px;">
                <input type="checkbox" id="mss-login-wf-delete-source" />
                <label for="mss-login-wf-delete-source">
                    Remove from this user's workflow folder after promotion
                </label>
            </div>
            <div style="margin-top:6px;">
                <small id="mss-login-wf-status" class="mss-login-muted"></small>
            </div>
        </div>
    `;

    const userSelect = container.querySelector("#mss-login-env-user");
    const listBtn = container.querySelector("#mss-login-env-list");
    const purgeBtn = container.querySelector("#mss-login-env-purge");
    const galleryToggle = container.querySelector("#mss-login-env-gallery-toggle");
    const output = container.querySelector("#mss-login-env-output");
    const fileSelect = container.querySelector("#mss-login-env-file");
    const deleteBtn = container.querySelector("#mss-login-env-delete");

    const wfUserSelect = container.querySelector("#mss-login-wf-user");
    const wfSelect = container.querySelector("#mss-login-wf-select");
    const wfLoadBtn = container.querySelector("#mss-login-wf-load");
    const wfPromoteBtn = container.querySelector("#mss-login-wf-promote");
    const wfDeleteSource = container.querySelector("#mss-login-wf-delete-source");
    const wfStatus = container.querySelector("#mss-login-wf-status");

    let envFiles = [];

    function getSelectedUser() {
        return userSelect?.value || null;
    }

    function getWorkflowUser() {
        return wfUserSelect?.value || getSelectedUser() || null;
    }

    function populateEnvFileOptions(files) {
        envFiles = files || [];
        if (!fileSelect) return;

        fileSelect.innerHTML = "";

        if (!envFiles.length) {
            fileSelect.innerHTML = `<option value="">(no files)</option>`;
            return;
        }

        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "(select a file...)";
        fileSelect.appendChild(placeholder);

        envFiles.forEach(path => {
            const opt = document.createElement("option");
            opt.value = path;
            opt.textContent = path;
            fileSelect.appendChild(opt);
        });
    }

    async function refreshStatus() {
        const user = getSelectedUser();
        if (!user) return;
        output.value = "Loading status...";
        try {
            const res = await api.fetchApi(USER_ENV_API_ENDPOINT, {
                method: "POST",
                body: JSON.stringify({ action: "status", user }),
            });
            if (res.status === 200) {
                const data = await res.json();
                galleryToggle.checked = !!data.is_gallery_root;
                const files = data.files || [];
                populateEnvFileOptions(files);
                output.value =
                    (data.message || "") +
                    (files.length
                        ? "\n\nFiles:\n" + files.join("\n")
                        : files.length === 0
                        ? "\n\n(no files reported)"
                        : "");
            } else {
                output.value = "Error loading status: " + res.status;
            }
        } catch (e) {
            console.error("[mss-login] env status error:", e);
            output.value = "Error loading status. See console.";
        }
    }

    userSelect.onchange = () => {
        if (wfUserSelect) wfUserSelect.value = userSelect.value;
        refreshStatus();
    };

    listBtn.onclick = async () => {
        const user = getSelectedUser();
        if (!user) return;
        output.value = "Listing files...";
        try {
            const res = await api.fetchApi(USER_ENV_API_ENDPOINT, {
                method: "POST",
                body: JSON.stringify({ action: "list", user }),
            });
            if (res.status === 200) {
                const data = await res.json();
                const files = data.files || [];
                populateEnvFileOptions(files);
                output.value = files.length
                    ? files.join("\n")
                    : "(no files found)";
            } else {
                output.value = "Error listing files: " + res.status;
            }
        } catch (e) {
            console.error("[mss-login] env list error:", e);
            output.value = "Error listing files. See console.";
        }
    };

    deleteBtn.onclick = async () => {
        const user = getSelectedUser();
        const file = fileSelect?.value;
        if (!user || !file) return;
        output.value = `Deleting '${file}'...`;
        try {
            const res = await api.fetchApi(USER_ENV_API_ENDPOINT, {
                method: "POST",
                body: JSON.stringify({ action: "delete_file", user, file }),
            });
            const data = await res.json();
            if (res.status === 200) {
                output.value = data.message || `Deleted '${file}'.`;
            } else {
                output.value = data.error || `Error deleting file: ${res.status}`;
            }
        } catch (e) {
            console.error("[mss-login] env delete_file error:", e);
            output.value = "Error deleting file. See console.";
        } finally {
            refreshStatus();
        }
    };

    purgeBtn.onclick = async () => {
        const user = getSelectedUser();
        if (!user) return;
        purgeBtn.disabled = true;
        output.value = "Purging...";
        try {
            const res = await api.fetchApi(USER_ENV_API_ENDPOINT, {
                method: "POST",
                body: JSON.stringify({ action: "purge", user }),
            });
            if (res.status === 200) {
                const data = await res.json();
                output.value = data.message || "Purge completed.";
            } else {
                output.value = "Error purging folders: " + res.status;
            }
        } catch (e) {
            console.error("[mss-login] env purge error:", e);
            output.value = "Error purging folders. See console.";
        } finally {
            purgeBtn.disabled = false;
            refreshStatus();
        }
    };

    galleryToggle.onchange = async () => {
        const user = getSelectedUser();
        if (!user) return;
        const enable = galleryToggle.checked;

        try {
            const res = await api.fetchApi(USER_ENV_API_ENDPOINT, {
                method: "POST",
                body: JSON.stringify({
                    action: "set_gallery_root",
                    user,
                    enable,
                }),
            });
            if (res.status === 200) {
                const data = await res.json();
                output.value = data.message || "Gallery root updated.";
            } else {
                output.value = "Error updating gallery root: " + res.status;
            }
        } catch (e) {
            console.error("[mss-login] env gallery toggle error:", e);
            output.value = "Error updating gallery root. See console.";
        }
    };

    // --- Workflow admin handlers ---

    wfUserSelect.onchange = () => {
        wfStatus.textContent = "";
        wfSelect.innerHTML = '<option value="">(load workflows...)</option>';
    };

    wfLoadBtn.onclick = async () => {
        const user = getWorkflowUser();
        if (!user) return;
        wfStatus.textContent = "Loading workflows...";
        wfSelect.innerHTML = '<option value="">(loading...)</option>';

        try {
            const res = await api.fetchApi(USER_ENV_API_ENDPOINT, {
                method: "POST",
                body: JSON.stringify({
                    action: "list_workflows",
                    user,
                }),
            });
            if (res.status === 200) {
                const data = await res.json();
                const workflows = data.workflows || [];
                wfSelect.innerHTML = "";

                if (!workflows.length) {
                    wfSelect.innerHTML =
                        '<option value="">(no workflows)</option>';
                } else {
                    workflows.forEach((wf) => {
                        const opt = document.createElement("option");
                        opt.value = wf;
                        opt.textContent = wf;
                        wfSelect.appendChild(opt);
                    });
                }

                wfStatus.textContent = `Found ${workflows.length} workflow(s) for ${user}.`;
            } else {
                wfStatus.textContent =
                    "Error loading workflows: " + res.status;
            }
        } catch (e) {
            console.error("[mss-login] list_workflows error:", e);
            wfStatus.textContent =
                "Error loading workflows. See console.";
        }
    };

    wfPromoteBtn.onclick = async () => {
        const user = getWorkflowUser();
        const workflow = wfSelect?.value;
        if (!user || !workflow) return;
        const delete_source = !!(wfDeleteSource && wfDeleteSource.checked);
        wfStatus.textContent = `Promoting '${workflow}'...`;

        try {
            const res = await api.fetchApi(USER_ENV_API_ENDPOINT, {
                method: "POST",
                body: JSON.stringify({
                    action: "promote_workflow",
                    user,
                    workflow,
                    delete_source,
                }),
            });
            const data = await res.json();
            if (res.status === 200) {
                wfStatus.textContent =
                    data.message || "Workflow promoted to defaults.";
                // If we deleted the source, refresh the list
                if (delete_source) {
                    wfLoadBtn.onclick();
                }
            } else {
                wfStatus.textContent =
                    data.error ||
                    "Error promoting workflow: " + res.status;
            }
        } catch (e) {
            console.error("[mss-login] promote_workflow error:", e);
            wfStatus.textContent =
                "Error promoting workflow. See console.";
        }
    };

    // Initial sync + status
    if (userSelect && wfUserSelect) {
        wfUserSelect.value = userSelect.value;
    }
    if (users.length > 0) {
        refreshStatus();
    }
}

async renderSharedModels(container, usersList) {
    const users = (usersList || []).filter(u => (u.username || "").toLowerCase() !== "guest");
    const userOptions = users.map(u => `<option value="${escapeHtml(u.username || "")}">${escapeHtml(u.username || "")}</option>`).join("");
    container.innerHTML = `
        <div class="mss-login-section">
            <h3>Shared Models / LoRAs / VAEs / Embeddings</h3>
            <p>Grant specific users access to specific ComfyUI items. Users without "View all ComfyUI items" see only items shared here.</p>
            <div class="mss-login-row" style="margin-top:12px; align-items:center; gap:8px; flex-wrap:wrap;">
                <label class="mss-login-field-label" style="margin:0;">User:</label>
                <select id="mss-login-shared-user" style="background:var(--comfy-input-bg); color:var(--input-text); border:1px solid #555; padding:6px 10px; border-radius:4px; min-width:140px;">
                    <option value="">-- Select user --</option>
                    ${userOptions}
                </select>
            </div>
            <div id="mss-login-shared-items-list" style="margin-top:12px; min-height:60px;">
                <p style="opacity:0.8;">Select a user to view and manage their shared items.</p>
            </div>
            <div class="mss-login-section" style="margin-top:16px;">
                <h4 style="margin:0 0 8px 0;">Toggle model access</h4>
                <p style="opacity:0.9; font-size:13px; margin-bottom:8px;">Expand a folder and check or uncheck items to grant or revoke access. Users without "View all ComfyUI items" see only checked items.</p>
                <div class="mss-login-row" style="gap:8px; margin-bottom:8px;">
                    <button class="mss-login-btn" id="mss-login-toggle-refresh">Refresh folders</button>
                </div>
                <div id="mss-login-toggle-folders" style="max-height:400px; overflow-y:auto;"></div>
            </div>
        </div>
    `;
    const userSelect = container.querySelector("#mss-login-shared-user");
    const listEl = container.querySelector("#mss-login-shared-items-list");
    const toggleFoldersEl = container.querySelector("#mss-login-toggle-folders");
    const toggleRefreshBtn = container.querySelector("#mss-login-toggle-refresh");

    let folders = [];
    const sharedSet = new Set();
    const loadedItems = {};

    async function fetchSharedSet(username) {
        sharedSet.clear();
        if (!username) return;
        try {
            const res = await api.fetchApi("/mss-login/api/users/" + encodeURIComponent(username) + "/shared-items", { method: "GET" });
            const data = await res.json();
            const items = data.items || [];
            items.forEach(it => sharedSet.add((it.folder || "") + "|" + (it.item_name || "")));
        } catch (e) {
            console.error("[mss-login] Failed to load shared set:", e);
        }
    }

    function renderToggleFolders() {
        if (!toggleFoldersEl) return;
        const username = userSelect.value;
        if (!username) {
            toggleFoldersEl.innerHTML = "<p style=\"opacity:0.8;\">Select a user above to toggle model access.</p>";
            return;
        }
        if (folders.length === 0) {
            toggleFoldersEl.innerHTML = "<p style=\"opacity:0.8;\">No folders loaded. Click Refresh folders.</p>";
            return;
        }
        toggleFoldersEl.innerHTML = folders.map(folder => {
            const safeFolder = escapeHtml(folder);
            return `<div class="mss-login-toggle-folder" data-folder="${safeFolder}" style="margin:6px 0;">
                <div style="display:flex; align-items:stretch; border:1px solid #555; border-radius:8px; overflow:hidden;">
                    <button type="button" class="mss-login-folder-chevron" data-folder="${safeFolder}" aria-label="Expand ${safeFolder}" style="min-width:44px; min-height:44px; border:none; border-right:1px solid #555; background:rgba(255,255,255,0.04); color:inherit; cursor:pointer; font-size:16px;">▸</button>
                    <button type="button" class="mss-login-folder-label" data-folder="${safeFolder}" style="flex:1; text-align:left; padding:10px 12px; border:none; background:transparent; color:inherit; cursor:pointer;">${safeFolder}</button>
                </div>
                <div class="mss-login-toggle-items" data-folder="${safeFolder}" hidden style="padding:8px 8px 8px 12px; max-height:200px; overflow-y:auto; border:1px solid #444; border-top:none; border-radius:0 0 8px 8px;">Loading...</div>
            </div>`;
        }).join("");

        const openFolder = async (folder, chevronBtn, itemsEl) => {
            const isOpen = !itemsEl.hidden;
            if (isOpen) {
                itemsEl.hidden = true;
                if (chevronBtn) chevronBtn.textContent = "▸";
                return;
            }
            itemsEl.hidden = false;
            if (chevronBtn) chevronBtn.textContent = "▾";
            if (itemsEl.dataset.loaded === "1") return;
            itemsEl.textContent = "Loading...";
            try {
                let res = await api.fetchApi("/mss-login/api/model-cache/folders/" + encodeURIComponent(folder) + "/items", { method: "GET" });
                if (!res.ok) res = await api.fetchApi("/mss-login/api/available-models/" + encodeURIComponent(folder), { method: "GET" });
                const data = await res.json();
                const items = data.items || [];
                loadedItems[folder] = items;
                itemsEl.dataset.loaded = "1";
                itemsEl.innerHTML = items.map(itemName => {
                    const key = folder + "|" + itemName;
                    const checked = sharedSet.has(key);
                    const safeItem = escapeHtml(itemName);
                    return `<label class="mss-login-toggle-item" style="display:block; margin:4px 0;"><input type="checkbox" class="mss-login-toggle-chk" data-folder="${escapeHtml(folder)}" data-item="${safeItem}" ${checked ? "checked" : ""}> ${safeItem}</label>`;
                }).join("");
                itemsEl.querySelectorAll(".mss-login-toggle-chk").forEach(chk => {
                    chk.onchange = async () => {
                        const f = chk.dataset.folder;
                        const item = chk.dataset.item;
                        const key = f + "|" + item;
                        const add = chk.checked;
                        try {
                            if (add) {
                                await api.fetchApi("/mss-login/api/users/" + encodeURIComponent(username) + "/shared-items", {
                                    method: "POST",
                                    body: JSON.stringify({ folder: f, item_name: item }),
                                });
                                sharedSet.add(key);
                            } else {
                                await api.fetchApi("/mss-login/api/users/" + encodeURIComponent(username) + "/shared-items", {
                                    method: "DELETE",
                                    body: JSON.stringify({ folder: f, item_name: item }),
                                });
                                sharedSet.delete(key);
                            }
                            await refreshSharedList();
                        } catch (e) {
                            console.error("[mss-login] Toggle shared item failed:", e);
                            chk.checked = !add;
                        }
                    };
                });
            } catch (e) {
                itemsEl.textContent = "Error loading items.";
                console.error("[mss-login] Failed to load items for folder:", e);
            }
        };

        toggleFoldersEl.querySelectorAll(".mss-login-folder-chevron, .mss-login-folder-label").forEach(btn => {
            btn.addEventListener("click", (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                const folder = btn.dataset.folder;
                const root = btn.closest(".mss-login-toggle-folder");
                const itemsEl = root && root.querySelector(".mss-login-toggle-items");
                const chevronBtn = root && root.querySelector(".mss-login-folder-chevron");
                if (folder && itemsEl) openFolder(folder, chevronBtn, itemsEl);
            });
            btn.addEventListener("mousedown", (ev) => ev.stopPropagation());
        });
    }
    try {
        let fr = await api.fetchApi("/mss-login/api/model-cache/folders", { method: "GET" });
        let fd = await fr.json();
        folders = fd.folders || [];
        if (folders.length === 0) {
            fr = await api.fetchApi("/mss-login/api/available-model-folders", { method: "GET" });
            fd = await fr.json();
            folders = fd.folders || [];
        }
    } catch (e) {
        console.error("[mss-login] Failed to load folders:", e);
        folders = ["checkpoints", "loras", "vae", "embeddings"];
    }

    async function refreshSharedList() {
        const username = userSelect.value;
        if (!username) {
            listEl.innerHTML = "<p style=\"opacity:0.8;\">Select a user to view and manage their shared items.</p>";
            return;
        }
        listEl.innerHTML = "<p>Loading...</p>";
        try {
            const res = await api.fetchApi("/mss-login/api/users/" + encodeURIComponent(username) + "/shared-items", { method: "GET" });
            const data = await res.json();
            const items = data.items || [];
            if (items.length === 0) {
                listEl.innerHTML = "<p style=\"opacity:0.8;\">No shared items for this user. Use the toggle tree below to grant access.</p>";
                return;
            }
            listEl.innerHTML = `
                <table class="mss-login-table">
                    <thead><tr><th>Folder</th><th>Item</th><th style="width:80px;\"></th></tr></thead>
                    <tbody>
                    ${items.map(it => `
                        <tr>
                            <td>${escapeHtml(it.folder)}</td>
                            <td>${escapeHtml(it.item_name)}</td>
                            <td><button class="mss-login-btn mss-login-btn-danger mss-login-shared-remove" data-folder="${escapeHtml(it.folder)}" data-item="${escapeHtml(it.item_name)}">Remove</button></td>
                        </tr>
                    `).join("")}
                    </tbody>
                </table>
            `;
            listEl.querySelectorAll(".mss-login-shared-remove").forEach(btn => {
                btn.onclick = async () => {
                    const folder = btn.dataset.folder;
                    const item = btn.dataset.item;
                    try {
                        await api.fetchApi("/mss-login/api/users/" + encodeURIComponent(username) + "/shared-items", {
                            method: "DELETE",
                            body: JSON.stringify({ folder, item_name: item }),
                        });
                        sharedSet.delete(folder + "|" + item);
                        await refreshSharedList();
                        const remChk = toggleFoldersEl ? Array.from(toggleFoldersEl.querySelectorAll(".mss-login-toggle-chk")).find(c => c.dataset.folder === folder && c.dataset.item === item) : null;
                        if (remChk) remChk.checked = false;
                    } catch (e) {
                        console.error("[mss-login] Remove shared item failed:", e);
                    }
                };
            });
        } catch (e) {
            listEl.innerHTML = "<p style=\"color:var(--error-text,red);\">Failed to load shared items.</p>";
        }
    }

    userSelect.onchange = async () => {
        await fetchSharedSet(userSelect.value);
        await refreshSharedList();
        renderToggleFolders();
    };

    toggleRefreshBtn.onclick = async () => {
        toggleRefreshBtn.disabled = true;
        try {
            const refreshRes = await api.fetchApi("/mss-login/api/model-cache/refresh", { method: "POST" });
            if (refreshRes.ok) {
                const refreshData = await refreshRes.json();
                folders = refreshData.folders || [];
            }
            if (folders.length === 0) {
                const fr = await api.fetchApi("/mss-login/api/available-model-folders", { method: "GET" });
                const fd = await fr.json();
                folders = fd.folders || [];
            }
            Object.keys(loadedItems).forEach(k => delete loadedItems[k]);
            renderToggleFolders();
        } catch (e) {
            console.error("[mss-login] Failed to refresh folders:", e);
        }
        toggleRefreshBtn.disabled = false;
    };

    await fetchSharedSet(userSelect.value);
    renderToggleFolders();
}

renderNsfwManagement(container) {
    container.innerHTML = `
        <div class="mss-login-section">
            <h3>NSFW Content Management</h3>
            <p>
                Manage NSFW detection and scanning for images in the output directory.
                Use these tools to scan, fix, or clear NSFW tags from images.
            </p>

            <div class="mss-login-row" style="margin-top:16px; gap:8px; flex-wrap:wrap;">
                <button class="mss-login-btn" id="mss-login-nsfw-scan-new">
                    Scan New Images
                </button>
                <button class="mss-login-btn" id="mss-login-nsfw-scan-all">
                    Force Rescan All Images
                </button>
                <button class="mss-login-btn secondary" id="mss-login-nsfw-fix">
                    Fix Incorrect Tags
                </button>
                <button class="mss-login-btn danger" id="mss-login-nsfw-clear">
                    Clear All Tags
                </button>
            </div>

            <div style="margin-top:12px;">
                <label class="mss-login-field-label">Operation Status / Results</label>
                <textarea id="mss-login-nsfw-output" class="mss-login-textarea" readonly style="min-height:120px;"></textarea>
            </div>

            <div style="margin-top:12px; padding:12px; background:rgba(255,255,255,0.05); border-radius:6px;">
                <h4 style="margin:0 0 8px 0; font-size:14px;">About NSFW Scanning</h4>
                <ul style="margin:0; padding-left:20px; font-size:13px; opacity:0.9;">
                    <li><strong>Scan New Images:</strong> Only scans images that don't have NSFW tags yet.</li>
                    <li><strong>Force Rescan All:</strong> Clears all tags and rescans every image (slow, but thorough).</li>
                    <li><strong>Fix Incorrect Tags:</strong> Removes tags from images incorrectly marked as NSFW.</li>
                    <li><strong>Clear All Tags:</strong> Removes all NSFW metadata from images (forces rescan on next access).</li>
                </ul>
            </div>
        </div>
    `;

    const scanNewBtn = container.querySelector("#mss-login-nsfw-scan-new");
    const scanAllBtn = container.querySelector("#mss-login-nsfw-scan-all");
    const fixBtn = container.querySelector("#mss-login-nsfw-fix");
    const clearBtn = container.querySelector("#mss-login-nsfw-clear");
    const output = container.querySelector("#mss-login-nsfw-output");

    async function executeAction(action, params = {}) {
        const btnMap = {
            "scan_all": scanAllBtn,
            "fix_incorrect": fixBtn,
            "clear_all_tags": clearBtn
        };
        const btn = btnMap[action] || scanNewBtn;
        
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Processing...";
        output.value = `Executing ${action}...\n`;

        try {
            const body = { action, ...params };
            if (action === "scan_all") {
                body.force_rescan = false;
            }
            
            const res = await api.fetchApi("/mss-login/api/nsfw-management", {
                method: "POST",
                body: JSON.stringify(body),
            });

            if (res.status === 200) {
                const data = await res.json();
                output.value = data.message || "Operation completed successfully.";
                if (data.stats) {
                    output.value += `\n\nStats:\n`;
                    output.value += `  - Scanned: ${data.stats.scanned || 0}\n`;
                    output.value += `  - NSFW Found: ${data.stats.nsfw_found || 0}\n`;
                    output.value += `  - Errors: ${data.stats.errors || 0}\n`;
                    output.value += `  - Total Images: ${data.stats.total_images || 0}`;
                }
                if (data.fixed_count !== undefined) {
                    output.value += `\n\nFixed ${data.fixed_count} images.`;
                }
            } else {
                const error = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
                output.value = `Error: ${error.error || `HTTP ${res.status}`}`;
            }
        } catch (e) {
            console.error("[mss-login] NSFW management error:", e);
            output.value = `Error: ${e.message || "See console for details."}`;
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }

    scanNewBtn.onclick = () => executeAction("scan_all", { force_rescan: false });
    scanAllBtn.onclick = () => executeAction("scan_all", { force_rescan: true });
    fixBtn.onclick = () => executeAction("fix_incorrect");
    clearBtn.onclick = () => {
        if (window.confirm("Are you sure you want to clear ALL NSFW tags from all images? This cannot be undone.")) {
            executeAction("clear_all_tags");
        }
    };
}

async renderTokenStorage(container) {
    let cfg = { backend: "sqlite", json_path: "data/api_tokens.json", sqlite_path: "data/mss_login_data.db", postgres_host: "localhost", postgres_port: 5432, postgres_database: "mss_login", postgres_user: "mss_login", mysql_host: "localhost", mysql_port: 3306, mysql_database: "mss_login", mysql_user: "mss_login" };
    try {
        const res = await api.fetchApi("/mss-login/api/token-storage-config", { method: "GET" });
        if (res.ok) {
            cfg = await res.json();
        }
    } catch (e) {
        console.warn("[mss-login] Token storage config load failed:", e);
    }
    const backend = (cfg.backend || "sqlite").toLowerCase();
    container.innerHTML = `
        <div class="mss-login-section">
            <h3>API Token Storage</h3>
            <p>One database is used for both user accounts and API tokens (same as Users Database). Choose SQLite for a single local file, or PostgreSQL/MySQL for a shared server. Passwords: <code>USERS_DB_PASSWORD</code>, <code>POSTGRES_PASSWORD</code>, or <code>MYSQL_PASSWORD</code> in environment only.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:8px; flex-wrap:wrap; align-items:center;">
                <label class="mss-login-field-label">Backend</label>
                <select id="mss-login-token-backend" class="mss-login-select">
                    <option value="sqlite" ${backend === "sqlite" ? "selected" : ""}>SQLite</option>
                    <option value="postgresql" ${backend === "postgresql" ? "selected" : ""}>PostgreSQL</option>
                    <option value="mysql" ${backend === "mysql" ? "selected" : ""}>MySQL</option>
                </select>
            </div>
            <div id="mss-login-token-sqlite-fields" class="mss-login-row" style="margin-top:8px; gap:8px; align-items:center; ${backend !== "sqlite" ? "display:none;" : ""}">
                <label class="mss-login-field-label">SQLite path</label>
                <input type="text" id="mss-login-token-sqlite-path" class="mss-login-input" value="${(cfg.sqlite_path || "data/mss_login_data.db").replace(/"/g, "&quot;")}" style="min-width:240px;">
            </div>
            <div id="mss-login-token-postgres-fields" style="margin-top:8px; ${backend !== "postgresql" ? "display:none;" : ""}">
                <div class="mss-login-row" style="gap:8px; align-items:center; margin-bottom:6px;">
                    <label class="mss-login-field-label">Host</label>
                    <input type="text" id="mss-login-token-pg-host" class="mss-login-input" value="${(cfg.postgres_host || "localhost").replace(/"/g, "&quot;")}" placeholder="localhost">
                    <label class="mss-login-field-label">Port</label>
                    <input type="number" id="mss-login-token-pg-port" class="mss-login-input" value="${cfg.postgres_port || 5432}" placeholder="5432" style="width:80px;">
                </div>
                <div class="mss-login-row" style="gap:8px; align-items:center;">
                    <label class="mss-login-field-label">Database</label>
                    <input type="text" id="mss-login-token-pg-database" class="mss-login-input" value="${(cfg.postgres_database || "mss-login").replace(/"/g, "&quot;")}" placeholder="mss-login">
                    <label class="mss-login-field-label">User</label>
                    <input type="text" id="mss-login-token-pg-user" class="mss-login-input" value="${(cfg.postgres_user || "mss-login").replace(/"/g, "&quot;")}" placeholder="mss-login">
                </div>
                <p class="mss-login-note" style="margin-top:6px;">Password: set <code>API_TOKEN_DB_PASSWORD</code> or <code>POSTGRES_PASSWORD</code> in environment (not stored in config).</p>
            </div>
            <div id="mss-login-token-mysql-fields" style="margin-top:8px; ${backend !== "mysql" ? "display:none;" : ""}">
                <div class="mss-login-row" style="gap:8px; align-items:center; margin-bottom:6px;">
                    <label class="mss-login-field-label">Host</label>
                    <input type="text" id="mss-login-token-mysql-host" class="mss-login-input" value="${(cfg.mysql_host || "localhost").replace(/"/g, "&quot;")}" placeholder="localhost">
                    <label class="mss-login-field-label">Port</label>
                    <input type="number" id="mss-login-token-mysql-port" class="mss-login-input" value="${cfg.mysql_port ?? 3306}" placeholder="3306" style="width:80px;">
                </div>
                <div class="mss-login-row" style="gap:8px; align-items:center;">
                    <label class="mss-login-field-label">Database</label>
                    <input type="text" id="mss-login-token-mysql-database" class="mss-login-input" value="${(cfg.mysql_database || "mss_login").replace(/"/g, "&quot;")}" placeholder="mss_login">
                    <label class="mss-login-field-label">User</label>
                    <input type="text" id="mss-login-token-mysql-user" class="mss-login-input" value="${(cfg.mysql_user || "mss_login").replace(/"/g, "&quot;")}" placeholder="mss_login">
                </div>
                <p class="mss-login-note" style="margin-top:6px;">Password: set <code>MYSQL_PASSWORD</code> or <code>USERS_DB_PASSWORD</code> in environment (not stored in config).</p>
            </div>
            <div class="mss-login-row" style="margin-top:16px; gap:8px;">
                <button class="mss-login-btn" id="mss-login-token-save">Save</button>
            </div>
            <p id="mss-login-token-status" class="mss-login-note" style="margin-top:8px;"></p>
        </div>
    `;
    const backendSelect = container.querySelector("#mss-login-token-backend");
    const sqliteFields = container.querySelector("#mss-login-token-sqlite-fields");
    const postgresFields = container.querySelector("#mss-login-token-postgres-fields");
    const mysqlFields = container.querySelector("#mss-login-token-mysql-fields");
    const statusEl = container.querySelector("#mss-login-token-status");
    function showFields() {
        const b = (backendSelect.value || "sqlite").toLowerCase();
        sqliteFields.style.display = b === "sqlite" ? "" : "none";
        postgresFields.style.display = b === "postgresql" ? "" : "none";
        if (mysqlFields) mysqlFields.style.display = b === "mysql" ? "" : "none";
    }
    backendSelect.onchange = showFields;
    container.querySelector("#mss-login-token-save").onclick = async () => {
        const b = (backendSelect.value || "sqlite").toLowerCase();
        const body = {
            backend: b,
            sqlite_path: container.querySelector("#mss-login-token-sqlite-path").value.trim() || "data/mss_login_data.db",
            postgres_host: container.querySelector("#mss-login-token-pg-host").value.trim() || "localhost",
            postgres_port: parseInt(container.querySelector("#mss-login-token-pg-port").value, 10) || 5432,
            postgres_database: container.querySelector("#mss-login-token-pg-database").value.trim() || "mss-login",
            postgres_user: container.querySelector("#mss-login-token-pg-user").value.trim() || "mss-login",
            mysql_host: container.querySelector("#mss-login-token-mysql-host")?.value.trim() || "localhost",
            mysql_port: parseInt(container.querySelector("#mss-login-token-mysql-port")?.value, 10) || 3306,
            mysql_database: container.querySelector("#mss-login-token-mysql-database")?.value.trim() || "mss_login",
            mysql_user: container.querySelector("#mss-login-token-mysql-user")?.value.trim() || "mss_login",
        };
        statusEl.textContent = "Saving...";
        try {
            const res = await api.fetchApi("/mss-login/api/token-storage-config", { method: "PUT", body: JSON.stringify(body) });
            if (res.ok) {
                statusEl.textContent = "Saved. Token store will use the new config on next request.";
            } else {
                const err = await res.json().catch(() => ({}));
                statusEl.textContent = "Error: " + (err.error || res.status);
            }
        } catch (e) {
            statusEl.textContent = "Error: " + (e.message || "Request failed");
        }
    };
}

async renderUsersDbConfig(container) {
    let cfg = { backend: "sqlite", sqlite_path: "data/mss_login_data.db", postgres_host: "localhost", postgres_port: 5432, postgres_database: "mss_login", postgres_user: "mss_login", mysql_host: "localhost", mysql_port: 3306, mysql_database: "mss_login", mysql_user: "mss_login" };
    try {
        const res = await api.fetchApi("/mss-login/api/users-db-config", { method: "GET" });
        if (res.ok) cfg = await res.json();
    } catch (e) {
        console.warn("[mss-login] Users DB config load failed:", e);
    }
    const backend = (cfg.backend || "sqlite").toLowerCase();
    container.innerHTML = `
        <div class="mss-login-section">
            <h3>Users Database (Credentials)</h3>
            <p>Configure where user accounts are stored (SQLite, PostgreSQL, or MySQL). No plain-text JSON. Restart required for new backend to take effect. Passwords: <code>USERS_DB_PASSWORD</code>, <code>POSTGRES_PASSWORD</code> (PostgreSQL), or <code>MYSQL_PASSWORD</code> (MySQL) in environment only.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:8px; align-items:center;">
                <label class="mss-login-field-label">Backend</label>
                <select id="mss-login-usersdb-backend" class="mss-login-select">
                    <option value="sqlite" ${backend === "sqlite" ? "selected" : ""}>SQLite</option>
                    <option value="postgresql" ${backend === "postgresql" ? "selected" : ""}>PostgreSQL</option>
                    <option value="mysql" ${backend === "mysql" ? "selected" : ""}>MySQL</option>
                </select>
            </div>
            <div id="mss-login-usersdb-sqlite-fields" class="mss-login-row" style="margin-top:8px; gap:8px; align-items:center; ${backend !== "sqlite" ? "display:none;" : ""}">
                <label class="mss-login-field-label">SQLite path</label>
                <input type="text" id="mss-login-usersdb-sqlite-path" class="mss-login-input" value="${escapeHtml(cfg.sqlite_path || "data/mss_login_data.db")}" style="min-width:240px;">
            </div>
            <div id="mss-login-usersdb-sqlite-enc" class="mss-login-row" style="margin-top:8px; gap:8px; align-items:center; ${backend !== "sqlite" ? "display:none;" : ""}">
                <label class="mss-login-field-label">SQLite Encryption</label>
                <select id="mss-login-usersdb-sqlite-encryption" class="mss-login-select">
                    <option value="" ${(!cfg.encryption_level || cfg.encryption_level === "disabled") ? "selected" : ""}>Disabled</option>
                    <option value="standard" ${cfg.encryption_level === "standard" ? "selected" : ""}>Standard (Recommended)</option>
                    <option value="secure" ${cfg.encryption_level === "secure" ? "selected" : ""}>Secure</option>
                </select>
            </div>
            <div id="mss-login-usersdb-postgres-fields" style="margin-top:8px; ${backend !== "postgresql" ? "display:none;" : ""}">
                <div class="mss-login-row" style="gap:8px; align-items:center; margin-bottom:6px;">
                    <label class="mss-login-field-label">Host</label>
                    <input type="text" id="mss-login-usersdb-pg-host" class="mss-login-input" value="${escapeHtml(cfg.postgres_host || "localhost")}" placeholder="localhost">
                    <label class="mss-login-field-label">Port</label>
                    <input type="number" id="mss-login-usersdb-pg-port" class="mss-login-input" value="${cfg.postgres_port || 5432}" placeholder="5432" style="width:80px;">
                </div>
                <div class="mss-login-row" style="gap:8px; align-items:center;">
                    <label class="mss-login-field-label">Database</label>
                    <input type="text" id="mss-login-usersdb-pg-database" class="mss-login-input" value="${escapeHtml(cfg.postgres_database || "mss-login")}" placeholder="mss-login">
                    <label class="mss-login-field-label">User</label>
                    <input type="text" id="mss-login-usersdb-pg-user" class="mss-login-input" value="${escapeHtml(cfg.postgres_user || "mss-login")}" placeholder="mss-login">
                </div>
                <p class="mss-login-note" style="margin-top:6px;">Password: set <code>USERS_DB_PASSWORD</code> or <code>POSTGRES_PASSWORD</code> in environment (never stored in config).</p>
            </div>
            <div id="mss-login-usersdb-mysql-fields" style="margin-top:8px; ${backend !== "mysql" ? "display:none;" : ""}">
                <div class="mss-login-row" style="gap:8px; align-items:center; margin-bottom:6px;">
                    <label class="mss-login-field-label">Host</label>
                    <input type="text" id="mss-login-usersdb-mysql-host" class="mss-login-input" value="${escapeHtml(cfg.mysql_host || "localhost")}" placeholder="localhost">
                    <label class="mss-login-field-label">Port</label>
                    <input type="number" id="mss-login-usersdb-mysql-port" class="mss-login-input" value="${cfg.mysql_port ?? 3306}" placeholder="3306" style="width:80px;">
                </div>
                <div class="mss-login-row" style="gap:8px; align-items:center;">
                    <label class="mss-login-field-label">Database</label>
                    <input type="text" id="mss-login-usersdb-mysql-database" class="mss-login-input" value="${escapeHtml(cfg.mysql_database || "mss_login")}" placeholder="mss_login">
                    <label class="mss-login-field-label">User</label>
                    <input type="text" id="mss-login-usersdb-mysql-user" class="mss-login-input" value="${escapeHtml(cfg.mysql_user || "mss_login")}" placeholder="mss_login">
                </div>
                <p class="mss-login-note" style="margin-top:6px;">Password: set <code>MYSQL_PASSWORD</code> or <code>USERS_DB_PASSWORD</code> in environment (never stored in config).</p>
            </div>
            <div class="mss-login-row" style="margin-top:16px; gap:8px;">
                <button class="mss-login-btn" id="mss-login-usersdb-save">Save</button>
            </div>
            <p id="mss-login-usersdb-status" class="mss-login-note" style="margin-top:8px;"></p>
        </div>
    `;
    const backendSelect = container.querySelector("#mss-login-usersdb-backend");
    const sqliteFields = container.querySelector("#mss-login-usersdb-sqlite-fields");
    const sqliteEnc = container.querySelector("#mss-login-usersdb-sqlite-enc");
    const postgresFields = container.querySelector("#mss-login-usersdb-postgres-fields");
    const mysqlFields = container.querySelector("#mss-login-usersdb-mysql-fields");
    const statusEl = container.querySelector("#mss-login-usersdb-status");
    function showFields() {
        const b = (backendSelect.value || "sqlite").toLowerCase();
        sqliteFields.style.display = b === "sqlite" ? "" : "none";
        if (sqliteEnc) sqliteEnc.style.display = b === "sqlite" ? "" : "none";
        postgresFields.style.display = b === "postgresql" ? "" : "none";
        mysqlFields.style.display = b === "mysql" ? "" : "none";
    }
    backendSelect.onchange = showFields;
    container.querySelector("#mss-login-usersdb-save").onclick = async () => {
        const b = (backendSelect.value || "sqlite").toLowerCase();
        const encEl = container.querySelector("#mss-login-usersdb-sqlite-encryption");
        const body = {
            backend: b,
            encryption_level: encEl ? encEl.value : "",
            sqlite_path: container.querySelector("#mss-login-usersdb-sqlite-path").value.trim() || "data/mss_login_data.db",
            postgres_host: container.querySelector("#mss-login-usersdb-pg-host").value.trim() || "localhost",
            postgres_port: parseInt(container.querySelector("#mss-login-usersdb-pg-port").value, 10) || 5432,
            postgres_database: container.querySelector("#mss-login-usersdb-pg-database").value.trim() || "mss-login",
            postgres_user: container.querySelector("#mss-login-usersdb-pg-user").value.trim() || "mss-login",
            mysql_host: container.querySelector("#mss-login-usersdb-mysql-host").value.trim() || "localhost",
            mysql_port: parseInt(container.querySelector("#mss-login-usersdb-mysql-port").value, 10) || 3306,
            mysql_database: container.querySelector("#mss-login-usersdb-mysql-database").value.trim() || "mss_login",
            mysql_user: container.querySelector("#mss-login-usersdb-mysql-user").value.trim() || "mss_login",
        };
        statusEl.textContent = "Saving...";
        try {
            const res = await api.fetchApi("/mss-login/api/users-db-config", { method: "PUT", body: JSON.stringify(body) });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                statusEl.textContent = data.message || "Saved. Restart required for new backend to take effect.";
            } else {
                statusEl.textContent = "Error: " + (data.error || res.status);
            }
        } catch (e) {
            statusEl.textContent = "Error: " + (e.message || "Request failed");
        }
    };
}

async renderModelDownload(container) {
    const isOwner = Array.isArray(currentUser?.groups)
        && currentUser.groups.map(g => String(g).toLowerCase()).includes("owner");
    let sourcesWithKeys = [];
    let hasDownloadPermission = true;
    try {
        const res = await api.fetchApi("/mss-login/api/model-download/sources", { method: "GET" });
        if (res.status === 403) {
            hasDownloadPermission = false;
        } else if (res.ok) {
            const data = await res.json();
            sourcesWithKeys = data.sources_with_keys || [];
        }
    } catch (e) {
        console.warn("[mss-login] Model download sources load failed:", e);
    }
    if (!hasDownloadPermission) {
        container.innerHTML = `
            <div class="mss-login-section">
                <h3>Model download</h3>
                <p>Your role does not have permission to view or manage model downloads.</p>
            </div>
        `;
        return;
    }
    let folders = ["checkpoints", "loras", "vae", "embeddings", "controlnet", "upscale_models"];
    try {
        const fr = await api.fetchApi("/mss-login/api/model-cache/folders", { method: "GET" });
        const fd = await fr.json();
        if (fd.folders && fd.folders.length) folders = fd.folders;
    } catch (e) {}
    const folderOptions = folders.map(f => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join("");
    const ownerPatternSection = isOwner ? `
        <div class="mss-login-section" style="margin-top:24px;">
            <h3>Model isolation folder naming</h3>
            <p>Choose whether user model isolation folders use the <code>username</code> (recommended for persistence across reinstalls/restores) or unique <code>user_id</code> (UUID). Changes take effect on next model action.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; align-items:center;">
                <label class="mss-login-field-label">Folder naming convention</label>
                <select id="mss-login-model-isolation-naming" class="mss-login-select">
                    <option value="username">Username (e.g. models/checkpoints/alice/...)</option>
                    <option value="user_id">User ID (e.g. models/checkpoints/019234a.../...)</option>
                </select>
                <button class="mss-login-btn btn-save" id="mss-login-model-isolation-naming-save">Save naming convention</button>
                <span id="mss-login-model-isolation-naming-status" class="mss-login-note"></span>
            </div>
        </div>
        <div class="mss-login-section" style="margin-top:24px;">
            <h3>Model isolation download redirect patterns</h3>
            <p>Owner override patterns used to detect third-party/core model download routes that should be redirected to per-user model folders while <code>experimental.model_isolation</code> is enabled.</p>
            <p class="mss-login-note">Defaults and launch-time auto-detected patterns (such as Civicomfy, when present) are always applied. Add one custom pattern per line below.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; flex-wrap:wrap;">
                <div style="min-width:360px; flex:2;">
                    <label class="mss-login-field-label">Configured patterns (owner-defined)</label>
                    <textarea id="mss-login-model-isolation-patterns" class="mss-login-input" style="min-height:120px; width:100%;" placeholder="/my/custom/route-pattern"></textarea>
                </div>
                <div style="min-width:360px; flex:2;">
                    <label class="mss-login-field-label">Effective patterns (read-only)</label>
                    <textarea id="mss-login-model-isolation-effective-patterns" class="mss-login-input" style="min-height:120px; width:100%;" readonly></textarea>
                </div>
            </div>
            <div class="mss-login-row" style="margin-top:12px;">
                <button class="mss-login-btn" id="mss-login-model-isolation-patterns-refresh">Refresh</button>
                <button class="mss-login-btn btn-save" id="mss-login-model-isolation-patterns-save">Save patterns</button>
            </div>
            <p id="mss-login-model-isolation-patterns-status" class="mss-login-note" style="margin-top:8px;"></p>
        </div>
    ` : `
        <div class="mss-login-section" style="margin-top:24px;">
            <h3>Model isolation folder naming</h3>
            <p class="mss-login-note">Model isolation folder naming convention is managed by the server owner.</p>
        </div>
    `;
    container.innerHTML = `
        <div class="mss-login-section">
            <h3>API keys (CivitAI / HuggingFace)</h3>
            <p>Store your API keys to download models. Keys are encrypted and bound to your user.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; flex-wrap:wrap;">
                <div>
                    <label class="mss-login-field-label">CivitAI</label>
                    <input type="password" id="mss-login-civitai-key" class="mss-login-input" placeholder="${sourcesWithKeys.includes("civitai") ? "•••••••• (set)" : "API key"}" style="min-width:200px;">
                </div>
                <div>
                    <label class="mss-login-field-label">HuggingFace</label>
                    <input type="password" id="mss-login-hf-key" class="mss-login-input" placeholder="${sourcesWithKeys.includes("huggingface") ? "•••••••• (set)" : "API key"}" style="min-width:200px;">
                </div>
                <div style="align-self:flex-end;">
                    <button class="mss-login-btn" id="mss-login-save-keys">Save keys</button>
                </div>
            </div>
            <p id="mss-login-keys-status" class="mss-login-note" style="margin-top:8px;"></p>
            <div class="mss-login-row" style="margin-top:16px; gap:12px; align-items:center; flex-wrap:wrap;">
                <label class="mss-login-field-label">CivitAI host</label>
                <select id="mss-login-civitai-host" class="mss-login-select">
                    <option value="civitai.com">civitai.com</option>
                    <option value="civitai.red">civitai.red</option>
                </select>
                <button class="mss-login-btn" id="mss-login-civitai-host-save">Save host preference</button>
                <span id="mss-login-civitai-host-status" class="mss-login-note"></span>
            </div>
            <p class="mss-login-note" style="margin-top:6px;">Host preference is not a secret. Pasting a .red URL still routes that download through .red for that job.</p>
        </div>
        <div class="mss-login-section" style="margin-top:24px;">
            <h3>Browse / Search</h3>
            <p>Search CivitAI or Hugging Face, pick a version/file, then queue a download.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:8px; align-items:center; flex-wrap:wrap;">
                <label class="mss-login-field-label">Provider</label>
                <select id="mss-login-browse-provider" class="mss-login-select">
                    <option value="civitai">CivitAI</option>
                    <option value="huggingface">HuggingFace</option>
                </select>
                <input type="text" id="mss-login-browse-query" class="mss-login-input" placeholder="Search models..." style="min-width:220px; flex:1;">
                <button class="mss-login-btn" id="mss-login-browse-search">Search</button>
            </div>
            <p id="mss-login-browse-status" class="mss-login-note" style="margin-top:8px;"></p>
            <div id="mss-login-browse-results" style="margin-top:12px; max-height:320px; overflow:auto;"></div>
            <div id="mss-login-browse-detail" style="margin-top:12px;"></div>
        </div>
        <div class="mss-login-section" style="margin-top:24px;">
            <h3>Download model</h3>
            <p>Download from CivitAI or HuggingFace to local models or S3 mount. Newly downloaded models are visible only to admin/owner until you assign them in Shared Models.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:8px; align-items:center;">
                <label class="mss-login-field-label">Source</label>
                <select id="mss-login-dl-source" class="mss-login-select">
                    <option value="civitai">CivitAI</option>
                    <option value="huggingface">HuggingFace</option>
                </select>
                <label class="mss-login-field-label">Destination</label>
                <select id="mss-login-dl-dest" class="mss-login-select">
                    <option value="local">Local models</option>
                    <option value="s3">S3 mount</option>
                </select>
                <label class="mss-login-field-label">Folder type</label>
                <select id="mss-login-dl-folder" class="mss-login-select">${folderOptions}</select>
            </div>
            <div id="mss-login-dl-civitai-fields" class="mss-login-row" style="margin-top:12px; gap:8px; align-items:center;">
                <label class="mss-login-field-label">Model version ID</label>
                <input type="text" id="mss-login-dl-civitai-version" class="mss-login-input" placeholder="e.g. 138296" style="min-width:120px;">
            </div>
            <div id="mss-login-dl-hf-fields" class="mss-login-row" style="margin-top:12px; gap:8px; align-items:center; display:none;">
                <label class="mss-login-field-label">Repo ID</label>
                <input type="text" id="mss-login-dl-hf-repo" class="mss-login-input" placeholder="username/repo-name" style="min-width:200px;">
                <label class="mss-login-field-label">Filename</label>
                <input type="text" id="mss-login-dl-hf-filename" class="mss-login-input" placeholder="model.safetensors" style="min-width:160px;">
            </div>
            <div class="mss-login-row" style="margin-top:16px;">
                <button class="mss-login-btn" id="mss-login-dl-start">Download</button>
            </div>
            <p id="mss-login-dl-status" class="mss-login-note" style="margin-top:8px;"></p>
            <div class="mss-login-section" style="margin-top:16px;">
                <h4 style="margin: 0 0 8px 0;">Download queue</h4>
                <p id="mss-login-dl-queue-summary" class="mss-login-note"></p>
                <div id="mss-login-dl-jobs"></div>
            </div>
        </div>
        ${ownerPatternSection}
    `;
    const sourceSelect = container.querySelector("#mss-login-dl-source");
    const civitaiFields = container.querySelector("#mss-login-dl-civitai-fields");
    const hfFields = container.querySelector("#mss-login-dl-hf-fields");
    const hostSelect = container.querySelector("#mss-login-civitai-host");
    function showSourceFields() {
        const v = sourceSelect.value;
        civitaiFields.style.display = v === "civitai" ? "" : "none";
        hfFields.style.display = v === "huggingface" ? "" : "none";
    }
    sourceSelect.onchange = showSourceFields;
    showSourceFields();

    (async () => {
        try {
            const prefRes = await api.fetchApi("/mss-login/api/model-download/preferences", { method: "GET" });
            if (prefRes.ok) {
                const pref = await prefRes.json();
                if (pref.civitai_host && hostSelect) {
                    hostSelect.value = pref.civitai_host;
                }
            }
        } catch (_) {}
        try {
            const me = await getData("/mss-login/api/me");
            if (me && !me.experimental?.s3) {
                const destSelect = container.querySelector("#mss-login-dl-dest");
                const s3Opt = destSelect && destSelect.querySelector('option[value="s3"]');
                if (s3Opt) s3Opt.remove();
            }
        } catch (_) {}
    })();

    container.querySelector("#mss-login-civitai-host-save").onclick = async () => {
        const statusEl = container.querySelector("#mss-login-civitai-host-status");
        statusEl.textContent = "Saving...";
        try {
            const res = await api.fetchApi("/mss-login/api/model-download/preferences", {
                method: "PUT",
                body: JSON.stringify({ civitai_host: hostSelect.value })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || res.status);
            hostSelect.value = data.civitai_host || hostSelect.value;
            statusEl.textContent = "Host preference saved.";
        } catch (e) {
            statusEl.textContent = "Error: " + (e.message || "Save failed");
        }
    };

    const browseResults = container.querySelector("#mss-login-browse-results");
    const browseDetail = container.querySelector("#mss-login-browse-detail");
    const browseStatus = container.querySelector("#mss-login-browse-status");
    const queueFromBrowse = async (bodyExtra) => {
        const dest = container.querySelector("#mss-login-dl-dest").value;
        const folder = container.querySelector("#mss-login-dl-folder").value;
        const body = {
            destination_type: dest,
            folder_type: folder,
            ...bodyExtra
        };
        if (body.source === "civitai") {
            body.civitai_host = hostSelect.value;
        }
        browseStatus.textContent = "Queueing download...";
        try {
            const res = await api.fetchApi("/mss-login/api/model-download/download", {
                method: "POST",
                body: JSON.stringify(body)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || res.status);
            browseStatus.textContent = data.job_id
                ? ("Queued download job: " + data.job_id)
                : "Download queued.";
            if (typeof refreshJobs === "function") await refreshJobs();
        } catch (e) {
            browseStatus.textContent = "Error: " + (e.message || "Queue failed");
        }
    };
    const renderCivitaiModelDetail = async (modelId) => {
        browseDetail.innerHTML = `<p class="mss-login-note">Loading model ${escapeHtml(String(modelId))}...</p>`;
        try {
            const res = await api.fetchApi(
                `/mss-login/api/model-download/civitai/models/${encodeURIComponent(modelId)}?civitai_host=${encodeURIComponent(hostSelect.value)}`,
                { method: "GET" }
            );
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || res.status);
            const model = data.model || {};
            const versions = Array.isArray(model.modelVersions) ? model.modelVersions : [];
            const versionOptions = versions.map((v) => {
                const vid = v.id;
                const name = v.name || ("v" + vid);
                const files = Array.isArray(v.files) ? v.files : [];
                const fileHint = files[0]?.name ? ` — ${files[0].name}` : "";
                return `<option value="${escapeHtml(String(vid))}">${escapeHtml(name)}${escapeHtml(fileHint)}</option>`;
            }).join("");
            browseDetail.innerHTML = `
                <div style="border:1px solid #333; border-radius:8px; padding:12px;">
                    <strong>${escapeHtml(model.name || ("Model " + modelId))}</strong>
                    <p class="mss-login-note">${escapeHtml((model.type || "") + (model.nsfw ? " (NSFW)" : ""))}</p>
                    <div class="mss-login-row" style="gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px;">
                        <label class="mss-login-field-label">Version</label>
                        <select id="mss-login-browse-version" class="mss-login-select">${versionOptions || '<option value="">No versions</option>'}</select>
                        <button class="mss-login-btn" id="mss-login-browse-queue-civitai">Queue download</button>
                        <button class="mss-login-btn" id="mss-login-browse-fill-civitai">Fill form</button>
                    </div>
                </div>
            `;
            const queueBtn = browseDetail.querySelector("#mss-login-browse-queue-civitai");
            const fillBtn = browseDetail.querySelector("#mss-login-browse-fill-civitai");
            const versionSel = browseDetail.querySelector("#mss-login-browse-version");
            if (queueBtn) {
                queueBtn.onclick = () => {
                    const vid = (versionSel && versionSel.value || "").trim();
                    if (!vid) {
                        browseStatus.textContent = "Select a model version.";
                        return;
                    }
                    queueFromBrowse({ source: "civitai", model_version_id: vid });
                };
            }
            if (fillBtn) {
                fillBtn.onclick = () => {
                    const vid = (versionSel && versionSel.value || "").trim();
                    sourceSelect.value = "civitai";
                    showSourceFields();
                    container.querySelector("#mss-login-dl-civitai-version").value = vid;
                    browseStatus.textContent = "Filled download form with version " + vid;
                };
            }
        } catch (e) {
            browseDetail.innerHTML = `<p class="mss-login-note" style="color:#ff8888;">Error: ${escapeHtml(e.message || "Load failed")}</p>`;
        }
    };
    container.querySelector("#mss-login-browse-search").onclick = async () => {
        const provider = container.querySelector("#mss-login-browse-provider").value;
        const query = container.querySelector("#mss-login-browse-query").value.trim();
        browseStatus.textContent = "Searching...";
        browseResults.innerHTML = "";
        browseDetail.innerHTML = "";
        try {
            if (provider === "civitai") {
                const qs = new URLSearchParams({
                    query,
                    page: "1",
                    limit: "20",
                    civitai_host: hostSelect.value
                });
                const res = await api.fetchApi(`/mss-login/api/model-download/civitai/search?${qs}`, { method: "GET" });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || res.status);
                const items = Array.isArray(data.result?.items) ? data.result.items : (Array.isArray(data.result) ? data.result : []);
                if (!items.length) {
                    browseResults.innerHTML = `<p class="mss-login-note">No CivitAI results.</p>`;
                    browseStatus.textContent = "No results.";
                    return;
                }
                browseResults.innerHTML = items.map((item) => {
                    const id = item.id;
                    const name = item.name || ("Model " + id);
                    const type = item.type || "";
                    return `<div style="border:1px solid #333; border-radius:8px; padding:10px; margin:8px 0; display:flex; justify-content:space-between; gap:8px; align-items:center;">
                        <div><strong>${escapeHtml(name)}</strong><div class="mss-login-note">${escapeHtml(type)} · id ${escapeHtml(String(id))}</div></div>
                        <button class="mss-login-btn mss-login-browse-open" data-model-id="${escapeHtml(String(id))}">Open</button>
                    </div>`;
                }).join("");
                browseResults.querySelectorAll(".mss-login-browse-open").forEach((btn) => {
                    btn.onclick = () => renderCivitaiModelDetail(btn.dataset.modelId);
                });
                browseStatus.textContent = `Found ${items.length} CivitAI model(s).`;
            } else {
                const qs = new URLSearchParams({ query, limit: "20" });
                const res = await api.fetchApi(`/mss-login/api/model-download/huggingface/search?${qs}`, { method: "GET" });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || res.status);
                const items = Array.isArray(data.items) ? data.items : [];
                if (!items.length) {
                    browseResults.innerHTML = `<p class="mss-login-note">No Hugging Face results.</p>`;
                    browseStatus.textContent = "No results.";
                    return;
                }
                browseResults.innerHTML = items.map((item) => {
                    const repoId = item.repo_id || item.id || "";
                    return `<div style="border:1px solid #333; border-radius:8px; padding:10px; margin:8px 0;">
                        <strong>${escapeHtml(repoId)}</strong>
                        <div class="mss-login-note">${escapeHtml(item.pipeline_tag || "")}</div>
                        <div class="mss-login-row" style="margin-top:8px; gap:8px; flex-wrap:wrap; align-items:center;">
                            <input type="text" class="mss-login-input mss-login-hf-filename" data-repo="${escapeHtml(repoId)}" placeholder="filename.safetensors" style="min-width:180px;">
                            <button class="mss-login-btn mss-login-hf-queue" data-repo="${escapeHtml(repoId)}">Queue</button>
                            <button class="mss-login-btn mss-login-hf-fill" data-repo="${escapeHtml(repoId)}">Fill form</button>
                        </div>
                    </div>`;
                }).join("");
                browseResults.querySelectorAll(".mss-login-hf-queue").forEach((btn) => {
                    btn.onclick = () => {
                        const row = btn.closest("div");
                        const input = row && row.querySelector(".mss-login-hf-filename");
                        const filename = (input && input.value || "").trim();
                        if (!filename) {
                            browseStatus.textContent = "Enter a filename for the Hugging Face file.";
                            return;
                        }
                        queueFromBrowse({
                            source: "huggingface",
                            repo_id: btn.dataset.repo,
                            filename
                        });
                    };
                });
                browseResults.querySelectorAll(".mss-login-hf-fill").forEach((btn) => {
                    btn.onclick = () => {
                        const row = btn.closest("div");
                        const input = row && row.querySelector(".mss-login-hf-filename");
                        sourceSelect.value = "huggingface";
                        showSourceFields();
                        container.querySelector("#mss-login-dl-hf-repo").value = btn.dataset.repo || "";
                        container.querySelector("#mss-login-dl-hf-filename").value = (input && input.value || "").trim();
                        browseStatus.textContent = "Filled download form.";
                    };
                });
                browseStatus.textContent = `Found ${items.length} Hugging Face model(s).`;
            }
        } catch (e) {
            browseStatus.textContent = "Error: " + (e.message || "Search failed");
        }
    };

    container.querySelector("#mss-login-save-keys").onclick = async () => {
        const statusEl = container.querySelector("#mss-login-keys-status");
        const civitaiKey = container.querySelector("#mss-login-civitai-key").value.trim();
        const hfKey = container.querySelector("#mss-login-hf-key").value.trim();
        statusEl.textContent = "Saving...";
        try {
            if (civitaiKey) {
                const r = await api.fetchApi("/mss-login/api/model-download/api-keys", { method: "PUT", body: JSON.stringify({ source: "civitai", api_key: civitaiKey }) });
                if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Failed");
            }
            if (hfKey) {
                const r = await api.fetchApi("/mss-login/api/model-download/api-keys", { method: "PUT", body: JSON.stringify({ source: "huggingface", api_key: hfKey }) });
                if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Failed");
            }
            statusEl.textContent = "Keys saved.";
            container.querySelector("#mss-login-civitai-key").value = "";
            container.querySelector("#mss-login-hf-key").value = "";
            container.querySelector("#mss-login-civitai-key").placeholder = "•••••••• (set)";
            container.querySelector("#mss-login-hf-key").placeholder = "•••••••• (set)";
        } catch (e) {
            statusEl.textContent = "Error: " + (e.message || "Save failed");
        }
    };

    container.querySelector("#mss-login-dl-start").onclick = async () => {
        const statusEl = container.querySelector("#mss-login-dl-status");
        const source = sourceSelect.value;
        const dest = container.querySelector("#mss-login-dl-dest").value;
        const folder = container.querySelector("#mss-login-dl-folder").value;
        const body = { source, destination_type: dest, folder_type: folder };
        if (source === "civitai") {
            body.model_version_id = container.querySelector("#mss-login-dl-civitai-version").value.trim();
            body.civitai_host = (container.querySelector("#mss-login-civitai-host") || {}).value || "civitai.com";
            if (!body.model_version_id) {
                statusEl.textContent = "Enter CivitAI model version ID.";
                return;
            }
        } else {
            body.repo_id = container.querySelector("#mss-login-dl-hf-repo").value.trim();
            body.filename = container.querySelector("#mss-login-dl-hf-filename").value.trim();
            if (!body.repo_id || !body.filename) {
                statusEl.textContent = "Enter HuggingFace repo ID and filename.";
                return;
            }
        }
        statusEl.textContent = "Queueing download...";
        try {
            const res = await api.fetchApi("/mss-login/api/model-download/download", { method: "POST", body: JSON.stringify(body) });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                statusEl.textContent = "Error: " + (data.error || res.status);
                return;
            }
            const data = await res.json().catch(() => ({}));
            statusEl.textContent = data?.job_id
                ? ("Queued download job: " + data.job_id)
                : "Download queued.";
            await refreshJobs();
        } catch (e) {
            statusEl.textContent = "Error: " + (e.message || "Download failed");
        }
    };

    const jobsWrap = container.querySelector("#mss-login-dl-jobs");
    const queueSummary = container.querySelector("#mss-login-dl-queue-summary");
    const humanBytes = (n) => {
        const num = Number(n || 0);
        if (num < 1024) return num.toFixed(0) + " B";
        if (num < 1024 * 1024) return (num / 1024).toFixed(1) + " KB";
        return (num / (1024 * 1024)).toFixed(1) + " MB";
    };
    const humanSpeed = (bps) => {
        const val = Number(bps || 0);
        if (!val || val <= 0) return "0 KB/s";
        if (val >= 1024 * 1024) return (val / (1024 * 1024)).toFixed(2) + " MB/s";
        return (val / 1024).toFixed(1) + " KB/s";
    };
    const humanEta = (etaSeconds) => {
        const s = Number(etaSeconds || 0);
        if (!s || s <= 0) return "estimating...";
        if (s >= 60) return Math.round(s / 60) + " min";
        return Math.round(s) + " sec";
    };
    const statusColor = (status) => {
        if (status === "failed" || status === "cancelled") return "#ff6666";
        if (status === "completed") return "#66cc88";
        if (status === "running") return "#6fa8ff";
        return "#aaa";
    };
    const renderJob = (job) => {
        const pct = Math.max(0, Math.min(100, Number(job.progress_pct || 0)));
        const speed = humanSpeed(job.speed_bps);
        const eta = humanEta(job.eta_seconds);
        const bytes = humanBytes(job.bytes_done || 0);
        const total = job.total_bytes ? humanBytes(job.total_bytes) : "?";
        const err = job.error ? `<div class="mss-login-note" style="color:#ff8888;">Error: ${escapeHtml(job.error)}</div>` : "";
        const canCancel = !!job.can_cancel;
        return `
            <div style="border:1px solid #333; border-radius:8px; padding:10px; margin:8px 0;">
                <div style="display:flex; justify-content:space-between; gap:8px; align-items:center;">
                    <div><strong>${escapeHtml(job.source || "unknown")}</strong> -> ${escapeHtml(job.folder_type || "")}</div>
                    <div style="color:${statusColor(job.status)}; text-transform:capitalize;">${escapeHtml(job.status || "queued")}</div>
                </div>
                <div style="height:10px; background:#222; border-radius:6px; margin-top:8px; overflow:hidden;">
                    <div style="height:10px; width:${pct}%; background:#4a90e2;"></div>
                </div>
                <div class="mss-login-note" style="margin-top:6px;">
                    ${pct.toFixed(1)}% • ${bytes} / ${total} • ${speed} • ETA ${eta}
                </div>
                ${err}
                ${canCancel ? `<button class="mss-login-btn mss-login-job-cancel" data-job-id="${escapeHtml(job.job_id)}" style="margin-top:8px;">Cancel</button>` : ""}
            </div>
        `;
    };
    const bindCancelButtons = () => {
        jobsWrap.querySelectorAll(".mss-login-job-cancel").forEach((btn) => {
            btn.onclick = async () => {
                const jobId = btn.dataset.jobId;
                try {
                    const res = await api.fetchApi(`/mss-login/api/model-download/jobs/${encodeURIComponent(jobId)}/cancel`, {
                        method: "POST"
                    });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) {
                        container.querySelector("#mss-login-dl-status").textContent = "Error: " + (data.error || res.status);
                        return;
                    }
                    container.querySelector("#mss-login-dl-status").textContent = "Cancel requested for " + jobId;
                    await refreshJobs();
                } catch (e) {
                    container.querySelector("#mss-login-dl-status").textContent = "Error: " + (e.message || "Cancel failed");
                }
            };
        });
    };
    const refreshJobs = async () => {
        try {
            const res = await api.fetchApi("/mss-login/api/model-download/jobs", { method: "GET" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                queueSummary.textContent = "Unable to load jobs: " + (data.error || res.status);
                return;
            }
            const stats = data.stats || {};
            queueSummary.textContent =
                `Active ${stats.active_total || 0}/${stats.limit_total || 5} | ` +
                `CivitAI ${stats.active_civitai || 0}/${stats.limit_civitai || 3} | ` +
                `HuggingFace ${stats.active_huggingface || 0}/${stats.limit_huggingface || 2} | ` +
                `Queued ${stats.pending_total || 0}`;
            const jobs = Array.isArray(data.jobs) ? data.jobs : [];
            if (!jobs.length) {
                jobsWrap.innerHTML = `<p class="mss-login-note">No queued or active model downloads.</p>`;
                return;
            }
            jobsWrap.innerHTML = jobs.map(renderJob).join("");
            bindCancelButtons();
        } catch (e) {
            queueSummary.textContent = "Unable to load jobs: " + (e.message || "Unknown error");
        }
    };
    if (this._modelDownloadPollTimer) {
        clearInterval(this._modelDownloadPollTimer);
        this._modelDownloadPollTimer = null;
    }
    await refreshJobs();
    this._modelDownloadPollTimer = setInterval(() => {
        refreshJobs().catch(() => {});
    }, 1500);

    if (isOwner) {
        const namingSelect = container.querySelector("#mss-login-model-isolation-naming");
        const namingSaveBtn = container.querySelector("#mss-login-model-isolation-naming-save");
        const namingStatus = container.querySelector("#mss-login-model-isolation-naming-status");

        const loadNaming = async () => {
            if (!namingSelect) return;
            try {
                const res = await api.fetchApi("/mss-login/api/settings/model-isolation", { method: "GET" });
                const data = await res.json().catch(() => ({}));
                if (res.ok && data.folder_naming) {
                    namingSelect.value = data.folder_naming;
                }
            } catch (e) {}
        };
        loadNaming();

        if (namingSaveBtn && namingSelect) {
            namingSaveBtn.onclick = async () => {
                if (namingStatus) namingStatus.textContent = "Saving...";
                try {
                    const res = await api.fetchApi("/mss-login/api/settings/model-isolation", {
                        method: "PUT",
                        body: JSON.stringify({ folder_naming: namingSelect.value })
                    });
                    const data = await res.json().catch(() => ({}));
                    if (res.ok) {
                        if (namingStatus) namingStatus.textContent = "Saved. Active convention: " + (data.folder_naming || namingSelect.value);
                    } else {
                        if (namingStatus) namingStatus.textContent = "Error: " + (data.error || res.status);
                    }
                } catch (e) {
                    if (namingStatus) namingStatus.textContent = "Error: " + e.message;
                }
            };
        }

        const cfgArea = container.querySelector("#mss-login-model-isolation-patterns");
        const effArea = container.querySelector("#mss-login-model-isolation-effective-patterns");
        const statusEl = container.querySelector("#mss-login-model-isolation-patterns-status");
        const refreshBtn = container.querySelector("#mss-login-model-isolation-patterns-refresh");
        const saveBtn = container.querySelector("#mss-login-model-isolation-patterns-save");

        const parsePatterns = (raw) => {
            return Array.from(new Set(
                String(raw || "")
                    .split("\n")
                    .map(x => x.trim().toLowerCase())
                    .filter(Boolean)
            ));
        };

        const loadPatterns = async () => {
            statusEl.textContent = "Loading patterns...";
            try {
                const res = await api.fetchApi("/mss-login/api/settings/model-isolation-download-patterns", { method: "GET" });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    statusEl.textContent = "Error: " + (data.error || res.status);
                    return;
                }
                cfgArea.value = (data.configured_patterns || []).join("\n");
                effArea.value = (data.effective_patterns || []).join("\n");
                statusEl.textContent = "Patterns loaded.";
            } catch (e) {
                statusEl.textContent = "Error: " + (e.message || "Failed to load patterns");
            }
        };

        refreshBtn.onclick = loadPatterns;
        saveBtn.onclick = async () => {
            const patterns = parsePatterns(cfgArea.value);
            statusEl.textContent = "Saving patterns...";
            try {
                const res = await api.fetchApi("/mss-login/api/settings/model-isolation-download-patterns", {
                    method: "PUT",
                    body: JSON.stringify({ patterns })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    statusEl.textContent = "Error: " + (data.error || res.status);
                    return;
                }
                cfgArea.value = (data.configured_patterns || []).join("\n");
                effArea.value = (data.effective_patterns || []).join("\n");
                statusEl.textContent = "Patterns saved.";
            } catch (e) {
                statusEl.textContent = "Error: " + (e.message || "Failed to save patterns");
            }
        };

        await loadPatterns();
    }
}

async renderLoginAppearance(container) {
    const isOwner = Array.isArray(currentUser?.groups)
        && currentUser.groups.map(g => String(g).toLowerCase()).includes("owner");
    if (!isOwner) {
        container.innerHTML = `
            <div class="mss-login-section">
                <h3>Login Appearance</h3>
                <p>Only the owner account can configure the login background.</p>
            </div>
        `;
        return;
    }
    container.innerHTML = `
        <div class="mss-login-section" style="max-width:560px; text-align:left;">
            <h3>Login Appearance</h3>
            <p class="mss-login-note">Custom /login background (experimental). Local files must be under the data directory (login-background/ recommended).</p>
            <p id="mss-login-bg-exp-note" class="mss-login-note" style="display:none; color:#fbbf24;"></p>
            <label style="display:flex; align-items:center; gap:8px; margin:12px 0;">
                <input type="checkbox" id="mss-login-bg-enabled"> Enable custom login background
            </label>
            <label class="mss-login-field-label">Source</label>
            <select id="mss-login-bg-source" class="mss-login-input" style="width:100%; margin-bottom:8px;">
                <option value="url">External URL</option>
                <option value="local">Local path</option>
            </select>
            <div id="mss-login-bg-url-wrap">
                <label class="mss-login-field-label">URL</label>
                <input type="url" id="mss-login-bg-url" class="mss-login-input" style="width:100%; margin-bottom:8px;" placeholder="https://example.com/bg.jpg">
            </div>
            <div id="mss-login-bg-local-wrap" style="display:none;">
                <label class="mss-login-field-label">Local path</label>
                <input type="text" id="mss-login-bg-local" class="mss-login-input" style="width:100%; margin-bottom:8px;" placeholder="login-background/my-bg.mp4">
            </div>
            <label class="mss-login-field-label">Media kind</label>
            <select id="mss-login-bg-kind" class="mss-login-input" style="width:100%; margin-bottom:12px;">
                <option value="auto">Auto-detect</option>
                <option value="image">Image</option>
                <option value="video">Video</option>
            </select>
            <button type="button" class="mss-login-launch-btn" id="mss-login-bg-save" style="min-width:200px;">Save login appearance</button>
            <p id="mss-login-bg-status" class="mss-login-note" style="margin-top:8px;"></p>
        </div>
    `;
    const sourceEl = container.querySelector("#mss-login-bg-source");
    const urlWrap = container.querySelector("#mss-login-bg-url-wrap");
    const localWrap = container.querySelector("#mss-login-bg-local-wrap");
    const syncSource = () => {
        const local = sourceEl.value === "local";
        urlWrap.style.display = local ? "none" : "block";
        localWrap.style.display = local ? "block" : "none";
    };
    sourceEl.addEventListener("change", syncSource);
    try {
        const res = await api.fetchApi("/mss-login/api/settings/login-background");
        const data = await res.json();
        if (res.ok && data) {
            container.querySelector("#mss-login-bg-enabled").checked = !!data.enabled;
            sourceEl.value = data.source === "local" ? "local" : "url";
            container.querySelector("#mss-login-bg-url").value = data.url || "";
            container.querySelector("#mss-login-bg-local").value = data.local_path || "";
            container.querySelector("#mss-login-bg-kind").value = data.media_kind || "auto";
            syncSource();
            if (!data.experimental_active) {
                const note = container.querySelector("#mss-login-bg-exp-note");
                note.style.display = "block";
                note.textContent = "Experimental login_background is off. Settings will save but will not apply on /login until enabled.";
            }
        }
    } catch (_) {}
    container.querySelector("#mss-login-bg-save").onclick = async () => {
        const statusEl = container.querySelector("#mss-login-bg-status");
        statusEl.textContent = "Saving…";
        try {
            const res = await api.fetchApi("/mss-login/api/settings/login-background", {
                method: "PUT",
                body: JSON.stringify({
                    enabled: !!container.querySelector("#mss-login-bg-enabled").checked,
                    source: sourceEl.value,
                    url: container.querySelector("#mss-login-bg-url").value,
                    local_path: container.querySelector("#mss-login-bg-local").value,
                    media_kind: container.querySelector("#mss-login-bg-kind").value,
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                statusEl.textContent = data?.error || "Save failed.";
                return;
            }
            statusEl.textContent = data.experimental_active
                ? "Saved. Reload /login to preview."
                : "Saved. Enable experimental login_background to apply on /login.";
        } catch (e) {
            statusEl.textContent = "Error: " + (e.message || "Failed");
        }
    };
}

async renderTokens(container) {
    if (!container) return;
    const isGuest = !currentUser || !currentUser.username || String(currentUser.username).toLowerCase() === "guest";
    if (isGuest) {
        container.innerHTML = `
            <div class="mss-login-section" style="max-width:560px; text-align:center; padding:24px;">
                <h3 style="margin-top:0;">Tokens & API Keys</h3>
                <p class="mss-login-note">Guest accounts cannot create or manage API tokens. Please log in with a registered user account.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="mss-login-section" style="max-width:760px; text-align:left; margin:0 auto;">
            <h3 style="margin-top:0;">API & Session Token Management</h3>
            <p class="mss-login-note" style="margin-bottom:14px;">Create long-lived API tokens for mobile applications, Comfy Portal, external scripts, or inspect your active browser sessions.</p>
            
            <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:16px; margin-bottom:20px;">
                <h4 style="margin:0 0 10px 0; color:#c4a4ff;">Create API Token</h4>
                <div class="mss-login-row" style="gap:10px; align-items:flex-end; flex-wrap:wrap;">
                    <div style="flex:1; min-width:180px;">
                        <label class="mss-login-field-label">Token Name (optional)</label>
                        <input type="text" id="mss-dialog-token-label" class="mss-login-input" placeholder="e.g. Mobile Tablet App">
                    </div>
                    <div style="width:140px;">
                        <label class="mss-login-field-label">Expires in hours</label>
                        <input type="number" id="mss-dialog-token-expire" class="mss-login-input" value="720" min="0" step="1">
                    </div>
                    <div style="flex:1; min-width:180px;">
                        <label class="mss-login-field-label">Re-enter Password</label>
                        <input type="password" id="mss-dialog-token-pass" class="mss-login-input" placeholder="Required for verification" autocomplete="current-password">
                    </div>
                    <div>
                        <button type="button" class="mss-login-btn" id="mss-dialog-token-create-btn">Create Token</button>
                    </div>
                </div>
                
                <div id="mss-dialog-token-mfa-row" style="display:none; gap:10px; margin-top:10px; flex-wrap:wrap;">
                    <div>
                        <label class="mss-login-field-label">MFA Code</label>
                        <input type="text" id="mss-dialog-token-mfa-code" class="mss-login-input" placeholder="123456" style="width:120px;">
                    </div>
                    <div>
                        <label class="mss-login-field-label">Backup Code</label>
                        <input type="text" id="mss-dialog-token-mfa-backup" class="mss-login-input" placeholder="XXXX-XXXX" style="width:140px;">
                    </div>
                </div>

                <div id="mss-dialog-token-output-wrap" style="display:none; margin-top:14px; background:#0c0d12; border:1px solid #9660fa; border-radius:8px; padding:12px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <span style="font-weight:600; font-size:0.85rem; color:#4ade80;">✓ New Token Generated</span>
                        <button type="button" class="mss-login-btn" id="mss-dialog-token-copy-btn" style="padding:4px 10px; font-size:0.8rem;">Copy Token</button>
                    </div>
                    <code id="mss-dialog-token-output" style="display:block; font-size:0.8rem; word-break:break-all; user-select:all; padding:8px; background:rgba(0,0,0,0.4); border-radius:4px; max-height:80px; overflow-y:auto;"></code>
                    <p class="mss-login-note" style="margin:6px 0 0 0; font-size:0.75rem; color:#fbbf24;">Save this token now. It cannot be displayed again.</p>
                </div>
                <p id="mss-dialog-token-status" class="mss-login-note" style="margin:8px 0 0 0;"></p>
            </div>

            <div style="margin-bottom:20px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <h4 style="margin:0;">Active API Tokens</h4>
                    <button type="button" class="mss-login-btn secondary" id="mss-dialog-token-refresh-btn" style="padding:3px 10px; font-size:0.8rem;">↻ Refresh</button>
                </div>
                <div id="mss-dialog-token-list" style="overflow-x:auto;">Loading tokens...</div>
            </div>

            <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <h4 style="margin:0;">Active Browser Sessions</h4>
                    <button type="button" class="mss-login-btn secondary" id="mss-dialog-sessions-refresh-btn" style="padding:3px 10px; font-size:0.8rem;">↻ Refresh</button>
                </div>
                <div id="mss-dialog-sessions-list" style="overflow-x:auto;">Loading sessions...</div>
            </div>
        </div>
    `;

    const labelInput = container.querySelector("#mss-dialog-token-label");
    const expireInput = container.querySelector("#mss-dialog-token-expire");
    const passInput = container.querySelector("#mss-dialog-token-pass");
    const createBtn = container.querySelector("#mss-dialog-token-create-btn");
    const mfaRow = container.querySelector("#mss-dialog-token-mfa-row");
    const mfaCodeInput = container.querySelector("#mss-dialog-token-mfa-code");
    const mfaBackupInput = container.querySelector("#mss-dialog-token-mfa-backup");
    const outputWrap = container.querySelector("#mss-dialog-token-output-wrap");
    const outputCode = container.querySelector("#mss-dialog-token-output");
    const copyBtn = container.querySelector("#mss-dialog-token-copy-btn");
    const statusText = container.querySelector("#mss-dialog-token-status");
    const tokensListContainer = container.querySelector("#mss-dialog-token-list");
    const sessionsListContainer = container.querySelector("#mss-dialog-sessions-list");
    const refreshTokensBtn = container.querySelector("#mss-dialog-token-refresh-btn");
    const refreshSessionsBtn = container.querySelector("#mss-dialog-sessions-refresh-btn");

    let mfaTempToken = null;

    const setStatus = (msg, isErr = false) => {
        statusText.textContent = msg || "";
        statusText.style.color = isErr ? "#f87171" : "#4ade80";
    };

    const loadTokens = async () => {
        tokensListContainer.innerHTML = "<p class='mss-login-note'>Loading tokens...</p>";
        try {
            const res = await api.fetchApi("/mss-login/api/tokens", { method: "GET" });
            if (!res.ok) {
                tokensListContainer.innerHTML = "<p class='mss-login-note'>Unable to load tokens.</p>";
                return;
            }
            const data = await res.json().catch(() => ({}));
            const list = Array.isArray(data.tokens) ? data.tokens : [];
            if (!list.length) {
                tokensListContainer.innerHTML = "<p class='mss-login-note'>No active API tokens found.</p>";
                return;
            }
            let html = "<table class='mss-login-table' style='width:100%; border-collapse:collapse; font-size:0.85rem;'><thead><tr style='border-bottom:1px solid #333;'><th style='padding:6px;'>Name</th><th style='padding:6px;'>Prefix</th><th style='padding:6px;'>Created</th><th style='padding:6px;'>Last Used</th><th style='padding:6px;'>Expires</th><th style='padding:6px;'>Action</th></tr></thead><tbody>";
            list.forEach((t) => {
                const label = escapeHtml(t.label || "Unlabeled");
                const prefix = escapeHtml(t.token_hash_prefix || "");
                const created = t.created_at_iso ? new Date(t.created_at_iso).toLocaleString() : "N/A";
                const lastUsed = t.last_used_at_iso ? new Date(t.last_used_at_iso).toLocaleString() : "Never";
                const expires = (t.expires_iso === "9999-12-31T23:59:59+00:00" || t.expires_iso?.startsWith("9999")) ? "Never" : (t.expires_iso ? new Date(t.expires_iso).toLocaleString() : "N/A");
                html += `<tr><td style='padding:6px;'>${label}</td><td style='padding:6px; font-family:monospace;'>${prefix}</td><td style='padding:6px;'>${created}</td><td style='padding:6px;'>${lastUsed}</td><td style='padding:6px;'>${expires}</td><td style='padding:6px;'><button type='button' class='mss-login-btn mss-login-btn-danger' data-revoke-prefix='${prefix}' style='padding:2px 8px; font-size:0.75rem;'>Revoke</button></td></tr>`;
            });
            html += "</tbody></table>";
            tokensListContainer.innerHTML = html;
            tokensListContainer.querySelectorAll("button[data-revoke-prefix]").forEach(btn => {
                btn.onclick = async () => {
                    const p = btn.getAttribute("data-revoke-prefix").replace(/\.+$/, "");
                    if (!confirm(`Revoke API token ${p}...? Connected apps will immediately lose access.`)) return;
                    try {
                        const r = await api.fetchApi("/mss-login/api/tokens", {
                            method: "DELETE",
                            body: JSON.stringify({ token_hash_prefix: p })
                        });
                        if (r.ok) {
                            loadTokens();
                        } else {
                            const errData = await r.json().catch(() => ({}));
                            alert(errData.error || "Failed to revoke token.");
                        }
                    } catch (e) {
                        alert("Error: " + (e.message || String(e)));
                    }
                };
            });
        } catch (_) {
            tokensListContainer.innerHTML = "<p class='mss-login-note'>Error loading tokens.</p>";
        }
    };

    const loadSessions = async () => {
        sessionsListContainer.innerHTML = "<p class='mss-login-note'>Loading sessions...</p>";
        try {
            const res = await api.fetchApi("/mss-login/me/sessions", { method: "GET" });
            if (!res.ok) {
                sessionsListContainer.innerHTML = "<p class='mss-login-note'>Unable to load sessions.</p>";
                return;
            }
            const data = await res.json().catch(() => ({}));
            const list = Array.isArray(data.sessions) ? data.sessions : [];
            if (!list.length) {
                sessionsListContainer.innerHTML = "<p class='mss-login-note'>No active sessions found.</p>";
                return;
            }
            let html = "<table class='mss-login-table' style='width:100%; border-collapse:collapse; font-size:0.85rem;'><thead><tr style='border-bottom:1px solid #333;'><th style='padding:6px;'>Token</th><th style='padding:6px;'>Created</th><th style='padding:6px;'>Last Used</th><th style='padding:6px;'>Type</th><th style='padding:6px;'>Action</th></tr></thead><tbody>";
            list.forEach(s => {
                const jti = s.jti || "";
                const last4 = jti.slice(-4);
                const created = s.created_at_iso ? new Date(s.created_at_iso).toLocaleString() : "N/A";
                const lastUsed = s.last_used_at_iso ? new Date(s.last_used_at_iso).toLocaleString() : "—";
                const isCurrent = !!s.is_current;
                html += `<tr data-jti='${escapeHtml(jti)}'><td style='padding:6px; font-family:monospace;'><span class='token-display' data-masked='true'>••••••••••••-${last4}</span> ${isCurrent ? "<button type='button' class='mss-eye-btn' title='Reveal token' style='background:none; border:none; cursor:pointer;'>👁️</button>" : ""}</td><td style='padding:6px;'>${created}</td><td style='padding:6px;'>${lastUsed}</td><td style='padding:6px;'>${isCurrent ? "<span style='color:#a855f7; font-weight:600;'>Current Session</span>" : "Other"}</td><td style='padding:6px;'><button type='button' class='mss-login-btn mss-login-btn-danger mss-revoke-session-btn' style='padding:2px 8px; font-size:0.75rem;'>Revoke</button></td></tr>`;
            });
            html += "</tbody></table>";
            sessionsListContainer.innerHTML = html;

            sessionsListContainer.querySelectorAll(".mss-eye-btn").forEach(btn => {
                btn.onclick = async () => {
                    const tr = btn.closest("tr");
                    const span = tr.querySelector(".token-display");
                    const isMasked = span.getAttribute("data-masked") === "true";
                    if (!isMasked) {
                        const jti = tr.getAttribute("data-jti");
                        span.textContent = "••••••••••••-" + jti.slice(-4);
                        span.setAttribute("data-masked", "true");
                        btn.textContent = "👁️";
                        return;
                    }
                    try {
                        const r = await api.fetchApi("/mss-login/me/current-token");
                        if (!r.ok) return;
                        const d = await r.json().catch(() => ({}));
                        if (!d.is_https) {
                            if (!confirm("Connection is not secure (HTTP). Revealing the token over unencrypted HTTP may expose it. Proceed anyway?")) return;
                        }
                        if (d.token) {
                            span.textContent = d.token;
                            span.setAttribute("data-masked", "false");
                            btn.textContent = "🔒";
                        }
                    } catch (_) {}
                };
            });

            sessionsListContainer.querySelectorAll(".mss-revoke-session-btn").forEach(btn => {
                btn.onclick = async () => {
                    const tr = btn.closest("tr");
                    const jti = tr.getAttribute("data-jti");
                    if (!confirm("Revoke this session? If this is your current device, you will be logged out.")) return;
                    try {
                        const r = await api.fetchApi("/mss-login/me/sessions/revoke", {
                            method: "POST",
                            body: JSON.stringify({ jti })
                        });
                        if (r.ok) {
                            tr.remove();
                            const isCur = tr.innerText.includes("Current Session");
                            if (isCur) window.location.href = "/logout";
                        }
                    } catch (_) {}
                };
            });
        } catch (_) {
            sessionsListContainer.innerHTML = "<p class='mss-login-note'>Error loading sessions.</p>";
        }
    };

    createBtn.onclick = async () => {
        setStatus("");
        const expireHours = expireInput.value.trim() || "720";
        const label = labelInput.value.trim();
        const pass = passInput.value;

        if (!mfaTempToken && !pass) {
            setStatus("Please re-enter your password to confirm.", true);
            return;
        }

        createBtn.disabled = true;
        try {
            const body = {
                label,
                expire_hours: expireHours,
                require_password_reauth: true,
                username: currentUser?.username || "",
            };
            if (mfaTempToken) {
                body.mfa_temp_token = mfaTempToken;
                const mfaCode = mfaCodeInput.value.trim().replace(/\s/g, "");
                const backup = mfaBackupInput.value.trim();
                if (backup) body.backup_code = backup;
                else if (mfaCode) body.code = mfaCode;
            } else {
                body.password = pass;
            }

            const res = await api.fetchApi("/mss-login/generate_token", {
                method: "POST",
                body: JSON.stringify(body)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                setStatus(data?.error || "Failed to create token.", true);
                createBtn.disabled = false;
                return;
            }

            if (data.mfa_required && data.mfa_temp_token) {
                mfaTempToken = data.mfa_temp_token;
                mfaRow.style.display = "flex";
                setStatus("MFA required. Enter code or backup code and click Create Token again.");
                createBtn.disabled = false;
                return;
            }

            if (data.jwt_token) {
                outputCode.textContent = data.jwt_token;
                outputWrap.style.display = "block";
                setStatus("Token created successfully!");
                passInput.value = "";
                mfaRow.style.display = "none";
                mfaTempToken = null;
                await loadTokens();
            } else {
                setStatus(data?.message || "Token created.");
            }
        } catch (e) {
            setStatus("Error: " + (e.message || String(e)), true);
        } finally {
            createBtn.disabled = false;
        }
    };

    copyBtn.onclick = async () => {
        const text = outputCode.textContent;
        if (!text) return;
        try {
            await navigator.clipboard.writeText(text);
            copyBtn.textContent = "✓ Copied!";
            setTimeout(() => { copyBtn.textContent = "Copy Token"; }, 2000);
        } catch (_) {
            alert("Clipboard copy failed. Please select and copy manually.");
        }
    };

    refreshTokensBtn.onclick = () => loadTokens();
    refreshSessionsBtn.onclick = () => loadSessions();

    await Promise.all([loadTokens(), loadSessions()]);
}

async renderS3Settings(container) {
    const isOwner = Array.isArray(currentUser?.groups)
        && currentUser.groups.map(g => String(g).toLowerCase()).includes("owner");
    if (!isOwner) {
        container.innerHTML = `
            <div class="mss-login-section">
                <h3>S3 Settings</h3>
                <p>Only the owner account can view or modify S3 mount settings.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="mss-login-section">
            <h3>S3 Mount Settings</h3>
            <p>Configure the mounted S3 storage used for shared models and per-user workflow sync. Secrets are encrypted at rest and are never sent back to the browser after save.</p>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; flex-wrap:wrap;">
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-enabled"> Enable S3 storage</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-mount-enabled"> Enable s3fs mount</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-workflow-enabled"> Enable workflow sync</label>
            </div>
            <div class="mss-login-row" style="margin-top:14px; gap:12px; flex-wrap:wrap;">
                <div style="min-width:260px; flex:1;">
                    <label class="mss-login-field-label">Bucket name</label>
                    <input type="text" id="mss-login-s3-bucket" class="mss-login-input" placeholder="my-bucket">
                </div>
                <div style="min-width:320px; flex:2;">
                    <label class="mss-login-field-label">Endpoint URL</label>
                    <input type="text" id="mss-login-s3-endpoint" class="mss-login-input" placeholder="https://s3.amazonaws.com">
                </div>
            </div>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; flex-wrap:wrap;">
                <div style="min-width:180px; flex:1;">
                    <label class="mss-login-field-label">Region</label>
                    <input type="text" id="mss-login-s3-region" class="mss-login-input" placeholder="us-east-1">
                </div>
                <div style="min-width:220px; flex:1;">
                    <label class="mss-login-field-label">Prefix</label>
                    <input type="text" id="mss-login-s3-prefix" class="mss-login-input" placeholder="comfyui">
                </div>
                <div style="min-width:260px; flex:2;">
                    <label class="mss-login-field-label">Local mount path</label>
                    <input type="text" id="mss-login-s3-mount-path" class="mss-login-input" placeholder="s3_mount">
                </div>
            </div>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; flex-wrap:wrap;">
                <div style="min-width:260px; flex:1;">
                    <label class="mss-login-field-label">Access key ID</label>
                    <input type="password" id="mss-login-s3-access-key" class="mss-login-input" placeholder="Leave blank to keep stored key">
                    <label style="display:flex; align-items:center; gap:8px; margin-top:6px;"><input type="checkbox" id="mss-login-s3-clear-access-key"> Clear stored access key</label>
                </div>
                <div style="min-width:260px; flex:1;">
                    <label class="mss-login-field-label">Secret access key</label>
                    <input type="password" id="mss-login-s3-secret-key" class="mss-login-input" placeholder="Leave blank to keep stored secret">
                    <label style="display:flex; align-items:center; gap:8px; margin-top:6px;"><input type="checkbox" id="mss-login-s3-clear-secret-key"> Clear stored secret</label>
                </div>
            </div>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; flex-wrap:wrap;">
                <div style="min-width:260px; flex:1;">
                    <label class="mss-login-field-label">Model folders</label>
                    <input type="text" id="mss-login-s3-model-folders" class="mss-login-input" placeholder="checkpoints, loras, vae">
                </div>
                <div style="min-width:180px; flex:1;">
                    <label class="mss-login-field-label">Workflow sync interval (seconds)</label>
                    <input type="number" id="mss-login-s3-workflow-interval" class="mss-login-input" min="10" step="10">
                </div>
                <div style="min-width:220px; flex:1;">
                    <label class="mss-login-field-label">Workflow conflict strategy</label>
                    <select id="mss-login-s3-conflict-strategy" class="mss-login-select">
                        <option value="newer_wins">Newer wins</option>
                        <option value="local_wins">Local wins</option>
                        <option value="s3_wins">S3 wins</option>
                    </select>
                </div>
            </div>
            <div class="mss-login-row" style="margin-top:12px; gap:12px; flex-wrap:wrap;">
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-use-path-style"> Use path-style requests</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-allow-other"> Allow other users/processes</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-auto-install"> Auto-install s3fs when possible</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-read-only"> Mount read-only</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-mount-output"> Expose output folder</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-mount-input"> Expose input folder</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-sync-on-save"> Sync workflows on save</label>
                <label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" id="mss-login-s3-sync-on-delete"> Sync workflows on delete</label>
            </div>
            <div class="mss-login-row" style="margin-top:16px; gap:8px; flex-wrap:wrap;">
                <button class="mss-login-btn" id="mss-login-s3-refresh">Refresh status</button>
                <button class="mss-login-btn" id="mss-login-s3-save">Save settings</button>
                <button class="mss-login-btn secondary" id="mss-login-s3-remount">Remount</button>
                <button class="mss-login-btn danger" id="mss-login-s3-unmount">Unmount</button>
            </div>
            <p id="mss-login-s3-status-text" class="mss-login-note" style="margin-top:8px;"></p>
            <label class="mss-login-field-label" style="margin-top:14px;">S3 runtime status</label>
            <textarea id="mss-login-s3-status" class="mss-login-textarea" readonly style="min-height:180px;"></textarea>
        </div>
    `;

    const getEl = (selector) => container.querySelector(selector);
    const statusText = getEl("#mss-login-s3-status-text");
    const statusBox = getEl("#mss-login-s3-status");

    function renderStatus(data) {
        const status = data?.status || data || {};
        const workflowStatus = data?.workflow_status || {};
        statusBox.value = JSON.stringify({
            status,
            workflow_status: workflowStatus,
        }, null, 2);
        const mounted = status?.mounted ? "mounted" : "not mounted";
        const mode = status?.mode || "unknown";
        statusText.textContent = `S3 runtime is ${mounted}. Mode: ${mode}. ${status?.last_error || ""}`.trim();
    }

    async function loadConfig() {
        statusText.textContent = "Loading S3 settings...";
        const res = await api.fetchApi("/mss-login/api/s3/config", { method: "GET" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            statusText.textContent = "Error: " + (data.error || res.status);
            return;
        }

        getEl("#mss-login-s3-enabled").checked = !!data.enabled;
        getEl("#mss-login-s3-mount-enabled").checked = !!data.mount_enabled;
        getEl("#mss-login-s3-workflow-enabled").checked = !!data.workflow_sync_enabled;
        getEl("#mss-login-s3-bucket").value = data.bucket_name || "";
        getEl("#mss-login-s3-endpoint").value = data.endpoint_url || "";
        getEl("#mss-login-s3-region").value = data.region || "";
        getEl("#mss-login-s3-prefix").value = data.prefix || "";
        getEl("#mss-login-s3-mount-path").value = data.mount_local_path || "";
        getEl("#mss-login-s3-model-folders").value = (data.model_folders || []).join(", ");
        getEl("#mss-login-s3-workflow-interval").value = data.workflow_sync_interval_seconds || 60;
        getEl("#mss-login-s3-conflict-strategy").value = data.workflow_conflict_strategy || "newer_wins";
        getEl("#mss-login-s3-use-path-style").checked = !!data.use_path_style;
        getEl("#mss-login-s3-allow-other").checked = !!data.allow_other;
        getEl("#mss-login-s3-auto-install").checked = !!data.auto_install;
        getEl("#mss-login-s3-read-only").checked = !!data.read_only;
        getEl("#mss-login-s3-mount-output").checked = !!data.mount_output;
        getEl("#mss-login-s3-mount-input").checked = !!data.mount_input;
        getEl("#mss-login-s3-sync-on-save").checked = !!data.workflow_sync_on_save;
        getEl("#mss-login-s3-sync-on-delete").checked = !!data.workflow_sync_on_delete;
        getEl("#mss-login-s3-access-key").placeholder = data.has_access_key ? "Stored securely. Leave blank to keep." : "Enter access key ID";
        getEl("#mss-login-s3-secret-key").placeholder = data.has_secret_key ? "Stored securely. Leave blank to keep." : "Enter secret access key";
        getEl("#mss-login-s3-clear-access-key").checked = false;
        getEl("#mss-login-s3-clear-secret-key").checked = false;
        renderStatus(data);
    }

    getEl("#mss-login-s3-refresh").onclick = async () => {
        try {
            await loadConfig();
        } catch (e) {
            statusText.textContent = "Error: " + (e.message || "Refresh failed");
        }
    };

    getEl("#mss-login-s3-save").onclick = async () => {
        statusText.textContent = "Saving S3 settings...";
        const body = {
            enabled: getEl("#mss-login-s3-enabled").checked,
            mount_enabled: getEl("#mss-login-s3-mount-enabled").checked,
            workflow_sync_enabled: getEl("#mss-login-s3-workflow-enabled").checked,
            bucket_name: getEl("#mss-login-s3-bucket").value.trim(),
            endpoint_url: getEl("#mss-login-s3-endpoint").value.trim(),
            region: getEl("#mss-login-s3-region").value.trim(),
            prefix: getEl("#mss-login-s3-prefix").value.trim(),
            mount_local_path: getEl("#mss-login-s3-mount-path").value.trim(),
            model_folders: getEl("#mss-login-s3-model-folders").value.trim(),
            workflow_sync_interval_seconds: Number(getEl("#mss-login-s3-workflow-interval").value || 60),
            workflow_conflict_strategy: getEl("#mss-login-s3-conflict-strategy").value,
            use_path_style: getEl("#mss-login-s3-use-path-style").checked,
            allow_other: getEl("#mss-login-s3-allow-other").checked,
            auto_install: getEl("#mss-login-s3-auto-install").checked,
            read_only: getEl("#mss-login-s3-read-only").checked,
            mount_output: getEl("#mss-login-s3-mount-output").checked,
            mount_input: getEl("#mss-login-s3-mount-input").checked,
            workflow_sync_on_save: getEl("#mss-login-s3-sync-on-save").checked,
            workflow_sync_on_delete: getEl("#mss-login-s3-sync-on-delete").checked,
            clear_access_key: getEl("#mss-login-s3-clear-access-key").checked,
            clear_secret_key: getEl("#mss-login-s3-clear-secret-key").checked,
        };
        const accessKey = getEl("#mss-login-s3-access-key").value.trim();
        const secretKey = getEl("#mss-login-s3-secret-key").value.trim();
        if (accessKey) body.access_key_id = accessKey;
        if (secretKey) body.secret_access_key = secretKey;

        try {
            const res = await api.fetchApi("/mss-login/api/s3/config", {
                method: "PUT",
                body: JSON.stringify(body),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                statusText.textContent = "Error: " + (data.error || res.status);
                return;
            }
            getEl("#mss-login-s3-access-key").value = "";
            getEl("#mss-login-s3-secret-key").value = "";
            statusText.textContent = "S3 settings saved.";
            renderStatus(data);
            await loadConfig();
        } catch (e) {
            statusText.textContent = "Error: " + (e.message || "Save failed");
        }
    };

    getEl("#mss-login-s3-remount").onclick = async () => {
        statusText.textContent = "Remounting S3...";
        try {
            const res = await api.fetchApi("/mss-login/api/s3/mount/remount", { method: "POST" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                statusText.textContent = "Error: " + (data.error || res.status);
                return;
            }
            statusText.textContent = data.remounted ? "S3 remounted." : "Remount failed.";
            renderStatus(data.status || data);
            await loadConfig();
        } catch (e) {
            statusText.textContent = "Error: " + (e.message || "Remount failed");
        }
    };

    getEl("#mss-login-s3-unmount").onclick = async () => {
        statusText.textContent = "Unmounting S3...";
        try {
            const res = await api.fetchApi("/mss-login/api/s3/mount/unmount", { method: "POST" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                statusText.textContent = "Error: " + (data.error || res.status);
                return;
            }
            statusText.textContent = data.message || "Unmount attempted.";
            renderStatus(data.mount_status || {});
            await loadConfig();
        } catch (e) {
            statusText.textContent = "Error: " + (e.message || "Unmount failed");
        }
    };

    await loadConfig();
}

    renderPerms(container) {
        // --- SCANNER: Find all Settings Categories ---
        const categories = new Set();
        const adminDefaultFalseKeys = new Set([
            "can_have_non_expiring_jwt",
            "can_view_console",
            "can_manage_model_sharing",
        ]);
        // Only the owner account may edit the admin column; owner column stays immutable
        const viewerIsOwner = !!(
            currentUser &&
            (currentUser.role === "owner" ||
                (Array.isArray(currentUser.groups) &&
                    currentUser.groups.map((g) => String(g).toLowerCase()).includes("owner")))
        );
        
        // 1. Scan app.extensions
        if (app.extensions) app.extensions.forEach(e => { if(e.name) categories.add(e.name); });
        
        // 2. Scan Settings (Sidebar Buttons)
        if (app.ui.settings.settings) {
            const items = (app.ui.settings.settings instanceof Map) ? Array.from(app.ui.settings.settings.values()) : Object.values(app.ui.settings.settings);
            items.forEach(s => {
                let c = s.category;
                if(Array.isArray(c)) c = c[0];
                if(!c && s.id) c = s.id.split(".")[0];
                if(c) categories.add(c);
            });
        }
        
        // 3. Explicit Whitelist (Ensure these appear even if scanner misses them)
        [
            "User", "Comfy", "LiteGraph", "Appearance", "Extension", 
            "3D", "Mask Editor", "Keybinding", "About",
            "iTools", "Crystools", "rgthree", "Gallery", "Impact"
        ].forEach(c => categories.add(c));
        
        // Clean exclusions
        categories.delete("mss-login"); 
        categories.delete("mss-login.Configuration");
        const sortedCats = Array.from(categories).sort();

        // IDs that are already explicitly defined in Sections 1 & 2
        // (and in CSS_BLOCK_MAP). We will NOT auto-generate rows for these.
        const explicitIds = new Set(Object.keys(CSS_BLOCK_MAP));

        // --- DRAW TABLE ---
        let html = "";
        if (viewerIsOwner) {
            html += `<p class="mss-login-help" style="margin:0 0 12px; opacity:0.85;">
                As owner, you can change administrator permissions. The Owner column remains locked.
            </p>`;
        } else {
            html += `<p class="mss-login-help" style="margin:0 0 12px; opacity:0.85;">
                Administrator permissions can only be changed by the owner account.
            </p>`;
        }
        html += `<table class="mss-login-table">
            <thead><tr><th>Feature / Category</th>${GROUPS.map(g => `<th class="mss-login-check-cell">${g.toUpperCase()}</th>`).join("")}</tr></thead>
            <tbody>`;

        const drawRow = (label, id, header=false) => {
            if(header) return `<tr class="mss-login-section-row"><td colspan="${GROUPS.length+1}">${label}</td></tr>`;
            let row = `<tr><td>${label}</td>`;
            GROUPS.forEach(g => {
                let val = groupsConfig[g]?.[id];
                
                // --- CRITICAL DEFAULT LOGIC ---
                // If a setting is new (undefined), should we block it?
                // Guest: Block by default.
                // Admin: use reserved-false defaults for owner-only privileges; else allow.
                // Others: Allow by default.
                if (val === undefined) {
                    val = (g === "admin" && adminDefaultFalseKeys.has(id)) ? false : (g !== "guest");
                }
                
                // Owner column is immutable (always fully privileged)
                if (g === "owner") val = true;

                // Admin column editable only by owner; owner column always locked
                const locked = g === "owner" || (g === "admin" && !viewerIsOwner);
                row += `<td class="mss-login-check-cell"><input type="checkbox" class="perm-chk" data-group="${g}" data-key="${id}" ${val?"checked":""} ${locked?"disabled":""}></td>`;
            });
            return row + `</tr>`;
        };

        // Section 1: Backend Security
        html += drawRow("Core API Permissions", null, true);
        html += drawRow("Access ComfyUI-Manager", "can_access_manager");
        html += drawRow("Access General API", "can_access_api");
        html += drawRow("Run Workflows (Execute)", "can_run");
        html += drawRow("Modify Workflows (Save)", "can_modify_workflows");
        html += drawRow("Upload Files", "can_upload");
        html += drawRow("Can Have API Tokens", "can_have_api_tokens");
        html += drawRow("Non-expiring JWT (session + API token 0 = never expire)", "can_have_non_expiring_jwt");
        html += drawRow("SettingsExtension", "settings_extension");
        html += drawRow("See Restricted Settings", "can_see_restricted_settings");
        html += drawRow("View built-in Console (bottom panel)", "can_view_console");
        html += drawRow("View all ComfyUI items (models, LoRAs, VAEs, embeddings)", "can_view_all_comfyui_items");
        html += drawRow("Access S3 Storage (mount, sync, API)", "can_access_s3_storage");
        html += drawRow("Download models (queue, view, cancel own jobs)", "can_download_models");
        html += drawRow("Manage model sharing (grant/revoke model access)", "can_manage_model_sharing");
        html += drawRow("Login locally without authentication (Tailscale / Local Network)", "can_login_locally_without_auth");
        html += drawRow("Update MSS-Login (apply self-update from release check)", "can_update_mss_login");

        // Section 2: Global UI
        html += drawRow("Interface Elements", null, true);
        html += drawRow("Allow Workflow Breadcrumb", "ui_workflow_breadcrumb");
        html += drawRow("Batch Count Widget", "ui_batch_widget");
        html += drawRow("Extra Options (Batch)", "ui_extra_options");
    
        html += drawRow("Sidebar / Floating Menu", null, true);
        html += drawRow("Sidebar Menu: Save", "ui_menu_save");
        html += drawRow("Sidebar Menu: Load", "ui_menu_load");
        html += drawRow("Sidebar Menu: Queue Button", "ui_queue_button");
        html += drawRow("Sidebar: History", "ui_side_history");
        html += drawRow("Sidebar: Queue", "ui_side_queue");
        html += drawRow("Sidebar: Assets", "ui_side_assets");
        html += drawRow("Sidebar: Templates", "ui_side_templates");
        html += drawRow("Sidebar: Models", "ui_side_models");
        html += drawRow("Sidebar Menu: Browse Templates", "ui_menu_templates");
        html += drawRow("Sidebar Menu: Manage Extensions", "ui_menu_extensions");
        html += drawRow("Sidebar Menu: Manager Button", "ui_menu_manager");

        //  Section 3: Settings Menu Options
        html += drawRow("Settings Menu", null, true);
        html += drawRow("Settings Menu: Comfy", "settings_comfy");
        html += drawRow("Settings Menu: Lite Graph", "settings_litegraph");
        html += drawRow("Settings Menu: Appearance", "settings_appearance");
        html += drawRow("Settings Menu: 3D", "settings_3d");
        html += drawRow("Settings Menu: User", "settings_user");
        html += drawRow("Settings Menu: Keybinding", "settings_keybinding");
        html += drawRow("Settings Menu: Extension", "settings_extension");
        html += drawRow("Settings Menu: Mask Editor", "settings_maskeditor");
        html += drawRow("Settings Menu: About", "settings_about");
        html += drawRow("Settings Menu: mss-login", "settings_mss_loginsettings");
        
        // Section 4: Extensions
        html += drawRow("Extension UI & Settings Categories", null, true);
        sortedCats.forEach(c => {
            const id = getSanitizedId(c);

            // If this id is already explicitly handled in Sections 1/2 (or in CSS_BLOCK_MAP),
            // skip it so we don't show duplicate/ghost toggles.
            if (explicitIds.has(id)) return;

            html += drawRow(c, id);
        });


        html += `</tbody></table>`;
        container.innerHTML = html;

        // Bind Checkboxes
        container.querySelectorAll(".perm-chk").forEach(chk => {
            chk.onchange = async () => {
                const g = chk.dataset.group;
                const k = chk.dataset.key;
                const v = chk.checked;
                
                if(!groupsConfig[g]) groupsConfig[g] = {};
                groupsConfig[g][k] = v;
                
                // Save to server
                await api.fetchApi("/mss-login/api/groups", { method: "PUT", body: JSON.stringify({ groups: { [g]: { [k]: v } } }) });
                
                // Apply immediately
                updateEnforcementStyles();
            };
        });
    }
}

// --- 4. ENFORCEMENT ENGINE (CSS INJECTION) ---

const SETTINGS_NAV_KEY_ALIASES = {
    comfy: "settings_comfy",
    litegraph: "settings_litegraph",
    "lite graph": "settings_litegraph",
    appearance: "settings_appearance",
    "3d": "settings_3d",
    "mask editor": "settings_maskeditor",
    user: "settings_user",
    keybinding: "settings_keybinding",
    extension: "settings_extension",
    about: "settings_about",
    "mss-login": "settings_mss_loginsettings",
    "mss-login.configuration": "settings_mss_loginsettings"
};

function settingsKeyFromNav(navId, label) {
    const raw = String(navId || label || "").trim();
    const lower = raw.toLowerCase();
    if (SETTINGS_NAV_KEY_ALIASES[lower]) return SETTINGS_NAV_KEY_ALIASES[lower];
    if (lower.startsWith("mss-login")) return "settings_mss_loginsettings";
    return getSanitizedId(raw);
}

function isPermissionAllowed(cfg, role, key) {
    if (role === "owner") return true;
    let val = cfg ? cfg[key] : undefined;
    if (val === undefined && key === "settings_appearance") {
        val = cfg ? cfg["settings_makadiappearance"] : undefined;
    }
    if (val === undefined && key === "settings_3d") {
        val = cfg ? cfg["Settings_3D"] : undefined;
    }
    if (val === undefined) {
        return role !== "guest";
    }
    return val === true;
}

function isMssLoginSettingsNav(key, navId, label) {
    const blob = `${key || ""} ${navId || ""} ${label || ""}`.toLowerCase();
    return blob.includes("mss-login") || key === "settings_mss_loginsettings";
}

async function updateEnforcementStyles() {
    if (!currentUser) currentUser = await getData("/mss-login/api/me");
    if (!currentUser) return;

    if (!groupsConfig || Object.keys(groupsConfig).length === 0) {
        const d = await getData("/mss-login/api/groups");
        groupsConfig = d?.groups || {};
    }

    const role = currentUser.role || "user";

    // 🔧 SAFETY OVERRIDE:
    // On the *UI side*, "guest" is NEVER treated as admin,
    // even if the backend accidentally flagged it.
    if (role === "guest") {
        currentUser.is_admin = false;
    }

    const baseCfg = groupsConfig[role] || {};

    //console.log("[mss-login] enforcement entry:", {
     //   role,
      //  is_admin: currentUser.is_admin,
       // baseCfgKeys: Object.keys(baseCfg),
        //guestCfgKeys: Object.keys(groupsConfig["guest"] || {})
    //});

    // Owner sees every UI control. Other roles, including admin, follow
    // their permission flags (e.g. admin can_view_console defaults to false).
    if (role === "owner") {
        const style = document.getElementById("mss-login-css-block");
        if (style) style.textContent = "";
        return;
    }

    let css = "";

    // 🔒 HARDENED LOGIC FOR GUEST
    if (role === "guest") {
        const guestCfg = groupsConfig["guest"] || {};

        for (const [key, selectors] of Object.entries(CSS_BLOCK_MAP)) {
            // Always allow mss-login settings so guests can log out / register
            if (key === "settings_mss_loginsettings") {
                continue;
            }

            const allowed = isPermissionAllowed(guestCfg, "guest", key);
            if (!allowed) {
                css +=
                    selectors.join(", ") +
                    " { display: none !important; opacity: 0 !important; pointer-events: none !important; } \n";
            }
        }

        css += `.mss-login-blocked-item { display: none !important; }`;
        // Always show logout button and mss-login menu - never hide them for guests
        css += `#mss-login-settings-logout-btn, [data-mss-login-always-visible="true"] { display: block !important; visibility: visible !important; opacity: 1 !important; }`;
        css += `li[aria-label='mss-login'], li[aria-label='mss-login'], li.p-listbox-option[aria-label='mss-login'], li.p-listbox-option[aria-label='mss-login'] { display: block !important; visibility: visible !important; opacity: 1 !important; }`;

        let styleTag = document.getElementById("mss-login-css-block");
        if (!styleTag) {
            styleTag = document.createElement("style");
            styleTag.id = "mss-login-css-block";
            document.head.appendChild(styleTag);
        }
        styleTag.textContent = css;

        enforceSidebar(guestCfg, role);
        enforceMenus(guestCfg, role);
        enforceConsole(guestCfg, role);
        enforceModels(guestCfg, role);
        patchSaveConfirmDialog(guestCfg, role);
        
        // Ensure logout button is always visible for guests
        const logoutBtn = document.getElementById("mss-login-settings-logout-btn");
        if (logoutBtn) {
            logoutBtn.style.display = "block";
            logoutBtn.style.visibility = "visible";
            logoutBtn.style.opacity = "1";
            logoutBtn.classList.remove("mss-login-blocked-item");
        }
        
        return;
    }

    // ... rest of non-guest logic ...
    const cfg = baseCfg;

    console.log("[mss-login] enforcement (non-guest):", {
        role,
        cfgKeys: Object.keys(cfg),
        ui_menu_templates: cfg["ui_menu_templates"],
        ui_menu_extensions: cfg["ui_menu_extensions"]
    });

    // --- A. BLOCK GLOBAL UI ELEMENTS (Fastest) ---
    for (const [key, selectors] of Object.entries(CSS_BLOCK_MAP)) {
        if (key === "settings_mss_loginsettings") {
            continue;
        }
        if (!isPermissionAllowed(cfg, role, key)) {
            const rule =
                selectors.join(", ") +
                " { display: none !important; opacity: 0 !important; pointer-events: none !important; } \n";
            css += rule;
        }
    }

    css += `.mss-login-blocked-item { display: none !important; }`;
    // Always show logout button - never hide it for any user
    css += `#mss-login-settings-logout-btn, [data-mss-login-always-visible="true"] { display: block !important; visibility: visible !important; opacity: 1 !important; }`;

    // Apply to Head
    let styleTag = document.getElementById("mss-login-css-block");
    if (!styleTag) {
        styleTag = document.createElement("style");
        styleTag.id = "mss-login-css-block";
        document.head.appendChild(styleTag);
    }
    styleTag.textContent = css;

    // Trigger the JS sidebar scanner immediately
    enforceSidebar(cfg, role);
    enforceMenus(cfg, role);
    enforceConsole(cfg, role);
    enforceModels(cfg, role);
    patchSaveConfirmDialog(cfg, role);
    
    // Ensure logout button is always visible
    const logoutBtn = document.getElementById("mss-login-settings-logout-btn");
    if (logoutBtn) {
        logoutBtn.style.display = "block";
        logoutBtn.style.visibility = "visible";
        logoutBtn.style.opacity = "1";
        logoutBtn.classList.remove("mss-login-blocked-item");
    }
}

function hideEl(el) {
    if (!el) return;
    el.classList.add("mss-login-blocked-item");
    el.style.setProperty("display", "none", "important");
    el.style.setProperty("visibility", "hidden", "important");
    el.style.setProperty("pointer-events", "none", "important");
}

function showEl(el) {
    if (!el) return;
    el.classList.remove("mss-login-blocked-item");
    el.style.removeProperty("display");
    el.style.removeProperty("visibility");
    el.style.removeProperty("pointer-events");
}

// Sidebar Scanner: hide settings categories in both legacy and current dialogs
function enforceSidebar(cfg, role) {
    const roots = [
        document.querySelector(".comfy-modal"),
        document.querySelector("[data-testid='settings-dialog']")
    ].filter(Boolean);
    if (!roots.length) return;

    roots.forEach((root) => {
        const items = root.querySelectorAll(
            "button, [role='button'], [data-nav-id], .comfy-settings-btn, tr, .pysssss-settings-category"
        );

        items.forEach(el => {
            if (el.id === "mss-login-settings-logout-btn" ||
                el.innerText?.includes("Logout current user") ||
                el.querySelector("#mss-login-settings-logout-btn")) {
                showEl(el);
                return;
            }

            const navId = el.getAttribute("data-nav-id") || "";
            const ariaLabel = el.getAttribute("aria-label") || "";
            const txt = (el.innerText || "").trim();
            if (!navId && (!txt || txt.length > 40 || txt === "Close" || txt === "Back")) return;

            const catId = settingsKeyFromNav(navId || ariaLabel, txt);

            if (isMssLoginSettingsNav(catId, navId, txt) || ariaLabel.toLowerCase() === "mss-login") {
                showEl(el);
                return;
            }

            if (!isPermissionAllowed(cfg, role, catId)) {
                hideEl(el);
            } else {
                showEl(el);
            }
        });
    });
}

function enforceConsole(cfg, role) {
    if (isPermissionAllowed(cfg, role, "can_view_console")) return;

    document.querySelectorAll(".side-bar-button").forEach((el) => {
        const label = `${el.getAttribute("aria-label") || ""} ${el.textContent || ""}`.toLowerCase();
        const hasTerminalIcon = !!el.querySelector("[class*='ph--terminal'], [class*='terminal-bold']");
        if (
            hasTerminalIcon ||
            label.includes("console") ||
            label.includes("logs") ||
            label.includes("terminal") ||
            label.includes("bottom panel")
        ) {
            hideEl(el);
        }
    });

    document.querySelectorAll("[role='tab'], .p-tab").forEach((tab) => {
        const title = (tab.textContent || "").trim().toUpperCase();
        if (title === "LOGS" || title === "TERMINAL" || title === "CONSOLE") {
            hideEl(tab);
            const panel = tab.closest(".p-splitterpanel") ||
                tab.closest("[class*='bottom-panel']") ||
                tab.closest(".flex.h-full.flex-col");
            if (panel) hideEl(panel);
        }
    });

    document.querySelectorAll(".xterm, .xterm-screen, .xterm-viewport").forEach((el) => {
        const pane = el.closest(".p-splitterpanel") || el.closest(".flex.h-full.flex-col") || el;
        hideEl(pane);
    });
}

function enforceModels(cfg, role) {
    if (isPermissionAllowed(cfg, role, "ui_side_models")) return;

    document.querySelectorAll(".side-bar-button").forEach((el) => {
        const label = `${el.getAttribute("aria-label") || ""} ${el.getAttribute("title") || ""} ${el.textContent || ""}`.toLowerCase();
        if (label.includes("models") && !label.includes("model manager")) {
            hideEl(el);
        }
    });

    document.querySelectorAll("[role='tab'], .p-tab, [data-panel-id='models']").forEach((tab) => {
        const title = `${tab.getAttribute("aria-label") || ""} ${tab.textContent || ""}`.trim().toLowerCase();
        if (title === "models" || title.includes("models")) {
            hideEl(tab);
        }
    });
}

// Top menu enforcement: runs on the PrimeVue menubar
function enforceMenus(cfg, role) {
    const shouldBlock = (key) => !isPermissionAllowed(cfg, role, key);

    // Block "Browse Templates"
    if (shouldBlock("ui_menu_templates")) {
        document
            .querySelectorAll("li.p-tieredmenu-item[aria-label='Browse Templates']")
            .forEach(el => el.remove());
    }

    // Block "Manage Extensions"
    if (shouldBlock("ui_menu_extensions")) {
        document
            .querySelectorAll("li.p-tieredmenu-item[aria-label='Manage Extensions']")
            .forEach(el => el.remove());
    }

    // Block File → Save / Save As / Export / Export (API)
    if (shouldBlock("ui_menu_save")) {
        document
            .querySelectorAll(
                "li.p-tieredmenu-item[aria-label='Save'], " +
                "li.p-tieredmenu-item[aria-label='Save As'], " +
                "li.p-tieredmenu-item[aria-label='Export'], " +
                "li.p-tieredmenu-item[aria-label='Export (API)']"
            )
            .forEach(el => el.remove());
    }

    // Block File → Open
    if (shouldBlock("ui_menu_load")) {
        document
            .querySelectorAll("li.p-tieredmenu-item[aria-label='Open']")
            .forEach(el => el.remove());
    }
}

function patchSaveConfirmDialog(cfg, role) {
    // Figure out if this role is allowed to save/modify
    let canModify = true;

    if (cfg["can_modify_workflows"] === false) {
        canModify = false;
    } else if (role === "guest") {
        // Guests are blocked unless explicitly allowed
        if (cfg["can_modify_workflows"] !== true && cfg["ui_menu_save"] !== true) {
            canModify = false;
        }
    }

    if (canModify) return;

    // Look for PrimeVue confirm dialogs
    const dialogs = document.querySelectorAll(".p-confirm-dialog, .p-dialog.p-confirm-dialog");
    dialogs.forEach((dlg) => {
        if (!dlg) return;

        // Avoid double-patching the same instance
        if (dlg.dataset.mss_loginPatched === "1") return;

        const titleEl =
            dlg.querySelector(".p-dialog-header .p-dialog-title") ||
            dlg.querySelector(".p-dialog-header") ||
            dlg.querySelector(".p-confirm-dialog-message h2");

        const msgEl =
            dlg.querySelector(".p-confirm-dialog-message") ||
            dlg.querySelector(".p-dialog-content");

        const rawTitle = (titleEl && titleEl.textContent) || "";
        const rawMsg = (msgEl && msgEl.textContent) || "";
        const combined = (rawTitle + " " + rawMsg).toLowerCase();

        // Only touch dialogs that look like "unsaved changes" / "save" prompts
        if (
            !combined ||
            (!combined.includes("save") &&
             !combined.includes("unsaved") &&
             !combined.includes("changes"))
        ) {
            return;
        }

        dlg.dataset.mss_loginPatched = "1";

        // --- Rewrite title / body text ---
        if (titleEl) {
            titleEl.textContent = "Save Changes?";
        }

        if (msgEl) {
            msgEl.innerHTML = `
                <div>
                    <h3 style="margin: 0 0 0.5rem;">Access denied</h3>
                    <p style="margin: 0 0 0.5rem;">
                        Your role is not allowed to save or modify workflows.
                    </p>
                    <p style="margin: 0;">
                        You may close the workflow without saving, or cancel to keep it open.
                    </p>
                </div>
            `;
        }

        // --- Hard-block ONLY the "Save" / "accept" button ---
        let saveBtn =
            dlg.querySelector(".p-confirm-dialog-accept") ||
            dlg.querySelector("button[data-pc-section='acceptbutton']");

        if (!saveBtn) {
            // Fallback: find a button whose label includes "save"
            dlg.querySelectorAll("button").forEach((btn) => {
                const label = (btn.textContent || "").trim().toLowerCase();
                if (!saveBtn && label.includes("save")) {
                    saveBtn = btn;
                }
            });
        }

        if (saveBtn) {
            // Visual hint that it's disabled
            saveBtn.style.opacity = "0.5";

            const block = (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                ev.stopImmediatePropagation();
                console.warn("[mss-login] Blocked Save in confirm dialog for this role");
                // Do NOT close the dialog; user can still click "Close without saving" / "Cancel"
            };

            // Catch both click and pointerdown before PrimeVue sees them
            saveBtn.addEventListener("click", block, { capture: true });
            saveBtn.addEventListener("pointerdown", block, { capture: true });
        }

        // We do NOT touch the reject / cancel button:
        // - .p-confirm-dialog-reject
        // - button[data-pc-section='rejectbutton']
        // Those remain fully usable so they can bail out safely.
    });
}

// Workflow Save / Load Interception
function isWorkflowSaveAllowed() {
    if (!currentUser || !groupsConfig) return true; // fail-open for safety until we know
    const role = currentUser.role || "user";
    const cfg = groupsConfig[role] || {};
    // If ui_menu_save is explicitly false → disallow
    if (cfg["ui_menu_save"] === false) return false;
    // Guests default to disallowed if not explicitly true
    if (role === "guest" && cfg["ui_menu_save"] !== true) return false;
    return true;
}

function isWorkflowLoadAllowed() {
    if (!currentUser || !groupsConfig) return true;
    const role = currentUser.role || "user";
    const cfg = groupsConfig[role] || {};
    if (cfg["ui_menu_load"] === false) return false;
    if (role === "guest" && cfg["ui_menu_load"] !== true) return false;
    return true;
}

// Intercept "unsaved workflow" dialogs for roles that cannot save
function guardUnsavedWorkflowDialog() {
    // If the current role IS allowed to save, do nothing
    if (isWorkflowSaveAllowed()) return;

    // PrimeVue dialogs generally use .p-dialog
    const dialogs = document.querySelectorAll(".p-dialog");
    dialogs.forEach(dialog => {
        // Skip if we already patched this dialog
        if (dialog.dataset.mss_loginGuarded === "1") return;

        const text = (dialog.innerText || "").toLowerCase();

        // Heuristic: look for dialogs that are clearly about saving workflows / unsaved changes
        if (
            !text.includes("save") || 
            (!text.includes("workflow") && !text.includes("unsaved"))
        ) {
            return;
        }

        dialog.dataset.mss_loginGuarded = "1";

        // Find the "Save" button in this dialog
        let saveButton = null;
        dialog.querySelectorAll("button").forEach(btn => {
            const label = (btn.innerText || "").trim().toLowerCase();
            if (label === "save" || label === "save workflow") {
                saveButton = btn;
            }
        });

        // If we found a Save button, kill it
        if (saveButton) {
            // You can either disable it or remove it:
            // Option A: Disable + style
            // saveButton.disabled = true;
            // saveButton.classList.add("mss-login-blocked-item");

            // Option B: Just remove it entirely (cleanest UX for guests)
            saveButton.remove();

            console.warn("[mss-login] Blocked workflow save from unsaved-changes dialog for this role.");
        }

        // Rewrite dialog content with an Access Denied style message
        const body = dialog.querySelector(".p-dialog-content");
        if (body) {
            body.innerHTML = `
                <p><strong>Access denied</strong></p>
                <p>Your role is not allowed to save or modify workflows.</p>
                <p>You may close the workflow without saving, or cancel to keep it open.</p>
            `;
        }
    });
}

// --- WORKFLOW SAVE DENIED UI HOOKS ---

function showWorkflowDeniedToast(message) {
    // Simple top-right toast; non-intrusive but visible
    let existing = document.getElementById("mss-login-workflow-denied-toast");
    if (existing) {
        existing.remove();
    }

const toast = document.createElement("div");
toast.id = "mss-login-workflow-denied-toast";

// --- Container Styling ---
Object.assign(toast.style, {
    position: "fixed",
    top: "50%",
    left: "50%",
    transform: "translateX(-50%)",
    zIndex: "99999",
    padding: "14px 18px",
    maxWidth: "380px",
    width: "calc(100% - 40px)",
    borderRadius: "10px",
    background: "rgba(30, 30, 30, 0.85)",
    backdropFilter: "blur(6px)",
    color: "#fff",
    fontSize: "14px",
    display: "flex",
    alignItems: "flex-start",
    gap: "12px",
    boxShadow: "0 6px 30px rgba(0,0,0,0.35)",
    border: "1px solid rgba(255,255,255,0.12)",
    opacity: "0",
    transition: "opacity 0.25s ease, transform 0.25s ease",
});

// --- Content ---
toast.innerHTML = `
    <div style="font-size:18px; line-height:1; margin-top:1px;">⛔</div>
    <div style="flex:1;">
        <div style="font-weight:600; margin-bottom:3px; font-size:15px;">Action blocked</div>
        <div style="opacity:0.9;">
            ${message || "You are not allowed to save or delete workflows with this account."}
        </div>
    </div>

    <button id="mss-login-toast-close" style="
        background:rgba(255,255,255,0.08);
        border:none;
        width:24px;
        height:24px;
        border-radius:6px;
        cursor:pointer;
        font-size:13px;
        color:#fff;
        display:flex;
        align-items:center;
        justify-content:center;
        transition:background 0.2s;
    ">✕</button>
`;

// --- Hover effect on close button ---
toast.querySelector("#mss-login-toast-close").onmouseover = () =>
    toast.querySelector("#mss-login-toast-close").style.background =
        "rgba(255,255,255,0.18)";
toast.querySelector("#mss-login-toast-close").onmouseout = () =>
    toast.querySelector("#mss-login-toast-close").style.background =
        "rgba(255,255,255,0.08)";

toast.querySelector("#mss-login-toast-close").onclick = () => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(-50%) translateY(-6px)";
    setTimeout(() => toast.remove(), 220);
};

// --- Animate in ---
setTimeout(() => {
    toast.style.opacity = "1";
    toast.style.transform = "translateX(-50%) translateY(0)";
}, 20);

    document.body.appendChild(toast);

    // Auto-hide after 6s
    setTimeout(() => {
        if (toast.parentNode) toast.remove();
    }, 6000);
}

function installWorkflowSaveDeniedWatcher() {
    // Avoid double-wrapping if something reloads
    if (window.fetch && window.fetch.__mss_loginWrapped) return;

    const originalFetch = window.fetch;

    async function wrappedFetch(input, init) {
        const response = await originalFetch(input, init);

        try {
            const url =
                typeof input === "string"
                    ? input
                    : (input && input.url) || "";

            // We only care about 403s on the workflow userdata endpoint
            if (response.status === 403 && url.includes("/api/userdata/workflows")) {
                console.debug(
                    "[mss-login] 403 on workflow endpoint (client-side watcher):",
                    url
                );

                let msg = "You are not allowed to perform workflow actions with this account.";

                // Try to peek at the JSON error if present
                try {
                    const clone = response.clone();
                    const data = await clone.json();
                    if (data && typeof data.error === "string") {
                        msg = data.error;
                    }
                } catch (e) {
                    // If body isn't JSON or cannot be parsed, just keep the default msg
                    console.debug("[mss-login] could not parse denied response JSON:", e);
                }

                // Extra safety: only show toast if this role is actually blocked from saving
                try {
                    if (!isWorkflowSaveAllowed()) {
                        showWorkflowDeniedToast(msg);
                    } else {
                        // If somehow a 403 slipped through for an allowed role, just log it
                        console.warn(
                            "[mss-login] Got 403 on workflow save despite isWorkflowSaveAllowed() = true. Message:",
                            msg
                        );
                    }
                } catch (e) {
                    // If helper blows up for some reason, still show the toast
                    console.warn("[mss-login] isWorkflowSaveAllowed() check failed:", e);
                    showWorkflowDeniedToast(msg);
                }
            }
        } catch (e) {
            console.warn("[mss-login] error in wrappedFetch watcher:", e);
        }

        return response;
    }

    wrappedFetch.__mss_loginWrapped = true;
    window.fetch = wrappedFetch;
}

// Intercept Ctrl+S / Ctrl+O globally for blocked roles
window.addEventListener("keydown", (ev) => {
    // Normalize
    const key = ev.key.toLowerCase();

    // Ctrl+S (save variants)
    if (ev.ctrlKey && !ev.shiftKey && key === "s") {
        if (!isWorkflowSaveAllowed()) {
            ev.preventDefault();
            ev.stopPropagation();
            console.warn("[mss-login] Blocked Ctrl+S for this role");
            return;
        }
    }

    // Ctrl+O (open workflow)
    if (ev.ctrlKey && !ev.shiftKey && key === "o") {
        if (!isWorkflowLoadAllowed()) {
            ev.preventDefault();
            ev.stopPropagation();
            console.warn("[mss-login] Blocked Ctrl+O for this role");
            return;
        }
    }

    // Ctrl/Cmd+` toggles the Logs/Terminal bottom panel
    if ((ev.ctrlKey || ev.metaKey) && (key === "`" || ev.code === "Backquote")) {
        const role = currentUser?.role || "user";
        const cfg = groupsConfig[role] || {};
        if (role !== "owner" && !isPermissionAllowed(cfg, role, "can_view_console")) {
            ev.preventDefault();
            ev.stopPropagation();
            console.warn("[mss-login] Blocked console/terminal shortcut for this role");
            return;
        }
    }
}, true); // use capture so we beat downstream listeners

// --- 5. INITIALIZATION ---

// Import logout functionality to ensure it loads
import("/mss-login/js/logout.js").catch(err => {
    console.error("[mss-login] Failed to load logout.js:", err);
    // Fallback: try to load it directly
    const script = document.createElement("script");
    script.src = "/mss-login/js/logout.js";
    script.type = "module";
    document.head.appendChild(script);
});

app.registerExtension({
    name: "mss-login.Settings",
    async setup() {
        // Expose dialog class globally for floating button and other extensions
        mss_loginDialog.expose();
        
        const style = document.createElement("style");
        style.innerHTML = ADMIN_STYLES;
        document.head.appendChild(style);

        // Replace the default ComfyUI favicon with the custom MSS logo
        document.querySelectorAll("link[rel~='icon']").forEach(l => l.remove());
        const faviconLink = document.createElement("link");
        faviconLink.rel = "icon";
        faviconLink.type = "image/x-icon";
        faviconLink.href = "/mss-login/assets/mss_logo.ico";
        document.head.appendChild(faviconLink);

        // Install backend 403 watcher for workflow save denials
        installWorkflowSaveDeniedWatcher();

        // Immediate Enforcement
        setTimeout(updateEnforcementStyles, 500);

        // Cache DOM queries to avoid repeated lookups
        let cachedLogoutBtn = null;
        let cachedMenuItems = null;
        let lastMenuCheck = 0;
        const MENU_CACHE_DURATION = 2000; // Cache menu items for 2 seconds

        // Continuous Enforcement (for late loading extensions & settings modal opening)
        const enforcementInterval = setInterval(() => {
            if (!currentUser || !groupsConfig) return;

            const role = currentUser.role || "user";
            const cfg = groupsConfig[role] || {};
            const now = Date.now();

            if (role !== "owner") {
                enforceSidebar(cfg, role);
                enforceMenus(cfg, role);
                enforceConsole(cfg, role);
                enforceModels(cfg, role);
                patchSaveConfirmDialog(cfg, role);
            }
            
            // Ensure logout button is always visible for all users - cache the query
            if (!cachedLogoutBtn || !cachedLogoutBtn.isConnected) {
                cachedLogoutBtn = document.getElementById("mss-login-settings-logout-btn");
            }
            if (cachedLogoutBtn) {
                cachedLogoutBtn.style.display = "block";
                cachedLogoutBtn.style.visibility = "visible";
                cachedLogoutBtn.style.opacity = "1";
                cachedLogoutBtn.classList.remove("mss-login-blocked-item");
            }
            
            // Ensure mss-login menu item is always visible - cache query results
            if (now - lastMenuCheck > MENU_CACHE_DURATION || !cachedMenuItems || cachedMenuItems.length === 0) {
                cachedMenuItems = document.querySelectorAll('li[aria-label="mss-login"], li.p-listbox-option[aria-label="mss-login"], [data-testid="settings-dialog"] [data-nav-id="mss-login"], [data-testid="settings-dialog"] [data-nav-id="MSS-Login"]');
                lastMenuCheck = now;
            }
            cachedMenuItems.forEach(item => {
                if (item.isConnected) {
                    item.style.display = "block";
                    item.style.visibility = "visible";
                    item.style.opacity = "1";
                    item.classList.remove("mss-login-blocked-item");
                }
            });

            // If CSS block was nuked, rebuild it
            if (!document.getElementById("mss-login-css-block")) {
                updateEnforcementStyles();
            }
        }, 1000);

        // Store interval ID for potential cleanup (though this extension typically lives for the page lifetime)
        window._mss_loginEnforcementInterval = enforcementInterval;

        // Register "Manage mss-login" Button in Settings
app.ui.settings.addSetting({
    id: "MSS-Login.Configuration",
    name: "",
    type: () => {
        const wrapper = document.createElement("div");
        wrapper.className = "mss-login-settings-card";
        wrapper.style.display = "flex";
        wrapper.style.flexDirection = "column";
        wrapper.style.alignItems = "center";
        wrapper.style.justifyContent = "center";
        wrapper.style.textAlign = "center";
        wrapper.style.gap = "12px";
        wrapper.style.padding = "20px 16px";
        wrapper.style.width = "100%";
        wrapper.style.maxWidth = "460px";
        wrapper.style.margin = "0 auto";
        wrapper.style.background = "linear-gradient(180deg, rgba(150, 96, 250, 0.08) 0%, rgba(15, 17, 23, 0.6) 100%)";
        wrapper.style.border = "1px solid rgba(150, 96, 250, 0.28)";
        wrapper.style.borderRadius = "12px";

        // Header
        const header = document.createElement("div");
        header.style.display = "flex";
        header.style.flexDirection = "column";
        header.style.alignItems = "center";
        header.style.gap = "4px";

        const title = document.createElement("h3");
        title.innerText = "MSS-Login Control Center";
        title.style.margin = "0";
        title.style.fontSize = "1.1rem";
        title.style.fontWeight = "600";
        title.style.color = "#f4f4f5";

        const userBadge = document.createElement("div");
        userBadge.id = "mss-login-settings-user-badge";
        userBadge.style.fontSize = "0.8rem";
        userBadge.style.color = "#a1a1aa";
        userBadge.innerText = "Checking session...";

        header.appendChild(title);
        header.appendChild(userBadge);
        wrapper.appendChild(header);

        // Actions
        const actionsWrap = document.createElement("div");
        actionsWrap.style.display = "flex";
        actionsWrap.style.flexDirection = "column";
        actionsWrap.style.alignItems = "center";
        actionsWrap.style.gap = "8px";
        actionsWrap.style.width = "100%";

        // Primary Control Center launcher
        const launchBtn = document.createElement("button");
        launchBtn.innerHTML = "<span>⚙️</span> Open MSS-Login Control Center";
        launchBtn.className = "mss-login-launch-btn";
        launchBtn.style.minWidth = "280px";
        launchBtn.style.background = "linear-gradient(135deg, #9660fa 0%, #7c3aed 100%)";
        launchBtn.style.borderColor = "#a855f7";
        launchBtn.style.color = "#ffffff";
        launchBtn.style.fontWeight = "600";
        launchBtn.onclick = () => new mss_loginDialog().show();
        actionsWrap.appendChild(launchBtn);

        // Model download button
        const modelDownloadBtn = document.createElement("button");
        modelDownloadBtn.innerText = "Model Download";
        modelDownloadBtn.className = "mss-login-launch-btn";
        modelDownloadBtn.style.minWidth = "280px";
        modelDownloadBtn.style.display = "none";
        modelDownloadBtn.setAttribute("data-mss-login-download-models", "true");
        modelDownloadBtn.onclick = () => {
            window._mss_loginPreferModelDownloadTab = true;
            new mss_loginDialog().show();
        };
        actionsWrap.appendChild(modelDownloadBtn);

        // Register new user (owner only)
        const registerBtn = document.createElement("button");
        registerBtn.innerText = "Register a New User";
        registerBtn.className = "mss-login-launch-btn";
        registerBtn.style.minWidth = "280px";
        registerBtn.style.display = "none";
        registerBtn.setAttribute("data-mss-login-owner-only", "true");
        registerBtn.onclick = () => {
            window._mss_loginPreferUsersTab = true;
            new mss_loginDialog().show();
        };
        actionsWrap.appendChild(registerBtn);

        // Export debug log
        const exportLogBtn = document.createElement("button");
        exportLogBtn.innerText = "Export Redacted Debug Log";
        exportLogBtn.className = "mss-login-launch-btn";
        exportLogBtn.style.minWidth = "280px";
        exportLogBtn.title = "Download sanitized debug log with secrets and credentials masked for GitHub troubleshooting.";
        exportLogBtn.onclick = () => {
            window.open("/mss-login/api/debug-log/export", "_blank");
        };
        actionsWrap.appendChild(exportLogBtn);

        // Two-Factor Authentication (MFA)
        const mfaBtn = document.createElement("button");
        mfaBtn.innerText = "Two-Factor Auth (MFA)";
        mfaBtn.className = "mss-login-launch-btn";
        mfaBtn.style.minWidth = "280px";
        mfaBtn.title = "Configure authenticator app (TOTP) or manage two-factor authentication.";
        mfaBtn.onclick = () => {
            window.open("/mfa", "_blank");
        };
        actionsWrap.appendChild(mfaBtn);

        // Passkey / WebAuthn management
        const passkeyBtn = document.createElement("button");
        passkeyBtn.innerText = "Passkeys & Devices";
        passkeyBtn.className = "mss-login-launch-btn";
        passkeyBtn.style.minWidth = "280px";
        passkeyBtn.title = "Manage biometric passkeys and WebAuthn credentials.";
        passkeyBtn.onclick = () => {
            window.open("/passkeys", "_blank");
        };
        actionsWrap.appendChild(passkeyBtn);

        // Logout
        const logoutBtn = document.createElement("button");
        logoutBtn.innerText = "Logout Current User";
        logoutBtn.className = "mss-login-launch-btn";
        logoutBtn.style.background = "#7a2525";
        logoutBtn.style.borderColor = "#aa3a3a";
        logoutBtn.id = "mss-login-settings-logout-btn";
        logoutBtn.style.minWidth = "280px";
        logoutBtn.setAttribute('data-mss-login-always-visible', 'true');
        logoutBtn.onclick = () => {
            if (typeof window.mssLoginLogout === "function") {
                window.mssLoginLogout();
                return;
            }
            window.location.href = "/logout";
        };
        actionsWrap.appendChild(logoutBtn);
        wrapper.appendChild(actionsWrap);

        // Helper note
        const note = document.createElement("p");
        note.className = "mss-login-note";
        note.style.margin = "4px 0 0 0";
        note.style.fontSize = "0.78rem";
        note.style.color = "#71717a";
        note.innerText = "Manage permissions, API tokens, push notifications, and settings in the unified Control Center.";
        wrapper.appendChild(note);

        // Populate session info
        (async () => {
            try {
                const me = await getData("/mss-login/api/me");
                if (me) {
                    const groups = Array.isArray(me.groups) ? me.groups.join(", ") : (me.is_admin ? "admin" : "user");
                    userBadge.innerText = `Logged in as ${me.username} (${groups})`;
                    if (me.can_download_models || me.is_admin) {
                        modelDownloadBtn.style.display = "block";
                    }
                    if (me.is_admin) {
                        const isOwner = Array.isArray(me.groups) && me.groups.map(g => String(g).toLowerCase()).includes("owner");
                        if (isOwner) registerBtn.style.display = "block";
                    }
                    if (me.has_passkeys || me.passkeys_count > 0) {
                        passkeyBtn.innerText = `Passkeys: ${me.passkeys_count} Active (Manage)`;
                        passkeyBtn.style.borderColor = "#9660fa";
                    }
                    if (me.mfa_enabled) {
                        mfaBtn.innerText = "Two-Factor Auth: Enabled (Manage)";
                    } else if (me.mfa_required) {
                        if (me.mfa_grace && me.mfa_grace.in_grace) {
                            mfaBtn.innerText = `Two-Factor Auth: Setup Due (${me.mfa_grace.days_remaining}d left)`;
                            mfaBtn.style.borderColor = "#f59e0b";
                        } else {
                            mfaBtn.innerText = "Two-Factor Auth: Setup Required";
                            mfaBtn.style.borderColor = "#ef4444";
                        }
                    }
                }
            } catch (_) {}
        })();

        // Layout helper for settings table
        setTimeout(() => {
            const td = wrapper.closest("td");
            if (td) {
                td.colSpan = 2;
                if (td.previousElementSibling) {
                    td.previousElementSibling.style.display = "none";
                }
            }
        }, 100);

        return wrapper;
    }
});

    }
});
