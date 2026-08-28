import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";
import { $el, ComfyDialog } from "../../scripts/ui.js";

const GROUPS = ["owner", "admin", "power", "user", "guest"];
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
    // --- Built-in Console (bottom-left panel): global can_view_console permission ---
    "can_view_console": [
        ".comfy-console",
        "#comfy-console",
        "[data-panel-id='console']",
        "[aria-label='Console']",
        ".comfy-log-panel",
        ".comfy-bottom-panel [data-tab='console']",
        ".p-panel-content .comfy-console",
        "button[aria-label='Console']",
        ".comfy-ui-panel-console"
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
        "li.p-listbox-option[aria-label='Comfy']"
    ],
    "settings_extension": [
        "li[aria-label='Extension']",
        "li.p-listbox-option[aria-label='Extension']"
    ],
    "settings_user": [
        "li[aria-label='User']",
        "li.p-listbox-option[aria-label='User']"
    ],
    "settings_keybinding": [
        "li[aria-label='Keybinding']",
        "li.p-listbox-option[aria-label='Keybinding']"
    ],
    "settings_appearance": [
        "li[aria-label='Appearance']",
        "li.p-listbox-option[aria-label='Appearance']"
    ],
    "settings_litegraph": [
        "li[aria-label='Lite Graph']",
        "li.p-listbox-option[aria-label='Lite Graph']"
    ],
    "Settings_3D": [
        "li[aria-label='3D']",
        "li.p-listbox-option[aria-label='3D']"
    ],
    "settings_maskeditor": [
        "li[aria-label='Mask Editor']",
        "li.p-listbox-option[aria-label='Mask Editor']"
    ],
    "settings_mss_loginsettings": [
        "li[aria-label='mss-login']",
        "li.p-listbox-option[aria-label='mss-login']"
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
    justify-content: space-between;
    align-items: center;
    z-index: 2;
    position: relative;
}
.mss-login-modal-title {
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #ffffff;
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
    background: var(--p-button-primary-bg, #3b82f6);
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
    background: #1d4ed8;                 /* blue on hover */
    border-color: #1e40af;
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

class mss_loginDialog extends ComfyDialog {
    constructor() {
        super();
        this.overlay = $el("div.mss-login-modal-overlay");
        this.element = $el("div.mss-login-modal");
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

        // Admin Guard
        if (!currentUser || !currentUser.is_admin) {
            this.element.innerHTML = `
                <div style="padding:40px; text-align:center; color:#ff6b6b;">
                    <h2>Access Denied</h2>
                    <p>Administrative privileges are required to modify system policies.</p>
                    <br><button id='s-close-btn' class='mss-login-btn'>Close</button>
                </div>`;
            this.element.querySelector("#s-close-btn").onclick = () => this.close();
            return;
        }

        // Get extension tabs
        const extensionTabs = window.mss_loginAdminTabs.getAll();
        
        // Build tabs HTML (built-in tabs first, then extension tabs)
        const isOwner = Array.isArray(currentUser?.groups) && currentUser.groups.map(g => String(g).toLowerCase()).includes("owner");
        const builtInTabs = [
            { id: "users", label: "Users & Roles", order: 0 },
            { id: "perms", label: "Permissions & UI", order: 1 },
            { id: "shared-models", label: "Shared Models", order: 2 },
            { id: "model-download", label: "Model download", order: 3 },
            ...(isOwner ? [{ id: "s3", label: "S3 Settings", order: 4 }] : []),
            { id: "ip", label: "IP Rules", order: 5 },
            { id: "env", label: "User Env", order: 6 },
            { id: "nsfw", label: "NSFW Management", order: 7 },
            { id: "token-storage", label: "Token Storage", order: 8 },
            { id: "users-db", label: "Users DB", order: 9 }
        ];
        
        // Combine and sort all tabs
        const allTabs = [...builtInTabs, ...extensionTabs.map(t => ({ id: t.id, label: t.label, order: t.order }))];
        allTabs.sort((a, b) => a.order - b.order);
        
        // Build tab menu HTML - mark "users" tab as active (first built-in tab)
        // Escape tab.label to prevent XSS (tab.id is already validated during registration)
        const initialActiveTabId = allTabs.find(tab => tab.id === "users")?.id || allTabs[0]?.id || "";
        const tabsHTML = allTabs.map((tab) => {
            // ID is validated during registration (lowercase alphanumeric + underscore/hyphen), safe for HTML attributes
            // Label needs escaping as it's user-provided text
            const escapedLabel = escapeHtml(tab.label);
            return `<button type="button" class="mss-login-tab-menu-item${tab.id === initialActiveTabId ? " active" : ""}" data-tab="${tab.id}">${escapedLabel}</button>`;
        }).join("");
        
        // Build content containers HTML - mark "users" content as active
        // tab.id is already validated during registration (lowercase alphanumeric + underscore/hyphen)
        const contentHTML = allTabs.map((tab) => {
            const isActive = tab.id === initialActiveTabId;
            return `<div class="mss-login-content${isActive ? " active" : ""}" id="mss-login-tab-${tab.id}"></div>`;
        }).join("");
        
        // Render Layout
        this.element.innerHTML = `
            <div class="mss-login-modal-header">
                <span class="mss-login-modal-title">MSS-Login Security Policy</span>
                <button class="mss-login-modal-close">✕</button>
            </div>
            <div class="mss-login-modal-body">
                <div class="mss-login-tab-menu">
                    <button type="button" class="mss-login-tab-toggle" id="mss-login-tab-toggle" aria-haspopup="true" aria-expanded="false">
                        <span class="mss-login-tab-toggle-icon">☰</span>
                        <span class="mss-login-tab-toggle-text">${escapeHtml(allTabs.find(t => t.id === initialActiveTabId)?.label || "Select section")}</span>
                    </button>
                    <div class="mss-login-tab-dropdown" id="mss-login-tab-dropdown" hidden>
                        ${tabsHTML}
                    </div>
                </div>
                ${contentHTML}
            </div>
        `;

        // Bindings
        this.element.querySelector(".mss-login-modal-close").onclick = () => this.close();
        this.overlay.onclick = (e) => { if (e.target === this.overlay) this.close(); };

        const tabMenu = this.element.querySelector(".mss-login-tab-menu");
        const tabToggle = this.element.querySelector("#mss-login-tab-toggle");
        const tabToggleText = this.element.querySelector(".mss-login-tab-toggle-text");
        const tabDropdown = this.element.querySelector("#mss-login-tab-dropdown");
        const tabButtons = this.element.querySelectorAll(".mss-login-tab-menu-item");

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

        const setActiveTab = (tabId) => {
            if (!tabId || !/^[a-z0-9_-]+$/.test(tabId)) {
                return;
            }
            const contentEl = this.element.querySelector(`#mss-login-tab-${tabId}`);
            if (!contentEl) {
                return;
            }
            tabButtons.forEach(btn => {
                btn.classList.toggle("active", btn.dataset.tab === tabId);
            });
            this.element.querySelectorAll(".mss-login-content").forEach(c => c.classList.remove("active"));
            contentEl.classList.add("active");
            const activeTab = allTabs.find(tab => tab.id === tabId);
            if (activeTab && tabToggleText) {
                tabToggleText.textContent = activeTab.label;
            }
        };

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

    let html = `
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
            ? `<span class="mss-login-owner-locked" title="Owner role cannot be changed">Owner (locked)</span>`
            : `<select
                        class="mss-login-role-select"
                        data-user="${uname}"
                        style="background:var(--comfy-input-bg); color:var(--input-text); border:1px solid #555; padding:6px 10px; border-radius:4px; width: 150px;"
                    >
                        ${GROUPS.map(g => `
                            <option value="${g}" ${g === grp ? "selected" : ""}>
                                ${g.toUpperCase()}
                            </option>
                        `).join("")}
                    </select>`;

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

    // --- Save handler per user ---
    container.querySelectorAll(".btn-save").forEach(btn => {
        btn.onclick = async () => {
            const u = btn.dataset.user;
            const isOwnerUser = btn.dataset.isOwner === "true";
            const g = isOwnerUser
                ? ["owner"]
                : [container.querySelector(`select[data-user="${u}"]`)?.value || "user"];

            const sfwCheckbox = container.querySelector(`.mss-login-sfw-toggle[data-user="${u}"]`);
            const sfw = sfwCheckbox ? sfwCheckbox.checked : true;

            btn.innerText = "Saving...";
            try {
                await api.fetchApi(`/mss-login/api/users/${u}`, {
                    method: "PUT",
                    body: JSON.stringify({
                        groups: g,
                        sfw_check: sfw,
                    }),
                });
                btn.innerText = "Saved";
            } catch (e) {
                console.error("[mss-login] Failed to update user:", e);
                btn.innerText = "Error";
            }
            setTimeout(() => (btn.innerText = "Save Changes"), 1000);
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
            return `<details class="mss-login-toggle-folder" data-folder="${safeFolder}">
                <summary>${safeFolder}</summary>
                <div class="mss-login-toggle-items" data-folder="${safeFolder}" style="padding:8px 0 8px 12px; max-height:200px; overflow-y:auto;">Loading...</div>
            </details>`;
        }).join("");

        toggleFoldersEl.querySelectorAll("details.mss-login-toggle-folder").forEach(detailsEl => {
            detailsEl.addEventListener("toggle", async () => {
                if (!detailsEl.open) return;
                const folder = detailsEl.dataset.folder;
                const itemsEl = detailsEl.querySelector(".mss-login-toggle-items");
                if (!itemsEl || itemsEl.dataset.loaded === "1") return;
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
            });
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
    const postgresFields = container.querySelector("#mss-login-usersdb-postgres-fields");
    const mysqlFields = container.querySelector("#mss-login-usersdb-mysql-fields");
    const statusEl = container.querySelector("#mss-login-usersdb-status");
    function showFields() {
        const b = (backendSelect.value || "sqlite").toLowerCase();
        sqliteFields.style.display = b === "sqlite" ? "" : "none";
        postgresFields.style.display = b === "postgresql" ? "" : "none";
        mysqlFields.style.display = b === "mysql" ? "" : "none";
    }
    backendSelect.onchange = showFields;
    container.querySelector("#mss-login-usersdb-save").onclick = async () => {
        const b = (backendSelect.value || "sqlite").toLowerCase();
        const body = {
            backend: b,
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
    ` : "";
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
    function showSourceFields() {
        const v = sourceSelect.value;
        civitaiFields.style.display = v === "civitai" ? "" : "none";
        hfFields.style.display = v === "huggingface" ? "" : "none";
    }
    sourceSelect.onchange = showSourceFields;
    showSourceFields();

    (async () => {
        try {
            const me = await getData("/mss-login/api/me");
            if (me && !me.experimental?.s3) {
                const destSelect = container.querySelector("#mss-login-dl-dest");
                const s3Opt = destSelect && destSelect.querySelector('option[value="s3"]');
                if (s3Opt) s3Opt.remove();
            }
        } catch (_) {}
    })();

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
        const adminLockedFalseKeys = new Set([
            "can_have_non_expiring_jwt",
            "can_view_console",
        ]);
        
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
        let html = `<table class="mss-login-table">
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
                // Others: Allow by default.
                if (val === undefined) {
                    val = (g === "admin" && adminLockedFalseKeys.has(id)) ? false : (g !== "guest"); 
                }
                
                // Admin keeps broad access, except for privileges intentionally reserved for owner.
                if (g === "admin" && !adminLockedFalseKeys.has(id)) val = true;
                // Owner column is immutable (same as admin)
                if (g === "owner") val = true;

                row += `<td class="mss-login-check-cell"><input type="checkbox" class="perm-chk" data-group="${g}" data-key="${id}" ${val?"checked":""} ${(g==="admin"||g==="owner")?"disabled":""}></td>`;
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
        html += drawRow("Sidebar Menu: Browse Templates", "ui_menu_templates");
        html += drawRow("Sidebar Menu: Manage Extensions", "ui_menu_extensions");
        html += drawRow("Sidebar Menu: Manager Button", "ui_menu_manager");

        //  Section 3: Settings Menu Options
        html += drawRow("Settings Menu", null, true);
        html += drawRow("Settings Menu: User", "settings_user");
        html += drawRow("Settings Menu: mss-login", "settings_mss_loginsettings");
        html += drawRow("Settings Menu: Mask Editor", "settings_maskeditor");
        html += drawRow("Settings Menu: Keybinding", "settings_keybinding");
        html += drawRow("Settings Menu: Appearance", "settings_makadiappearance");
        
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

    // --- BYPASS ADMIN COMPLETELY ---
    if (currentUser.is_admin) {
        const style = document.getElementById("mss-login-css-block");
        if (style) style.textContent = "";
        return;
    }

    let css = "";

    // 🔒 HARDENED LOGIC FOR GUEST
    if (role === "guest") {
        const guestCfg = groupsConfig["guest"] || {};

        for (const [key, selectors] of Object.entries(CSS_BLOCK_MAP)) {
            // Always allow mss-login settings menu and logout for guests
            if (key === "settings_mss_loginsettings" || key === "settings_mss_loginsettings") {
                continue; // Skip blocking this menu item
            }
            
            const allowed = guestCfg[key] === true; // only explicit true is allowed
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
        let val = cfg[key];

        // ⚠️ Do NOT touch this default – it works for you:
        // undefined = allowed by default for non-guest
        if (val === undefined) {
            val = true;
        }

        if (val === false) {
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

// Sidebar Scanner: Runs periodically to hide settings menu buttons by text content
function enforceSidebar(cfg, role) {
    const modal = document.querySelector(".comfy-modal");
    if (!modal) return;

    const items = modal.querySelectorAll(
        "button, .comfy-settings-btn, tr, .pysssss-settings-category"
    );

    items.forEach(el => {
        // Never hide the logout button - it should always be visible
        if (el.id === "mss-login-settings-logout-btn" || 
            el.innerText?.includes("Logout current user") ||
            el.querySelector("#mss-login-settings-logout-btn")) {
            el.classList.remove("mss-login-blocked-item");
            el.style.display = "";
            return;
        }
        
        // Never hide the mss-login menu item - guests need it to logout
        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel && (ariaLabel.toLowerCase() === 'mss-login' || ariaLabel === 'mss-login')) {
            el.classList.remove("mss-login-blocked-item");
            el.style.display = "";
            return;
        }
        
        const txt = (el.innerText || "").trim();
        if (!txt || txt.length > 30 || txt === "Close" || txt === "Back") return;

        const catId = getSanitizedId(txt);

        let val = cfg[catId];
        
        // Always allow mss-login menu for guests
        if (catId === "mss_loginsettings" || catId === "mss-login" || txt.toLowerCase() === "mss-login") {
            el.classList.remove("mss-login-blocked-item");
            el.style.display = "";
            return;
        }

        // Default logic:
        //  - guest: undefined = BLOCK
        //  - others: undefined = ALLOW
        if (val === undefined) {
            val = (role !== "guest");
        }

        if (val === false) {
            el.classList.add("mss-login-blocked-item");
            el.style.display = "none"; // Inline force
        } else {
            el.classList.remove("mss-login-blocked-item");
            el.style.display = "";
        }
    });
}

// Top menu enforcement: runs on the PrimeVue menubar
function enforceMenus(cfg, role) {
    const shouldBlock = (key) => {
        let val = cfg[key];

        // Same semantics as elsewhere:
        //  - guest: undefined = BLOCK
        //  - others: undefined = ALLOW
        if (val === undefined) {
            val = (role !== "guest");
        }
        return val === false;
    };

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
        let cachedModal = null;
        let cachedLogoutBtn = null;
        let cachedMenuItems = null;
        let lastMenuCheck = 0;
        const MENU_CACHE_DURATION = 2000; // Cache menu items for 2 seconds

        // Continuous Enforcement (for late loading extensions & settings modal opening)
        const enforcementInterval = setInterval(() => {
            if (!currentUser || !groupsConfig) return;

            const role = currentUser.role || "user";
            const cfg = groupsConfig[role] || {};

            // Cache modal query - only update if needed
            const now = Date.now();
            if (!cachedModal || !cachedModal.isConnected) {
                cachedModal = document.querySelector(".comfy-modal");
            }

            // Settings modal - only run expensive operations when modal is open
            if (cachedModal) {
                enforceSidebar(cfg, role);
            }

            // Menus & save-confirm popup
            enforceMenus(cfg, role);
            patchSaveConfirmDialog(cfg, role);
            
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
                cachedMenuItems = document.querySelectorAll('li[aria-label="mss-login"], li.p-listbox-option[aria-label="mss-login"]');
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
        wrapper.style.display = "flex";
        wrapper.style.flexDirection = "column";
        wrapper.style.alignItems = "center";
        wrapper.style.justifyContent = "center";
        wrapper.style.textAlign = "center";
        wrapper.style.gap = "10px";
        wrapper.style.padding = "16px 0";
        wrapper.style.width = "100%";

        const actionsWrap = document.createElement("div");
        actionsWrap.style.display = "flex";
        actionsWrap.style.flexDirection = "column";
        actionsWrap.style.alignItems = "center";
        actionsWrap.style.gap = "8px";
        actionsWrap.style.width = "100%";

        // Logout button (above) - ALWAYS visible for all users including guests
        const logoutBtn = document.createElement("button");
        logoutBtn.innerText = "Logout Current User";
        logoutBtn.className = "mss-login-launch-btn";
        logoutBtn.style.background = "#7a2525";
        logoutBtn.style.borderColor = "#aa3a3a";
        logoutBtn.id = "mss-login-settings-logout-btn";
        logoutBtn.style.minWidth = "260px";
        // Ensure logout button is never hidden by enforcement
        logoutBtn.setAttribute('data-mss-login-always-visible', 'true');
        logoutBtn.style.display = "block"; // Force display
        
        logoutBtn.onclick = () => {
            // Hard redirect so cookies + state reset properly
            window.location.href = "/logout";
        };

        // Main management button
        const btn = document.createElement("button");
        btn.innerText = "Manage MSS-Login Permissions";
        btn.className = "mss-login-launch-btn";
        btn.style.minWidth = "260px";
        btn.onclick = () => new mss_loginDialog().show();

        // Register a new user (admins only). Kept out of the public login page
        // to reduce brute-force exposure of the registration form.
        const registerBtn = document.createElement("button");
        registerBtn.innerText = "Register a New User";
        registerBtn.className = "mss-login-launch-btn";
        registerBtn.style.minWidth = "260px";
        registerBtn.style.display = "none";
        registerBtn.setAttribute("data-mss-login-admin-only", "true");
        registerBtn.onclick = () => {
            window.location.href = "/register";
        };

        actionsWrap.appendChild(btn);
        actionsWrap.appendChild(registerBtn);
        actionsWrap.appendChild(logoutBtn);
        wrapper.appendChild(actionsWrap);

        // Guest JWT toggle (Admin only)
        const guestJwtRow = document.createElement("div");
        guestJwtRow.id = "mss-login-guest-jwt-row";
        guestJwtRow.style.display = "none";
        guestJwtRow.style.alignItems = "center";
        guestJwtRow.style.justifyContent = "center";
        guestJwtRow.style.gap = "8px";
        guestJwtRow.style.marginTop = "6px";
        const guestJwtLabel = document.createElement("label");
        guestJwtLabel.style.display = "flex";
        guestJwtLabel.style.alignItems = "center";
        guestJwtLabel.style.gap = "8px";
        guestJwtLabel.style.cursor = "pointer";
        const guestJwtCheck = document.createElement("input");
        guestJwtCheck.type = "checkbox";
        guestJwtCheck.id = "mss-login-allow-guest-jwt";
        guestJwtLabel.appendChild(guestJwtCheck);
        guestJwtLabel.appendChild(document.createTextNode("Allow guest JWT tokens (guest login issues session JWT)"));
        guestJwtRow.appendChild(guestJwtLabel);
        guestJwtCheck.onchange = async () => {
            try {
                const res = await api.fetchApi("/mss-login/api/settings/guest-jwt", {
                    method: "PUT",
                    body: JSON.stringify({ allow_guest_jwt: guestJwtCheck.checked })
                });
                if (res && res.ok) {
                    if (window.showToast) window.showToast(guestJwtCheck.checked ? "Guest JWT enabled." : "Guest JWT disabled.");
                } else {
                    guestJwtCheck.checked = !guestJwtCheck.checked;
                }
            } catch (e) {
                guestJwtCheck.checked = !guestJwtCheck.checked;
            }
        };
        wrapper.appendChild(guestJwtRow);
        (async () => {
            try {
                const me = await getData("/mss-login/api/me");
                if (me && me.is_admin) {
                    registerBtn.style.display = "block";
                    const cfg = await getData("/mss-login/api/settings/guest-jwt");
                    guestJwtRow.style.display = "flex";
                    guestJwtCheck.checked = !!cfg.allow_guest_jwt;
                }
            } catch (_) {}
        })();

        // Push notifications (ntfy) - Admin only
        const ntfySection = document.createElement("div");
        ntfySection.id = "mss-login-ntfy-section";
        ntfySection.style.display = "none";
        ntfySection.style.marginTop = "12px";
        ntfySection.style.width = "min(100%, 560px)";
        ntfySection.style.textAlign = "left";
        const ntfyHeading = document.createElement("h4");
        ntfyHeading.style.margin = "0 0 8px 0";
        ntfyHeading.textContent = "Push notifications (ntfy)";
        ntfySection.appendChild(ntfyHeading);
        const ntfyTopicLabel = document.createElement("label");
        ntfyTopicLabel.textContent = "Topic (e.g. my-secret-topic): ";
        const ntfyTopicInput = document.createElement("input");
        ntfyTopicInput.type = "text";
        ntfyTopicInput.placeholder = "ntfy.sh topic";
        ntfyTopicInput.style.width = "200px";
        ntfyTopicInput.style.marginLeft = "6px";
        ntfyTopicLabel.appendChild(ntfyTopicInput);
        ntfySection.appendChild(ntfyTopicLabel);
        const ntfyBaseUrlLabel = document.createElement("label");
        ntfyBaseUrlLabel.style.display = "block";
        ntfyBaseUrlLabel.style.marginTop = "8px";
        ntfyBaseUrlLabel.textContent = "Server URL: ";
        const ntfyBaseUrlInput = document.createElement("input");
        ntfyBaseUrlInput.type = "url";
        ntfyBaseUrlInput.placeholder = "https://ntfy.sh";
        ntfyBaseUrlInput.style.width = "min(100%, 320px)";
        ntfyBaseUrlInput.style.marginLeft = "6px";
        ntfyBaseUrlLabel.appendChild(ntfyBaseUrlInput);
        ntfySection.appendChild(ntfyBaseUrlLabel);
        const ntfyTokenLabel = document.createElement("label");
        ntfyTokenLabel.style.display = "block";
        ntfyTokenLabel.style.marginTop = "8px";
        ntfyTokenLabel.textContent = "API token (optional): ";
        const ntfyTokenInput = document.createElement("input");
        ntfyTokenInput.type = "password";
        ntfyTokenInput.autocomplete = "off";
        ntfyTokenInput.placeholder = "leave blank to keep existing";
        ntfyTokenInput.style.width = "min(100%, 320px)";
        ntfyTokenInput.style.marginLeft = "6px";
        ntfyTokenLabel.appendChild(ntfyTokenInput);
        ntfySection.appendChild(ntfyTokenLabel);
        const ntfyClearTokenLabel = document.createElement("label");
        ntfyClearTokenLabel.style.display = "block";
        ntfyClearTokenLabel.style.marginTop = "4px";
        const ntfyClearTokenCheck = document.createElement("input");
        ntfyClearTokenCheck.type = "checkbox";
        ntfyClearTokenLabel.appendChild(ntfyClearTokenCheck);
        ntfyClearTokenLabel.appendChild(document.createTextNode(" Clear stored API token on save"));
        ntfySection.appendChild(ntfyClearTokenLabel);
        const ntfyCheckWrap = document.createElement("div");
        ntfyCheckWrap.style.marginTop = "8px";
        ntfyCheckWrap.id = "mss-login-ntfy-checks";
        ntfySection.appendChild(ntfyCheckWrap);
        const ntfySaveBtn = document.createElement("button");
        ntfySaveBtn.className = "mss-login-launch-btn";
        ntfySaveBtn.textContent = "Save ntfy settings";
        ntfySaveBtn.style.marginTop = "8px";
        ntfySaveBtn.onclick = async () => {
            try {
                const enabled = [];
                ntfyCheckWrap.querySelectorAll("input[type=checkbox]:checked").forEach(cb => enabled.push(cb.value));
                const payload = {
                    topic: ntfyTopicInput.value.trim(),
                    base_url: ntfyBaseUrlInput.value.trim(),
                    enabled_events: enabled
                };
                if (ntfyClearTokenCheck.checked) {
                    payload.api_token = "";
                } else if (ntfyTokenInput.value.trim()) {
                    payload.api_token = ntfyTokenInput.value.trim();
                }
                const res = await api.fetchApi("/mss-login/api/settings/ntfy", {
                    method: "PUT",
                    body: JSON.stringify(payload)
                });
                if (res && res.ok) {
                    ntfyTokenInput.value = "";
                    ntfyClearTokenCheck.checked = false;
                    if (window.showToast) window.showToast("ntfy settings saved.");
                }
            } catch (_) {}
        };
        ntfySection.appendChild(ntfySaveBtn);
        wrapper.appendChild(ntfySection);
        (async () => {
            try {
                const me = await getData("/mss-login/api/me");
                if (me && me.is_admin) {
                    const cfg = await getData("/mss-login/api/settings/ntfy");
                    ntfySection.style.display = "block";
                    ntfyTopicInput.value = cfg.topic || "";
                    ntfyBaseUrlInput.value = cfg.base_url || "https://ntfy.sh";
                    if (cfg.has_api_token) {
                        ntfyTokenInput.placeholder = "token configured (enter new to replace)";
                    }
                    const eventLabels = {
                        nsfw_block: "Notify when user blocked for NSFW",
                        user_created: "Notify when new user created",
                        user_login: "Notify when user logs in (include IP)",
                        user_logout: "Notify when user logs out",
                        api_token_created: "Notify when API/JWT token created",
                        login_failure: "Notify on login failure",
                        shared_items_added: "Notify when shared model/item added",
                        shared_items_removed: "Notify when shared model/item removed",
                        experimental_recovery: "Notify when experimental failsafe/recovery runs"
                    };
                    const keys = cfg.event_keys || ["nsfw_block", "user_created", "user_login", "user_logout", "api_token_created", "login_failure"];
                    ntfyCheckWrap.innerHTML = "";
                    keys.forEach(k => {
                        const label = document.createElement("label");
                        label.style.display = "block";
                        label.style.marginBottom = "4px";
                        const cb = document.createElement("input");
                        cb.type = "checkbox";
                        cb.value = k;
                        if ((cfg.enabled_events || []).includes(k)) cb.checked = true;
                        label.appendChild(cb);
                        label.appendChild(document.createTextNode(" " + (eventLabels[k] || k)));
                        ntfyCheckWrap.appendChild(label);
                    });
                }
            } catch (_) {}
        })();

        // Experimental features (per-feature toggles) - Admin only, shown when master experimental_features is on
        const experimentalSection = document.createElement("div");
        experimentalSection.id = "mss-login-experimental-section";
        experimentalSection.style.display = "none";
        experimentalSection.style.marginTop = "12px";
        experimentalSection.style.width = "min(100%, 560px)";
        experimentalSection.style.textAlign = "left";
        const experimentalHeading = document.createElement("h4");
        experimentalHeading.style.margin = "0 0 8px 0";
        experimentalHeading.textContent = "Experimental features";
        experimentalSection.appendChild(experimentalHeading);
        const experimentalSubtext = document.createElement("p");
        experimentalSubtext.style.margin = "0 0 8px 0";
        experimentalSubtext.style.fontSize = "0.9em";
        experimentalSubtext.style.color = "#888";
        experimentalSubtext.textContent = "Enable experimental features one by one. Master switch is in config (experimental_features).";
        experimentalSection.appendChild(experimentalSubtext);
        const experimentalChecks = document.createElement("div");
        experimentalChecks.id = "mss-login-experimental-checks";
        experimentalChecks.style.marginTop = "8px";
        experimentalSection.appendChild(experimentalChecks);
        const experimentalSaveBtn = document.createElement("button");
        experimentalSaveBtn.className = "mss-login-launch-btn";
        experimentalSaveBtn.textContent = "Save experimental settings";
        experimentalSaveBtn.style.marginTop = "8px";
        experimentalSaveBtn.onclick = async () => {
            try {
                const payload = { experimental: {} };
                ["mfa", "s3", "loading_screen", "news"].forEach(k => {
                    const cb = document.getElementById("mss-login-exp-" + k);
                    if (cb) payload.experimental[k] = !!cb.checked;
                });
                const res = await api.fetchApi("/mss-login/api/settings/experimental", {
                    method: "PUT",
                    body: JSON.stringify(payload)
                });
                if (res && res.ok) {
                    if (window.showToast) window.showToast("Experimental settings saved.");
                }
            } catch (e) {
                if (window.showToast) window.showToast("Save failed: " + (e.message || "Unknown error"));
            }
        };
        experimentalSection.appendChild(experimentalSaveBtn);
        wrapper.appendChild(experimentalSection);
        (async () => {
            try {
                const me = await getData("/mss-login/api/me");
                if (me && me.is_admin && me.experimental_features) {
                    const cfg = await getData("/mss-login/api/settings/experimental");
                    experimentalSection.style.display = "block";
                    const labels = { mfa: "MFA (two-factor authentication)", s3: "S3 storage", loading_screen: "Loading screen (post-login)", news: "News / RSS feed" };
                    experimentalChecks.innerHTML = "";
                    ["mfa", "s3", "loading_screen", "news"].forEach(k => {
                        const label = document.createElement("label");
                        label.style.display = "block";
                        label.style.marginBottom = "4px";
                        const cb = document.createElement("input");
                        cb.type = "checkbox";
                        cb.id = "mss-login-exp-" + k;
                        cb.checked = !!(cfg.experimental && cfg.experimental[k]);
                        label.appendChild(cb);
                        label.appendChild(document.createTextNode(" " + (labels[k] || k)));
                        experimentalChecks.appendChild(label);
                    });
                }
            } catch (_) {}
        })();

        // Experimental failsafe (non-experimental safety control) - Admin only
        const failsafeSection = document.createElement("div");
        failsafeSection.id = "mss-login-failsafe-section";
        failsafeSection.style.display = "none";
        failsafeSection.style.marginTop = "12px";
        failsafeSection.style.width = "min(100%, 560px)";
        failsafeSection.style.textAlign = "left";
        failsafeSection.innerHTML = `
            <h4 style="margin:0 0 8px 0;">Experimental failsafe</h4>
            <p style="margin:0 0 8px 0; font-size:0.9em; color:#888;">
                If critical experimental startup failures occur, MSS-Login can auto-disable experimental features.
                Credentials/user DB data are preserved during this safety reset.
            </p>
            <label style="display:block; margin-bottom:6px;">
                <input type="checkbox" id="mss-login-failsafe-enabled"> Enable experimental failsafe
            </label>
            <label style="display:block; margin-bottom:8px;">
                <input type="checkbox" id="mss-login-failsafe-escalate"> Escalate to recovery update after repeated failures
            </label>
            <p id="mss-login-failsafe-state" class="mss-login-note" style="margin:0 0 8px 0;"></p>
            <div id="mss-login-failsafe-details" class="mss-login-note" style="margin:0 0 8px 0; border:1px solid #333; border-radius:8px; padding:8px;"></div>
            <button class="mss-login-launch-btn" id="mss-login-failsafe-save">Save failsafe settings</button>
        `;
        wrapper.appendChild(failsafeSection);
        (async () => {
            try {
                const me = await getData("/mss-login/api/me");
                if (!(me && me.is_admin)) return;
                const cfg = await getData("/mss-login/api/settings/experimental-failsafe");
                if (!cfg) return;
                failsafeSection.style.display = "block";
                const enabled = failsafeSection.querySelector("#mss-login-failsafe-enabled");
                const escalate = failsafeSection.querySelector("#mss-login-failsafe-escalate");
                const state = failsafeSection.querySelector("#mss-login-failsafe-state");
                const details = failsafeSection.querySelector("#mss-login-failsafe-details");
                const renderFailsafeDetails = (data) => {
                    const action = String(data.last_recovery_action || "none");
                    const reason = String(data.last_failure_reason || "");
                    const failureCount = Number(data.failure_count || 0);
                    let guidance = "No recovery action has been needed yet.";
                    if (action === "config_reset") {
                        guidance = "Experimental flags were auto-disabled. Restart ComfyUI to confirm stability.";
                    } else if (action === "recovery_update") {
                        guidance = "Escalation update succeeded after repeated failures. Verify service health now.";
                    } else if (action === "recovery_update_failed") {
                        guidance = "Escalation update failed. Review logs and perform controlled maintenance.";
                    }
                    details.innerHTML = `
                        <div><strong>Last action:</strong> ${escapeHtml(action)}</div>
                        <div><strong>Last reason:</strong> ${escapeHtml(reason || "n/a")}</div>
                        <div><strong>Safety guidance:</strong> ${escapeHtml(guidance)}</div>
                        <div><strong>Credential safety:</strong> Failsafe avoids wiping user DB credentials or token stores.</div>
                        <div><strong>Failure count:</strong> ${failureCount}</div>
                    `;
                };
                enabled.checked = !!cfg.enabled;
                escalate.checked = !!cfg.escalate_after_repeated_failure;
                state.textContent =
                    `Failures: ${cfg.failure_count || 0}` +
                    (cfg.last_failure_at ? ` • Last: ${cfg.last_failure_at}` : "") +
                    (cfg.last_recovery_action ? ` • Action: ${cfg.last_recovery_action}` : "");
                renderFailsafeDetails(cfg);
                failsafeSection.querySelector("#mss-login-failsafe-save").onclick = async () => {
                    try {
                        const res = await api.fetchApi("/mss-login/api/settings/experimental-failsafe", {
                            method: "PUT",
                            body: JSON.stringify({
                                enabled: !!enabled.checked,
                                escalate_after_repeated_failure: !!escalate.checked
                            })
                        });
                        const data = await res.json().catch(() => ({}));
                        if (!res.ok) {
                            if (window.showToast) window.showToast("Save failed: " + (data.error || res.status));
                            return;
                        }
                        state.textContent =
                            `Failures: ${data.failure_count || 0}` +
                            (data.last_failure_at ? ` • Last: ${data.last_failure_at}` : "") +
                            (data.last_recovery_action ? ` • Action: ${data.last_recovery_action}` : "");
                        renderFailsafeDetails(data);
                        if (window.showToast) window.showToast("Failsafe settings saved.");
                    } catch (e) {
                        if (window.showToast) window.showToast("Save failed: " + (e.message || "Unknown error"));
                    }
                };
            } catch (_) {}
        })();

        // MFA section (Two-Factor Authentication with Google Authenticator)
        const mfaSection = document.createElement("div");
        mfaSection.id = "mss-login-mfa-section";
        mfaSection.style.marginTop = "12px";
        mfaSection.style.display = "none";
        mfaSection.style.width = "min(100%, 560px)";
        mfaSection.style.textAlign = "left";
        const mfaHeading = document.createElement("h4");
        mfaHeading.style.margin = "0 0 8px 0";
        mfaHeading.textContent = "Two-Factor Authentication (MFA)";
        mfaSection.appendChild(mfaHeading);
        const mfaSubtext = document.createElement("p");
        mfaSubtext.style.margin = "0 0 8px 0";
        mfaSubtext.style.fontSize = "0.9em";
        mfaSubtext.style.color = "#888";
        mfaSubtext.textContent = "You can enable MFA here for extra security, whether or not your role requires it.";
        mfaSection.appendChild(mfaSubtext);
        const mfaStatus = document.createElement("p");
        mfaStatus.id = "mss-login-mfa-status";
        mfaStatus.style.margin = "0 0 8px 0";
        mfaSection.appendChild(mfaStatus);
        const mfaSetupBtn = document.createElement("button");
        mfaSetupBtn.className = "mss-login-launch-btn";
        mfaSetupBtn.textContent = "Set up MFA (Google Authenticator)";
        mfaSetupBtn.id = "mss-login-mfa-setup-btn";
        mfaSetupBtn.style.display = "none";
        mfaSetupBtn.onclick = async () => {
            try {
                const setupResp = await api.fetchApi("/mss-login/api/mfa/setup", {
                    method: "POST",
                    body: JSON.stringify({}),
                });
                if (!setupResp.provisioning_uri) {
                    if (window.showToast) window.showToast(setupResp.error || "MFA setup failed.");
                    return;
                }
                const dlg = new ComfyDialog();
                const content = document.createElement("div");
                content.style.display = "flex";
                content.style.flexDirection = "column";
                content.style.gap = "12px";
                content.innerHTML = "<p>Scan the QR code with Google Authenticator (or any TOTP app), then enter the 6-digit code.</p>";
                const qrDiv = document.createElement("div");
                qrDiv.id = "mss-login-mfa-dialog-qr";
                content.appendChild(qrDiv);
                const renderQr = () => {
                    qrDiv.innerHTML = "";
                    if (typeof QRCode !== "undefined") {
                        new QRCode(qrDiv, { text: setupResp.provisioning_uri, width: 200, height: 200 });
                    } else {
                        qrDiv.innerHTML = '<p><a href="' + setupResp.provisioning_uri + '" target="_blank">Open in authenticator (or add manually)</a></p>';
                    }
                };
                if (typeof QRCode === "undefined") {
                    const script = document.createElement("script");
                    script.src = "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js";
                    script.crossOrigin = "anonymous";
                    script.onload = renderQr;
                    script.onerror = renderQr;
                    document.head.appendChild(script);
                } else {
                    renderQr();
                }
                if (setupResp.backup_code) {
                    const backupP = document.createElement("p");
                    backupP.innerHTML = "<strong>Backup code (save this):</strong> <code>" + setupResp.backup_code + "</code>";
                    content.appendChild(backupP);
                }
                const codeLabel = document.createElement("label");
                codeLabel.textContent = "Enter 6-digit code from app:";
                const codeInput = document.createElement("input");
                codeInput.type = "text";
                codeInput.placeholder = "000000";
                codeInput.maxLength = 8;
                codeInput.style.width = "120px";
                codeLabel.appendChild(codeInput);
                content.appendChild(codeLabel);
                const btnRow = document.createElement("div");
                const completeBtn = document.createElement("button");
                completeBtn.className = "mss-login-launch-btn";
                completeBtn.textContent = "Complete Setup";
                completeBtn.onclick = async () => {
                    const code = (codeInput.value || "").replace(/\s/g, "");
                    if (!code || code.length !== 6) {
                        if (window.showToast) window.showToast("Enter a 6-digit code");
                        return;
                    }
                    completeBtn.disabled = true;
                    try {
                        const verifyResp = await api.fetchApi("/mss-login/api/mfa/verify-setup", {
                            method: "POST",
                            body: JSON.stringify({ code }),
                        });
                        if (verifyResp.error) {
                            if (window.showToast) window.showToast(verifyResp.error);
                            completeBtn.disabled = false;
                            return;
                        }
                        dlg.close();
                        if (window.showToast) window.showToast("MFA enabled successfully.");
                        if (currentUser) currentUser.mfa_enabled = true;
                        mfaStatus.textContent = "MFA is enabled.";
                        mfaSetupBtn.style.display = "none";
                    } catch (e) {
                        if (window.showToast) window.showToast("Setup failed: " + (e.message || "Unknown error"));
                        completeBtn.disabled = false;
                    }
                };
                btnRow.appendChild(completeBtn);
                content.appendChild(btnRow);
                dlg.show("Set Up Two-Factor Authentication", [content]);
            } catch (e) {
                if (window.showToast) window.showToast("MFA setup failed: " + (e.message || "Unknown error"));
            }
        };
        mfaSection.appendChild(mfaSetupBtn);
        wrapper.appendChild(mfaSection);
        (async () => {
            try {
                const me = await getData("/mss-login/api/me");
                if (me && me.username && me.username.toLowerCase() !== "guest" && me.experimental?.mfa) {
                    mfaSection.style.display = "block";
                    if (me.mfa_enabled) {
                        mfaStatus.textContent = "MFA is enabled.";
                        mfaSetupBtn.style.display = "none";
                    } else {
                        mfaStatus.textContent = "Add an extra layer of security with MFA (optional). Enable even if your role does not require it.";
                        mfaSetupBtn.style.display = "block";
                    }
                }
            } catch (_) {}
        })();

        // API/JWT token section (requires password re-entry and shows token names)
        const apiJwtSection = document.createElement("div");
        apiJwtSection.id = "mss-login-api-jwt-tokens";
        apiJwtSection.style.marginTop = "12px";
        apiJwtSection.style.width = "min(100%, 760px)";
        apiJwtSection.style.textAlign = "left";
        apiJwtSection.innerHTML = `
            <h4 style="margin:0 0 8px 0;">API/JWT Tokens</h4>
            <p class="mss-login-note" style="margin:0 0 10px 0;">
                Create long-lived API/JWT tokens from inside ComfyUI. For security, you must re-enter your password.
            </p>
            <div class="mss-login-row" style="gap:10px; align-items:flex-end; flex-wrap:wrap;">
                <div>
                    <label class="mss-login-field-label">Token name (optional)</label>
                    <input type="text" id="mss-login-api-token-label" class="mss-login-input" placeholder="e.g. Tablet App">
                </div>
                <div>
                    <label class="mss-login-field-label">Expires in hours</label>
                    <input type="number" id="mss-login-api-token-expire-hours" class="mss-login-input" value="720" min="0" step="1" style="width:120px;">
                </div>
                <div style="flex:1; min-width:220px;">
                    <label class="mss-login-field-label">Re-enter password</label>
                    <input type="password" id="mss-login-api-token-password" class="mss-login-input" placeholder="Required for creation">
                </div>
                <div>
                    <button class="mss-login-btn" id="mss-login-api-token-create">Create token</button>
                </div>
            </div>
            <div id="mss-login-api-token-mfa-row" class="mss-login-row" style="margin-top:8px; gap:10px; align-items:flex-end; display:none; flex-wrap:wrap;">
                <div>
                    <label class="mss-login-field-label">MFA code</label>
                    <input type="text" id="mss-login-api-token-mfa-code" class="mss-login-input" placeholder="123456" style="width:120px;">
                </div>
                <div>
                    <label class="mss-login-field-label">Backup code</label>
                    <input type="text" id="mss-login-api-token-mfa-backup" class="mss-login-input" placeholder="XXXX-XXXX" style="width:160px;">
                </div>
            </div>
            <p id="mss-login-api-token-create-status" class="mss-login-note" style="margin-top:8px;"></p>
            <div id="mss-login-api-token-output-wrap" style="display:none; margin-top:8px;">
                <label class="mss-login-field-label">New token (shown once)</label>
                <div class="mss-login-row" style="gap:8px; align-items:center; flex-wrap:wrap;">
                    <code id="mss-login-api-token-output" style="padding:8px; border-radius:8px; background:#0f1117; display:block; max-width:100%; overflow:auto;"></code>
                    <button class="mss-login-btn secondary" id="mss-login-api-token-copy">Copy</button>
                </div>
                <p class="mss-login-note" style="margin-top:6px;">Store this token securely. It cannot be shown again.</p>
            </div>
            <h5 style="margin:14px 0 8px 0;">My API/JWT tokens</h5>
            <div id="mss-login-api-token-list" class="mss-login-note">Loading tokens...</div>
        `;
        wrapper.appendChild(apiJwtSection);
        (async () => {
            let mfaTempToken = null;
            const me = await getData("/mss-login/api/me");
            const createBtn = apiJwtSection.querySelector("#mss-login-api-token-create");
            const labelInput = apiJwtSection.querySelector("#mss-login-api-token-label");
            const expireInput = apiJwtSection.querySelector("#mss-login-api-token-expire-hours");
            const passwordInput = apiJwtSection.querySelector("#mss-login-api-token-password");
            const createStatus = apiJwtSection.querySelector("#mss-login-api-token-create-status");
            const mfaRow = apiJwtSection.querySelector("#mss-login-api-token-mfa-row");
            const mfaCodeInput = apiJwtSection.querySelector("#mss-login-api-token-mfa-code");
            const mfaBackupInput = apiJwtSection.querySelector("#mss-login-api-token-mfa-backup");
            const tokenOutputWrap = apiJwtSection.querySelector("#mss-login-api-token-output-wrap");
            const tokenOutput = apiJwtSection.querySelector("#mss-login-api-token-output");
            const copyBtn = apiJwtSection.querySelector("#mss-login-api-token-copy");
            const tokenListContainer = apiJwtSection.querySelector("#mss-login-api-token-list");

            const setCreateStatus = (msg, isError = false) => {
                createStatus.textContent = msg || "";
                createStatus.style.color = isError ? "#ff8a8a" : "#9ce3a5";
            };

            const formatDateTime = (value, fallback) => {
                if (!value) return fallback;
                try {
                    return new Date(value).toLocaleString();
                } catch (_) {
                    return fallback;
                }
            };

            const refreshApiTokenList = async () => {
                if (!tokenListContainer) return;
                tokenListContainer.textContent = "Loading tokens...";
                try {
                    const res = await api.fetchApi("/mss-login/api/tokens", { method: "GET" });
                    if (!res.ok) {
                        tokenListContainer.textContent = "Log in to view your API/JWT tokens.";
                        return;
                    }
                    const data = await res.json().catch(() => ({}));
                    const tokens = Array.isArray(data.tokens) ? data.tokens : [];
                    if (tokens.length === 0) {
                        tokenListContainer.textContent = "No API/JWT tokens found.";
                        return;
                    }
                    const table = document.createElement("table");
                    table.className = "mss-login-table";
                    const thead = document.createElement("thead");
                    thead.innerHTML = "<tr><th>Name</th><th>Hash Prefix</th><th>Created</th><th>Last Used</th><th>Expires</th><th>Action</th></tr>";
                    table.appendChild(thead);
                    const tbody = document.createElement("tbody");
                    tokens.forEach((token) => {
                        const tr = document.createElement("tr");
                        const nameTd = document.createElement("td");
                        const label = typeof token.label === "string" ? token.label.trim() : "";
                        nameTd.textContent = label || "Unlabeled";
                        tr.appendChild(nameTd);
                        const prefixTd = document.createElement("td");
                        prefixTd.style.fontFamily = "monospace";
                        prefixTd.textContent = token.token_hash_prefix || "";
                        tr.appendChild(prefixTd);
                        const createdTd = document.createElement("td");
                        createdTd.textContent = formatDateTime(token.created_at_iso, "N/A");
                        tr.appendChild(createdTd);
                        const lastUsedTd = document.createElement("td");
                        lastUsedTd.textContent = formatDateTime(token.last_used_at_iso, "Never");
                        tr.appendChild(lastUsedTd);
                        const expiresTd = document.createElement("td");
                        const neverExpires = token.expires_iso === "9999-12-31T23:59:59+00:00";
                        expiresTd.textContent = neverExpires ? "Never" : formatDateTime(token.expires_iso, "N/A");
                        tr.appendChild(expiresTd);
                        const actionTd = document.createElement("td");
                        const revokeBtn = document.createElement("button");
                        revokeBtn.className = "mss-login-btn mss-login-btn-danger";
                        revokeBtn.textContent = "Revoke";
                        revokeBtn.onclick = async () => {
                            const shouldRevoke = window.confirm("Revoke this API/JWT token? This cannot be undone.");
                            if (!shouldRevoke) return;
                            try {
                                const revokeRes = await api.fetchApi("/mss-login/api/tokens", {
                                    method: "DELETE",
                                    body: JSON.stringify({
                                        token_hash_prefix: String(token.token_hash_prefix || "").replace(/\.+$/, ""),
                                    }),
                                });
                                const revokeData = await revokeRes.json().catch(() => ({}));
                                if (!revokeRes.ok) {
                                    setCreateStatus(revokeData.error || "Failed to revoke token.", true);
                                    return;
                                }
                                setCreateStatus(revokeData.message || "Token revoked.");
                                refreshApiTokenList();
                            } catch (e) {
                                setCreateStatus("Failed to revoke token: " + (e.message || "Unknown error"), true);
                            }
                        };
                        actionTd.appendChild(revokeBtn);
                        tr.appendChild(actionTd);
                        tbody.appendChild(tr);
                    });
                    table.appendChild(tbody);
                    tokenListContainer.innerHTML = "";
                    tokenListContainer.appendChild(table);
                } catch (_) {
                    tokenListContainer.textContent = "Could not load API/JWT tokens.";
                }
            };

            if (!me || !me.username || String(me.username).toLowerCase() === "guest") {
                createBtn.disabled = true;
                if (passwordInput) passwordInput.disabled = true;
                setCreateStatus("Guest accounts cannot create API/JWT tokens.", true);
                await refreshApiTokenList();
                return;
            }

            createBtn.onclick = async () => {
                const expireHours = String(expireInput.value || "720").trim();
                const label = String(labelInput.value || "").trim();
                createBtn.disabled = true;
                tokenOutputWrap.style.display = "none";
                tokenOutput.textContent = "";
                setCreateStatus("");

                try {
                    const formData = new FormData();
                    formData.append("expire_hours", expireHours || "720");
                    formData.append("label", label);
                    formData.append("require_password_reauth", "true");

                    if (mfaTempToken) {
                        const mfaCode = String(mfaCodeInput.value || "").replace(/\s/g, "");
                        const backupCode = String(mfaBackupInput.value || "").replace(/\s/g, "");
                        formData.append("mfa_temp_token", mfaTempToken);
                        if (backupCode) {
                            formData.append("backup_code", backupCode);
                        } else if (mfaCode) {
                            formData.append("code", mfaCode);
                        }
                    } else {
                        const password = String(passwordInput.value || "");
                        if (!password) {
                            setCreateStatus("Please re-enter your password first.", true);
                            createBtn.disabled = false;
                            return;
                        }
                        formData.append("username", String(me.username));
                        formData.append("password", password);
                    }

                    const response = await fetch("/mss-login/generate_token", {
                        method: "POST",
                        body: formData,
                        credentials: "same-origin",
                    });
                    const result = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        setCreateStatus(result.error || "Failed to create token.", true);
                        createBtn.disabled = false;
                        return;
                    }

                    if (result.mfa_required && result.mfa_temp_token) {
                        mfaTempToken = result.mfa_temp_token;
                        if (mfaRow) mfaRow.style.display = "flex";
                        setCreateStatus("MFA required. Enter a code or backup code, then click Create token again.");
                        createBtn.textContent = "Verify and create token";
                        createBtn.disabled = false;
                        return;
                    }

                    if (result.jwt_token) {
                        if (mfaRow) mfaRow.style.display = "none";
                        if (mfaCodeInput) mfaCodeInput.value = "";
                        if (mfaBackupInput) mfaBackupInput.value = "";
                        mfaTempToken = null;
                        createBtn.textContent = "Create token";
                        tokenOutput.textContent = String(result.jwt_token);
                        tokenOutputWrap.style.display = "block";
                        setCreateStatus(result.message || "Token created successfully.");
                        passwordInput.value = "";
                        await refreshApiTokenList();
                    } else {
                        setCreateStatus(result.message || "Token created.");
                    }
                } catch (e) {
                    setCreateStatus("Failed to create token: " + (e.message || "Unknown error"), true);
                }
                createBtn.disabled = false;
            };

            copyBtn.onclick = async () => {
                const tokenText = String(tokenOutput.textContent || "");
                if (!tokenText) return;
                try {
                    await navigator.clipboard.writeText(tokenText);
                    setCreateStatus("Copied token to clipboard.");
                } catch (_) {
                    setCreateStatus("Copy failed. Please copy manually.", true);
                }
            };

            await refreshApiTokenList();
        })();

        // Session JWT section (list, masked by default, eye to reveal, revoke)
        const jwtSection = document.createElement("div");
        jwtSection.id = "mss-login-my-jwt-tokens";
        jwtSection.style.marginTop = "12px";
        const jwtHeading = document.createElement("h4");
        jwtHeading.style.margin = "0 0 8px 0";
        jwtHeading.textContent = "My Session JWT Tokens";
        jwtSection.appendChild(jwtHeading);
        const jwtTableWrap = document.createElement("div");
        jwtTableWrap.innerHTML = "<table class='mss-login-sessions-table'><thead><tr><th>Token</th><th>Created</th><th>Last used</th><th>Actions</th></tr></thead><tbody id='mss-login-sessions-tbody'></tbody></table>";
        jwtSection.appendChild(jwtTableWrap);
        wrapper.appendChild(jwtSection);
        (async () => {
            try {
                const res = await getData("/mss-login/me/sessions");
                const sessions = (res && res.sessions) ? res.sessions : [];
                const tbody = document.getElementById("mss-login-sessions-tbody");
                if (!tbody) return;
                tbody.innerHTML = "";
                for (const s of sessions) {
                    const jti = s.jti || "";
                    const last4 = jti.slice(-4);
                    const tr = document.createElement("tr");
                    const tdToken = document.createElement("td");
                    tdToken.style.fontFamily = "monospace";
                    const maskedSpan = document.createElement("span");
                    maskedSpan.textContent = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022-" + last4;
                    maskedSpan.setAttribute("data-jti", jti);
                    tdToken.appendChild(maskedSpan);
                    tr.appendChild(tdToken);
                    const tdCreated = document.createElement("td");
                    tdCreated.textContent = s.created_at_iso ? new Date(s.created_at_iso).toLocaleString() : "";
                    tr.appendChild(tdCreated);
                    const tdLastUsed = document.createElement("td");
                    tdLastUsed.textContent = s.last_used_at_iso ? new Date(s.last_used_at_iso).toLocaleString() : "—";
                    tr.appendChild(tdLastUsed);
                    const tdActions = document.createElement("td");
                    if (s.is_current) {
                        const eyeBtn = document.createElement("button");
                        eyeBtn.textContent = "\uD83D\uDC41\uFE0F";
                        eyeBtn.title = "Reveal token";
                        eyeBtn.style.marginRight = "6px";
                        eyeBtn.onclick = async () => {
                            try {
                                const res = await api.fetchApi("/mss-login/me/current-token");
                                if (!res.ok) return;
                                const r = await res.json().catch(() => ({}));
                                const isHttps = !!r.is_https;
                                if (!isHttps) {
                                    const go = confirm("Connection is not secure. Revealing the token over HTTP may expose it. Please notify your administrator to enable HTTPS.\n\nReveal anyway?");
                                    if (!go) return;
                                }
                                const token = r.token;
                                if (token) {
                                    if (maskedSpan.textContent === "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022-" + last4) {
                                        maskedSpan.textContent = token;
                                        eyeBtn.textContent = "\uD83D\uDD12";
                                        eyeBtn.title = "Hide token";
                                    } else {
                                        maskedSpan.textContent = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022-" + last4;
                                        eyeBtn.textContent = "\uD83D\uDC41\uFE0F";
                                        eyeBtn.title = "Reveal token";
                                    }
                                }
                            } catch (_) {}
                        };
                        tdActions.appendChild(eyeBtn);
                    }
                    const revokeBtn = document.createElement("button");
                    revokeBtn.textContent = "Revoke";
                    revokeBtn.onclick = async () => {
                        try {
                            const res = await api.fetchApi("/mss-login/me/sessions/revoke", { method: "POST", body: JSON.stringify({ jti }) });
                            if (!res.ok) return;
                            const data = await res.json().catch(() => ({}));
                            if (data && data.status === "ok") {
                                tr.remove();
                                if (s.is_current) window.location.href = "/logout";
                            }
                        } catch (_) {}
                    };
                    tdActions.appendChild(revokeBtn);
                    tr.appendChild(tdActions);
                    tbody.appendChild(tr);
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
