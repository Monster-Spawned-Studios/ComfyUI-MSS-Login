/* Passkey + recovery-code management UI (no inline scripts). */
(function () {
	function toast(msg, kind) {
		var toasts = document.getElementById("toasts");
		if (toasts) {
			var t = document.createElement("div");
			t.className = "toast hide " + (kind || "info");
			t.textContent = msg;
			toasts.appendChild(t);
			setTimeout(function () { t.classList.replace("hide", "show"); }, 50);
			setTimeout(function () { t.classList.replace("show", "hide"); }, 4000);
			setTimeout(function () { t.remove(); }, 4600);
		} else if (window.addToast) {
			window.addToast(msg, kind || "info");
		} else {
			console.log(kind || "info", msg);
		}
	}

	function sanitize(text) {
		if (window.DOMPurify) return DOMPurify.sanitize(String(text || ""), { ALLOWED_TAGS: [] });
		return String(text || "").replace(/[<>&"']/g, "");
	}

	async function api(path, opts) {
		var r = await fetch(path, Object.assign({ credentials: "same-origin" }, opts || {}));
		var data = await r.json().catch(function () {
			return {};
		});
		return { ok: r.ok, status: r.status, data: data };
	}

	function bufferToBase64url(buf) {
		var bytes = new Uint8Array(buf);
		var str = "";
		for (var i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
		return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
	}

	function base64urlToBuffer(b64) {
		var pad = "=".repeat((4 - (b64.length % 4)) % 4);
		var s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
		var raw = atob(s);
		var out = new Uint8Array(raw.length);
		for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
		return out.buffer;
	}

	function publicKeyFromJson(opts) {
		var o = Object.assign({}, opts);
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
				attestationObject: cred.response.attestationObject
					? bufferToBase64url(cred.response.attestationObject)
					: undefined,
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

	async function refreshList() {
		var res = await api("/mss-login/webauthn/credentials");
		var ul = document.getElementById("passkey-list");
		var badge = document.getElementById("passkey-count-badge");
		if (!ul) return;
		ul.innerHTML = "";
		if (!res.ok) {
			toast(res.data.error || "Could not list passkeys", "error");
			return;
		}
		var creds = res.data.credentials || [];
		if (badge) {
			badge.textContent = creds.length + " registered";
		}
		if (creds.length === 0) {
			var empty = document.createElement("div");
			empty.className = "passkey-empty";
			empty.textContent = "No passkeys registered yet. Add a passkey below to enable biometric or hardware security key login.";
			ul.appendChild(empty);
			return;
		}
		creds.forEach(function (c) {
			var li = document.createElement("li");
			li.className = "passkey-card";

			var infoDiv = document.createElement("div");
			if (infoDiv.style) infoDiv.style.flex = "1";

			var titleRow = document.createElement("div");
			if (titleRow.style) {
				titleRow.style.display = "flex";
				titleRow.style.alignItems = "center";
				titleRow.style.flexWrap = "wrap";
				titleRow.style.gap = "6px";
			}

			var nameSpan = document.createElement("strong");
			if (nameSpan.style) nameSpan.style.color = "#f4f4f5";
			nameSpan.textContent = sanitize(c.name);

			var badgeSpan = document.createElement("span");
			badgeSpan.className = "passkey-badge";
			badgeSpan.textContent = c.synced ? "Synced Passkey" : "Hardware Device";

			titleRow.appendChild(nameSpan);
			titleRow.appendChild(badgeSpan);
			infoDiv.appendChild(titleRow);

			var metaDiv = document.createElement("div");
			if (metaDiv.style) {
				metaDiv.style.fontSize = "0.78rem";
				metaDiv.style.color = "#71717a";
				metaDiv.style.marginTop = "4px";
			}
			metaDiv.textContent = c.last_used_at
				? "Last used: " + new Date(c.last_used_at * 1000).toLocaleString()
				: "Never used yet";
			infoDiv.appendChild(metaDiv);

			var btn = document.createElement("button");
			btn.type = "button";
			btn.className = "passkey-delete-btn";
			btn.textContent = "Revoke";
			btn.addEventListener("click", async function () {
				if (typeof confirm === "function" && !confirm('Are you sure you want to revoke passkey "' + c.name + '"?')) return;
				var del = await api("/mss-login/webauthn/credentials", {
					method: "DELETE",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ id: c.id }),
				});
				if (!del.ok) toast(del.data.error || "Delete failed", "error");
				else {
					toast("Passkey revoked", "success");
					refreshList();
				}
			});

			li.appendChild(infoDiv);
			li.appendChild(btn);
			ul.appendChild(li);
		});
	}

	async function init() {
		var status = await api("/mss-login/api/webauthn/status");
		var insecure = document.getElementById("passkey-insecure");
		var section = document.getElementById("passkey-section");
		var data = status.data || {};
		var secureOrigin =
			data.origins !== undefined
				? Boolean(
						window.isSecureContext &&
							(data.origins.includes(location.origin) ||
								data.origins.includes("*") ||
								data.origins.some(function (o) {
									return o.replace(/\/+$/, "") === location.origin;
								}))
				  )
				: Boolean(data.secure_origin);

		if (!data.available || !secureOrigin) {
			if (insecure) {
				insecure.hidden = false;
				if (insecure.style) insecure.style.display = "block";
			}
			if (section) {
				section.hidden = true;
				if (section.style) section.style.display = "none";
			}
		} else {
			if (insecure) {
				insecure.hidden = true;
				if (insecure.style) insecure.style.display = "none";
			}
			if (section) {
				section.hidden = false;
				if (section.style) section.style.display = "block";
			}
		}
		await refreshList();

		var regBtn = document.getElementById("passkey-register");
		if (regBtn) {
			regBtn.addEventListener("click", async function () {
				regBtn.disabled = true;
				var origText = regBtn.innerHTML;
				regBtn.textContent = "Waiting for device...";
				var opt = await api("/mss-login/webauthn/register/options", { method: "POST" });
				if (!opt.ok) {
					toast(opt.data.error || opt.data.message || "Passkeys registration failed", "error");
					regBtn.disabled = false;
					regBtn.innerHTML = origText;
					return;
				}
				try {
					var cred = await navigator.credentials.create({
						publicKey: publicKeyFromJson(opt.data),
					});
					var name = (document.getElementById("passkey-name").value || "").trim() || "Passkey";
					var ver = await api("/mss-login/webauthn/register/verify", {
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ credential: credentialToJson(cred), name: name }),
					});
					if (!ver.ok) toast(ver.data.error || "Registration verification failed", "error");
					else {
						toast("Passkey successfully registered!", "success");
						var nameInput = document.getElementById("passkey-name");
						if (nameInput) nameInput.value = "";
						refreshList();
					}
				} catch (e) {
					if (e.name !== "NotAllowedError") {
						toast(e.message || "Passkey registration cancelled", "error");
					}
				} finally {
					regBtn.disabled = false;
					regBtn.innerHTML = origText;
				}
			});
		}

		var regenBtn = document.getElementById("recovery-regen");
		if (regenBtn) {
			regenBtn.addEventListener("click", async function () {
				regenBtn.disabled = true;
				var res = await api("/mss-login/api/mfa/recovery-codes/regenerate", { method: "POST" });
				regenBtn.disabled = false;
				if (!res.ok) {
					toast(res.data.error || res.data.message || "Could not generate recovery codes", "error");
					return;
				}
				var wrapper = document.getElementById("recovery-codes-wrapper");
				var pre = document.getElementById("recovery-codes");
				if (pre) {
					pre.hidden = false;
					pre.textContent = (res.data.recovery_codes || []).join("\n");
				}
				if (wrapper) {
					wrapper.hidden = false;
					if (wrapper.style) wrapper.style.display = "block";
				}
				toast("Save these codes now — shown once!", "success");
			});
		}

		var copyBtn = document.getElementById("recovery-copy");
		if (copyBtn) {
			copyBtn.addEventListener("click", function () {
				var pre = document.getElementById("recovery-codes");
				if (pre && pre.textContent) {
					if (navigator.clipboard && navigator.clipboard.writeText) {
						navigator.clipboard.writeText(pre.textContent).then(function () {
							toast("Recovery codes copied to clipboard!", "success");
						}).catch(function () {
							toast("Please copy codes manually from box.", "info");
						});
					}
				}
			});
		}
	}

	if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
	else init();
})();
