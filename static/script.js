document.addEventListener('DOMContentLoaded', () => {
    // --- NUEVO: PANEL DE CONSOLA ADMIN (Oculto) ---
    const adminPanel = document.getElementById('admin-logs-panel');
    if (adminPanel) adminPanel.style.display = 'none';

    // Atajo: Ctrl + AltGr + 0 (En JS web: Ctrl + Alt + 0)
    document.addEventListener('keydown', function(event) {
        if (event.ctrlKey && event.altKey && event.key === '0') {
            event.preventDefault();
            
            // Validar credenciales
            const user = prompt("🛡️ Autenticación de Consola\nIngrese Usuario:");
            if (user === "ogmhabas") {
                const pass = prompt("🔑 Ingrese Contraseña:");
                if (pass === "blz-tadmin") {
                    if (adminPanel) {
                        adminPanel.style.display = 'flex';
                        addLog("Conexión segura establecida. Autenticado como: " + user);
                        addLog("Iniciando escucha de base de datos y logs del sistema...");
                    }
                } else {
                    alert("Acceso denegado: Contraseña incorrecta.");
                }
            } else if (user) {
                alert("Acceso denegado: Usuario no reconocido.");
            }
        }
    });

    function addLog(text) {
        const consoleOutput = document.getElementById('console-output');
        if (!consoleOutput) return;
        const timestamp = new Date().toLocaleTimeString();
        consoleOutput.innerHTML += `<div style="margin-bottom: 4px; border-bottom: 1px solid #111; padding-bottom: 2px;">[${timestamp}] <span style="color: #00ffcc;">${text}</span></div>`;
        consoleOutput.scrollTop = consoleOutput.scrollHeight;
    }


    // --- REFERENCIAS BASE ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');
    
    // HEADER REFERENCES
    const chatChannelName = document.getElementById('chat-channel-name');
    const chatUptime = document.getElementById('chat-uptime');
    let timerInterval;

    window._currentChannelId = null;
    // Expose via window so other scripts can read it
    Object.defineProperty(window, 'currentChannelId', { get: () => window._currentChannelId, set: v => { window._currentChannelId = v; } });
    let isFetching = false;
    let botName = null;
    let channelMap = {};
    let mentionCache = { users: {}, roles: {} };
    let lastMessageId = {};

    // --- INICIO ---
    // Fetch bot info used to mark bot-authored messages
    (async function fetchBotInfo(){
        try{
            const r = await fetch('/api/botinfo');
            if (r.ok){
                const j = await r.json();
                botName = j.name || null;
                window._botId = j.id || null;
            }
        }catch(e){ console.warn('botinfo fetch failed', e); }
    })();
    fetchChannels();
    setInterval(() => {
        if (!isFetching && currentChannelId) fetchMessages(false);
    }, 500);

    if(msgInput) {
        msgInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') window.sendMessage();
        });
        document.getElementById('send-btn').onclick = window.sendMessage;
    }

    // --- CANALES (CADENAS) ---
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();

            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach((ch, idx) => {
                const cName = ch.name || "Unknown";
                const cId = ch.id; 
                channelMap[cId] = cName;

                const link = document.createElement('div');
                link.className = 'channel-item';
                link.setAttribute('data-index', String(idx + 1).padStart(2, '0'));

                const nameSpan = document.createElement('span');
                nameSpan.className = 'ch-name';
                nameSpan.textContent = cName.toUpperCase();
                link.appendChild(nameSpan);

                link.onclick = () => {
                    currentChannelId = cId;
                    window._currentChannelId = cId;
                    document.querySelectorAll('.channel-item').forEach(b => b.classList.remove('active'));
                    link.classList.add('active');

                    chatChannelName.innerText = cName.toUpperCase();
                    startTimer();

                    chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">⚡</div><p class="feed-empty-title">Receiving signal...</p><p class="feed-empty-sub">信号受信中</p></div>';
                    fetchMessages(true);
                };

                channelList.appendChild(link);
            });

            // Update count badge
            const countEl = document.getElementById('channel-count');
            if (countEl) countEl.textContent = channels.length;
        } catch(e) { console.error(e); }
    }

    function startTimer() {
        if (timerInterval) clearInterval(timerInterval);
        let seconds = 0;
        chatUptime.innerText = "00:00";
        timerInterval = setInterval(() => {
            seconds++;
            const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
            const secs = (seconds % 60).toString().padStart(2, '0');
            chatUptime.innerText = `${mins}:${secs}`;
            // Color shift: green → amber → coral based on session length
            if (seconds < 300)       chatUptime.style.color = 'var(--teal)';
            else if (seconds < 900)  chatUptime.style.color = 'var(--amber)';
            else                     chatUptime.style.color = 'var(--coral)';
        }, 1000);
    }

    // --- MENSAJES (FUSED) ---
    async function fetchMessages(initial=false) {
        if (!currentChannelId) return;
        isFetching = true;
        // Remove optimistic messages (client-side placeholders) before loading real ones
        try {
            const now = Date.now();
            document.querySelectorAll('[data-optimistic="1"]').forEach(n => {
                const ts = parseInt(n.dataset.optimisticTs || '0', 10);
                // keep optimistic nodes for up to 5 seconds to avoid flicker; remove older ones
                if (ts && (now - ts) > 5000) n.remove();
            });
        } catch (e) {}

        // If we don't have the bot name yet, try fetching it so we can mark bot messages
        if (!botName) {
            try {
                const r = await fetch('/api/botinfo');
                if (r.ok) {
                    const j = await r.json(); botName = j.name || null;
                }
            } catch (e) { /* ignore */ }
        }

        try {
            // If initial, fetch full history; otherwise fetch only messages newer than lastMessageId
            let res;
            if (initial) {
                res = await fetch(`/api/messages?channel_id=${encodeURIComponent(currentChannelId)}&limit=1000`);
            } else {
                const since = encodeURIComponent(lastMessageId[currentChannelId] || 0);
                res = await fetch(`/api/messages?channel_id=${encodeURIComponent(currentChannelId)}&since_id=${since}`);
            }
            const msgs = await res.json();
            const isScrolledToBottom = (chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight) < 150;

            // Collect mention ids (users and roles) from messages to batch-resolve
            const userIds = new Set();
            const roleIds = new Set();
            const mentionUserRe = /<@!?(\d+)>/g;
            const mentionRoleRe = /<@&(\d+)>/g;
            msgs.forEach(m => {
                let m1; while ((m1 = mentionUserRe.exec(m.content || '')) !== null) userIds.add(m1[1]);
                let m2; while ((m2 = mentionRoleRe.exec(m.content || '')) !== null) roleIds.add(m2[1]);
            });

            // Request lookups for any ids not already cached
            const usersToLookup = Array.from(userIds).filter(id => !mentionCache.users[id]);
            const rolesToLookup = Array.from(roleIds).filter(id => !mentionCache.roles[id]);
            if ((usersToLookup.length > 0 || rolesToLookup.length > 0) && (usersToLookup.length + rolesToLookup.length) <= 100) {
                try {
                    const r = await fetch('/api/mention_lookup', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ users: usersToLookup, roles: rolesToLookup })
                    });
                    if (r.ok) {
                        const j = await r.json();
                                        if (j.users) Object.assign(mentionCache.users, j.users);
                                        if (j.roles) Object.assign(mentionCache.roles, j.roles);
                    }
                } catch (e) { console.warn('mention lookup failed', e); }
            }

            // If initial load, replace content; otherwise append messages
            if (initial) {
                chatFeed.innerHTML = '';
                if (!msgs || msgs.length === 0) {
                    chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">無</div><p class="feed-empty-title">No messages yet</p><p class="feed-empty-sub">信号なし</p></div>';
                } else {
                    msgs.forEach(msg => {
                        const message = document.createElement('div');
                        message.className = 'message';

                        if (botName && msg.author_id && String(msg.author_id) === String((window._botId || ''))) message.classList.add('msg-me');
                        else if (botName && msg.author_name === botName) message.classList.add('msg-me');

                        const rendered = renderDiscordContent(msg.content || '');

                        message.innerHTML = `
                                <div class="msg-header"><div class="msg-time">${formatTimestamp(msg.timestamp || '')}</div>
                                <div class="msg-author">${escapeHtml(msg.author_name || 'Unknown')}</div></div>
                                <div class="msg-content">${rendered}</div>
                            `;
                        // Ensure visibility (prevent unexpected hidden styles)
                        message.style.display = '';
                        message.style.visibility = 'visible';
                        // dedupe: skip if message with same id already present
                        if (msg.message_id) {
                            const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
                            if (existing) return; // skip this message
                            message.dataset.msgId = String(msg.message_id);
                        }
                        renderComponents(message, msg.components || null);
                        decorateMessage(message);
                        chatFeed.appendChild(message);

                        if (msg.message_id) { const prev = lastMessageId[currentChannelId] || '0'; lastMessageId[currentChannelId] = BigInt(String(msg.message_id)) > BigInt(prev) ? String(msg.message_id) : prev; }
                    });
                }

                if (isScrolledToBottom) chatFeed.scrollTop = chatFeed.scrollHeight;
            } else {
                // incremental append
                if (msgs && msgs.length > 0) {
                    msgs.forEach(msg => {
                        const message = document.createElement('div');
                        message.className = 'message';

                        if (botName && msg.author_id && String(msg.author_id) === String((window._botId || ''))) message.classList.add('msg-me');
                        else if (botName && msg.author_name === botName) message.classList.add('msg-me');

                        const rendered = renderDiscordContent(msg.content || '');
                        message.innerHTML = `
                            <div class="msg-header"><div class="msg-time">${formatTimestamp(msg.timestamp || '')}</div>
                            <div class="msg-author">${escapeHtml(msg.author_name || 'Unknown')}</div></div>
                            <div class="msg-content">${rendered}</div>
                        `;
                        message.style.display = '';
                        message.style.visibility = 'visible';
                        // dedupe before appending
                        if (msg.message_id) {
                            const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
                            if (existing) return;
                            message.dataset.msgId = String(msg.message_id);
                        }
                        renderComponents(message, msg.components || null);
                        decorateMessage(message);
                        chatFeed.appendChild(message);
                        chatFeed.scrollTop = chatFeed.scrollHeight;
                        // Subtle flash on the new message
                        requestAnimationFrame(() => {
                            message.style.setProperty('--flash', '1');
                            setTimeout(() => message.style.removeProperty('--flash'), 600);
                        });

                        if (msg.message_id) { const prev = lastMessageId[currentChannelId] || '0'; lastMessageId[currentChannelId] = BigInt(String(msg.message_id)) > BigInt(prev) ? String(msg.message_id) : prev; }
                    });
                }
            }

            // After rendering messages, try to enrich any unresolved mentions shown as @123 or @role:123
            try { enrichUnresolvedMentions(); } catch (e) { /* ignore */ }

        } catch(e) { console.error(e); }
        finally { isFetching = false; }
    }

    function formatLinks(text) {
        if (!text) return "";
        return text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
    }

    // Simple HTML escape
    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // Format ISO timestamp to "DD/MM HH:MM" in the browser's local timezone
    function formatTimestamp(ts) {
        if (!ts) return '——';
        try {
            // Discord/SQLite timestamps can arrive as "2024-01-15T20:30:00+00:00",
            // "2024-01-15 20:30:00", or already as ISO strings.
            // Normalise space-separated to T-separated so Date() parses correctly.
            const normalised = ts.replace(' ', 'T');
            const d = new Date(normalised);
            if (isNaN(d.getTime())) return ts.slice(0, 16) || '——';

            const now = new Date();
            const isToday =
                d.getDate()     === now.getDate()  &&
                d.getMonth()    === now.getMonth() &&
                d.getFullYear() === now.getFullYear();

            const locale = navigator.language || 'es-ES';
            const timeStr = d.toLocaleTimeString(locale, {
                hour: '2-digit', minute: '2-digit', hour12: false
            });

            if (isToday) return timeStr;

            const dateStr = d.toLocaleDateString(locale, {
                day: '2-digit', month: '2-digit'
            });
            return `${dateStr} ${timeStr}`;
        } catch (e) { return '——'; }
    }

    function stripHtml(html) {
        const tmp = document.createElement('div');
        tmp.innerHTML = html || '';
        return tmp.textContent || tmp.innerText || '';
    }

    function normalizeText(s) {
        return String(s || '').replace(/\s+/g, ' ').trim();
    }

    // Render a subset of Discord markdown and replace mentions
    function renderDiscordContent(text) {
        if (!text) return '';

        // Preserve code blocks first
        const codeBlockRe = /```([\s\S]*?)```/g;
        const codeBlocks = [];
        const textWithoutCode = text.replace(codeBlockRe, (m, p1) => {
            const idx = codeBlocks.push(p1) - 1;
            return `@@CODEBLOCK${idx}@@`;
        });

        // We'll replace mentions/channels/roles with placeholders first, then escape the whole string,
        // then substitute placeholders with safe HTML. This avoids issues where escaping would hide <@...> tokens.
        let working = textWithoutCode;

        // channel mentions -> placeholder
        working = working.replace(/<#(\d+)>/g, (m, id) => `@@CH_${id}@@`);
        // user mentions
        working = working.replace(/<@!?(\d+)>/g, (m, id) => `@@MU_${id}@@`);
        // role mentions
        working = working.replace(/<@&(\d+)>/g, (m, id) => `@@MR_${id}@@`);

        // Now escape the rest
        working = escapeHtml(working);

        // Inline code
        working = working.replace(/`([^`]+)`/g, '<code>$1</code>');
        // Bold
        working = working.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        // Underline
        working = working.replace(/__([^_]+)__/g, '<u>$1</u>');
        // Italic
        working = working.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        working = working.replace(/_([^_]+)_/g, '<em>$1</em>');
        // Strikethrough
        working = working.replace(/~~([^~]+)~~/g, '<del>$1</del>');
        // Spoiler
        working = working.replace(/\|\|([^|]+)\|\|/g, '<span class="spoiler">$1</span>');
        // Links
        working = formatLinks(working);

        // Restore placeholders with safe HTML
        working = working.replace(/@@CH_(\d+)@@/g, (m, id) => {
            const name = channelMap[id];
            return name ? `<span class="mention mention-channel">#${escapeHtml(name)}</span>` : `<span class="mention">#${escapeHtml(id)}</span>`;
        });

        working = working.replace(/@@MU_(\d+)@@/g, (m, id) => {
            const resolved = mentionCache.users[id];
            if (resolved) {
                const display = (typeof resolved === 'string') ? resolved : (resolved.display || `@${id}`);
                const tag = (typeof resolved === 'string') ? null : (resolved.tag || null);
                const title = tag ? ` title="${escapeHtml(tag)}"` : '';
                return `<span class="mention mention-user"${title}>${escapeHtml(display)}</span>`;
            }
            return `<span class="mention">@${escapeHtml(id)}</span>`;
        });

        working = working.replace(/@@MR_(\d+)@@/g, (m, id) => {
            const resolved = mentionCache.roles[id];
            return resolved ? `<span class="mention mention-role">${escapeHtml(resolved)}</span>` : `<span class="mention">@role:${escapeHtml(id)}</span>`;
        });

        // Restore code blocks (escape inner HTML)
        working = working.replace(/@@CODEBLOCK(\d+)@@/g, (m, idx) => {
            const src = codeBlocks[Number(idx)] || '';
            return `<pre><code>${escapeHtml(src)}</code></pre>`;
        });

        return working;
    }

    // Find unresolved mention placeholders in the DOM and batch-resolve them
    async function enrichUnresolvedMentions() {
        const unresolvedUsers = new Set();
        const unresolvedRoles = new Set();
        const els = Array.from(chatFeed.querySelectorAll('.mention'));

        els.forEach(el => {
            const t = el.textContent || '';
            const mUser = t.match(/^@(\d+)$/);
            const mRole = t.match(/^@role:(\d+)$/);
            if (mUser) unresolvedUsers.add(mUser[1]);
            else if (mRole) unresolvedRoles.add(mRole[1]);
        });

        const usersToLookup = Array.from(unresolvedUsers).filter(id => !mentionCache.users[id]);
        const rolesToLookup = Array.from(unresolvedRoles).filter(id => !mentionCache.roles[id]);
        if (usersToLookup.length === 0 && rolesToLookup.length === 0) return;

        // Mark elements as loading
        els.forEach(el => {
            const t = el.textContent || '';
            if (/^@\d+$/.test(t) || /^@role:\d+$/.test(t)) {
                el.classList.add('mention-loading');
                el.setAttribute('title', 'Resolving...');
            }
        });

        try {
            const r = await fetch('/api/mention_lookup', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ users: usersToLookup, roles: rolesToLookup })
            });
            if (!r.ok) return;
            const j = await r.json();
            if (j.users) Object.assign(mentionCache.users, j.users);
            if (j.roles) Object.assign(mentionCache.roles, j.roles);

            // Update DOM elements
            els.forEach(el => {
                const t = el.textContent || '';
                const mUser = t.match(/^@(\d+)$/);
                const mRole = t.match(/^@role:(\d+)$/);
                if (mUser) {
                    const id = mUser[1];
                    const rp = mentionCache.users[id];
                    if (rp) {
                        const display = (typeof rp === 'string') ? rp : (rp.display || `@${id}`);
                        const tag = (typeof rp === 'string') ? null : (rp.tag || null);
                        if (tag) el.setAttribute('title', tag);
                        else el.removeAttribute('title');
                        el.textContent = display;
                        el.classList.remove('mention-loading');
                        el.classList.add('mention-user');
                    } else {
                        el.classList.remove('mention-loading');
                        el.removeAttribute('title');
                    }
                } else if (mRole) {
                    const id = mRole[1];
                    const rp = mentionCache.roles[id];
                    if (rp) {
                        el.textContent = rp;
                        el.classList.remove('mention-loading');
                        el.classList.add('mention-role');
                    } else {
                        el.classList.remove('mention-loading');
                        el.removeAttribute('title');
                    }
                }
            });
        } catch (e) {
            console.warn('enrichUnresolvedMentions error', e);
            els.forEach(el => el.classList.remove('mention-loading'));
        }
    }

    // ═══════════════════════════════════════════════
    // DISCORD COMPONENTS (buttons from bots)
    // ═══════════════════════════════════════════════

    // Style labels for button types
    const BTN_STYLE = {
        1: 'btn-primary',    // Blurple
        2: 'btn-secondary',  // Grey
        3: 'btn-success',    // Green
        4: 'btn-danger',     // Red
        5: 'btn-link',       // URL
    };

    function renderComponents(msgEl, componentsJson) {
        if (!componentsJson) return;
        let rows;
        try { rows = JSON.parse(componentsJson); } catch(e) { return; }
        if (!rows || !rows.length) return;

        const container = document.createElement('div');
        container.className = 'msg-components';

        rows.forEach(row => {
            if (!row.components || !row.components.length) return;
            const rowEl = document.createElement('div');
            rowEl.className = 'comp-row';

            row.components.forEach(comp => {
                if (comp.type !== 2) return; // only buttons for now

                const btn = document.createElement('button');
                const styleClass = BTN_STYLE[comp.style] || 'btn-secondary';
                btn.className = 'comp-btn ' + styleClass;
                if (comp.disabled) btn.disabled = true;

                // Label + emoji
                if (comp.emoji) {
                    const em = document.createElement('span');
                    em.className = 'comp-btn-emoji';
                    em.textContent = comp.emoji.name || '';
                    btn.appendChild(em);
                }
                if (comp.label) {
                    const lbl = document.createElement('span');
                    lbl.textContent = comp.label;
                    btn.appendChild(lbl);
                }

                // Link button
                if (comp.style === 5 && comp.url) {
                    btn.onclick = () => window.open(comp.url, '_blank');
                } else if (comp.custom_id) {
                    btn.title = 'Pulsa este botón en Discord';
                    btn.classList.add('comp-btn--readonly');
                    btn.setAttribute('aria-disabled', 'true');
                    btn.onclick = (e) => {
                        e.preventDefault();
                        showToast('Abre Discord para pulsar este botón', 'info');
                    };
                }
                rowEl.appendChild(btn);
            });

            if (rowEl.children.length) container.appendChild(rowEl);
        });

        if (container.children.length) {
            const contentEl = msgEl.querySelector('.msg-content');
            if (contentEl) contentEl.after(container);
            else msgEl.appendChild(container);
        }
    }

    // ═══════════════════════════════════════════════
    // MESSAGE ACTIONS: edit / delete / react
    // ═══════════════════════════════════════════════

    const EMOJI_LIST = [
        '👍','👎','❤️','🔥','⚡','😂','😭','😤','💀','👀',
        '✅','❌','🎯','⚔️','🛡️','🏆','💪','🤝','🫡','💯',
        '🗡️','🔴','🔵','⭐','💥','🙏','😮','🤔','😈','🥶'
    ];

    let emojiPicker = null;
    let emojiTargetMsgId = null;
    let emojiTargetChannelId = null;

    function getEmojiPicker() {
        if (emojiPicker) return emojiPicker;
        emojiPicker = document.createElement('div');
        emojiPicker.id = 'emoji-picker';
        const pickerHeader = document.createElement('div');
        pickerHeader.className = 'emoji-picker-hdr';
        pickerHeader.textContent = 'React';
        emojiPicker.appendChild(pickerHeader);
        const grid = document.createElement('div');
        grid.className = 'emoji-grid';
        emojiPicker.appendChild(grid);
        EMOJI_LIST.forEach(emoji => {
            const btn = document.createElement('button');
            btn.className = 'emoji-btn';
            btn.textContent = emoji;
            btn.type = 'button';
            btn.addEventListener('click', (ev) => {
                ev.stopPropagation();
                ev.preventDefault();
                const targetId  = emojiTargetMsgId;
                const targetCh  = emojiTargetChannelId;
                closeEmojiPicker();
                if (targetId && targetCh) doReact(targetId, targetCh, emoji, btn);
            });
            grid.appendChild(btn);
        });
        document.body.appendChild(emojiPicker);
        // Close on outside click — use capture phase so it fires before anything else
        document.addEventListener('click', (ev) => {
            if (!emojiPicker || !emojiPicker.classList.contains('open')) return;
            if (!emojiPicker.contains(ev.target) && !ev.target.closest('.msg-action-btn')) {
                closeEmojiPicker();
            }
        }, true);
        return emojiPicker;
    }

    function openEmojiPicker(msgId, channelId, anchorEl) {
        emojiTargetMsgId     = msgId;
        emojiTargetChannelId = channelId;
        const picker = getEmojiPicker();

        // Smart positioning: above or below the anchor
        const rect    = anchorEl.getBoundingClientRect();
        const pickerH = 220; // approx height
        const pickerW = 280;
        const spaceAbove = rect.top;
        const spaceBelow = window.innerHeight - rect.bottom;

        picker.style.left = Math.min(
            Math.max(8, rect.left),
            window.innerWidth - pickerW - 8
        ) + 'px';

        if (spaceAbove > pickerH || spaceAbove > spaceBelow) {
            picker.style.top    = 'auto';
            picker.style.bottom = (window.innerHeight - rect.top + 6) + 'px';
        } else {
            picker.style.bottom = 'auto';
            picker.style.top    = (rect.bottom + 6) + 'px';
        }

        picker.classList.add('open');
    }

    function closeEmojiPicker() {
        if (emojiPicker) emojiPicker.classList.remove('open');
        emojiTargetMsgId     = null;
        emojiTargetChannelId = null;
    }

    async function doDelete(msgId, channelId, msgEl) {
        msgEl.style.opacity = '0.4';
        try {
            const r = await fetch('/api/messages/' + msgId + '/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: channelId })
            });
            const j = await r.json();
            if (r.ok) {
                addLog(`[ACTION] Mensaje borrado -> ID: ${msgId}`); // LOG ADDED
                msgEl.style.transition = 'opacity 200ms, max-height 200ms, padding 200ms';
                msgEl.style.opacity    = '0';
                msgEl.style.maxHeight  = '0';
                msgEl.style.overflow   = 'hidden';
                msgEl.style.padding    = '0';
                setTimeout(() => msgEl.remove(), 220);
            } else {
                msgEl.style.opacity = '1';
                showMsgError(msgEl, j.error || 'Error al borrar');
            }
        } catch(e) { msgEl.style.opacity = '1'; showMsgError(msgEl, 'Error de red'); }
    }

    async function doEdit(msgId, channelId, msgEl, newContent) {
        try {
            const r = await fetch('/api/messages/' + msgId + '/edit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: channelId, content: newContent })
            });
            const j = await r.json();
            if (r.ok) {
                addLog(`[ACTION] Mensaje editado -> ID: ${msgId}`); // LOG ADDED
                const contentEl = msgEl.querySelector('.msg-content');
                if (contentEl) {
                    contentEl.innerHTML = renderDiscordContent(newContent);
                    if (!contentEl.querySelector('.msg-edited')) {
                        const tag = document.createElement('span');
                        tag.className = 'msg-edited';
                        tag.textContent = ' (editado)';
                        tag.style.cssText = 'font-size:0.63rem;color:var(--dim);font-family:var(--f-mono);';
                        contentEl.appendChild(tag);
                    }
                }
                cancelEdit(msgEl);
            } else {
                showMsgError(msgEl, j.error || 'Error al editar');
            }
        } catch(e) { showMsgError(msgEl, 'Error de red'); }
    }

    async function doReact(msgId, channelId, emoji, originBtn) {
        // Flash the react button in the message
        const msgEl = chatFeed.querySelector('[data-msg-id="' + msgId + '"]');
        const reactBtn = msgEl ? msgEl.querySelector('.msg-action-btn') : null;
        if (reactBtn) reactBtn.textContent = emoji;

        try {
            const r = await fetch('/api/messages/' + msgId + '/react', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: channelId, emoji })
            });
            if (r.ok) {
                addLog(`[ACTION] Reacción enviada: ${emoji} -> ID: ${msgId}`); // LOG ADDED
                if (reactBtn) {
                    reactBtn.classList.add('react-sent');
                    setTimeout(() => {
                        reactBtn.textContent = '😊';
                        reactBtn.classList.remove('react-sent');
                    }, 1800);
                }
            } else {
                const j = await r.json();
                if (reactBtn) reactBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 13s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>';
                showToast(j.error || 'Error al reaccionar', 'error');
            }
        } catch(e) {
            if (reactBtn) reactBtn.textContent = '😊';
            showToast('Error de red', 'error');
        }
    }

    function openEdit(msgEl) {
        if (msgEl.querySelector('.msg-edit-wrap')) return;
        const contentEl = msgEl.querySelector('.msg-content');
        if (!contentEl) return;
        const rawText = contentEl.innerText || '';

        const wrap    = document.createElement('div');
        wrap.className = 'msg-edit-wrap';
        const ta      = document.createElement('textarea');
        ta.className   = 'msg-edit-input';
        ta.value       = rawText.replace(' (editado)', '');
        ta.rows        = Math.max(1, Math.min(6, (rawText.match(/\n/g) || []).length + 1));
        const saveBtn  = document.createElement('button');
        saveBtn.className = 'msg-edit-save';
        saveBtn.textContent = 'SAVE';
        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'msg-edit-cancel';
        cancelBtn.textContent = 'ESC';

        wrap.appendChild(ta);
        wrap.appendChild(saveBtn);
        wrap.appendChild(cancelBtn);
        contentEl.after(wrap);
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
        
        // Cierra al cancelar
        cancelBtn.onclick = () => cancelEdit(msgEl);
        
        // Guarda
        saveBtn.onclick = () => {
            const newContent = ta.value.trim();
            if (newContent !== '') {
                const msgId = msgEl.dataset.msgId;
                if (msgId && currentChannelId) {
                    doEdit(msgId, currentChannelId, msgEl, newContent);
                }
            } else {
                cancelEdit(msgEl);
            }
        }
    }

    function cancelEdit(msgEl) {
        const wrap = msgEl.querySelector('.msg-edit-wrap');
        if (wrap) wrap.remove();
    }
    
    function showMsgError(msgEl, errText) {
        const err = document.createElement('div');
        err.style.color = '#ff4444';
        err.style.fontSize = '12px';
        err.innerText = errText;
        msgEl.appendChild(err);
        setTimeout(() => err.remove(), 3000);
    }
    
    function showToast(message, type) {
        // Implementación dummy. Ya que usabas 'showToast' en el original, la declaro.
        console.log(`[TOAST] ${type.toUpperCase()}: ${message}`);
    }

    function decorateMessage(msgEl) {
        // Decorador final de herramientas para cada mensaje
        const actionsBox = document.createElement('div');
        actionsBox.className = 'msg-actions';
        actionsBox.style.cssText = 'position:absolute; top:4px; right:4px; display:none; background:rgba(0,0,0,0.8); padding:4px; border-radius:4px;';
        
        const btnEdit = document.createElement('button');
        btnEdit.innerText = '✏️';
        btnEdit.style.cssText = 'background:none; border:none; cursor:pointer; font-size:12px; margin-right:4px;';
        btnEdit.onclick = () => openEdit(msgEl);
        
        const btnDelete = document.createElement('button');
        btnDelete.innerText = '🗑️';
        btnDelete.style.cssText = 'background:none; border:none; cursor:pointer; font-size:12px; margin-right:4px;';
        btnDelete.onclick = () => {
            if (confirm('Delete this message?')) {
                const msgId = msgEl.dataset.msgId;
                if (msgId && currentChannelId) doDelete(msgId, currentChannelId, msgEl);
            }
        };

        const btnReact = document.createElement('button');
        btnReact.innerText = '😊';
        btnReact.className = 'msg-action-btn'; // crucial for close detection
        btnReact.style.cssText = 'background:none; border:none; cursor:pointer; font-size:12px;';
        btnReact.onclick = (e) => {
            const msgId = msgEl.dataset.msgId;
            if (msgId && currentChannelId) openEmojiPicker(msgId, currentChannelId, btnReact);
        };

        actionsBox.appendChild(btnEdit);
        actionsBox.appendChild(btnDelete);
        actionsBox.appendChild(btnReact);
        
        msgEl.style.position = 'relative';
        msgEl.appendChild(actionsBox);
        
        msgEl.onmouseenter = () => actionsBox.style.display = 'block';
        msgEl.onmouseleave = () => actionsBox.style.display = 'none';
    }

    // Export function to window
    window.sendMessage = async function() {
        if (!currentChannelId || !msgInput) return;
        const val = msgInput.value.trim();
        if (!val) return;
        
        msgInput.value = '';
        addLog(`[ACTION] Enviando mensaje a DB -> ${val}`);
        
        try {
            const r = await fetch('/api/messages/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: val })
            });
            if (!r.ok) {
                console.error('Send failed');
            }
        } catch(e) { console.error('Send error', e); }
    }
});
