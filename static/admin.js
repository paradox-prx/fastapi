(() => {
  const pageRoot = document.querySelector("[data-page]");
  if (!pageRoot) return;
  const page = pageRoot.dataset.page;

  const errorPanel = document.getElementById("errorPanel");

  function setError(message) {
    if (errorPanel) errorPanel.textContent = message || "";
  }

  function getAdminKey() {
    let key = localStorage.getItem("ADMIN_KEY");
    if (!key) {
      key = window.prompt("Enter ADMIN API key");
      if (key) localStorage.setItem("ADMIN_KEY", key);
    }
    return key;
  }

  async function adminFetch(path, options = {}) {
    const key = getAdminKey();
    if (!key) throw new Error("Missing admin key.");
    const headers = options.headers || {};
    headers["X-Admin-Key"] = key;
    const res = await fetch(path, { ...options, headers });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(`${res.status} ${JSON.stringify(body)}`);
    }
    return res.json().catch(() => ({}));
  }

  function renderList(container, items, formatter) {
    container.innerHTML = "";
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = formatter(item);
      container.appendChild(row);
    });
  }

  async function loadDocuments() {
    const list = document.getElementById("documentsList");
    const data = await adminFetch("/v1/admin/documents");
    renderList(list, data.documents || [], (d) => {
      return `<strong>${d.title}</strong><div class="muted">${d.id}</div>`;
    });
  }

  async function initDocuments() {
    const form = document.getElementById("docForm");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError("");
      const fd = new FormData(form);
      try {
        await adminFetch("/v1/admin/documents", { method: "POST", body: fd });
        form.reset();
        await loadDocuments();
      } catch (err) {
        setError(String(err));
      }
    });
    await loadDocuments();
  }

  async function loadStores() {
    return adminFetch("/v1/admin/file-stores");
  }

  async function loadDocs() {
    return adminFetch("/v1/admin/documents");
  }

  async function initFileStores() {
    const storeForm = document.getElementById("storeForm");
    const attachForm = document.getElementById("attachForm");
    const storeSelect = document.getElementById("storeSelect");
    const docsSelect = document.getElementById("docsSelect");
    const storesList = document.getElementById("storesList");

    async function refreshData() {
      const stores = await loadStores();
      const docs = await loadDocs();
      renderList(storesList, stores.file_stores || [], (s) => {
        return `<strong>${s.name}</strong><div class="muted">${s.id}</div>`;
      });
      storeSelect.innerHTML = "";
      (stores.file_stores || []).forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s.id;
        opt.textContent = s.name;
        storeSelect.appendChild(opt);
      });
      docsSelect.innerHTML = "";
      (docs.documents || []).forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.id;
        opt.textContent = d.title;
        docsSelect.appendChild(opt);
      });
    }

    storeForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError("");
      const payload = {
        name: storeForm.name.value.trim(),
        description: storeForm.description.value.trim(),
      };
      try {
        await adminFetch("/v1/admin/file-stores", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        storeForm.reset();
        await refreshData();
      } catch (err) {
        setError(String(err));
      }
    });

    attachForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError("");
      const storeId = storeSelect.value;
      const docIds = Array.from(docsSelect.selectedOptions).map((o) => o.value);
      if (!storeId) {
        setError("Select a file store.");
        return;
      }
      if (!docIds.length) {
        setError("Select at least one document to attach.");
        return;
      }
      const payload = {
        document_ids: docIds,
        create_ingestion_job: document.getElementById("createJob").checked,
      };
      try {
        const res = await adminFetch(`/v1/admin/file-stores/${storeId}/documents`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (res.job_id) {
          window.location.href = `/admin/jobs?job=${encodeURIComponent(res.job_id)}`;
        }
      } catch (err) {
        setError(String(err));
      }
    });

    await refreshData();
  }

  async function initPages() {
    const pageForm = document.getElementById("pageForm");
    const recipientSelect = document.getElementById("recipientSelect");
    const refreshRecipients = document.getElementById("refreshRecipients");
    const pageStoresSelect = document.getElementById("pageStoresSelect");
    const displayDocsList = document.getElementById("displayDocsList");
    const pagesList = document.getElementById("pagesList");
    const systemPrompt = document.getElementById("systemPrompt");

    async function refreshRecipientsList() {
      const data = await adminFetch("/v1/admin/recipients");
      recipientSelect.innerHTML = "";
      (data.recipients || []).forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.id;
        opt.textContent = `${r.name} (${r.company_name})`;
        recipientSelect.appendChild(opt);
      });
    }

    async function refreshStoresList() {
      const data = await adminFetch("/v1/admin/file-stores");
      pageStoresSelect.innerHTML = "";
      (data.file_stores || []).forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s.id;
        opt.textContent = s.name;
        pageStoresSelect.appendChild(opt);
      });
    }

    async function refreshDocsList() {
      const data = await adminFetch("/v1/admin/documents");
      displayDocsList.innerHTML = "";
      (data.documents || []).forEach((d) => {
        const row = document.createElement("div");
        row.className = "list-row";
        row.innerHTML = `
          <label>
            <input type="checkbox" data-doc-id="${d.id}" />
            ${d.title}
          </label>
          <input class="input" data-doc-title="${d.id}" placeholder="Display title" />
          <input class="input" data-doc-caption="${d.id}" placeholder="Display caption" />
          <input class="input" data-doc-order="${d.id}" placeholder="Sort order" />
        `;
        displayDocsList.appendChild(row);
      });
    }

    async function refreshPagesList() {
      const data = await adminFetch("/v1/admin/pages");
      renderList(pagesList, data.pages || [], (p) => {
        return `<strong>${p.title}</strong><div class="muted">/p/${p.slug}</div>`;
      });
    }

    refreshRecipients.addEventListener("click", async () => {
      try {
        await refreshRecipientsList();
      } catch (err) {
        setError(String(err));
      }
    });

    pageForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError("");
      try {
        let recipientId = recipientSelect.value;
        const newName = pageForm.recipient_name.value.trim();
        if (newName) {
          if (!pageForm.recipient_email.value.trim() || !pageForm.recipient_company.value.trim()) {
            setError("Recipient email and company name are required.");
            return;
          }
          const newRecipient = await adminFetch("/v1/admin/recipients", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: newName,
              email: pageForm.recipient_email.value.trim(),
              company_name: pageForm.recipient_company.value.trim(),
              persona: pageForm.recipient_persona.value.trim(),
            }),
          });
          recipientId = newRecipient.id;
        }
        if (!recipientId) {
          setError("Select an existing recipient or create a new one.");
          return;
        }

        const displayDocs = [];
        displayDocsList.querySelectorAll("input[type='checkbox']").forEach((cb) => {
          if (!cb.checked) return;
          const docId = cb.dataset.docId;
          const titleInput = displayDocsList.querySelector(`[data-doc-title='${docId}']`);
          const captionInput = displayDocsList.querySelector(`[data-doc-caption='${docId}']`);
          const orderInput = displayDocsList.querySelector(`[data-doc-order='${docId}']`);
          displayDocs.push({
            document_id: docId,
            display_title: titleInput.value || "Document",
            display_caption: captionInput.value || "",
            sort_order: parseInt(orderInput.value || "0", 10),
          });
        });

        const payload = {
          slug: pageForm.slug.value.trim() || null,
          title: pageForm.title.value.trim(),
          recipient_id: recipientId,
          template_key: pageForm.template_key.value,
          system_prompt_template: pageForm.system_prompt_template.value,
          summary_markdown: pageForm.summary_markdown.value,
          details_markdown: pageForm.details_markdown.value,
          file_store_ids: Array.from(pageStoresSelect.selectedOptions).map((o) => o.value),
          display_documents: displayDocs,
        };

        const res = await adminFetch("/v1/admin/pages", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        pageForm.reset();
        await refreshPagesList();
        if (res.public_url) {
          window.alert(`Page created: ${res.public_url}`);
        }
      } catch (err) {
        setError(String(err));
      }
    });

    await refreshRecipientsList();
    await refreshStoresList();
    await refreshDocsList();
    await refreshPagesList();

    pageForm.querySelectorAll("[data-placeholder]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!systemPrompt) return;
        const token = btn.dataset.placeholder || "";
        const start = systemPrompt.selectionStart || 0;
        const end = systemPrompt.selectionEnd || 0;
        const value = systemPrompt.value || "";
        systemPrompt.value = value.slice(0, start) + token + value.slice(end);
        systemPrompt.focus();
        systemPrompt.selectionStart = systemPrompt.selectionEnd = start + token.length;
      });
    });
  }

  async function initJobs() {
    const jobIdInput = document.getElementById("jobIdInput");
    const loadJobBtn = document.getElementById("loadJob");
    const runJobBtn = document.getElementById("runJob");
    const jobStatus = document.getElementById("jobStatus");
    const jobEvents = document.getElementById("jobEvents");
    const jobsList = document.getElementById("jobsList");

    let currentJobId = null;
    let lastEventId = 0;
    let pollTimer = null;
    let eventsTimer = null;

    async function refreshJobsList() {
      const data = await adminFetch("/v1/admin/ingestion-jobs");
      renderList(jobsList, data.jobs || [], (j) => {
        return `<strong>${j.id}</strong><div class="muted">${j.status} (${j.progress}/${j.total})</div>`;
      });
    }

    async function loadJob(jobId) {
      currentJobId = jobId;
      lastEventId = 0;
      jobEvents.innerHTML = "";
      await refreshJobStatus();
      await refreshJobEvents();
      if (pollTimer) clearInterval(pollTimer);
      if (eventsTimer) clearInterval(eventsTimer);
      pollTimer = setInterval(refreshJobStatus, 2000);
      eventsTimer = setInterval(refreshJobEvents, 2000);
    }

    async function refreshJobStatus() {
      if (!currentJobId) return;
      const data = await adminFetch(`/v1/admin/ingestion-jobs/${currentJobId}`);
      jobStatus.innerHTML = `
        <div><strong>Status:</strong> ${data.status}</div>
        <div><strong>Progress:</strong> ${data.progress}/${data.total}</div>
        <div><strong>Error:</strong> ${data.error || ""}</div>
      `;
    }

    async function refreshJobEvents() {
      if (!currentJobId) return;
      const data = await adminFetch(
        `/v1/admin/ingestion-jobs/${currentJobId}/events?after_id=${lastEventId}`
      );
      (data.events || []).forEach((ev) => {
        lastEventId = Math.max(lastEventId, ev.id);
        const row = document.createElement("div");
        row.className = "list-row";
        row.innerHTML = `<strong>${ev.message}</strong><div class="muted">${ev.ts}</div>`;
        jobEvents.appendChild(row);
      });
    }

    loadJobBtn.addEventListener("click", async () => {
      const id = jobIdInput.value.trim();
      if (!id) return;
      try {
        await loadJob(id);
      } catch (err) {
        setError(String(err));
      }
    });

    runJobBtn.addEventListener("click", async () => {
      if (!currentJobId) return;
      try {
        await adminFetch(`/v1/admin/ingestion-jobs/${currentJobId}/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
      } catch (err) {
        setError(String(err));
      }
    });

    const params = new URLSearchParams(window.location.search);
    const jobParam = params.get("job");
    if (jobParam) {
      jobIdInput.value = jobParam;
      await loadJob(jobParam);
    }
    await refreshJobsList();
  }

  async function initAnalytics() {
    const daysSelect = document.getElementById("daysSelect");
    const refreshBtn = document.getElementById("refreshAnalytics");
    const topPages = document.getElementById("topPages");
    const topChatPages = document.getElementById("topChatPages");
    const statEvents = document.getElementById("statEvents");
    const statSessions = document.getElementById("statSessions");
    const statMessages = document.getElementById("statMessages");
    let chartDay = null;
    let chartType = null;
    let chartSessions = null;
    let chartMessages = null;

    function destroyCharts() {
      if (chartDay) {
        chartDay.destroy();
        chartDay = null;
      }
      if (chartType) {
        chartType.destroy();
        chartType = null;
      }
      if (chartSessions) {
        chartSessions.destroy();
        chartSessions = null;
      }
      if (chartMessages) {
        chartMessages.destroy();
        chartMessages = null;
      }
    }

    function renderTopPages(rows) {
      topPages.innerHTML = "";
      rows.forEach((row) => {
        const item = document.createElement("div");
        item.className = "list-row";
        item.innerHTML = `<strong>${row.title}</strong><div class="muted">/p/${row.slug} • ${row.count} events</div>`;
        topPages.appendChild(item);
      });
    }

    function renderTopChatPages(rows) {
      topChatPages.innerHTML = "";
      rows.forEach((row) => {
        const item = document.createElement("div");
        item.className = "list-row";
        item.innerHTML = `<strong>${row.title}</strong><div class="muted">/p/${row.slug} • ${row.count} sessions</div>`;
        topChatPages.appendChild(item);
      });
    }

    async function refresh() {
      setError("");
      destroyCharts();
      const days = parseInt(daysSelect.value, 10);
      const data = await adminFetch(`/v1/admin/analytics/summary?days=${days}`);

      const dayLabels = (data.by_day || []).map((r) => new Date(r.day).toLocaleDateString());
      const dayCounts = (data.by_day || []).map((r) => r.count);
      const typeLabels = (data.by_type || []).map((r) => r.event_type);
      const typeCounts = (data.by_type || []).map((r) => r.count);
      const sessionLabels = (data.chat_sessions_by_day || []).map((r) => new Date(r.day).toLocaleDateString());
      const sessionCounts = (data.chat_sessions_by_day || []).map((r) => r.count);
      const messageLabels = (data.chat_messages_by_day || []).map((r) => new Date(r.day).toLocaleDateString());
      const messageCounts = (data.chat_messages_by_day || []).map((r) => r.count);
      const totals = data.totals || {};

      if (statEvents) statEvents.textContent = totals.events || 0;
      if (statSessions) statSessions.textContent = totals.sessions || 0;
      if (statMessages) statMessages.textContent = totals.messages || 0;

      const dayCtx = document.getElementById("eventsByDay");
      const typeCtx = document.getElementById("eventsByType");
      const sessionsCtx = document.getElementById("chatSessionsByDay");
      const messagesCtx = document.getElementById("chatMessagesByDay");
      if (window.Chart && dayCtx) {
        chartDay = new Chart(dayCtx, {
          type: "line",
          data: {
            labels: dayLabels,
            datasets: [
              {
                label: "Events",
                data: dayCounts,
                borderColor: "#b5762b",
                backgroundColor: "rgba(181, 118, 43, 0.2)",
                tension: 0.2,
              },
            ],
          },
          options: { responsive: true },
        });
      }
      if (window.Chart && typeCtx) {
        chartType = new Chart(typeCtx, {
          type: "bar",
          data: {
            labels: typeLabels,
            datasets: [
              {
                label: "Count",
                data: typeCounts,
                backgroundColor: "#8b7d6b",
              },
            ],
          },
          options: { responsive: true },
        });
      }
      if (window.Chart && sessionsCtx) {
        chartSessions = new Chart(sessionsCtx, {
          type: "line",
          data: {
            labels: sessionLabels,
            datasets: [
              {
                label: "Chat sessions",
                data: sessionCounts,
                borderColor: "#5c3b1e",
                backgroundColor: "rgba(92, 59, 30, 0.2)",
                tension: 0.2,
              },
            ],
          },
          options: { responsive: true },
        });
      }
      if (window.Chart && messagesCtx) {
        chartMessages = new Chart(messagesCtx, {
          type: "line",
          data: {
            labels: messageLabels,
            datasets: [
              {
                label: "Chat messages",
                data: messageCounts,
                borderColor: "#1f5b3a",
                backgroundColor: "rgba(31, 91, 58, 0.2)",
                tension: 0.2,
              },
            ],
          },
          options: { responsive: true },
        });
      }
      renderTopPages(data.by_page || []);
      renderTopChatPages(data.top_chat_pages || []);
    }

    refreshBtn.addEventListener("click", () => {
      refresh().catch((err) => setError(String(err)));
    });
    daysSelect.addEventListener("change", () => {
      refresh().catch((err) => setError(String(err)));
    });
    await refresh();
  }

  async function init() {
    try {
      if (page === "documents") await initDocuments();
      if (page === "file-stores") await initFileStores();
      if (page === "pages") await initPages();
      if (page === "jobs") await initJobs();
      if (page === "analytics") await initAnalytics();
    } catch (err) {
      setError(String(err));
    }
  }

  init();
})();
