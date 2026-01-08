document.addEventListener("DOMContentLoaded", () => {

    const form = document.getElementById("changePasswordForm");
    const message = document.getElementById("message");

    const oldPassword = document.getElementById("oldPassword");
    const newPassword = document.getElementById("newPassword");
    const confirmPassword = document.getElementById("confirmPassword");

    /* ===== Show / Hide Password ===== */

    document.querySelectorAll(".toggle-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.preventDefault();

            const inputId = btn.dataset.target;
            const input = document.getElementById(inputId);

            if (!input) return;

            if (input.type === "password") {
                input.type = "text";
                btn.textContent = "🙈";
            } else {
                input.type = "password";
                btn.textContent = "👁️";
            }
        });
    });

    /* ===== Form Submit ===== */

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        message.textContent = "";

        if (newPassword.value !== confirmPassword.value) {
            message.textContent = "New passwords do not match";
            return;
        }

        const response = await fetch("/change-password", {
            method: "POST",
            headers: {
                "Authorization": "Bearer " + localStorage.getItem("token"),
                "Content-Type": "application/x-www-form-urlencoded"
            },
            body: new URLSearchParams({
                old_password: oldPassword.value,
                new_password: newPassword.value
            })
        });

        const data = await response.json();

        if (!response.ok) {
            message.textContent = data.detail || "Password update failed";
            return;
        }

        message.textContent = "Password updated successfully. Redirecting…";

        setTimeout(() => {
            window.location.href = "/print";
        }, 1200);
    });

});

