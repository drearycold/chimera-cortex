document.addEventListener("DOMContentLoaded", () => {
    // ==========================================================================
    // DOM Elements - Chat Portal
    // ==========================================================================
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatMessages = document.getElementById("chat-messages");
    const servantList = document.getElementById("servant-list");
    const servantSearch = document.getElementById("servant-search");
    const clearServantSearch = document.getElementById("clear-servant-search");
    const servantCount = document.getElementById("servant-count");
    const corpusListMeta = document.getElementById("corpus-list-meta");
    const cacheStatus = document.getElementById("cache-status");
    const responseTime = document.getElementById("response-time");
    const retrievedContexts = document.getElementById("retrieved-contexts");
    const chatKbSelect = document.getElementById("chat-kb-select");
    
    // Modal
    const documentModal = document.getElementById("document-modal");
    const modalTitle = document.getElementById("modal-title");
    const modalContent = document.getElementById("modal-content");
    const closeModal = document.getElementById("close-modal");
    
    // ==========================================================================
    // DOM Elements - Navigation Tabs
    // ==========================================================================
    const tabChat = document.getElementById("tab-chat");
    const tabManage = document.getElementById("tab-manage");
    const tabBenchmark = document.getElementById("tab-benchmark");
    const chatLayout = document.getElementById("chat-layout");
    const manageLayout = document.getElementById("manage-layout");
    const benchmarkLayout = document.getElementById("benchmark-layout");

    // ==========================================================================
    // DOM Elements - RAG Audit Dashboard
    // ==========================================================================
    const runsList = document.getElementById("runs-list");
    const runCount = document.getElementById("run-count");
    const judgeSelect = document.getElementById("judge-select");
    const datasetSelect = document.getElementById("dataset-select");
    const reuseCacheCheck = document.getElementById("reuse-cache-check");
    const btnRunBenchmark = document.getElementById("btn-run-benchmark");
    const btnStopBenchmark = document.getElementById("btn-stop-benchmark");
    
    // Live Progress
    const liveProgressContainer = document.getElementById("live-progress-container");
    const liveProgressStatus = document.getElementById("live-progress-status");
    const liveProgressFill = document.getElementById("live-progress-fill");

    // Detail Panel
    const benchmarkWelcome = document.getElementById("benchmark-welcome");
    const benchmarkDetail = document.getElementById("benchmark-detail");
    const runTitle = document.getElementById("run-title");
    const runDatasetText = document.getElementById("run-dataset-text");
    const runJudgeText = document.getElementById("run-judge-text");
    const btnDeleteRun = document.getElementById("btn-delete-run");

    // KPIs
    const kpiCorrectness = document.getElementById("kpi-correctness");
    const kpiFaithfulness = document.getElementById("kpi-faithfulness");
    const kpiRetrieval = document.getElementById("kpi-retrieval");
    const kpiPassrate = document.getElementById("kpi-passrate");
    const kpiLatency = document.getElementById("kpi-latency");

    // Latency
    const detailTotalLatencyText = document.getElementById("detail-total-latency-text");
    const detailLatencyBar = document.getElementById("detail-latency-bar");

    // Filters & Sorting
    const qSearch = document.getElementById("q-search");
    const qSort = document.getElementById("q-sort");
    const auditQuestionsContainer = document.getElementById("audit-questions-container");

    // ==========================================================================
    // Global State
    // ==========================================================================
    let documentsData = [];
    let activeTab = "chat";
    let knowledgeBases = [];
    let selectedKbSlug = "fgo-lore";
    let managedKb = null;
    let managedSources = [];
    let managedDocuments = [];
    let managedLogs = [];
    let managedCache = null;
    let globalCacheStats = null;
    let cacheOffset = 0;
    const cacheLimit = 50;
    let cacheQuery = "";
    let cacheSearchTimer = null;
    let cacheRequestId = 0;
    let cacheDetailTrigger = null;
    let activeManageView = "sources";
    let managementMode = null;
    let historicalRuns = [];
    let selectedRunId = null;
    let activeRunDetail = null;
    let isPolling = false;
    let pollInterval = null;

    // Filters state
    let difficultyFilter = "all";
    let cacheFilter = "all";
    let fidelityFilter = "all";
    let textQuery = "";
    let sortOption = "id";

    // ==========================================================================
    // Initialization
    // ==========================================================================
    fetchSystemStatus();
    fetchKnowledgeBases();
    
    // Periodic status check (every 15 seconds)
    setInterval(fetchSystemStatus, 15000);
    
    // Start status polling for benchmark execution
    startProgressPolling();

    // ==========================================================================
    // Tab Navigation Event Handlers
    // ==========================================================================
    tabChat.addEventListener("click", () => switchTab("chat"));
    tabManage.addEventListener("click", () => switchTab("manage"));
    tabBenchmark.addEventListener("click", () => switchTab("benchmark"));

    function switchTab(tabName) {
        if (activeTab === tabName) return;
        activeTab = tabName;

        tabChat.classList.toggle("active", tabName === "chat");
        tabManage.classList.toggle("active", tabName === "manage");
        tabBenchmark.classList.toggle("active", tabName === "benchmark");
        chatLayout.style.display = tabName === "chat" ? "flex" : "none";
        manageLayout.style.display = tabName === "manage" ? "grid" : "none";
        benchmarkLayout.style.display = tabName === "benchmark" ? "flex" : "none";

        if (tabName === "manage") refreshManagement();
        if (tabName === "benchmark") fetchRuns();
    }

    // ==========================================================================
    // Suggested Queries & Meta Links Event Handler
    // ==========================================================================
    document.addEventListener("click", (e) => {
        if (e.target.classList.contains("suggested-btn")) {
            chatInput.value = e.target.textContent;
            chatInput.focus();
        }
        
        if (e.target.classList.contains("meta-source")) {
            openSourceDocument(e.target.dataset.filename);
        }
    });

    // ==========================================================================
    // Modal Event Handlers
    // ==========================================================================
    closeModal.addEventListener("click", () => {
        documentModal.style.display = "none";
    });
    
    window.addEventListener("click", (e) => {
        if (e.target === documentModal) {
            documentModal.style.display = "none";
        }
    });
    
    // Search corpus filter
    servantSearch.addEventListener("input", (e) => {
        const query = e.target.value.toLowerCase().trim();
        clearServantSearch.hidden = query.length === 0;
        filterDocuments(query);
    });

    clearServantSearch.addEventListener("click", () => {
        servantSearch.value = "";
        clearServantSearch.hidden = true;
        filterDocuments("");
        servantSearch.focus();
    });
    
    // ==========================================================================
    // Chat Submit Handler
    // ==========================================================================
    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const query = chatInput.value.trim();
        if (!query) return;
        
        chatInput.value = "";
        appendMessage("user", query);
        const typingId = appendTypingIndicator();
        const startTime = performance.now();
        
        try {
            const res = await fetch(`/api/kb/${selectedKbSlug}/chat`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ query: query })
            });
            
            removeTypingIndicator(typingId);
            
            if (!res.ok) throw new Error(`Server returned status ${res.status}`);
            
            const data = await res.json();
            appendMessage("assistant", data.answer);
            
            const endTime = performance.now();
            const elapsed = Math.round(endTime - startTime);
            responseTime.textContent = `${elapsed} ms`;
            
            if (data.cache_hit) {
                cacheStatus.textContent = "HIT";
                cacheStatus.className = "stat-value success";
            } else {
                cacheStatus.textContent = "MISS";
                cacheStatus.className = "stat-value miss";
            }
            
            renderContexts(data.contexts);
            
        } catch (err) {
            removeTypingIndicator(typingId);
            appendMessage("assistant", `⚠️ Error: Could not connect to RAG server. Details: ${err.message}`);
            console.error("Chat error:", err);
        }
    });
    
    // Fetch connection health status
    async function fetchSystemStatus() {
        try {
            const res = await fetch("/api/status");
            if (!res.ok) throw new Error("status not ok");
            const data = await res.json();
            
            updateStatusDot("status-mysql", data.mysql);
            updateStatusDot("status-minio", data.minio);
            updateStatusDot("status-redis", data.redis);
            updateStatusDot("status-infinity", data.infinity);
            updateStatusDot("status-ollama", data.ollama);
            updateStatusDot("status-reranker", data.reranker);
        } catch (err) {
            console.error("Error fetching system status:", err);
        }
    }
    
    function updateStatusDot(elementId, isOk) {
        const el = document.getElementById(elementId);
        if (!el) return;
        const dot = el.querySelector(".dot");
        if (isOk) {
            dot.className = "dot green-glow";
        } else {
            dot.className = "dot red-glow";
        }
    }
    
    // Fetch documents in MySQL corpus
    async function fetchDocuments() {
        try {
            const res = await fetch(`/api/kb/${selectedKbSlug}/documents`);
            if (!res.ok) throw new Error("documents list not ok");
            const data = await res.json();
            documentsData = data.documents;
            servantCount.textContent = `${documentsData.length} docs`;
            renderDocumentList(documentsData);
        } catch (err) {
            servantList.innerHTML = `<li class="loading-item" style="color: var(--danger-color)">Failed to load documents.</li>`;
            console.error("Error fetching documents:", err);
        }
    }
    
    function renderDocumentList(list) {
        const total = documentsData.length;
        corpusListMeta.textContent = list.length === total
            ? `${total} documents`
            : `${list.length} of ${total} documents`;
        if (list.length === 0) {
            servantList.innerHTML = `<li class="loading-item">No matching documents.</li>`;
            return;
        }
        
        servantList.innerHTML = "";
        list.forEach(doc => {
            const documentTitle = doc.title || doc.filename || "Untitled document";
            const li = document.createElement("li");
            li.className = "corpus-row";

            const button = document.createElement("button");
            button.type = "button";
            button.className = "servant-item";
            button.title = `Open ${documentTitle}`;

            const title = document.createElement("span");
            title.className = "corpus-item-title";
            title.textContent = documentTitle;

            const meta = document.createElement("span");
            meta.className = "corpus-item-meta";
            const chunks = `${doc.chunk_count || 0} chunk${doc.chunk_count === 1 ? "" : "s"}`;
            meta.textContent = doc.source_name ? `${doc.source_name} · ${chunks}` : chunks;

            const icon = document.createElement("i");
            icon.dataset.lucide = "file-text";
            icon.setAttribute("aria-hidden", "true");

            const copy = document.createElement("span");
            copy.className = "corpus-item-copy";
            copy.append(title, meta);
            button.append(icon, copy);
            button.addEventListener("click", () => openSourceDocument(doc.filename));
            li.appendChild(button);
            servantList.appendChild(li);
        });
        window.lucide?.createIcons();
    }
    
    function filterDocuments(query) {
        const filtered = documentsData.filter(d =>
            [d.title, d.filename, d.source_name]
                .filter(Boolean)
                .some(value => String(value).toLowerCase().includes(query))
        );
        renderDocumentList(filtered);
    }
    
    // Messages Formatter
    function appendMessage(sender, text) {
        const welcomeCard = chatMessages.querySelector(".system-welcome-card");
        if (welcomeCard) welcomeCard.remove();
        
        const messageDiv = document.createElement("div");
        messageDiv.className = `message ${sender}`;
        
        const label = document.createElement("span");
        label.className = "message-label";
        label.textContent = sender === "user" ? "User Query" : "Cortex AI Response";
        
        const bubble = document.createElement("div");
        bubble.className = "message-bubble";
        bubble.innerHTML = formatMarkdown(text);
        
        messageDiv.appendChild(label);
        messageDiv.appendChild(bubble);
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    
    function formatMarkdown(text) {
        let html = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
            
        html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
        html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        html = html.replace(/\n/g, "<br>");
        return html;
    }
    
    function appendTypingIndicator() {
        const welcomeCard = chatMessages.querySelector(".system-welcome-card");
        if (welcomeCard) welcomeCard.remove();
        
        const id = "typing-" + Date.now();
        const messageDiv = document.createElement("div");
        messageDiv.className = "message assistant";
        messageDiv.id = id;
        
        const label = document.createElement("span");
        label.className = "message-label";
        label.textContent = "Cortex System Processing...";
        
        const bubble = document.createElement("div");
        bubble.className = "message-bubble";
        
        const indicator = document.createElement("div");
        indicator.className = "typing-indicator";
        indicator.innerHTML = "<span></span><span></span><span></span>";
        
        bubble.appendChild(indicator);
        messageDiv.appendChild(label);
        messageDiv.appendChild(bubble);
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        return id;
    }
    
    function removeTypingIndicator(id) {
        const el = document.getElementById(id);
        if (el) el.remove();
    }
    
    function renderContexts(contexts) {
        if (!contexts || contexts.length === 0) {
            retrievedContexts.innerHTML = `<div class="no-context">No context matched. (Ollama answered from base knowledge).</div>`;
            return;
        }
        
        retrievedContexts.innerHTML = "";
        contexts.forEach(ctx => {
            const card = document.createElement("div");
            card.className = "context-card";
            
            const meta = document.createElement("div");
            meta.className = "context-meta";
            
            const source = document.createElement("span");
            source.className = "meta-source";
            source.textContent = ctx.filename;
            source.dataset.filename = ctx.filename;
            source.title = "Click to inspect full document in MinIO";
            
            const score = document.createElement("span");
            score.className = "meta-score";
            score.textContent = `Score: ${ctx.distance.toFixed(4)}`;
            
            meta.appendChild(source);
            meta.appendChild(score);
            
            const textDiv = document.createElement("div");
            textDiv.className = "context-text";
            textDiv.textContent = ctx.content;
            
            card.appendChild(meta);
            card.appendChild(textDiv);
            retrievedContexts.appendChild(card);
        });
    }
    
    async function openSourceDocument(filename) {
        modalTitle.textContent = `Source Manuscript: ${filename}`;
        modalContent.textContent = "Loading original file from MinIO Object Storage...";
        documentModal.style.display = "flex";
        
        try {
            const document = documentsData.find(item => item.filename === filename);
            if (!document) throw new Error("Document is not present in the selected knowledge base");
            const res = await fetch(`/api/kb/${selectedKbSlug}/documents/${document.id}/content`);
            if (!res.ok) throw new Error(`Status ${res.status}`);
            const data = await res.json();
            modalContent.textContent = data.content;
        } catch (err) {
            modalContent.textContent = `⚠️ Error fetching document from MinIO: ${err.message}`;
            console.error("MinIO fetch error:", err);
        }
    }

    // ==========================================================================
    // Knowledge Base Management
    // ==========================================================================
    const kbList = document.getElementById("kb-list");
    const kbSearch = document.getElementById("kb-search");
    const managementModal = document.getElementById("management-modal");
    const managementForm = document.getElementById("management-form");
    const managementFormBody = document.getElementById("management-form-body");

    async function apiRequest(url, options = {}) {
        const response = await fetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
        return data;
    }

    function refreshIcons() {
        if (window.lucide) window.lucide.createIcons();
    }

    async function fetchKnowledgeBases(preferredSlug = selectedKbSlug) {
        try {
            const data = await apiRequest("/api/kb");
            knowledgeBases = data.knowledge_bases || [];
            if (!knowledgeBases.some(kb => kb.slug === preferredSlug)) {
                preferredSlug = knowledgeBases[0]?.slug || "";
            }
            selectedKbSlug = preferredSlug;
            renderKnowledgeBaseSelectors();
            await fetchDocuments();
        } catch (error) {
            kbList.innerHTML = `<p class="loading-item">${escapeHtml(error.message)}</p>`;
        }
    }

    function renderKnowledgeBaseSelectors() {
        const options = knowledgeBases.map(kb =>
            `<option value="${escapeHtml(kb.slug)}">${escapeHtml(kb.name)}</option>`
        ).join("");
        chatKbSelect.innerHTML = options;
        chatKbSelect.value = selectedKbSlug;
        document.getElementById("compare-left").innerHTML = options;
        document.getElementById("compare-right").innerHTML = options;
        document.getElementById("compare-left").value = selectedKbSlug;
        const comparison = knowledgeBases.find(kb => kb.slug !== selectedKbSlug);
        document.getElementById("compare-right").value = comparison?.slug || selectedKbSlug;
        renderKnowledgeBaseList();
        renderOverview();
        renderComparison();
    }

    function renderKnowledgeBaseList() {
        const query = kbSearch.value.trim().toLowerCase();
        const filtered = knowledgeBases.filter(kb =>
            `${kb.name} ${kb.slug}`.toLowerCase().includes(query)
        );
        kbList.innerHTML = filtered.length ? filtered.map(kb => `
            <button class="kb-list-item ${kb.slug === managedKb?.slug ? "active" : ""}" data-kb-slug="${escapeHtml(kb.slug)}">
                <strong>${escapeHtml(kb.name)}</strong><small>${kb.stats?.document_count || 0} docs</small>
                <span>${escapeHtml(kb.slug)}</span><small>${kb.enabled ? "Active" : "Disabled"}</small>
            </button>
        `).join("") : `<p class="loading-item">No matching knowledge bases.</p>`;
    }

    function renderOverview() {
        const totals = knowledgeBases.reduce((sum, kb) => ({
            sources: sum.sources + (kb.stats?.source_count || 0),
            documents: sum.documents + (kb.stats?.document_count || 0),
            chunks: sum.chunks + (kb.stats?.chunk_count || 0),
        }), { sources: 0, documents: 0, chunks: 0 });
        document.getElementById("overview-kbs").textContent = knowledgeBases.length;
        document.getElementById("overview-sources").textContent = totals.sources;
        document.getElementById("overview-documents").textContent = totals.documents;
        document.getElementById("overview-chunks").textContent = totals.chunks.toLocaleString();
    }

    function renderComparison() {
        const left = knowledgeBases.find(kb => kb.slug === document.getElementById("compare-left").value);
        const right = knowledgeBases.find(kb => kb.slug === document.getElementById("compare-right").value);
        const summary = document.getElementById("compare-summary");
        if (!left || !right) {
            summary.textContent = "Select two knowledge bases";
            return;
        }
        summary.textContent = `${left.stats.document_count} vs ${right.stats.document_count} docs; ${left.stats.chunk_count.toLocaleString()} vs ${right.stats.chunk_count.toLocaleString()} chunks`;
    }

    async function refreshManagement() {
        await fetchKnowledgeBases(managedKb?.slug || selectedKbSlug);
        if (selectedKbSlug) await selectManagedKnowledgeBase(selectedKbSlug);
    }

    async function selectManagedKnowledgeBase(slug) {
        try {
            const [kb, sourcesData, documentsDataResult, logsData] = await Promise.all([
                apiRequest(`/api/kb/${slug}`),
                apiRequest(`/api/kb/${slug}/sources`),
                apiRequest(`/api/kb/${slug}/documents`),
                apiRequest(`/api/kb/${slug}/ingestion-logs`),
            ]);
            managedKb = kb;
            selectedKbSlug = slug;
            managedSources = sourcesData.sources || [];
            managedDocuments = documentsDataResult.documents || [];
            managedLogs = logsData.logs || [];
            managedCache = null;
            cacheOffset = 0;
            cacheQuery = "";
            document.getElementById("cache-search").value = "";
            document.getElementById("clear-cache-search").hidden = true;
            chatKbSelect.value = slug;
            document.getElementById("manage-empty").style.display = "none";
            document.getElementById("manage-detail").style.display = "block";
            renderKnowledgeBaseList();
            renderManagedKnowledgeBase();
            if (activeManageView === "cache") await fetchManagedCache();
        } catch (error) {
            alert(`Could not load knowledge base: ${error.message}`);
        }
    }

    function renderManagedKnowledgeBase() {
        document.getElementById("manage-kb-name").textContent = managedKb.name;
        document.getElementById("manage-kb-description").textContent = managedKb.description || managedKb.slug;
        const status = document.getElementById("manage-kb-status");
        status.textContent = managedKb.enabled ? "ACTIVE" : "DISABLED";
        status.classList.toggle("disabled", !managedKb.enabled);
        document.getElementById("source-count-label").textContent = `${managedSources.length} configured`;
        document.getElementById("document-count-label").textContent = `${managedDocuments.length} indexed`;
        document.getElementById("ingest-config-preview").textContent = JSON.stringify(managedKb.ingest_config, null, 2);
        document.getElementById("generation-config-preview").textContent = JSON.stringify(managedKb.generation_config, null, 2);
        renderSourcesTable();
        renderManagedDocuments();
        renderActivity();
        refreshIcons();
    }

    function emptyRow(columns, text) {
        return `<tr><td colspan="${columns}" class="empty-cell">${escapeHtml(text)}</td></tr>`;
    }

    function renderSourcesTable() {
        const body = document.getElementById("sources-table-body");
        body.innerHTML = managedSources.length ? managedSources.map(source => `
            <tr>
                <td title="${escapeHtml(source.name)}">${escapeHtml(source.name)}</td>
                <td><span class="type-badge">${escapeHtml(source.type)}</span></td>
                <td>${escapeHtml(source.sync_mode)}</td>
                <td>${formatDate(source.last_synced_at)}</td>
                <td><span class="result-badge ${source.enabled ? "ok" : "error"}">${source.enabled ? "Enabled" : "Disabled"}</span></td>
                <td class="table-actions">
                    ${source.sync_mode !== "push" ? `<button class="mini-button" data-source-action="sync" data-source-id="${source.id}" title="Sync"><i data-lucide="refresh-cw"></i></button>` : ""}
                    <button class="mini-button" data-source-action="edit" data-source-id="${source.id}" title="Edit"><i data-lucide="pencil"></i></button>
                    <button class="mini-button danger" data-source-action="delete" data-source-id="${source.id}" title="Delete"><i data-lucide="trash-2"></i></button>
                </td>
            </tr>
        `).join("") : emptyRow(6, "No sources configured.");
    }

    function renderManagedDocuments() {
        const body = document.getElementById("documents-table-body");
        body.innerHTML = managedDocuments.length ? managedDocuments.map(doc => `
            <tr>
                <td title="${escapeHtml(doc.title)}">${escapeHtml(doc.title)}</td>
                <td>${escapeHtml(doc.source_name)}</td>
                <td><span class="type-badge">${escapeHtml(doc.format || "-")}</span></td>
                <td>${doc.chunk_count || 0}</td>
                <td>${formatDate(doc.indexed_at)}</td>
                <td class="table-actions">
                    <button class="mini-button" data-document-action="view" data-document-id="${doc.id}" title="View"><i data-lucide="eye"></i></button>
                    <button class="mini-button danger" data-document-action="delete" data-document-id="${doc.id}" title="Delete"><i data-lucide="trash-2"></i></button>
                </td>
            </tr>
        `).join("") : emptyRow(6, "No documents indexed.");
    }

    function renderActivity() {
        const body = document.getElementById("activity-table-body");
        body.innerHTML = managedLogs.length ? managedLogs.map(log => `
            <tr>
                <td>${formatDate(log.created_at)}</td>
                <td>${escapeHtml(log.source_name || "Deleted source")}</td>
                <td>${escapeHtml(log.action)}</td>
                <td>${log.docs_processed} / ${log.docs_skipped}</td>
                <td>${Number(log.duration_seconds || 0).toFixed(1)}s</td>
                <td><span class="result-badge ${log.docs_failed || log.error_detail ? "error" : "ok"}">${log.docs_failed || log.error_detail ? "Failed" : "Success"}</span></td>
            </tr>
        `).join("") : emptyRow(6, "No ingestion activity recorded.");
    }

    function formatBytes(value) {
        const bytes = Number(value || 0);
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function formatTtl(value) {
        if (value === null || value === undefined || value < 0) return "No expiry";
        const minutes = Math.floor(value / 60);
        const seconds = value % 60;
        if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
        return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
    }

    async function fetchManagedCache() {
        if (!managedKb) return;
        const requestId = ++cacheRequestId;
        const notice = document.getElementById("cache-notice");
        const body = document.getElementById("cache-table-body");
        notice.textContent = "";
        body.innerHTML = emptyRow(6, "Loading cache entries...");
        try {
            const queryParam = cacheQuery ? `&q=${encodeURIComponent(cacheQuery)}` : "";
            const [cacheData, statsData] = await Promise.all([
                apiRequest(`/api/kb/${managedKb.slug}/cache?offset=${cacheOffset}&limit=${cacheLimit}${queryParam}`),
                apiRequest("/api/cache/stats"),
            ]);
            if (requestId !== cacheRequestId) return;
            managedCache = cacheData;
            globalCacheStats = statsData;
            renderManagedCache();
        } catch (error) {
            if (requestId !== cacheRequestId) return;
            managedCache = null;
            body.innerHTML = emptyRow(6, "Cache data is unavailable.");
            notice.textContent = error.message;
            notice.className = "cache-notice error";
        }
    }

    function renderManagedCache() {
        const summary = managedCache.summary;
        const entries = managedCache.entries || [];
        const total = summary.entry_count;
        const filtered = managedCache.filtered_count;
        document.getElementById("cache-count-label").textContent = cacheQuery
            ? `${filtered} matching of ${total}`
            : `${total} cached response${total === 1 ? "" : "s"}`;
        document.getElementById("cache-kb-entries").textContent = total.toLocaleString();
        document.getElementById("cache-kb-size").textContent = formatBytes(summary.size_bytes);
        document.getElementById("cache-global-entries").textContent = globalCacheStats.entry_count.toLocaleString();
        document.getElementById("cache-global-size").textContent = formatBytes(globalCacheStats.size_bytes);
        document.getElementById("cache-average-ttl").textContent = formatTtl(summary.average_ttl_seconds);
        document.getElementById("cache-expiring-soon").textContent = summary.expiring_soon_count.toLocaleString();
        const start = filtered ? cacheOffset + 1 : 0;
        const end = Math.min(cacheOffset + entries.length, filtered);
        document.getElementById("cache-page-label").textContent = `${start}–${end} of ${filtered}`;
        document.getElementById("btn-cache-prev").disabled = cacheOffset === 0;
        document.getElementById("btn-cache-next").disabled = cacheOffset + cacheLimit >= filtered;
        document.getElementById("cache-notice").className = "cache-notice";
        document.getElementById("cache-table-body").innerHTML = entries.length ? entries.map(entry => `
            <tr class="cache-row" tabindex="0" data-cache-entry-digest="${entry.digest}" aria-label="Open cache details for ${escapeHtml(entry.query)}">
                <td class="cache-query" title="${escapeHtml(entry.query)}">${escapeHtml(entry.query)}</td>
                <td>${formatBytes(entry.size_bytes)}</td>
                <td>${formatDate(entry.created_at)}</td>
                <td>${formatDate(entry.expires_at)}</td>
                <td>${formatTtl(entry.ttl_seconds)}</td>
                <td class="table-actions"><button class="mini-button danger" data-cache-delete-digest="${entry.digest}" title="Delete cache entry" aria-label="Delete cache entry"><i data-lucide="trash-2"></i></button></td>
            </tr>
        `).join("") : emptyRow(6, cacheQuery ? "No cached queries match this search." : (cacheOffset ? "This cache page expired. Refresh to continue." : "No cached responses for this knowledge base."));
        refreshIcons();
    }

    function renderCacheDetail(detail) {
        const contexts = detail.contexts || [];
        const citations = detail.citations || [];
        const timings = Object.entries(detail.timings || {});
        document.getElementById("cache-detail-title").textContent = detail.query;
        document.getElementById("cache-detail-body").innerHTML = `
            <div class="cache-detail-facts">
                <div><span>TTL</span><strong>${formatTtl(detail.ttl_seconds)}</strong></div>
                <div><span>Size</span><strong>${formatBytes(detail.size_bytes)}</strong></div>
                <div><span>Created</span><strong>${formatDate(detail.created_at)}</strong></div>
                <div><span>Expires</span><strong>${formatDate(detail.expires_at)}</strong></div>
            </div>
            <section class="cache-detail-section"><h4>Answer</h4><p>${escapeHtml(detail.answer || "No answer stored.")}</p></section>
            <section class="cache-detail-section"><h4>Contexts <span>${contexts.length}</span></h4><div class="cache-context-list">${contexts.length ? contexts.map((context, index) => `
                <article><header><strong>${escapeHtml(context.document_title || context.filename || `Context ${index + 1}`)}</strong>${context.distance !== undefined ? `<span>${Number(context.distance).toFixed(4)}</span>` : ""}</header><p>${escapeHtml(context.content || context.text || "")}</p></article>
            `).join("") : `<p class="cache-detail-empty">No contexts stored.</p>`}</div></section>
            <section class="cache-detail-section"><h4>Citations <span>${citations.length}</span></h4><div class="cache-citation-list">${citations.length ? citations.map(citation => `<div><strong>${escapeHtml(citation.title || "Untitled")}</strong><span>${escapeHtml(citation.external_id || "Internal document")}${citation.ordinal !== null && citation.ordinal !== undefined ? ` · #${citation.ordinal}` : ""}</span></div>`).join("") : `<p class="cache-detail-empty">No citations stored.</p>`}</div></section>
            <section class="cache-detail-section"><h4>Timings</h4><div class="cache-timing-grid">${timings.length ? timings.map(([name, value]) => `<div><span>${escapeHtml(name.replaceAll("_", " "))}</span><strong>${Number(value).toFixed(2)} ms</strong></div>`).join("") : `<p class="cache-detail-empty">No timing data stored.</p>`}</div></section>
        `;
    }

    async function openCacheDetail(digest, trigger) {
        cacheDetailTrigger = trigger;
        const drawer = document.getElementById("cache-detail-drawer");
        document.getElementById("cache-detail-title").textContent = "Cache details";
        document.getElementById("cache-detail-body").innerHTML = `<p class="loading-item">Loading cache details...</p>`;
        drawer.hidden = false;
        document.getElementById("close-cache-detail").focus();
        try {
            const detail = await apiRequest(`/api/kb/${managedKb.slug}/cache/${digest}`);
            renderCacheDetail(detail);
        } catch (error) {
            document.getElementById("cache-detail-body").innerHTML = `<p class="cache-detail-error">${escapeHtml(error.message)}</p>`;
        }
    }

    function closeCacheDetail() {
        document.getElementById("cache-detail-drawer").hidden = true;
        cacheDetailTrigger?.focus();
        cacheDetailTrigger = null;
    }

    function formatDate(value) {
        return value ? new Date(value).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "Never";
    }

    function showManagementModal(mode, item = null) {
        managementMode = { mode, item };
        const isKb = mode === "create-kb" || mode === "edit-kb";
        document.getElementById("management-modal-title").textContent = isKb ? `${item ? "Edit" : "Create"} knowledge base` : `${item ? "Edit" : "Add"} source`;
        managementFormBody.innerHTML = isKb ? knowledgeBaseFields(item) : sourceFields(item);
        managementModal.style.display = "flex";
        bindSourceTypeTemplate();
        refreshIcons();
    }

    function field(name, label, value = "", options = {}) {
        const full = options.full ? " full" : "";
        const required = options.required === false ? "" : " required";
        if (options.type === "textarea") {
            return `<div class="management-field${full}"><label for="field-${name}">${label}</label><textarea id="field-${name}" name="${name}"${required}>${escapeHtml(value)}</textarea></div>`;
        }
        if (options.choices) {
            const choices = options.choices.map(choice => `<option value="${choice}" ${choice === value ? "selected" : ""}>${choice}</option>`).join("");
            return `<div class="management-field${full}"><label for="field-${name}">${label}</label><select id="field-${name}" name="${name}"${required}>${choices}</select></div>`;
        }
        return `<div class="management-field${full}"><label for="field-${name}">${label}</label><input id="field-${name}" name="${name}" type="${options.type || "text"}" value="${escapeHtml(value)}"${required}${options.readonly ? " readonly" : ""}></div>`;
    }

    function knowledgeBaseFields(kb) {
        return field("slug", "Slug", kb?.slug || "", { readonly: Boolean(kb) }) +
            field("name", "Name", kb?.name || "") +
            field("description", "Description", kb?.description || "", { full: true, required: false }) +
            field("ingest_config", "Ingestion config (JSON)", JSON.stringify(kb?.ingest_config || defaultIngestConfig(), null, 2), { type: "textarea", full: true }) +
            field("generation_config", "Generation config (JSON)", JSON.stringify(kb?.generation_config || defaultGenerationConfig(), null, 2), { type: "textarea", full: true });
    }

    function sourceFields(source) {
        return field("name", "Name", source?.name || "") +
            field("type", "Type", source?.type || "directory", { choices: source ? [source.type] : ["directory", "web", "calibre", "cloud_drive", "external"] }) +
            field("sync_mode", "Sync mode", source?.sync_mode || "manual", { choices: ["manual", "watch", "scheduled", "push"] }) +
            field("sync_cron", "Cron (scheduled only)", source?.sync_cron || "", { required: false }) +
            field("config", "Source config (JSON)", JSON.stringify(source?.config || sourceConfigTemplate("directory"), null, 2), { type: "textarea", full: true }) +
            `<p class="management-help">Credentials must be environment variable names. Secrets are never stored in source configuration.</p>`;
    }

    function bindSourceTypeTemplate() {
        if (managementMode?.item || !managementMode?.mode.includes("source")) return;
        const type = document.getElementById("field-type");
        type?.addEventListener("change", () => {
            document.getElementById("field-config").value = JSON.stringify(sourceConfigTemplate(type.value), null, 2);
            document.getElementById("field-sync_mode").value = type.value === "external" ? "push" : "manual";
        });
    }

    function sourceConfigTemplate(type) {
        const templates = {
            directory: { path: "documents" },
            web: { url: "https://example.com/docs", max_depth: 1, max_pages: 25 },
            calibre: { base_url: "http://192.168.11.65:8080", library_id: "Calibre_Library", preferred_formats: ["EPUB", "PDF"] },
            cloud_drive: { provider: "google_drive", folder_id: "", oauth_token_file_env: "GOOGLE_OAUTH_TOKEN_FILE" },
            external: { source_key: "dsreaderhelper" },
        };
        return templates[type];
    }

    function defaultIngestConfig() {
        return { embedding: { model: "nomic-embed-text:latest", dimensions: 768, provider: "ollama" }, chunking: { strategy: "markdown_aware", max_chars: 600, overlap_chars: 120 }, search: { bm25_enabled: true, initial_topn: 20, rrf_k: 60, context_window: 2 } };
    }

    function defaultGenerationConfig() {
        return { model: "qwen3:8b", provider: "ollama", temperature: 0, max_tokens: 256, top_k_contexts: 10, query_rewrite: { enabled: true, model: "qwen3:8b" }, reranker: { enabled: true } };
    }

    managementForm.addEventListener("submit", async event => {
        event.preventDefault();
        const values = Object.fromEntries(new FormData(managementForm));
        try {
            if (managementMode.mode.includes("kb")) {
                const payload = { name: values.name, description: values.description || null, ingest_config: JSON.parse(values.ingest_config), generation_config: JSON.parse(values.generation_config) };
                if (managementMode.mode === "create-kb") payload.slug = values.slug;
                const url = managementMode.mode === "create-kb" ? "/api/kb" : `/api/kb/${managedKb.slug}`;
                await apiRequest(url, { method: managementMode.mode === "create-kb" ? "POST" : "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
                selectedKbSlug = values.slug || managedKb.slug;
            } else {
                const payload = { name: values.name, config: JSON.parse(values.config), sync_mode: values.sync_mode, sync_cron: values.sync_cron || null };
                if (managementMode.mode === "create-source") payload.type = values.type;
                const sourceId = managementMode.item?.id;
                const url = sourceId ? `/api/kb/${managedKb.slug}/sources/${sourceId}` : `/api/kb/${managedKb.slug}/sources`;
                await apiRequest(url, { method: sourceId ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
            }
            closeManagementModal();
            await refreshManagement();
        } catch (error) {
            let errorNode = managementFormBody.querySelector(".management-error");
            if (!errorNode) {
                errorNode = document.createElement("p");
                errorNode.className = "management-error";
                managementFormBody.appendChild(errorNode);
            }
            errorNode.textContent = error.message;
        }
    });

    function closeManagementModal() {
        managementModal.style.display = "none";
        managementMode = null;
    }

    chatKbSelect.addEventListener("change", async event => {
        selectedKbSlug = event.target.value;
        await fetchDocuments();
    });
    kbSearch.addEventListener("input", renderKnowledgeBaseList);
    kbList.addEventListener("click", event => {
        const item = event.target.closest("[data-kb-slug]");
        if (item) selectManagedKnowledgeBase(item.dataset.kbSlug);
    });
    document.getElementById("compare-left").addEventListener("change", renderComparison);
    document.getElementById("compare-right").addEventListener("change", renderComparison);
    document.getElementById("btn-refresh-manage").addEventListener("click", refreshManagement);
    document.getElementById("btn-create-kb").addEventListener("click", () => showManagementModal("create-kb"));
    document.getElementById("btn-edit-kb").addEventListener("click", () => showManagementModal("edit-kb", managedKb));
    document.getElementById("btn-add-source").addEventListener("click", () => showManagementModal("create-source"));
    document.getElementById("close-management-modal").addEventListener("click", closeManagementModal);
    document.getElementById("btn-cancel-management").addEventListener("click", closeManagementModal);
    document.getElementById("workspace-tabs").addEventListener("click", event => {
        const button = event.target.closest("[data-view]");
        if (!button) return;
        activeManageView = button.dataset.view;
        document.querySelectorAll("#workspace-tabs button").forEach(item => item.classList.toggle("active", item === button));
        document.querySelectorAll(".workspace-view").forEach(view => view.classList.toggle("active", view.id === `manage-${button.dataset.view}-view`));
        button.scrollIntoView({ block: "nearest", inline: "nearest" });
        if (activeManageView === "cache") fetchManagedCache();
    });

    document.getElementById("sources-table-body").addEventListener("click", async event => {
        const button = event.target.closest("[data-source-action]");
        if (!button) return;
        const source = managedSources.find(item => item.id === Number(button.dataset.sourceId));
        if (button.dataset.sourceAction === "edit") return showManagementModal("edit-source", source);
        if (button.dataset.sourceAction === "delete" && !confirm(`Delete source "${source.name}" and all indexed documents?`)) return;
        try {
            const url = `/api/kb/${managedKb.slug}/sources/${source.id}` + (button.dataset.sourceAction === "sync" ? "/sync" : "");
            await apiRequest(url, { method: button.dataset.sourceAction === "sync" ? "POST" : "DELETE" });
            if (button.dataset.sourceAction === "sync") alert("Source sync started.");
            await refreshManagement();
        } catch (error) { alert(error.message); }
    });

    document.getElementById("documents-table-body").addEventListener("click", async event => {
        const button = event.target.closest("[data-document-action]");
        if (!button) return;
        const doc = managedDocuments.find(item => item.id === Number(button.dataset.documentId));
        if (button.dataset.documentAction === "view") {
            documentsData = managedDocuments;
            return openSourceDocument(doc.filename);
        }
        if (!confirm(`Delete document "${doc.title}"?`)) return;
        try {
            await apiRequest(`/api/kb/${managedKb.slug}/documents/${doc.id}`, { method: "DELETE" });
            await refreshManagement();
        } catch (error) { alert(error.message); }
    });

    document.getElementById("btn-refresh-cache").addEventListener("click", fetchManagedCache);
    document.getElementById("cache-search").addEventListener("input", event => {
        const value = event.target.value;
        document.getElementById("clear-cache-search").hidden = value.length === 0;
        clearTimeout(cacheSearchTimer);
        document.getElementById("cache-notice").textContent = "Searching...";
        cacheSearchTimer = setTimeout(() => {
            cacheQuery = value.trim();
            cacheOffset = 0;
            fetchManagedCache();
        }, 250);
    });
    document.getElementById("clear-cache-search").addEventListener("click", () => {
        clearTimeout(cacheSearchTimer);
        document.getElementById("cache-search").value = "";
        document.getElementById("clear-cache-search").hidden = true;
        cacheQuery = "";
        cacheOffset = 0;
        fetchManagedCache();
        document.getElementById("cache-search").focus();
    });
    document.getElementById("btn-clear-kb-cache").addEventListener("click", async () => {
        if (!confirm(`Clear every cached response for "${managedKb.name}"?`)) return;
        try {
            const result = await apiRequest(`/api/kb/${managedKb.slug}/cache/clear`, { method: "POST" });
            cacheOffset = 0;
            await fetchManagedCache();
            document.getElementById("cache-notice").textContent = result.message;
        } catch (error) { alert(error.message); }
    });
    document.getElementById("btn-clear-global-cache").addEventListener("click", async () => {
        if (!confirm("Clear all Chimera RAG cache entries across every knowledge base?")) return;
        try {
            const result = await apiRequest("/api/cache/clear", { method: "POST" });
            cacheOffset = 0;
            await fetchManagedCache();
            document.getElementById("cache-notice").textContent = result.message;
        } catch (error) { alert(error.message); }
    });
    document.getElementById("cache-table-body").addEventListener("click", async event => {
        const deleteButton = event.target.closest("[data-cache-delete-digest]");
        if (!deleteButton) {
            const row = event.target.closest("[data-cache-entry-digest]");
            if (row) openCacheDetail(row.dataset.cacheEntryDigest, row);
            return;
        }
        event.stopPropagation();
        if (!confirm("Delete this cached response?")) return;
        try {
            await apiRequest(`/api/kb/${managedKb.slug}/cache/${deleteButton.dataset.cacheDeleteDigest}`, { method: "DELETE" });
            await fetchManagedCache();
        } catch (error) {
            if (error.message.includes("expired")) await fetchManagedCache();
            else alert(error.message);
        }
    });
    document.getElementById("cache-table-body").addEventListener("keydown", event => {
        if (!event.target.matches("[data-cache-entry-digest]") || !["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        openCacheDetail(event.target.dataset.cacheEntryDigest, event.target);
    });
    document.getElementById("close-cache-detail").addEventListener("click", closeCacheDetail);
    document.getElementById("cache-detail-drawer").addEventListener("click", event => {
        if (event.target.id === "cache-detail-drawer") closeCacheDetail();
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !document.getElementById("cache-detail-drawer").hidden) closeCacheDetail();
    });
    document.getElementById("btn-cache-prev").addEventListener("click", () => {
        cacheOffset = Math.max(0, cacheOffset - cacheLimit);
        fetchManagedCache();
    });
    document.getElementById("btn-cache-next").addEventListener("click", () => {
        cacheOffset += cacheLimit;
        fetchManagedCache();
    });
    document.getElementById("btn-delete-kb").addEventListener("click", async () => {
        if (!confirm(`Delete knowledge base "${managedKb.name}" and all of its storage?`)) return;
        try {
            await apiRequest(`/api/kb/${managedKb.slug}`, { method: "DELETE" });
            managedKb = null;
            document.getElementById("manage-empty").style.display = "grid";
            document.getElementById("manage-detail").style.display = "none";
            await refreshManagement();
        } catch (error) { alert(error.message); }
    });

    refreshIcons();

    // ==========================================================================
    // RAG Audit Dashboard - REST Helpers
    // ==========================================================================
    
    // Fetch historical run records
    async function fetchRuns() {
        try {
            const res = await fetch("/api/benchmarks");
            if (!res.ok) throw new Error("Could not fetch benchmark runs.");
            const data = await res.json();
            historicalRuns = data.runs;
            runCount.textContent = historicalRuns.length;
            renderRunsList();
        } catch (err) {
            runsList.innerHTML = `<li class="loading-item" style="color: var(--danger-color)">Error loading audit history: ${err.message}</li>`;
        }
    }

    function renderRunsList() {
        if (historicalRuns.length === 0) {
            runsList.innerHTML = `<li class="loading-item">No audit runs recorded yet.</li>`;
            return;
        }

        runsList.innerHTML = "";
        historicalRuns.forEach(run => {
            const li = document.createElement("li");
            li.className = "run-item";
            if (selectedRunId === run.id) li.classList.add("active");
            
            const dateStr = run.created_at ? new Date(run.created_at).toLocaleString() : "Unknown date";
            const passRateText = run.status === 'completed' ? `${run.pass_rate.toFixed(0)}%` : 'N/A';
            const avgCorrectText = run.status === 'completed' ? run.avg_correctness.toFixed(2) : 'N/A';
            
            li.innerHTML = `
                <div class="run-item-meta">
                    <span class="run-item-id">Run #${run.id}</span>
                    <span class="run-status-badge ${run.status}">${run.status}</span>
                </div>
                <div class="run-item-scores">
                    <span class="score-tag" title="Pass Rate (>= 4 correctness)"><div class="score-tag-dot" style="background: var(--accent-color);"></div>Pass: ${passRateText}</span>
                    <span class="score-tag" title="Avg Correctness"><div class="score-tag-dot" style="background: var(--success-color);"></div>Avg Correctness: ${avgCorrectText}</span>
                </div>
                <div class="run-item-meta">
                    <span class="run-item-date">${dateStr}</span>
                </div>
            `;
            
            li.addEventListener("click", () => {
                selectedRunId = run.id;
                // Render active styling
                document.querySelectorAll(".run-item").forEach(item => item.classList.remove("active"));
                li.classList.add("active");
                loadRunDetail(run.id);
            });
            
            runsList.appendChild(li);
        });
    }

    // Load full details for a run
    async function loadRunDetail(runId) {
        try {
            const res = await fetch(`/api/benchmarks/${runId}`);
            if (!res.ok) throw new Error("Failed to load run details.");
            activeRunDetail = await res.json();
            
            // Render detailed metrics
            benchmarkWelcome.style.display = "none";
            benchmarkDetail.style.display = "flex";
            
            runTitle.textContent = `Audit Run #${activeRunDetail.id}`;
            runDatasetText.textContent = activeRunDetail.dataset_name;
            runJudgeText.textContent = activeRunDetail.judge_model;

            // Display Comment / Code Changes
            const runCommentContainer = document.getElementById("run-comment-container");
            const runCommentText = document.getElementById("run-comment-text");
            if (activeRunDetail.comment) {
                runCommentText.textContent = activeRunDetail.comment;
                runCommentContainer.style.display = "flex";
            } else {
                runCommentContainer.style.display = "none";
            }

            // Fill KPI Cards
            kpiCorrectness.innerHTML = `${activeRunDetail.avg_correctness.toFixed(2)}<span class="kpi-suffix">/ 5</span>`;
            kpiCorrectness.style.color = getScoreColor(activeRunDetail.avg_correctness);

            kpiFaithfulness.innerHTML = `${activeRunDetail.avg_faithfulness.toFixed(2)}<span class="kpi-suffix">/ 5</span>`;
            kpiFaithfulness.style.color = getScoreColor(activeRunDetail.avg_faithfulness);

            kpiRetrieval.innerHTML = `${activeRunDetail.avg_relevance.toFixed(2)}<span class="kpi-suffix">/ 5</span>`;
            kpiRetrieval.style.color = getScoreColor(activeRunDetail.avg_relevance);

            kpiPassrate.innerHTML = `${activeRunDetail.pass_rate.toFixed(1)}<span class="kpi-suffix">%</span>`;
            kpiPassrate.style.color = activeRunDetail.pass_rate >= 80 ? 'var(--success-color)' : activeRunDetail.pass_rate >= 50 ? '#fbbf24' : 'var(--danger-color)';

            // Compute Average Latency of completed questions in run
            const completedQ = activeRunDetail.results || [];
            let totalLatency = 0;
            let timedCount = 0;
            let sumEmbed = 0, sumRetr = 0, sumRerank = 0, sumGen = 0;

            completedQ.forEach(q => {
                if (q.latency_total) {
                    totalLatency += q.latency_total;
                    sumEmbed += q.latency_embedding || 0;
                    sumRetr += q.latency_retrieval || 0;
                    sumRerank += q.latency_rerank || 0;
                    sumGen += q.latency_generation || 0;
                    timedCount++;
                }
            });

            const avgTotalMs = timedCount > 0 ? (totalLatency / timedCount) : 0;
            kpiLatency.innerHTML = `${avgTotalMs.toFixed(0)}<span class="kpi-suffix">ms</span>`;
            detailTotalLatencyText.textContent = `${avgTotalMs.toFixed(1)} ms`;

            // Draw Global Latency Breakdown bar
            if (timedCount > 0 && totalLatency > 0) {
                const embedPct = (sumEmbed / totalLatency) * 100;
                const retrPct = (sumRetr / totalLatency) * 100;
                const rerankPct = (sumRerank / totalLatency) * 100;
                const genPct = (sumGen / totalLatency) * 100;

                detailLatencyBar.innerHTML = `
                    <div class="latency-segment seg-embed" style="width: ${embedPct}%" title="Avg Embedding: ${(sumEmbed/timedCount).toFixed(1)}ms (${embedPct.toFixed(1)}%)">Embedding</div>
                    <div class="latency-segment seg-retrieval" style="width: ${retrPct}%" title="Avg Retrieval: ${(sumRetr/timedCount).toFixed(1)}ms (${retrPct.toFixed(1)}%)">Retrieval</div>
                    <div class="latency-segment seg-rerank" style="width: ${rerankPct}%" title="Avg Rerank: ${(sumRerank/timedCount).toFixed(1)}ms (${rerankPct.toFixed(1)}%)">Rerank</div>
                    <div class="latency-segment seg-gen" style="width: ${genPct}%" title="Avg LLM Synthesis: ${(sumGen/timedCount).toFixed(1)}ms (${genPct.toFixed(1)}%)">Synthesis</div>
                `;
            } else {
                detailLatencyBar.innerHTML = `<div style="padding: 4px; text-align: center; font-size: 11px; color: var(--text-secondary); width: 100%;">Latency telemetry unavailable</div>`;
            }

            // Render evaluated questions list
            renderAuditQuestions();

        } catch (err) {
            console.error("Error loading run:", err);
            alert(`Failed to load run details: ${err.message}`);
        }
    }

    function getScoreColor(val) {
        if (val >= 4) return 'var(--success-color)';
        if (val >= 3) return '#fbbf24';
        return 'var(--danger-color)';
    }

    function getScoreClass(val) {
        if (val >= 4) return 'score-high';
        if (val >= 3) return 'score-mid';
        return 'score-low';
    }

    // Delete a benchmark run record
    btnDeleteRun.addEventListener("click", async () => {
        if (!selectedRunId) return;
        if (!confirm(`Are you sure you want to permanently delete Audit Run #${selectedRunId} from the database?`)) return;

        try {
            const res = await fetch(`/api/benchmarks/${selectedRunId}`, { method: "DELETE" });
            if (!res.ok) throw new Error("Deletion failed on server.");
            
            selectedRunId = null;
            activeRunDetail = null;
            benchmarkWelcome.style.display = "flex";
            benchmarkDetail.style.display = "none";
            
            fetchRuns();
        } catch (err) {
            alert(`Delete failed: ${err.message}`);
        }
    });

    // ==========================================================================
    // Filters & Sorting for Audit Detail
    // ==========================================================================
    
    // Text search query
    qSearch.addEventListener("input", (e) => {
        textQuery = e.target.value.toLowerCase().trim();
        renderAuditQuestions();
    });

    // Sorting selector
    qSort.addEventListener("change", (e) => {
        sortOption = e.target.value;
        renderAuditQuestions();
    });

    // Difficulty and Cache click pill bindings
    setupFilterPills("filter-difficulty", (val) => { difficultyFilter = val; renderAuditQuestions(); });
    setupFilterPills("filter-cache", (val) => { cacheFilter = val; renderAuditQuestions(); });
    setupFilterPills("filter-fidelity", (val) => { fidelityFilter = val; renderAuditQuestions(); });

    function setupFilterPills(containerId, callback) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const pills = container.querySelectorAll(".filter-pill");
        pills.forEach(pill => {
            pill.addEventListener("click", () => {
                pills.forEach(p => p.classList.remove("active"));
                pill.classList.add("active");
                callback(pill.dataset.val);
            });
        });
    }

    // Render granular accordion cards
    function renderAuditQuestions() {
        if (!activeRunDetail || !activeRunDetail.results) return;
        
        auditQuestionsContainer.innerHTML = "";
        let items = [...activeRunDetail.results];

        // Apply filters
        items = items.filter(item => {
            // Difficulty
            if (difficultyFilter !== "all" && item.difficulty !== difficultyFilter) return false;
            
            // Cache hit
            if (cacheFilter === "hit" && !item.cache_hit) return false;
            if (cacheFilter === "miss" && item.cache_hit) return false;
            
            // Fidelity (Correctness)
            if (fidelityFilter === "pass" && item.answer_correctness < 4) return false;
            if (fidelityFilter === "fail" && item.answer_correctness >= 4) return false;

            // Search box
            if (textQuery) {
                const questionText = (item.question || "").toLowerCase();
                const refText = (item.reference_answer || "").toLowerCase();
                const ragText = (item.rag_answer || "").toLowerCase();
                const rationaleText = (item.rationale || "").toLowerCase();
                if (!questionText.includes(textQuery) && 
                    !refText.includes(textQuery) && 
                    !ragText.includes(textQuery) &&
                    !rationaleText.includes(textQuery)) return false;
            }
            return true;
        });

        // Apply sorts
        if (sortOption === "slowest") {
            items.sort((a, b) => (b.latency_total || 0) - (a.latency_total || 0));
        } else if (sortOption === "fastest") {
            items.sort((a, b) => (a.latency_total || 999999) - (b.latency_total || 999999));
        } else if (sortOption === "correctness") {
            items.sort((a, b) => (a.answer_correctness || 1) - (b.answer_correctness || 1));
        }

        if (items.length === 0) {
            auditQuestionsContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 40px; font-size: 13px;">No audited questions match your active filters.</div>`;
            return;
        }

        items.forEach(item => {
            const card = document.createElement("div");
            card.className = "qa-card";
            card.id = `qa-card-${item.id}`;

            const correctClass = getScoreClass(item.answer_correctness);
            const faithClass = getScoreClass(item.faithfulness);
            const retrieveClass = getScoreClass(item.retrieval_relevance);

            const cacheBadge = item.cache_hit ? '<span class="badge cache-hit">CACHE HIT</span>' : '<span class="badge cache-miss">COLD RUN</span>';
            const diffBadge = item.difficulty === 'cross-chunk' ? '<span class="badge diff-cross">CROSS-CHUNK</span>' : '<span class="badge diff-simple">SIMPLE</span>';
            
            const totalLatencyText = item.latency_total ? `${item.latency_total.toFixed(0)} ms` : 'N/A';

            card.innerHTML = `
                <div class="qa-header">
                    <div class="header-left">
                        <span class="id-badge">${item.question_id || 'Q'}</span>
                        <span class="question-summary">${item.question}</span>
                    </div>
                    <div class="header-right">
                        ${diffBadge}
                        ${cacheBadge}
                        <span class="badge" style="background: rgba(251,191,36,0.06); color: #fbbf24; border: 1px solid rgba(251,191,36,0.15);">${totalLatencyText}</span>
                        <div class="score-badge-group">
                            <div class="score-badge ${correctClass}" title="Answer Correctness: ${item.answer_correctness}">C:${item.answer_correctness}</div>
                            <div class="score-badge ${faithClass}" title="Faithfulness: ${item.faithfulness}">F:${item.faithfulness}</div>
                            <div class="score-badge ${retrieveClass}" title="Retrieval Relevance: ${item.retrieval_relevance}">R:${item.retrieval_relevance}</div>
                        </div>
                        <span class="header-arrow">▼</span>
                    </div>
                </div>
                <div class="qa-details" style="display: none;">
                    <!-- Timing info -->
                    <div class="timing-chips">
                        <div class="timing-chip"><b>Embedding:</b> ${(item.latency_embedding || 0).toFixed(1)} ms</div>
                        <div class="timing-chip"><b>Retrieval:</b> ${(item.latency_retrieval || 0).toFixed(1)} ms</div>
                        <div class="timing-chip"><b>Rerank:</b> ${(item.latency_rerank || 0).toFixed(1)} ms</div>
                        <div class="timing-chip"><b>Synthesis:</b> ${(item.latency_generation || 0).toFixed(1)} ms</div>
                        <div class="timing-chip total"><b>Total Pipeline:</b> ${(item.latency_total || 0).toFixed(1)} ms</div>
                    </div>

                    <!-- mini-bar -->
                    ${item.latency_total > 0 ? `
                        <div class="q-latency-container" style="margin-bottom: 12px;">
                            <div class="latency-bar-container" style="height: 10px; border-radius: 4px;">
                                <div class="latency-segment seg-embed" style="width: ${(item.latency_embedding/item.latency_total*100)}%" title="Embedding"></div>
                                <div class="latency-segment seg-retrieval" style="width: ${(item.latency_retrieval/item.latency_total*100)}%" title="Retrieval"></div>
                                <div class="latency-segment seg-rerank" style="width: ${(item.latency_rerank/item.latency_total*100)}%" title="Rerank"></div>
                                <div class="latency-segment seg-gen" style="width: ${(item.latency_generation/item.latency_total*100)}%" title="Synthesis"></div>
                            </div>
                        </div>
                    ` : ''}

                    <!-- Answers Grid -->
                    <div class="answers-grid">
                        <div class="answer-panel reference">
                            <div class="panel-hdr">Ground-Truth Reference Answer</div>
                            <div class="answer-body">${item.reference_answer}</div>
                        </div>
                        <div class="answer-panel rag">
                            <div class="panel-hdr">Chimera Cortex Generated Answer</div>
                            <div class="answer-body">${item.rag_answer}</div>
                        </div>
                    </div>

                    <!-- Judge Rationale -->
                    <div style="margin-bottom: 16px;">
                        <div class="kpi-label" style="font-size: 10px; margin-bottom: 4px;">LLM Judge Evaluation Rationale</div>
                        <div class="rationale-text">${item.rationale || 'No explanation provided.'}</div>
                    </div>

                    <!-- Rankings table -->
                    <div class="rankings-panel">
                        <div class="panel-hdr" style="color: #fff; margin-bottom: 4px;">Candidate Chunk Rankings & Rerank Rank Shifts</div>
                        <table class="rankings-table">
                            <thead>
                                <tr>
                                    <th>Candidate Chunk Source</th>
                                    <th>1st-Stage Vector Rank (Similarity)</th>
                                    <th>2nd-Stage Rerank Rank (Logit / Sigmoid)</th>
                                    <th>Rank Shift</th>
                                    <th style="text-align: right;">Inspection</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${buildRankingsTable(item)}
                            </tbody>
                        </table>
                    </div>

                    <!-- Prompt Logs collapsibles -->
                    <div class="collapsible-section">
                        <div class="sec-hdr btn-toggle-prompt">
                            <span>RAW GENERATIVE PROMPT LOGS INGESTED BY OLLAMA</span>
                            <span class="prompt-arrow">▶</span>
                        </div>
                        <div class="sec-body">
                            <pre class="prompt-pre">${escapeHtml(item.llm_prompt || 'N/A')}</pre>
                        </div>
                    </div>
                </div>
            `;

            // Setup Accordion Toggles
            const header = card.querySelector(".qa-header");
            const details = card.querySelector(".qa-details");
            header.addEventListener("click", () => {
                const isOpen = card.classList.contains("open");
                if (isOpen) {
                    card.classList.remove("open");
                    details.style.display = "none";
                } else {
                    card.classList.add("open");
                    details.style.display = "block";
                }
            });

            // Toggle Prompt Logs
            const togglePromptBtn = card.querySelector(".btn-toggle-prompt");
            const secBody = card.querySelector(".sec-body");
            const arrow = card.querySelector(".prompt-arrow");
            togglePromptBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                const isSecOpen = secBody.style.display === "block";
                if (isSecOpen) {
                    secBody.style.display = "none";
                    arrow.textContent = "▶";
                } else {
                    secBody.style.display = "block";
                    arrow.textContent = "▼";
                }
            });

            // Toggle chunks buttons inside candidate table
            const viewChunkButtons = card.querySelectorAll(".view-chunk-btn");
            viewChunkButtons.forEach(btn => {
                btn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const idx = btn.dataset.idx;
                    const drawer = card.querySelector(`#chunk-text-${item.id}-${idx}`);
                    if (drawer.style.display === "block") {
                        drawer.style.display = "none";
                        btn.textContent = "View Text";
                    } else {
                        drawer.style.display = "block";
                        btn.textContent = "Close Text";
                    }
                });
            });

            auditQuestionsContainer.appendChild(card);
        });
    }

    function buildRankingsTable(item) {
        // Safe parser helper
        const firstStage = item.first_stage_candidates || [];
        const secondStage = item.second_stage_candidates || [];

        if (firstStage.length === 0) {
            return `<tr><td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 12px;">RAG Backend did not capture telemetry candidate logs.</td></tr>`;
        }

        // Group by sub-query
        const groups = {};
        const queryOrder = [];
        
        secondStage.forEach(candidate => {
            const sq = candidate.sub_query || "Unknown Query";
            if (!groups[sq]) {
                groups[sq] = [];
                queryOrder.push(sq);
            }
            groups[sq].push(candidate);
        });

        // Sort groups such that the original query group is always first
        queryOrder.sort((a, b) => {
            const isAOriginal = a.toLowerCase() === (item.question || "").toLowerCase();
            const isBOriginal = b.toLowerCase() === (item.question || "").toLowerCase();
            if (isAOriginal && !isBOriginal) return -1;
            if (!isAOriginal && isBOriginal) return 1;
            return 0;
        });

        let rows = "";
        let globalIdx = 0;

        queryOrder.forEach(sq => {
            const isOriginal = sq.toLowerCase() === (item.question || "").toLowerCase();
            const groupTitle = isOriginal ? `Original Query: "${sq}"` : `Decomposed Query: "${sq}"`;
            const headerColor = isOriginal ? '#10b981' : '#60a5fa'; // Emerald for original, Blue for decomposed
            
            rows += `
                <tr class="subquery-group-hdr" style="background: rgba(255,255,255,0.02);">
                    <td colspan="5" style="padding: 8px 10px; color: ${headerColor}; font-family: Outfit, sans-serif; font-size: 11px;">
                        ${groupTitle}
                    </td>
                </tr>
            `;

            groups[sq].forEach(candidate => {
                const fn = candidate.filename;
                const chunkIdx = candidate.chunk_index;
                const r1 = candidate.first_stage_rank;
                const r2 = candidate.rank;
                const sim = candidate.first_stage_score;
                const logit = candidate.rerank_logit;
                const sigmoid = candidate.rerank_score;
                
                // Shift
                const shiftVal = r1 - r2;
                let shiftBadge = "";
                if (shiftVal > 0) {
                    shiftBadge = `<span class="shift-badge shift-up">▲ +${shiftVal}</span>`;
                } else if (shiftVal < 0) {
                    shiftBadge = `<span class="shift-badge shift-down">▼ ${shiftVal}</span>`;
                } else {
                    shiftBadge = `<span class="shift-badge shift-none">•</span>`;
                }

                // Check if chunk was actually injected into the LLM prompt (dynamic check, no hardcoding)
                const isInPrompt = item.llm_prompt && item.llm_prompt.includes(candidate.content);
                const sliceBadge = isInPrompt ? ' <span class="top-slice-badge">PROMPT INJECT</span>' : "";

                const currentIdx = globalIdx++;

                rows += `
                    <tr>
                        <td><b>${fn}</b> (Chunk ${chunkIdx})${sliceBadge}</td>
                        <td>Rank ${r1} <span style="color: var(--text-secondary)">(${sim.toFixed(4)})</span></td>
                        <td>Rank ${r2} <span style="color: var(--text-secondary)">(logit: ${logit !== null ? logit.toFixed(2) : 'N/A'} | sig: ${sigmoid !== null ? sigmoid.toFixed(4) : 'N/A'})</span></td>
                        <td>${shiftBadge}</td>
                        <td style="text-align: right;">
                            <button class="view-chunk-btn" data-idx="${currentIdx}">View Text</button>
                        </td>
                    </tr>
                    <tr>
                        <td colspan="5" style="padding: 0; border: none;">
                            <div class="chunk-drawer" id="chunk-text-${item.id}-${currentIdx}">${escapeHtml(candidate.content)}</div>
                        </td>
                    </tr>
                `;
            });
        });

        return rows;
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return "";
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // ==========================================================================
    // Benchmark Start & Stop API Integration
    // ==========================================================================
    
    // Trigger benchmark execution
    btnRunBenchmark.addEventListener("click", async () => {
        const dataset = document.getElementById("dataset-select").value;
        const judge = document.getElementById("judge-select").value;
        const reuseCache = document.getElementById("reuse-cache-check").checked;
        const comment = document.getElementById("benchmark-comment").value.trim();

        btnRunBenchmark.style.display = "none";
        btnStopBenchmark.style.display = "inline-block";

        try {
            const res = await fetch("/api/benchmarks/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    dataset: dataset,
                    judge_model: judge,
                    reuse_cache: reuseCache,
                    comment: comment || null
                })
            });

            // Clear the comment input after triggering
            document.getElementById("benchmark-comment").value = "";

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || "Trigger failed");
            }
            
            const data = await res.json();
            console.log("Benchmark started, run_id:", data.run_id);
            
            // Switch progress UI
            liveProgressContainer.style.display = "block";
            liveProgressStatus.textContent = "Evaluation run initiated...";
            liveProgressFill.style.width = "0%";
            
            // Reload sidebar history list
            fetchRuns();
            
            // Trigger status polling
            startProgressPolling();

        } catch (err) {
            alert(`Failed to start benchmark: ${err.message}`);
            btnRunBenchmark.style.display = "inline-block";
            btnStopBenchmark.style.display = "none";
        }
    });

    // Terminate benchmark execution
    btnStopBenchmark.addEventListener("click", async () => {
        if (!confirm("Are you sure you want to stop the currently running benchmark? Completed evaluations will be preserved.")) return;

        try {
            const res = await fetch("/api/benchmarks/stop", { method: "POST" });
            if (res.ok) {
                console.log("Benchmark stop request issued.");
                liveProgressStatus.textContent = "Cancelling audit execution...";
            }
        } catch (err) {
            console.error("Stop request error:", err);
        }
    });

    // Status polling
    function startProgressPolling() {
        if (isPolling) return;
        isPolling = true;
        
        // Immediate check
        checkBenchmarkProgress();
        
        // Loop check every 3.5 seconds
        pollInterval = setInterval(checkBenchmarkProgress, 3500);
    }

    async function checkBenchmarkProgress() {
        try {
            const res = await fetch("/api/benchmarks/status");
            if (!res.ok) throw new Error("Failed to fetch running status.");
            const data = await res.json();
            
            if (data.status === "running") {
                // UI changes
                btnRunBenchmark.style.display = "none";
                btnStopBenchmark.style.display = "inline-block";
                liveProgressContainer.style.display = "block";
                
                // Fetch progress details by querying this specific running run_id
                const runId = data.run_id;
                try {
                    const detailRes = await fetch(`/api/benchmarks/${runId}`);
                    if (detailRes.ok) {
                        const runDetails = await detailRes.json();
                        const completed = runDetails.results.length;
                        const total = runDetails.total_questions;
                        const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
                        
                        liveProgressStatus.textContent = `Auditing: question ${completed} / ${total} (${pct}%)`;
                        liveProgressFill.style.width = `${pct}%`;
                        
                        // If this is currently selected, reload details dynamically in real-time!
                        if (selectedRunId === runId) {
                            activeRunDetail = runDetails;
                            loadRunDetail(runId);
                        }
                    }
                } catch (err) {
                    console.warn("Could not fetch progress metrics:", err);
                }
            } else {
                // Idle state
                const wasRunning = btnStopBenchmark.style.display === "inline-block";
                btnRunBenchmark.style.display = "inline-block";
                btnStopBenchmark.style.display = "none";
                liveProgressContainer.style.display = "none";
                
                if (wasRunning) {
                    console.log("Benchmark completed or stopped. Fetching history.");
                    fetchRuns();
                }
            }
        } catch (err) {
            console.error("Error polling benchmark status:", err);
        }
    }
});
