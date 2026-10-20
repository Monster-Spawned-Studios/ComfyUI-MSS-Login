/* Passkey + recovery-code management UI (no inline scripts). */
(function () {
	function toast(msg, kind) {
		if (window.addToast) window.addToast(msg, kind || "info");
		else console.log(kind || "info", msg);
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
		ul.innerHTML = "";
		if (!res.ok) {
			toast(res.data.error || "Could not list passkeys", "error");
			return;
		}
		(res.data.credentials || []).forEach(function (c) {
			var li = document.createElement("li");
			li.textContent =
				sanitize(c.name) +
				" — " +
				(c.synced ? "synced" : "device-bound") +
				(c.last_used_at ? " · last used " + new Date(c.last_used_at * 1000).toISOString() : "");
			var btn = document.createElement("button");
			btn.type = "button";
			btn.textContent = "Delete";
			btn.addEventListener("click", async function () {
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
			li.appendChild(document.createTextNode(" "));
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
			insecure.hidden = false;
			section.hidden = true;
		} else {
			insecure.hidden = true;
			section.hidden = false;
			await refreshList();
		}

		document.getElementById("passkey-register").addEventListener("click", async function () {
			var opt = await api("/mss-login/webauthn/register/options", { method: "POST" });
			if (!opt.ok) {
				toast(opt.data.error || opt.data.message || "Step-up required", "error");
				return;
			}
			try {
				var cred = await navigator.credentials.create({
					publicKey: publicKeyFromJson(opt.data),
				});
				var name = document.getElementById("passkey-name").value || "Passkey";
				var ver = await api("/mss-login/webauthn/register/verify", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ credential: credentialToJson(cred), name: name }),
				});
				if (!ver.ok) toast(ver.data.error || "Register failed", "error");
				else {
					toast("Passkey registered", "success");
					refreshList();
				}
			} catch (e) {
				toast("Passkey cancelled or failed", "error");
			}
		});

		document.getElementById("recovery-regen").addEventListener("click", async function () {
			var res = await api("/mss-login/api/mfa/recovery-codes/regenerate", { method: "POST" });
			if (!res.ok) {
				toast(res.data.error || res.data.message || "Re-authenticate and try again", "error");
				return;
			}
			var pre = document.getElementById("recovery-codes");
			pre.hidden = false;
			pre.textContent = (res.data.recovery_codes || []).join("\n");
			toast("Save these codes now — shown once", "success");
		});
	}

	if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
	else init();
})();
