let currentThreadId = localStorage.getItem("travel_thread_id") || null;
let latestAnswerMarkdown = "";
let isSearching = false;

function setPrompt(text) {
    document.getElementById("userInput").value = text;
}

function setLoading(isLoading) {
    const sendBtn = document.getElementById("sendBtn");
    const btnText = document.getElementById("btnText");
    const btnLoader = document.getElementById("btnLoader");

    sendBtn.disabled = isLoading;

    if (isLoading) {
        btnText.classList.add("hidden");
        btnLoader.classList.remove("hidden");
    } else {
        btnText.classList.remove("hidden");
        btnLoader.classList.add("hidden");
    }
}

function showError(message) {
    const errorBox = document.getElementById("errorBox");
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
}

function hideError() {
    const errorBox = document.getElementById("errorBox");
    errorBox.classList.add("hidden");
    errorBox.textContent = "";
}

function showResult(answer, threadId) {
    latestAnswerMarkdown = answer;

    const resultSection = document.getElementById("resultSection");
    const resultBox = document.getElementById("resultBox");
    const threadInfo = document.getElementById("threadInfo");

    if (typeof marked !== "undefined") {
        resultBox.innerHTML = marked.parse(answer);
    } else {
        resultBox.innerText = answer;
    }

    threadInfo.textContent = `Thread ID: ${threadId}`;

    resultSection.classList.remove("hidden");

    resultSection.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });
}

async function sendMessage() {
    hideError();

    const input = document.getElementById("userInput");
    const message = input.value.trim();

    if (!message) {
        showError("Please enter your travel request first.");
        return;
    }

    setLoading(true);

    try {
        const response = await fetch("/api/travel", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: message,
                thread_id: currentThreadId
            })
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || "Something went wrong.");
        }

        currentThreadId = data.thread_id;
        localStorage.setItem("travel_thread_id", currentThreadId);

        showResult(data.answer, data.thread_id);

    } catch (error) {
        showError(error.message);
    } finally {
        setLoading(false);
    }
}

function copyResult() {
    const resultBox = document.getElementById("resultBox");
    const text = resultBox.innerText;

    if (!text) {
        return;
    }

    navigator.clipboard.writeText(text)
        .then(() => {
            const copyBtn = document.querySelector(".copy-btn");
            const oldText = copyBtn.textContent;

            copyBtn.textContent = "Copied!";

            setTimeout(() => {
                copyBtn.textContent = oldText;
            }, 1400);
        })
        .catch(() => {
            showError("Could not copy result.");
        });
}

function downloadPDF() {
    const pdfContent = document.getElementById("pdfContent");

    if (!latestAnswerMarkdown || !pdfContent) {
        showError("No travel plan available to download.");
        return;
    }

    const downloadBtn = document.querySelector(".download-btn");
    const oldText = downloadBtn.textContent;

    downloadBtn.textContent = "Preparing PDF...";
    downloadBtn.disabled = true;

    const options = {
        margin: 0.5,
        filename: "ai-travel-plan.pdf",
        image: {
            type: "jpeg",
            quality: 0.98
        },
        html2canvas: {
            scale: 2,
            useCORS: true,
            backgroundColor: "#ffffff"
        },
        jsPDF: {
            unit: "in",
            format: "a4",
            orientation: "portrait"
        },
        pagebreak: {
            mode: ["avoid-all", "css", "legacy"]
        }
    };

    html2pdf()
        .set(options)
        .from(pdfContent)
        .save()
        .then(() => {
            downloadBtn.textContent = oldText;
            downloadBtn.disabled = false;
        })
        .catch(() => {
            downloadBtn.textContent = oldText;
            downloadBtn.disabled = false;
            showError("Could not download PDF.");
        });
}

function performWebSearch(searchQuery) {
    // Show that we're searching
    setLoading(true);
    showError(""); // Hide any previous errors

    return fetch("/api/search", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            query: searchQuery
        })
    })
    .then(async response => {
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || "Web search failed.");
        }
        const data = await response.json();
        return data.results;
    })
    .catch(error => {
        console.error("Search error:", error);
        return `<div class="error-box" style="margin-top: 20px; padding: 15px; background: rgba(239, 68, 68, 0.13); border: 1px solid rgba(239, 68, 68, 0.35); color: #fecaca; border-radius: 10px;">
                    <strong>Web Search Error:</strong> ${error.message}<br>
                    <small>Please try a different query or check your Tavily API key.</small>
                  </div>`;
    })
    .finally(() => {
        setLoading(false);
    });
}

function setPrompt(text) {
    document.getElementById("userInput").value = text;
}

function addSearchResultsToPage(resultsHtml) {
    const resultSection = document.getElementById("resultSection");
    const resultBox = document.getElementById("resultBox");

    // Create a search results section
    const searchSection = document.createElement("div");
    searchSection.className = "search-results-section";
    searchSection.innerHTML = `
        <div style="background: rgba(37, 99, 235, 0.1); border: 1px solid rgba(37, 99, 235, 0.3); border-radius: 12px; padding: 20px; margin: 20px 0;">
            <h3 style="margin-top: 0; color: #2563eb; margin-bottom: 15px;">Web Search Results</h3>
            ${resultsHtml}
        </div>
    `;

    // Insert before the result summary
    resultBox.insertBefore(searchSection, resultBox.firstChild);
}

// Quick prompt handlers
document.querySelectorAll(".quick-prompts button").forEach(button => {
    button.addEventListener("click", function() {
        const prompt = this.getAttribute("onclick");
        // Extract the prompt string from the onclick attribute
        const match = prompt.match(/'([^']+)'/);
        if (match) {
            setPrompt(match[1]);
            sendMessage();
        }
    });
});
</script>