document.addEventListener("DOMContentLoaded", () => {
    // ==========================================================================
    // DOM Elements - Chat Portal
    // ==========================================================================
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatMessages = document.getElementById("chat-messages");
    const servantList = document.getElementById("servant-list");
    const servantSearch = document.getElementById("servant-search");
    const servantCount = document.getElementById("servant-count");
    const cacheStatus = document.getElementById("cache-status");
    const responseTime = document.getElementById("response-time");
    const retrievedContexts = document.getElementById("retrieved-contexts");
    
    // Modal
    const documentModal = document.getElementById("document-modal");
    const modalTitle = document.getElementById("modal-title");
    const modalContent = document.getElementById("modal-content");
    const closeModal = document.getElementById("close-modal");
    
    // ==========================================================================
    // DOM Elements - Navigation Tabs
    // ==========================================================================
    const tabChat = document.getElementById("tab-chat");
    const tabBenchmark = document.getElementById("tab-benchmark");
    const chatLayout = document.getElementById("chat-layout");
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
    let activeTab = "chat"; // "chat" or "benchmark"
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
    fetchDocuments();
    
    // Periodic status check (every 15 seconds)
    setInterval(fetchSystemStatus, 15000);
    
    // Start status polling for benchmark execution
    startProgressPolling();

    // ==========================================================================
    // Tab Navigation Event Handlers
    // ==========================================================================
    tabChat.addEventListener("click", () => switchTab("chat"));
    tabBenchmark.addEventListener("click", () => switchTab("benchmark"));

    function switchTab(tabName) {
        if (activeTab === tabName) return;
        activeTab = tabName;

        if (tabName === "chat") {
            tabChat.classList.add("active");
            tabBenchmark.classList.remove("active");
            chatLayout.style.display = "flex";
            benchmarkLayout.style.display = "none";
        } else {
            tabBenchmark.classList.add("active");
            tabChat.classList.remove("active");
            benchmarkLayout.style.display = "flex";
            chatLayout.style.display = "none";
            
            // Reload run history when opening tab
            fetchRuns();
        }
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
            const filename = e.target.dataset.filename;
            openSourceDocument(filename);
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
        filterDocuments(query);
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
            const res = await fetch("/api/chat", {
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
            const res = await fetch("/api/documents");
            if (!res.ok) throw new Error("documents list not ok");
            const data = await res.json();
            documentsData = data.documents;
            servantCount.textContent = documentsData.length;
            renderDocumentList(documentsData);
        } catch (err) {
            servantList.innerHTML = `<li class="loading-item" style="color: var(--danger-color)">Failed to load documents.</li>`;
            console.error("Error fetching documents:", err);
        }
    }
    
    function renderDocumentList(list) {
        if (list.length === 0) {
            servantList.innerHTML = `<li class="loading-item">No documents found.</li>`;
            return;
        }
        
        servantList.innerHTML = "";
        list.forEach(doc => {
            const li = document.createElement("li");
            li.className = "servant-item";
            li.textContent = doc.title;
            li.title = doc.title;
            li.addEventListener("click", () => {
                chatInput.value = `Tell me about ${doc.title}.`;
                chatInput.focus();
            });
            servantList.appendChild(li);
        });
    }
    
    function filterDocuments(query) {
        const filtered = documentsData.filter(d => d.title.toLowerCase().includes(query));
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
            const res = await fetch(`/api/document/${filename}`);
            if (!res.ok) throw new Error(`Status ${res.status}`);
            const data = await res.json();
            modalContent.textContent = data.content;
        } catch (err) {
            modalContent.textContent = `⚠️ Error fetching document from MinIO: ${err.message}`;
            console.error("MinIO fetch error:", err);
        }
    }

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

        let rows = "";
        secondStage.forEach((candidate, idx) => {
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

            // Top slice highlights
            const sliceBadge = r2 <= 3 ? ' <span class="top-slice-badge">PROMPT INJECT</span>' : "";

            rows += `
                <tr>
                    <td><b>${fn}</b> (Chunk ${chunkIdx})${sliceBadge}</td>
                    <td>Rank ${r1} <span style="color: var(--text-secondary)">(${sim.toFixed(4)})</span></td>
                    <td>Rank ${r2} <span style="color: var(--text-secondary)">(logit: ${logit !== null ? logit.toFixed(2) : 'N/A'} | sig: ${sigmoid !== null ? sigmoid.toFixed(4) : 'N/A'})</span></td>
                    <td>${shiftBadge}</td>
                    <td style="text-align: right;">
                        <button class="view-chunk-btn" data-idx="${idx}">View Text</button>
                    </td>
                </tr>
                <tr>
                    <td colspan="5" style="padding: 0; border: none;">
                        <div class="chunk-drawer" id="chunk-text-${item.id}-${idx}">${escapeHtml(candidate.content)}</div>
                    </td>
                </tr>
            `;
        });

        return rows;
    }

    function escapeHtml(text) {
        if (!text) return "";
        return text
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
        const dataset = datasetSelect.value;
        const judgeModel = judgeSelect.value;
        const reuseCache = reuseCacheCheck.checked;

        btnRunBenchmark.style.display = "none";
        btnStopBenchmark.style.display = "inline-block";

        try {
            const res = await fetch("/api/benchmarks/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    dataset: dataset,
                    judge_model: judgeModel,
                    reuse_cache: reuseCache
                })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || "Trigger failed");
            }
            
            const data = await res.json();
            console.log("Benchmark started, run_id:", data.run_id);
            
            // Switch progress UI
            liveProgressContainer.style.style = "block";
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
