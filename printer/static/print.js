const token = localStorage.getItem("token");
const role = localStorage.getItem("role");

if (!token) {
    // Not logged in
    window.location.href = "/login";
}

const printBtn = document.getElementById("printBtn");
const cancelBtn = document.getElementById("cancelBtn");
const statusText = document.getElementById("status");
const fileInput = document.getElementById("fileInput");
const quotaText = document.getElementById("quotaText");
const copiesInput = document.getElementById("copies");
const colorModeSelect = document.getElementById("colorMode");
const sidesSelect = document.getElementById("sides");
const printerStatusText = document.getElementById("printerStatus");


if (role !== "admin") {
    cancelBtn.style.display = "none";
}

let currentJobId = null;
let pollInterval = null;

const jobHistoryTitle = document.getElementById("jobHistoryTitle");

jobHistoryTitle.textContent =
    role === "admin" ? "All Jobs" : "My Jobs";

const logoutBtn = document.getElementById("logoutBtn");

logoutBtn.addEventListener("click", () => {
    // Remove auth data
    localStorage.removeItem("token");
    localStorage.removeItem("role");

    // Stop polling if running
    if (pollInterval) {
        clearInterval(pollInterval);
    }

    // Redirect to login
    window.location.href = "/login";
});


printBtn.addEventListener("click", async () => {
    const file = fileInput.files[0];

    if (!file) {
        statusText.textContent = "Please select a file";
        return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("copies", copiesInput.value);
    formData.append("color_mode", colorModeSelect.value);
    formData.append("sides", sidesSelect.value);


    statusText.textContent = "Submitting print job...";

    try {
        const response = await fetch("/print", {
            method: "POST",
            headers: {
                "Authorization": "Bearer " + token
            },
            body: formData
        });

        const data = await response.json();

        if (!response.ok) {
            statusText.textContent = data.detail || "Print failed";
            return;
        }

        currentJobId = data.job_id;
        statusText.textContent = "Job submitted. Job ID: " + currentJobId;
        loadQuota();

        if (role === "admin") {
            cancelBtn.style.display = "inline";
        }

        startPolling();

    } catch (err) {
        statusText.textContent = "Server error";
    }
});

async function loadQuota() {
    try {
        // Admins are not limited
        if (role === "admin") {
            quotaText.textContent = "Quota: Unlimited (Admin)";
            return;
        }

        const response = await fetch("/quota", {
            headers: {
                "Authorization": "Bearer " + token
            }
        });

        const data = await response.json();

        if (!response.ok) {
            quotaText.textContent = "Quota information unavailable";
            return;
        }

        quotaText.textContent = `Quota: ${data.used} / ${data.limit} papers used (this month)`;

    } catch (err) {
        quotaText.textContent = "Quota information unavailable";
    }
}

function startPolling() {
    pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/job/${currentJobId}`, {
                headers: {
                    "Authorization": "Bearer " + token
                }
            });

            const data = await response.json();

            if (!response.ok) {
                statusText.textContent = data.detail || "Error checking status";
                return;
            }

            statusText.textContent = "Status: " + data.status;

            if (
                data.status === "completed" ||
                data.status === "failed" ||
                data.status === "cancelled"
            ) {
                clearInterval(pollInterval);
                cancelBtn.style.display = "none";
                loadQuota();
            }

        } catch (err) {
            statusText.textContent = "Status check failed";
        }
    }, 2000); // poll every 2 seconds
}

cancelBtn.addEventListener("click", async () => {
    if (role !== "admin") return;
    if (!currentJobId) return;

    statusText.textContent = "Cancelling job...";

    try {
        const response = await fetch(`/admin/job/${currentJobId}/cancel`, {
            method: "POST",
            headers: {
                "Authorization": "Bearer " + token
            }
        });

        const data = await response.json();

        if (!response.ok) {
            statusText.textContent = data.detail || "Cancel failed";
            return;
        }

        statusText.textContent = data.message;

    } catch (err) {
        statusText.textContent = "Cancel request failed";
    }
});

async function loadJobHistory() {
    console.log("loadJobHistory() called");

    try {
        const url = role === "admin" ? "/admin/jobs" : "/jobs";

        const response = await fetch(url, {
            headers: {
                "Authorization": "Bearer " + token
            }
        });

        console.log("Job history response status:", response.status);

        const data = await response.json();
        console.log("Job history data:", data);

        if (!response.ok) {
            console.error("Failed to load jobs");
            return;
        }

        jobTableBody.innerHTML = "";

        data.forEach(job => {
            const row = document.createElement("tr");

            row.innerHTML = `
                <td>${job.job_id}</td>
                <td class="filename" title="${job.filename}">${job.filename}</td>
                <td>${job.papers}</td>
                <td class="status-${job.status}">${job.status}</td>
                <td class="datetime" title="${job.created_at}">${job.created_at}</td>

            `;


            jobTableBody.appendChild(row);
        });

    } catch (err) {
        console.error("Error loading job history:", err);
    }
}

async function loadPrinterCapabilities() {
    try {
        const response = await fetch("/printer/capabilities", {
            headers: {
                "Authorization": "Bearer " + token
            }
        });

        const caps = await response.json();
        if (!response.ok) return;

        // Color
        if (!caps.color) {
            colorModeSelect.value = "bw";
            colorModeSelect.disabled = true;
        }

        // Duplex
        if (!caps.duplex) {
            sidesSelect.value = "one-sided";
            sidesSelect.disabled = true;
        }

        // Copies
        copiesInput.max = caps.max_copies;

    } catch (err) {
        console.warn("Could not load printer capabilities");
    }
}

async function loadPrinterStatus() {
    try {
        const response = await fetch("/printer/status", {
            headers: {
                "Authorization": "Bearer " + token
            }
        });

        const data = await response.json();
        if (!response.ok) return;

        printerStatusText.textContent =
            "Printer status: " + data.status.toUpperCase();

        printerStatusText.className = "printer-" + data.status;

        // Disable printing if offline
        printBtn.disabled = data.status === "offline";

    } catch (err) {
        printerStatusText.textContent = "Printer status: unknown";
    }
}

// Load job history initially
loadJobHistory();
loadPrinterStatus();
// Refresh job history every 5 seconds
setInterval(loadJobHistory, 5000);
setInterval(loadPrinterStatus, 5000);
loadQuota();
loadPrinterCapabilities();

