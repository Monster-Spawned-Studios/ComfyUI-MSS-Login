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

	function _fallbackCopyText(text) {
		try {
			const ta = document.createElement("textarea");
			ta.value = text;
			ta.style.position = "fixed";
			ta.style.opacity = "0";
			document.body.appendChild(ta);
			ta.select();
			document.execCommand("copy");
			document.body.removeChild(ta);
			addToast("Secret key copied to clipboard!", "success");
		} catch (e) {
			addToast("Could not copy automatically. Please copy manually.", "info");
		}
	}

	function clearMfaStorage() {
		sessionStorage.removeItem(STORAGE_KEY_TOKEN);
		sessionStorage.removeItem(STORAGE_KEY_MODE);
	}

	function redirectToLogin() {
		clearMfaStorage();
		window.location.href = "/login";
	}

	function bufferToBase64url(buf) {
		const bytes = new Uint8Array(buf);
		let str = "";
		for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
		return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
	}

	function base64urlToBuffer(b64) {
		const pad = "=".repeat((4 - (b64.length % 4)) % 4);
		const s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
		const raw = atob(s);
		const out = new Uint8Array(raw.length);
		for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
		return out.buffer;
	}

	function publicKeyFromJson(opts) {
		const o = Object.assign({}, opts);
		o.challenge = base64urlToBuffer(o.challenge);
		if (o.user && o.user.id) o.user.id = base64urlToBuffer(o.user.id);
		if (o.excludeCredentials) {
			o.excludeCredentials = o.excludeCredentials.map(function (c) {
				return Object.assign({}, c, { id: base64urlToBuffer(c.id) });
			});
		}
		if (o.allowCredentials) {
			o.allowCredentials = o.allowCredentials.map(function (c) {
				return Object.assign({}, c, { id: base64urlToBuffer(c.id) });
			});
		}
		return o;
	}

	function credentialToJson(cred) {
		return {
			id: cred.id,
			rawId: bufferToBase64url(cred.rawId),
			type: cred.type,
			response: {
				clientDataJSON: bufferToBase64url(cred.response.clientDataJSON),
				authenticatorData: cred.response.authenticatorData
					? bufferToBase64url(cred.response.authenticatorData)
					: undefined,
				signature: cred.response.signature
					? bufferToBase64url(cred.response.signature)
					: undefined,
				userHandle: cred.response.userHandle
					? bufferToBase64url(cred.response.userHandle)
					: undefined,
			},
		};
	}

	const mfaPage = {
		token: null,
		mode: null,

		init: function () {
			var verifySection = document.getElementById("mfa-verify-section");
			var setupSection = document.getElementById("mfa-setup-section");
			// Never run when MFA DOM is missing (main app, loading page, or before DOM ready). Prevents "Cannot read properties of null (reading 'style')" at line 57 in bundled/cached builds.
			if (!verifySection && !setupSection) {
				return;
			}

			this.token = sessionStorage.getItem(STORAGE_KEY_TOKEN);
			this.mode = sessionStorage.getItem(STORAGE_KEY_MODE);

			if (verifySection) verifySection.style.display = "none";
			if (setupSection) setupSection.style.display = "none";
			const loadingEl = document.getElementById("mfa-loading");
			if (loadingEl) loadingEl.style.display = "none";

			if (!this.token || !this.mode) {
				var self = this;
				fetch("/mss-login/api/me", { credentials: "same-origin" })
					.then(function (r) { return r.ok ? r.json() : null; })
					.then(function (me) {
						if (me && me.username && me.username.toLowerCase() !== "guest") {
							self.mode = "setup";
							self.token = "";
							self.loadSetup();
						} else {
							redirectToLogin();
						}
					})
					.catch(function () {
						redirectToLogin();
					});
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
				if (verifySection) verifySection.style.display = "block";
				var codeInput = document.getElementById("mfa-code");
				if (codeInput) codeInput.focus();
				this.initPasskeyVerify();
			} else if (this.mode === "setup") {
				this.loadSetup();
			} else {
				clearMfaStorage();
				redirectToLogin();
			}
		},

		initPasskeyVerify: function () {
			const container = document.getElementById("mfa-passkey-container");
			const btn = document.getElementById("mfa-passkey-btn");
			if (!container || !btn || !window.PublicKeyCredential) return;
			const self = this;

			fetch("/mss-login/api/webauthn/status", { credentials: "same-origin" })
				.then(function (r) { return r.ok ? r.json() : null; })
				.then(function (status) {
					if (!status || !status.available) return;
					const secureOrigin = status.origins !== undefined
						? Boolean(
								window.isSecureContext &&
									(status.origins.includes(location.origin) ||
										status.origins.includes("*") ||
										status.origins.some(function (o) {
											return o.replace(/\/+$/, "") === location.origin;
										}))
						  )
						: Boolean(status.secure_origin);
					if (!secureOrigin) return;

					container.style.display = "block";

					btn.addEventListener("click", async function () {
						btn.disabled = true;
						const origHtml = btn.innerHTML;
						btn.textContent = "Verifying passkey...";
						try {
							const optRes = await fetch("/mss-login/webauthn/auth/options", {
								method: "POST",
								credentials: "same-origin",
								headers: { "Content-Type": "application/json" },
								body: JSON.stringify({ mfa_temp_token: self.token })
							});
							if (!optRes.ok) {
								const err = await optRes.json().catch(function () { return {}; });
								addToast(err.error || "Passkey verification not available", "error");
								btn.disabled = false;
								btn.innerHTML = origHtml;
								return;
							}
							const optData = await optRes.json();
							const publicKey = publicKeyFromJson(optData);
							const cred = await navigator.credentials.get({ publicKey: publicKey });
							if (!cred) {
								addToast("No passkey selected", "error");
								btn.disabled = false;
								btn.innerHTML = origHtml;
								return;
							}
							const rememberMe = sessionStorage.getItem("mss_login_remember_me") === "1";
							const verifyRes = await fetch("/mss-login/webauthn/auth/verify", {
								method: "POST",
								credentials: "same-origin",
								headers: { "Content-Type": "application/json" },
								body: JSON.stringify({
									credential: credentialToJson(cred),
									mfa_temp_token: self.token,
									remember_me: rememberMe
								})
							});
							const verifyData = await verifyRes.json().catch(function () { return {}; });
							if (verifyRes.ok && (verifyData.jwt_token || verifyData.redirect_url)) {
								clearMfaStorage();
								addToast(verifyData.message || "Passkey verified. Login successful.", "success");
								window.location.href = verifyData.redirect_url || "/";
							} else {
								addToast(verifyData.error || "Passkey verification failed", "error");
								btn.disabled = false;
								btn.innerHTML = origHtml;
							}
						} catch (e) {
							if (e.name !== "NotAllowedError") {
								addToast(e.message || "Passkey error", "error");
							}
							btn.disabled = false;
							btn.innerHTML = origHtml;
						}
					});
				})
				.catch(function () {});
		},

		loadSetup: function () {
			const section = document.getElementById("mfa-setup-section");
			if (!section) return;
			section.style.display = "block";
			const qrContainer = document.getElementById("mfa-qr-container");
			const backupDisplay = document.getElementById("mfa-backup-display");
			const backupCodeEl = document.getElementById("mfa-backup-code");
			const secretDisplay = document.getElementById("mfa-secret-display");
			const secretKeyEl = document.getElementById("mfa-secret-key");

			fetch("/mss-login/api/mfa/setup", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				credentials: "same-origin",
				body: JSON.stringify(this.token ? { mfa_temp_token: this.token } : {}),
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
						let qrRendered = false;
						if (typeof QRCode !== "undefined" && data.provisioning_uri) {
							try {
								new QRCode(qrContainer, {
									text: data.provisioning_uri,
									width: 200,
									height: 200,
									colorDark: "#000000",
									colorLight: "#ffffff",
									correctLevel:
										typeof QRCode.CorrectLevel !== "undefined"
											? QRCode.CorrectLevel.M
											: 0,
								});
								qrRendered = true;
							} catch (qrErr) {
								console.error("Failed to render QR code:", qrErr);
							}
						}
						if (!qrRendered) {
							qrContainer.innerHTML = DOMPurify.sanitize(
								`<p style="margin: 0.5rem 0; text-align: center;"><a class="btn mss-auth-btn" href="${encodeURI(
									data.provisioning_uri || ""
								)}" target="_blank" rel="noopener noreferrer">Open in Authenticator App</a></p>`
							);
						}
					}
					// Display secret key for manual entry with formatting and copy support
					let secretKey = data.secret;
					if (!secretKey && data.provisioning_uri) {
						const match = /[?&]secret=([A-Z0-9]+)/i.exec(data.provisioning_uri);
						if (match) secretKey = match[1];
					}
					if (secretKey && secretKeyEl) {
						const cleanKey = secretKey.replace(/\s+/g, "").toUpperCase();
						secretKeyEl.dataset.raw = cleanKey;
						// Format with spaces every 4 characters for readability
						secretKeyEl.textContent = cleanKey.replace(/(.{4})/g, "$1 ").trim();

						const toggleBtn = document.getElementById("mfa-toggle-secret-btn");
						if (toggleBtn && secretDisplay) {
							toggleBtn.onclick = function () {
								if (secretDisplay.style.display === "none" || !secretDisplay.style.display) {
									secretDisplay.style.display = "block";
									toggleBtn.textContent = "Hide manual secret key";
								} else {
									secretDisplay.style.display = "none";
									toggleBtn.textContent = "Can't scan QR code? Show manual secret key";
								}
							};
						}
						const copyBtn = document.getElementById("mfa-copy-secret-btn");
						if (copyBtn) {
							copyBtn.onclick = function () {
								const toCopy = secretKeyEl.dataset.raw || cleanKey;
								if (navigator.clipboard && navigator.clipboard.writeText) {
									navigator.clipboard
										.writeText(toCopy)
										.then(function () {
											addToast("Secret key copied to clipboard!", "success");
										})
										.catch(function () {
											_fallbackCopyText(toCopy);
										});
								} else {
									_fallbackCopyText(toCopy);
								}
							};
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
			var btn = document.querySelector(
				"#mfa-page-verify-form button[type='submit'], #mfa-verify-form button[type='submit']"
			);
			if (btn) {
				btn.disabled = true;
				btn.textContent = "Verifying...";
			}
			fetch("/mss-login/api/mfa/verify", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				credentials: "same-origin",
				body: JSON.stringify(body),
			})
				.then(function (r) {
					return r.json();
				})
				.then(function (result) {
					if (result && !result.error && (result.jwt_token || result.redirect_url)) {
						// Server sets HttpOnly session cookie; do not mirror jwt_token in JS.
						clearMfaStorage();
						addToast(result.message || "Login successful", "success");
						window.location.href = result.redirect_url || "/";
					} else {
						addToast((result && result.error) || "Invalid code", "error");
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
			var btn = document.querySelector(
				"#mfa-page-setup-form button[type='submit'], #mfa-setup-form button[type='submit']"
			);
			if (btn) {
				btn.disabled = true;
				btn.textContent = "Verifying...";
			}
			var setupPayload = {
				code: code,
				remember_me: sessionStorage.getItem("mss_login_remember_me") === "1",
			};
			if (this.token) setupPayload.mfa_temp_token = this.token;
			// verify-setup issues the session on success — never reuse the same TOTP on /verify.
			fetch("/mss-login/api/mfa/verify-setup", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				credentials: "same-origin",
				body: JSON.stringify(setupPayload),
			})
				.then(function (r) {
					return r.json().then(function (data) {
						return { ok: r.ok, data: data };
					});
				})
				.then(function (result) {
					var verifySetupData = result.data || {};
					if (verifySetupData.error) {
						addToast(verifySetupData.error || "Invalid code", "error");
						if (btn) {
							btn.disabled = false;
							btn.textContent = "Complete Setup";
						}
						return;
					}
					clearMfaStorage();
					addToast(verifySetupData.message || "MFA enabled. Login successful.", "success");
					window.location.href = verifySetupData.redirect_url || "/";
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
