/** Shared API helpers for the MSS-Login Vue config dialog. */

export async function apiJson(path, options = {}) {
	const opts = {
		credentials: "same-origin",
		...options,
		headers: {
			...(options.body && !(options.body instanceof FormData)
				? { "Content-Type": "application/json" }
				: {}),
			...(options.headers || {}),
		},
	};
	if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
		opts.body = JSON.stringify(opts.body);
	}
	const res = await fetch(path, opts);
	let data = null;
	try {
		data = await res.json();
	} catch (_) {
		data = null;
	}
	return { ok: res.ok, status: res.status, data };
}

export function escapeHtml(str) {
	return String(str ?? "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}

export function isOwnerUser(user) {
	return (
		Array.isArray(user?.groups) &&
		user.groups.map((g) => String(g).toLowerCase()).includes("owner")
	);
}
