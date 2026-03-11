/**
 * MFA page: verify or setup two-factor authentication.
 * Token and mode are read from sessionStorage (set by login page before redirect).
 * APIs: /mss-login/api/mfa/setup, verify-setup, verify.
 */

/** DOMPurify for sanitizing the MFA forms (loaded by the HTML page when used standalone). */
(function () {
	"use strict";

	const STORAGE_KEY_TOKEN = "mfa_temp_token";
	const STORAGE_KEY_MODE = "mfa_mode";

	function addToast(message, type) {
		const toasts = document.getElementById("toasts");
		if (!toasts) return;
		const toast = document.createElement("div");
		toast.classList.add("toast", "hide", type);
		toast.textContent = message;
		toasts.appendChild(toast);
		setTimeout(function () {
			toast.classList.replace("hide", "show");
		}, 500);
		setTimeout(function () {
			toast.classList.replace("show", "hide");
		}, 4500);
		setTimeout(function () {
			toast.remove();
		}, 5500);
	}

	function clearMfaStorage() {
		sessionStorage.removeItem(STORAGE_KEY_TOKEN);
		sessionStorage.removeItem(STORAGE_KEY_MODE);
	}

	function setCookieFromJwt(jwtToken) {
		let cookieString = "jwt_token=" + jwtToken + "; path=/; HttpOnly; SameSite=Strict";
		if (window.location.protocol === "https:") {
			cookieString += "; Secure";
		}
		document.cookie = cookieString;
	}

	function redirectToLogin() {
		clearMfaStorage();
		window.location.href = "/login";
	}

	const mfaPage = {
		token: null,
		mode: null,

		init: function () {
			this.token = sessionStorage.getItem(STORAGE_KEY_TOKEN);
			this.mode = sessionStorage.getItem(STORAGE_KEY_MODE);

			document.getElementById("mfa-verify-section").style.display = "none";
			document.getElementById("mfa-setup-section").style.display = "none";
			const loadingEl = document.getElementById("mfa-loading");
			if (loadingEl) loadingEl.style.display = "none";

			if (!this.token || !this.mode) {
				redirectToLogin();
				return;
			}

			// Back to login: clear storage then go to /login
			function bindBackLink(id) {
				const el = document.getElementById(id);
				if (el) {
					el.addEventListener("click", function (e) {
						e.preventDefault();
						redirectToLogin();
					});
				}
			}
			bindBackLink("mfa-verify-back-link");
			bindBackLink("mfa-setup-back-link");

			if (this.mode === "verify") {
				document.getElementById("mfa-verify-section").style.display = "block";
				var codeInput = document.getElementById("mfa-code");
				if (codeInput) codeInput.focus();
			} else if (this.mode === "setup") {
				this.loadSetup();
			} else {
				clearMfaStorage();
				redirectToLogin();
			}
		},

		loadSetup: function () {
			const section = document.getElementById("mfa-setup-section");
			section.style.display = "block";
			const qrContainer = document.getElementById("mfa-qr-container");
			const backupDisplay = document.getElementById("mfa-backup-display");
			const backupCodeEl = document.getElementById("mfa-backup-code");

			fetch("/mss-login/api/mfa/setup", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ mfa_temp_token: this.token }),
			})
				.then(function (r) {
					return r.json();
				})
				.then(function (data) {
					if (data.error) {
						addToast(data.error || "MFA setup failed", "error");
						return;
					}
					if (qrContainer) {
						qrContainer.innerHTML = "";
						if (typeof QRCode !== "undefined") {
							new QRCode(qrContainer, {
								text: data.provisioning_uri,
								width: 200,
								height: 200,
							});
						} else {
							qrContainer.innerText = DOMPurify.sanitize(
								`<p><a href="${data.provisioning_uri || ""}" target="_blank">Open in authenticator</a></p>`
							);
						}
					}
					if (data.backup_code && backupCodeEl && backupDisplay) {
						backupCodeEl.textContent = data.backup_code;
						backupDisplay.style.display = "block";
					}
					var setupCodeInput = document.getElementById("mfa-setup-code");
					if (setupCodeInput) setupCodeInput.focus();
				})
				.catch(function (err) {
					addToast("Setup failed: " + (err.message || "Unknown error"), "error");
				});
		},

		submitVerify: function (event) {
			event.preventDefault();
			var self = this;
			var code = (document.getElementById("mfa-code").value || "").replace(/\s/g, "");
			var backupRaw = (document.getElementById("mfa-backup").value || "")
				.replace(/\s/g, "")
				.replace(/-/g, "")
				.toUpperCase();
			var body = { mfa_temp_token: this.token };
			if (backupRaw) body.backup_code = backupRaw;
			else if (code) body.code = code;
			else {
				addToast("Enter verification code or backup code", "error");
				return;
			}
			var btn = document.querySelector("#mfa-verify-form button[type='submit']");
			if (btn) {
				btn.disabled = true;
				btn.textContent = "Verifying...";
			}
			fetch("/mss-login/api/mfa/verify", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			})
				.then(function (r) {
					return r.json();
				})
				.then(function (result) {
					if (result.jwt_token) {
						setCookieFromJwt(result.jwt_token);
						clearMfaStorage();
						addToast(result.message || "Login successful", "success");
						window.location.href = result.redirect_url || "/";
					} else {
						addToast(result.error || "Invalid code", "error");
						if (btn) {
							btn.disabled = false;
							btn.textContent = "Verify";
						}
					}
				})
				.catch(function (err) {
					addToast("Error: " + err.message, "error");
					if (btn) {
						btn.disabled = false;
						btn.textContent = "Verify";
					}
				});
		},

		submitSetup: function (event) {
			event.preventDefault();
			var self = this;
			var code = (document.getElementById("mfa-setup-code").value || "").replace(/\s/g, "");
			if (!code || code.length !== 6) {
				addToast("Enter a 6-digit code from your authenticator app", "error");
				return;
			}
			var btn = document.querySelector("#mfa-setup-form button[type='submit']");
			if (btn) {
				btn.disabled = true;
				btn.textContent = "Verifying...";
			}
			fetch("/mss-login/api/mfa/verify-setup", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ mfa_temp_token: this.token, code: code }),
			})
				.then(function (r) {
					return r.json();
				})
				.then(function (verifySetupData) {
					if (verifySetupData.error) {
						addToast(verifySetupData.error || "Invalid code", "error");
						if (btn) {
							btn.disabled = false;
							btn.textContent = "Complete Setup";
						}
						return;
					}
					return fetch("/mss-login/api/mfa/verify", {
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ mfa_temp_token: self.token, code: code }),
					});
				})
				.then(function (resp) {
					if (!resp || !resp.json) return;
					return resp.json();
				})
				.then(function (verifyData) {
					if (!verifyData) return;
					if (verifyData.jwt_token) {
						setCookieFromJwt(verifyData.jwt_token);
						clearMfaStorage();
						addToast(verifyData.message || "MFA enabled. Login successful.", "success");
						window.location.href = verifyData.redirect_url || "/";
					} else {
						addToast(verifyData.error || "Verification failed", "error");
						if (btn) {
							btn.disabled = false;
							btn.textContent = "Complete Setup";
						}
					}
				})
				.catch(function (err) {
					addToast("Error: " + (err && err.message ? err.message : "Unknown error"), "error");
					if (btn) {
						btn.disabled = false;
						btn.textContent = "Complete Setup";
					}
				});
		},
	};

	window.mfaPage = mfaPage;
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			mfaPage.init();
		});
	} else {
		mfaPage.init();
	}
})();
