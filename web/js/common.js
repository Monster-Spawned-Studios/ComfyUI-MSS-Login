/**
 * Copyright © 2026 Monster Spawned Studios
 * https://monsterspawned.studio/
 * All Rights Reserved.
 */

/**
 * Common functions for the MSS-Login web application.
 */

/**
 * Add a toast notification to the page.
 */
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

/**
 * Enable DEBUG_MODE based on the DEBUG_MODE environment variable.
 */
function isDebugMode() {
  return fetch("/mss-login/api/debug-mode").then(response => response.json()).then(data => data.debugMode);
}

/**
 * Write a debug message to the console.
 */
function debug(message) {
  if (isDebugMode()) {
    console.log(message);
  }
}

/**
 * Write an error message to the console.
 */
function error(message) {
  console.error(message);
}

/**
 * Write a warning message to the console.
 */
function warning(message) {
  console.warn(message);
}

/**
 * Write an info message to the console.
 */
function info(message) {
  console.info(message);
}

/**
 * Write a success message to the console.
 */
function success(message) {
  console.log(message);
}