document.addEventListener('DOMContentLoaded', () => {
    const agentSelect = document.getElementById('agent-select');
    const messageInput = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const stopBtn = document.getElementById('stop-btn');
    const newChatBtn = document.getElementById('new-chat-btn');
    const chatForm = document.getElementById('chat-form');
    const messagesDiv = document.getElementById('messages');

    // Attachment state
    const fileInput = document.getElementById('file-input');
    const attachBtn = document.getElementById('attach-btn');
    const attachBadge = document.getElementById('attach-badge');
    const attachBadgeName = document.getElementById('attach-badge-name');
    const attachRemove = document.getElementById('attach-remove');
    const ATTACH_MAX_BYTES = 500 * 1024; // 500 KB
    let attachedFile = null; // { name, content }

    function setAttachment(name, content) {
        attachedFile = { name, content };
        attachBadgeName.textContent = name;
        attachBadge.hidden = false;
        attachBtn.classList.add('has-attachment');
        attachBtn.title = `Attached: ${name} — click to replace`;
    }

    function clearAttachment() {
        attachedFile = null;
        fileInput.value = '';
        attachBadge.hidden = true;
        attachBtn.classList.remove('has-attachment');
        attachBtn.title = 'Attach a text file';
    }

    attachBtn.addEventListener('click', () => fileInput.click());
    attachRemove.addEventListener('click', clearAttachment);

    if (newChatBtn) {
        newChatBtn.addEventListener('click', async () => {
            if (streaming) return;
            // Close the current session server-side before discarding it.
            if (sessionId) {
                try {
                    await fetch(`/api/sessions/${sessionId}/close`, { method: 'POST' });
                } catch (_) { /* non-fatal */ }
            }
            sessionId = null;
            clearAttachment();
            messageInput.value = '';
            messagesDiv.innerHTML = '<p class="empty-state">New session started — send a message to begin.</p>';
            refreshContextMeter(agentSelect.value);
            messageInput.focus();
        });
    }

    fileInput.addEventListener('change', () => {
        const file = fileInput.files[0];
        if (!file) return;
        if (file.size > ATTACH_MAX_BYTES) {
            alert(`File too large (${(file.size / 1024).toFixed(0)} KB). Maximum is ${ATTACH_MAX_BYTES / 1024} KB.`);
            fileInput.value = '';
            return;
        }
        const reader = new FileReader();
        reader.onload = (e) => setAttachment(file.name, e.target.result);
        reader.onerror = () => alert('Could not read the file.');
        reader.readAsText(file, 'utf-8');
    });

    const LAST_AGENT_KEY = 'autobot.chat.lastAgentId';
    const HIDE_TOOL_KEY = 'autobot.chat.hideTool';
    const hideToolToggle = document.getElementById('hide-tool-toggle');

    const meterEl = document.getElementById('context-meter');
    const meterPctEl = document.getElementById('context-meter-pct');
    const meterFillEl = meterEl ? meterEl.querySelector('.context-meter-fill') : null;
    const meterDetailEl = document.getElementById('context-meter-detail');

    let sessionId = null;
    let streaming = false;
    let currentAgentName = '';
    let contextBudget = null;
    let abortController = null;

    function setStreamingUI(isStreaming) {
        streaming = isStreaming;
        sendBtn.hidden = isStreaming;
        stopBtn.hidden = !isStreaming;
        sendBtn.disabled = isStreaming;
        messageInput.disabled = isStreaming;
        attachBtn.disabled = isStreaming;
        if (newChatBtn) newChatBtn.disabled = isStreaming;
    }

    function formatTokens(n) {
        if (n == null) return '–';
        if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
        return String(n);
    }

    function renderContextMeter(totalTokens, budget) {
        if (!meterEl) return;
        if (!budget || totalTokens == null) {
            meterEl.hidden = true;
            return;
        }
        const pct = Math.min(100, Math.max(0, (totalTokens / budget) * 100));
        const state = pct >= 90 ? 'crit' : pct >= 70 ? 'warn' : 'ok';
        meterEl.hidden = false;
        meterEl.dataset.state = state;
        meterFillEl.style.width = pct.toFixed(1) + '%';
        meterPctEl.textContent = pct.toFixed(pct >= 10 ? 0 : 1) + '%';
        meterDetailEl.textContent = `${formatTokens(totalTokens)} / ${formatTokens(budget)} tokens`;
    }

    async function refreshContextMeter(agentId) {
        if (!agentId) {
            if (meterEl) meterEl.hidden = true;
            return;
        }
        try {
            const url = sessionId
                ? `/api/chat/context?agent_id=${agentId}&session_id=${sessionId}`
                : `/api/chat/context?agent_id=${agentId}`;
            const res = await fetch(url);
            if (!res.ok) return;
            const data = await res.json();
            contextBudget = data.budget;
            renderContextMeter(data.total_tokens, data.budget);
        } catch (_) {
            // Non-fatal — the meter simply won't update until the next turn.
        }
    }

    function applyHideTool() {
        messagesDiv.classList.toggle('hide-tool', hideToolToggle.checked);
    }

    hideToolToggle.checked = localStorage.getItem(HIDE_TOOL_KEY) === '1';
    applyHideTool();
    hideToolToggle.addEventListener('change', () => {
        localStorage.setItem(HIDE_TOOL_KEY, hideToolToggle.checked ? '1' : '0');
        applyHideTool();
    });

    async function loadHistory(agentId) {
        sessionId = null;
        messagesDiv.innerHTML = '';
        try {
            const res = await fetch(`/api/chat/history?agent_id=${agentId}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (data.session && data.messages && data.messages.length) {
                sessionId = data.session.id;
                for (const m of data.messages) {
                    renderPersistedMessage(m);
                }
            } else {
                messagesDiv.innerHTML = '<p class="empty-state">Start chatting with the agent.</p>';
            }
        } catch (err) {
            messagesDiv.innerHTML = `<p class="empty-state">Could not load history: ${err.message}</p>`;
        }
        scrollToBottom();
    }

    function renderPersistedMessage(m) {
        if (m.role === 'user' || m.role === 'assistant') {
            appendMessage(m.role, m.content, labelFor(m.role));
        } else if (m.role === 'tool' || m.role === 'system') {
            appendMessage('tool', m.content);
        }
    }

    if (window.marked && marked.setOptions) {
        marked.setOptions({ breaks: true, gfm: true });
    }

    function normalizeSpacing(text) {
        // Add a space after a sentence-ending period when immediately followed
        // by an uppercase letter (models often omit this space).
        return text.replace(/\.([A-ZÁÉÍÓÚÜÑÀÈÌÒÙÂÊÎÔÛÄËÏÖÜ])/g, '. $1');
    }

    function renderMarkdown(text) {
        if (!text) return '';
        const normalized = normalizeSpacing(text);
        if (!window.marked || !window.DOMPurify) {
            return escapeHtml(normalized);
        }
        try {
            return DOMPurify.sanitize(marked.parse(normalized));
        } catch (_) {
            return escapeHtml(normalized);
        }
    }

    function labelFor(role) {
        if (role === 'assistant') return currentAgentName || 'assistant';
        return role;
    }

    async function onAgentChange() {
        const agentId = agentSelect.value;
        const enabled = agentId !== '';
        messageInput.disabled = !enabled;
        sendBtn.disabled = !enabled;
        attachBtn.disabled = !enabled;
        if (newChatBtn) newChatBtn.disabled = !enabled;
        currentAgentName = enabled
            ? (agentSelect.options[agentSelect.selectedIndex].text || '').trim()
            : '';
        if (enabled) {
            localStorage.setItem(LAST_AGENT_KEY, agentId);
            await loadHistory(agentId);
            messageInput.focus();
            refreshContextMeter(agentId);
        } else {
            sessionId = null;
            messagesDiv.innerHTML = '<p class="empty-state">Select an agent and start chatting.</p>';
            if (meterEl) meterEl.hidden = true;
        }
    }

    agentSelect.addEventListener('change', onAgentChange);

    // Restore last-selected agent on page load
    const savedAgentId = localStorage.getItem(LAST_AGENT_KEY);
    if (savedAgentId && [...agentSelect.options].some(o => o.value === savedAgentId)) {
        agentSelect.value = savedAgentId;
        onAgentChange();
    }

    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const rawText = messageInput.value.trim();
        if (streaming || !agentSelect.value || (!rawText && !attachedFile)) return;

        // Build the full message: optional file block + user text
        let message = rawText;
        const fileSnapshot = attachedFile;
        if (fileSnapshot) {
            const ext = fileSnapshot.name.split('.').pop() || '';
            const fence = `\`\`\`${ext}`;
            const fileBlock = `[Attached: ${fileSnapshot.name}]\n${fence}\n${fileSnapshot.content}\n\`\`\``;
            message = rawText ? `${fileBlock}\n\n${rawText}` : fileBlock;
        }

        messageInput.value = '';
        clearAttachment();

        if (messagesDiv.querySelector('.empty-state')) {
            messagesDiv.innerHTML = '';
        }

        // Display user bubble: show filename badge + text separately for readability
        const displayText = fileSnapshot
            ? (rawText ? `📎 ${fileSnapshot.name}\n\n${rawText}` : `📎 ${fileSnapshot.name}`)
            : message;
        appendMessage('user', displayText);

        setStreamingUI(true);

        const assistantDiv = appendMessage('assistant', '', labelFor('assistant'));
        const contentSpan = assistantDiv.querySelector('.chat-msg-content');

        abortController = new AbortController();
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    agent_id: parseInt(agentSelect.value),
                    message: message,
                    session_id: sessionId
                }),
                signal: abortController.signal
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const data = line.slice(6);
                    if (!data) continue;

                    try {
                        const chunk = JSON.parse(data);
                        handleChunk(chunk, contentSpan);
                    } catch (err) {
                        // Skip malformed chunks
                    }
                }
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                contentSpan.textContent += '\n\n[stopped]';
            } else {
                // Network/connection error — the backend may have finished and
                // saved its response even though the stream was interrupted.
                // Show a friendly notice and try to recover from session history.
                const isNetworkErr = err.message === 'Load failed' ||
                    err.message === 'Failed to fetch' ||
                    err.message === 'NetworkError when attempting to fetch resource.';

                if (isNetworkErr) {
                    contentSpan.dataset.raw = (contentSpan.dataset.raw || '') +
                        '\n\n*Connection interrupted — checking if the response was saved…*';
                    contentSpan.innerHTML = renderMarkdown(contentSpan.dataset.raw);
                    scrollToBottom();
                    // Wait briefly then check session history for a saved reply.
                    await _recoverFromSession(assistantDiv, contentSpan);
                } else {
                    contentSpan.textContent += `\n[Error: ${err.message}]`;
                }
            }
        } finally {
            abortController = null;
        }

        setStreamingUI(false);
        messageInput.focus();
        scrollToBottom();
    });

    stopBtn.addEventListener('click', () => {
        if (abortController) {
            abortController.abort();
        }
    });

    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.dispatchEvent(new Event('submit'));
        }
    });

    async function _recoverFromSession(assistantDiv, contentSpan) {
        if (!sessionId || !agentSelect.value) return;
        // Poll up to 3 times (3 s apart) waiting for the backend to finish.
        for (let attempt = 0; attempt < 3; attempt++) {
            await new Promise(r => setTimeout(r, 3000));
            try {
                const res = await fetch(`/api/sessions/${sessionId}/messages`);
                if (!res.ok) continue;
                const msgs = await res.json();
                // Find the last assistant message after the last user message.
                let lastUserIdx = -1;
                for (let i = msgs.length - 1; i >= 0; i--) {
                    if (msgs[i].role === 'user') { lastUserIdx = i; break; }
                }
                const savedAssistant = msgs.slice(lastUserIdx + 1).find(m => m.role === 'assistant');
                if (savedAssistant && savedAssistant.content) {
                    // Replace the partial/error content with the saved response.
                    contentSpan.dataset.raw = savedAssistant.content;
                    contentSpan.innerHTML = renderMarkdown(savedAssistant.content);
                    // Append a subtle note so the user knows it was recovered.
                    const note = document.createElement('p');
                    note.style.cssText = 'font-size:11px;color:var(--fg-dim);margin-top:6px;';
                    note.textContent = '⚠ Connection was interrupted — response recovered from session.';
                    assistantDiv.appendChild(note);
                    scrollToBottom();
                    return;
                }
            } catch (_) {}
        }
        // Backend didn't finish in time — replace placeholder with clear message.
        contentSpan.dataset.raw = '';
        contentSpan.innerHTML = '';
        contentSpan.textContent = '[Connection lost — the backend may still be processing. Refresh the page to check, or resend your message.]';
        scrollToBottom();
    }

    function handleChunk(chunk, contentSpan) {
        switch (chunk.type) {
            case 'session':
                if (chunk.data && chunk.data.id) sessionId = chunk.data.id;
                break;

            case 'token':
                contentSpan.dataset.raw = (contentSpan.dataset.raw || '') + chunk.data;
                contentSpan.innerHTML = renderMarkdown(contentSpan.dataset.raw);
                scrollToBottom();
                break;

            case 'tool_call':
                appendMessage('tool', `Using tool: ${chunk.data.name}`);
                break;

            case 'tool_result':
                const resultText = typeof chunk.data.result === 'object'
                    ? JSON.stringify(chunk.data.result, null, 2)
                    : String(chunk.data.result);
                appendMessage('tool', `Result from ${chunk.data.tool}:\n${resultText}`);
                break;

            case 'error':
                contentSpan.textContent += `\n[Error: ${chunk.data}]`;
                break;

            case 'done':
                if (chunk.usage) {
                    const usage = chunk.usage;
                    appendMessage('tool', `Tokens: ${usage.input_tokens || 0} in / ${usage.output_tokens || 0} out`);
                    // Update the meter from the model's real input_tokens —
                    // that's exactly the size of the prompt we just sent.
                    if (usage.budget) {
                        contextBudget = usage.budget;
                        renderContextMeter(usage.input_tokens || 0, usage.budget);
                    }
                }
                break;
        }
    }

    function appendMessage(role, content, label) {
        const div = document.createElement('div');
        div.className = `chat-msg chat-msg-${role}`;
        const displayLabel = label || role;
        const useMarkdown = role === 'user' || role === 'assistant';
        const contentHtml = useMarkdown ? renderMarkdown(content) : escapeHtml(content);
        const contentClass = useMarkdown ? 'chat-msg-content md' : 'chat-msg-content';
        div.innerHTML = `
            <div class="chat-msg-role">${escapeHtml(displayLabel)}</div>
            <div class="${contentClass}"></div>
        `;
        const contentEl = div.querySelector('.chat-msg-content');
        if (useMarkdown) {
            contentEl.dataset.raw = content || '';
            contentEl.innerHTML = contentHtml;
        } else {
            contentEl.textContent = content || '';
        }
        messagesDiv.appendChild(div);
        scrollToBottom();
        return div;
    }

    function scrollToBottom() {
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
});
