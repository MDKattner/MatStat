/* Login form handler. Kept as an external file so the app can ship a strict
 * `script-src 'self'` Content-Security-Policy without inline-script exceptions.
 */

const form = document.getElementById("login-form");
const passwordInput = document.getElementById("password");
const error = document.getElementById("login-error");
const btn = document.getElementById("login-btn");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.textContent = "";
  btn.disabled = true;
  try {
    const resp = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: passwordInput.value }),
    });
    if (resp.ok) {
      window.location.href = "/";
      return;
    }
    const body = await resp.json().catch(() => ({}));
    error.textContent = body.detail || "Login failed.";
  } catch {
    error.textContent = "Could not reach the server.";
  }
  btn.disabled = false;
  passwordInput.select();
});
