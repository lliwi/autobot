document.addEventListener('DOMContentLoaded', () => {
    const agentSelect = document.getElementById('agent-select');
    const messageInput = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const chatForm = document.getElementById('chat-form');
    const messagesDiv = document.getElementById('messages');

    const LAST_AGENT_KEY = 'autobot.chat.lastAgentId';
    const HIDE_TOOL_KEY = 'autobot.chat.hideTool';
    const hideToolToggle = document.getElementById('hide-tool-toggle');

    let sessionId = null;
    let streaming = false;
    let currentAgentName = '';

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

    function labelFor(role) {
        if (role === 'assistant') return currentAgentName || 'assistant';
        return role;
    }

    async function onAgentChange() {
        const agentId = agentSelect.value;
        const enabled = agentId !== '';
        messageInput.disabled = !enabled;
        sendBtn.disabled = !enabled;
        currentAgentName = enabled
            ? (agentSelect.options[agentSelect.selectedIndex].text || '').trim()
            : '';
        if (enabled) {
            localStorage.setItem(LAST_AGENT_KEY, agentId);
            await loadHistory(agentId);
            messageInput.focus();
        } else {
            sessionId = null;
            messagesDiv.innerHTML = '<p class="empty-state">Select an agent and start chatting.</p>';
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
        if (streaming || !agentSelect.value || !messageInput.value.trim()) return;

        const message = messageInput.value.trim();
        messageInput.value = '';

        if (messagesDiv.querySelector('.empty-state')) {
            messagesDiv.innerHTML = '';
        }

        appendMessage('user', message);

        streaming = true;
        sendBtn.disabled = true;
        messageInput.disabled = true;

        const assistantDiv = appendMessage('assistant', '', labelFor('assistant'));
        const contentSpan = assistantDiv.querySelector('.chat-msg-content');

        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    agent_id: parseInt(agentSelect.value),
                    message: message,
                    session_id: sessionId
                })
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
            contentSpan.textContent += `\n[Error: ${err.message}]`;
        }

        streaming = false;
        sendBtn.disabled = false;
        messageInput.disabled = false;
        messageInput.focus();
        scrollToBottom();
    });

    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.dispatchEvent(new Event('submit'));
        }
    });

    function handleChunk(chunk, contentSpan) {
        switch (chunk.type) {
            case 'session':
                if (chunk.data && chunk.data.id) sessionId = chunk.data.id;
                break;

            case 'token':
                contentSpan.textContent += chunk.data;
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
                }
                break;
        }
    }

    function appendMessage(role, content, label) {
        const div = document.createElement('div');
        div.className = `chat-msg chat-msg-${role}`;
        const displayLabel = label || role;
        div.innerHTML = `
            <div class="chat-msg-role">${escapeHtml(displayLabel)}</div>
            <div class="chat-msg-content">${escapeHtml(content)}</div>
        `;
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
