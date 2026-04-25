import { createApp } from "vue";
import "./auth.css";

function attachFormListeners() {
	const loginForm = document.getElementById("login-form");
	if (loginForm && typeof window.login === "function") {
		loginForm.addEventListener("submit", (event) => window.login(event));
	}

	const registerForm = document.getElementById("register-form");
	if (registerForm && typeof window.register === "function") {
		registerForm.addEventListener("submit", (event) => window.register(event));
	}

	const generateForm = document.getElementById("generate-form");
	if (generateForm && typeof window.generate === "function") {
		generateForm.addEventListener("submit", (event) => window.generate(event));
	}

	const mfaVerifyForm = document.getElementById("mfa-verify-form");
	if (mfaVerifyForm && typeof window.generateMfaVerify === "function") {
		mfaVerifyForm.addEventListener("submit", (event) => window.generateMfaVerify(event));
	}

	const guestBtn = document.getElementById("guest-login-btn");
	if (guestBtn && typeof window.guestLogin === "function") {
		guestBtn.addEventListener("click", (event) => window.guestLogin(event));
	}

	const backGenerateBtn = document.getElementById("back-generate-btn");
	if (backGenerateBtn && typeof window.backToGenerateForm === "function") {
		backGenerateBtn.addEventListener("click", () => window.backToGenerateForm());
	}

	const mfaPageVerify = document.getElementById("mfa-page-verify-form");
	if (mfaPageVerify && window.mfaPage && typeof window.mfaPage.submitVerify === "function") {
		mfaPageVerify.addEventListener("submit", (event) => window.mfaPage.submitVerify(event));
	}

	const mfaPageSetup = document.getElementById("mfa-page-setup-form");
	if (mfaPageSetup && window.mfaPage && typeof window.mfaPage.submitSetup === "function") {
		mfaPageSetup.addEventListener("submit", (event) => window.mfaPage.submitSetup(event));
	}
}

createApp({
	mounted() {
		attachFormListeners();
		document.body.classList.add("mss-auth-ready");
	},
	template: "<div></div>",
}).mount("#mss-login-auth-app");
