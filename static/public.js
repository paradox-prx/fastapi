(() => {
  const root = document.getElementById("publicPage");
  if (!root) return;
  const slug = root.dataset.slug;
  const pageTitle = document.getElementById("pageTitle");
  const recipientInfo = document.getElementById("recipientInfo");
  const pageSummary = document.getElementById("pageSummary");
  const pageDetails = document.getElementById("pageDetails");
  const docList = document.getElementById("docList");
  const chatLog = document.getElementById("chatLog");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSend");
  const chatError = document.getElementById("chatError");

  let sessionId = null;

  function renderError(target, message) {
    target.textContent = message || "";
  }

  function appendChat(role, text, citations) {
    const line = document.createElement("div");
    line.className = `chat-line ${role}`;
    if (role === "assistant") {
      line.innerHTML = `<strong>${role}:</strong> ${renderMarkdown(text)}`;
      if (Array.isArray(citations) && citations.length) {
        const badgeWrap = document.createElement("div");
        badgeWrap.className = "citation-row";
        citations.forEach((c, idx) => {
          const badge = document.createElement("span");
          badge.className = "citation-badge";
          badge.textContent = `cite ${idx + 1}`;
          badge.title = JSON.stringify(c);
          badgeWrap.appendChild(badge);
        });
        line.appendChild(badgeWrap);
      }
    } else {
      line.textContent = `${role}: ${text}`;
    }
    chatLog.appendChild(line);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function renderDocs(docs) {
    docList.innerHTML = "";
    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = "list-row";
      const title = document.createElement("div");
      title.textContent = doc.display_title;
      const caption = document.createElement("div");
      caption.className = "muted";
      caption.textContent = doc.display_caption;
      const link = document.createElement("a");
      link.href = doc.download_url;
      link.textContent = "Download";
      link.target = "_blank";
      link.addEventListener("click", async () => {
        try {
          await fetch(`/v1/pages/${encodeURIComponent(slug)}/events/click`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ doc_id: doc.document_id, target: "download_pdf", url: doc.download_url }),
          });
        } catch (err) {
          // ignore analytics failures
        }
      });
      row.appendChild(title);
      row.appendChild(caption);
      row.appendChild(link);
      docList.appendChild(row);
    });
  }

  function renderMarkdown(text) {
    const escaped = String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    const withLinks = escaped.replace(
      /(https?:\/\/[^\s]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    const withBold = withLinks.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    const withItalic = withBold.replace(/\*(.*?)\*/g, "<em>$1</em>");
    const lines = withItalic.split("\n");
    return lines
      .map((line) => {
        if (line.startsWith("### ")) return `<h3>${line.slice(4)}</h3>`;
        if (line.startsWith("## ")) return `<h2>${line.slice(3)}</h2>`;
        if (line.startsWith("# ")) return `<h1>${line.slice(2)}</h1>`;
        return `<p>${line}</p>`;
      })
      .join("");
  }

  async function loadPage() {
    const res = await fetch(`/v1/pages/${encodeURIComponent(slug)}`);
    if (!res.ok) {
      pageTitle.textContent = "Page not found";
      return;
    }
    const data = await res.json();
    pageTitle.textContent = data.title;
    recipientInfo.textContent = `${data.recipient.name} • ${data.recipient.company_name}`;
    pageSummary.innerHTML = renderMarkdown(data.summary_markdown || "");
    pageDetails.innerHTML = renderMarkdown(data.details_markdown || "");
    renderDocs(data.documents || []);
  }

  async function logOpen() {
    try {
      await fetch(`/v1/pages/${encodeURIComponent(slug)}/events/open`, { method: "POST" });
    } catch (err) {
      // ignore analytics failures
    }
  }

  async function createSession() {
    const res = await fetch(`/v1/pages/${encodeURIComponent(slug)}/chat/sessions`, {
      method: "POST",
    });
    if (!res.ok) {
      renderError(chatError, "Unable to start chat session.");
      return;
    }
    const data = await res.json();
    sessionId = data.session_id;
  }

  async function sendMessage() {
    const message = chatInput.value.trim();
    if (!message || !sessionId) return;
    chatInput.value = "";
    renderError(chatError, "");
    appendChat("user", message);
    const res = await fetch(`/v1/chat/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      renderError(chatError, `Chat failed: ${res.status} ${JSON.stringify(err)}`);
      return;
    }
    const data = await res.json();
    appendChat("assistant", data.answer || "", data.citations || []);
  }

  chatSend.addEventListener("click", sendMessage);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage();
  });

  loadPage()
    .then(logOpen)
    .then(createSession)
    .catch((err) => renderError(chatError, String(err)));
})();
