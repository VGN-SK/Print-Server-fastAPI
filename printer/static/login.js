document.getElementById("loginForm").addEventListener("submit", async function (e) {
    e.preventDefault(); // stop normal form submission

    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;

    const message = document.getElementById("message");
    message.textContent = "";

    let response; // ✅ DECLARE OUTSIDE

    try {
        response = await fetch("/login", {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded"
            },
            body: new URLSearchParams({
                username: username,
                password: password
            })
        });
    } catch (err) {
        message.textContent = "Server error";
        return;
    }

    let data;
    try {
        data = await response.json();
    } catch {
        message.textContent = "Invalid server response";
        return;
    }

    // ❌ LOGIN FAILED
    if (!response.ok) {
        if (Array.isArray(data.detail)) {
            message.textContent = data.detail
                .map(err => err.msg)
                .join(", ");
        } else {
            message.textContent = data.detail || "Invalid credentials";
        }
        return; // ⛔ STOP HERE
    }

    // ✅ LOGIN SUCCESS
    localStorage.setItem("token", data.token);
    localStorage.setItem("role", data.role);

    if (data.must_change_password) {
        window.location.href = "/change-password";
    } else {
        window.location.href = "/print";
    }
});
