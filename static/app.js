document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
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
    
    // State
    let documentsData = [];
    
    // Initialize
    fetchSystemStatus();
    fetchDocuments();
    
    // Periodic status check (every 10 seconds)
    setInterval(fetchSystemStatus, 10000);
    
    // Suggestion Buttons Click
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

    // Modal Close
    closeModal.addEventListener("click", () => {
        documentModal.style.display = "none";
    });
    
    window.addEventListener("click", (e) => {
        if (e.target === documentModal) {
            documentModal.style.display = "none";
        }
    });
    
    // Search Filter
    servantSearch.addEventListener("input", (e) => {
        const query = e.target.value.toLowerCase().trim();
        filterDocuments(query);
    });
    
    // Chat Submit
    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const query = chatInput.value.trim();
        if (!query) return;
        
        // Reset inputs
        chatInput.value = "";
        
        // Append user bubble
        appendMessage("user", query);
        
        // Append typing indicator
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
            
            if (!res.ok) {
                throw new Error(`Server returned status ${res.status}`);
            }
            
            const data = await res.json();
            
            // Append assistant bubble
            appendMessage("assistant", data.answer);
            
            // Update RAG analytics
            const endTime = performance.now();
            const elapsed = Math.round(endTime - startTime);
            responseTime.textContent = `${elapsed} ms`;
            
            // Update cache status
            if (data.cache_hit) {
                cacheStatus.textContent = "HIT";
                cacheStatus.className = "stat-value success";
            } else {
                cacheStatus.textContent = "MISS";
                cacheStatus.className = "stat-value miss";
            }
            
            // Render retrieved contexts
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
    
    // Fetch document list
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
    
    // Message rendering
    function appendMessage(sender, text) {
        // Remove welcome card on first user message
        const welcomeCard = chatMessages.querySelector(".system-welcome-card");
        if (welcomeCard) {
            welcomeCard.remove();
        }
        
        const messageDiv = document.createElement("div");
        messageDiv.className = `message ${sender}`;
        
        const label = document.createElement("span");
        label.className = "message-label";
        label.textContent = sender === "user" ? "User Query" : "Cortex AI Response";
        
        const bubble = document.createElement("div");
        bubble.className = "message-bubble";
        
        // Simple Markdown formatter for rendering response text bold/lists
        bubble.innerHTML = formatMarkdown(text);
        
        messageDiv.appendChild(label);
        messageDiv.appendChild(bubble);
        chatMessages.appendChild(messageDiv);
        
        // Auto scroll to bottom
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    
    function formatMarkdown(text) {
        // Safe escaping
        let html = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
            
        // Inline code
        html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
        
        // Bold
        html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        
        // Newlines to breaks
        html = html.replace(/\n/g, "<br>");
        
        return html;
    }
    
    function appendTypingIndicator() {
        const welcomeCard = chatMessages.querySelector(".system-welcome-card");
        if (welcomeCard) {
            welcomeCard.remove();
        }
        
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
    
    // Render Semantic Context Cards
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
    
    // Open Document Modal from MinIO
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
});
