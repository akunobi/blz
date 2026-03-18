document.addEventListener('DOMContentLoaded', () => {
    // --- REFERENCIAS ---
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

    let currentChannelId = null;
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
                    btn.onclick = async () => {
                        if (btn.disabled) return;
                        btn.classList.add('comp-btn--loading');
                        btn.disabled = true;
                        try {
                            const r = await fetch('/api/messages/' + msgEl.dataset.msgId + '/interact', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    channel_id: currentChannelId,
                                    custom_id: comp.custom_id
                                })
                            });
                            const j = await r.json();
                            if (!r.ok) showMsgError(msgEl, j.error || 'Error');
                        } catch(e) {
                            showMsgError(msgEl, 'Error de red');
                        } finally {
                            btn.classList.remove('comp-btn--loading');
                            btn.disabled = false;
                        }
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
                if (reactBtn) {
                    reactBtn.classList.add('react-sent');
                    setTimeout(() => {
                        reactBtn.textContent = '😊';
                        reactBtn.classList.remove('react-sent');
                    }, 1800);
                }
            } else {
                const j = await r.json();
                if (reactBtn) reactBtn.textContent = '😊';
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

        const msgId = msgEl.dataset.msgId;
        const chId  = currentChannelId;
        saveBtn.onclick   = () => { const v = ta.value.trim(); if (v) doEdit(msgId, chId, msgEl, v); else cancelEdit(msgEl); };
        cancelBtn.onclick = () => cancelEdit(msgEl);
        ta.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveBtn.click(); }
            else if (e.key === 'Escape') cancelEdit(msgEl);
        });
    }

    function cancelEdit(msgEl) {
        const w = msgEl.querySelector('.msg-edit-wrap');
        if (w) w.remove();
    }

    function showMsgError(msgEl, text) {
        let err = msgEl.querySelector('.msg-err');
        if (!err) {
            err = document.createElement('span');
            err.className = 'msg-err';
            err.style.cssText = 'font-size:0.63rem;color:var(--red);font-family:var(--f-mono);margin-top:2px;display:block;';
            msgEl.appendChild(err);
        }
        err.textContent = text;
        setTimeout(() => { if (err.parentNode) err.remove(); }, 3000);
    }

    // ── Toast notification ──
    let _toastTimer = null;
    function showToast(msg, type = 'info') {
        let toast = document.getElementById('blz-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'blz-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.className = 'blz-toast blz-toast--' + type + ' blz-toast--show';
        clearTimeout(_toastTimer);
        _toastTimer = setTimeout(() => toast.classList.remove('blz-toast--show'), 2800);
    }

    // ── Custom delete confirm modal ──
    let _deleteModal = null;
    function showDeleteConfirm(msgId, channelId, msgEl) {
        if (!_deleteModal) {
            _deleteModal = document.createElement('div');
            _deleteModal.id = 'delete-modal';
            _deleteModal.innerHTML = `
                <div class="del-modal-backdrop"></div>
                <div class="del-modal-card">
                    <div class="del-modal-icon">🗑️</div>
                    <div class="del-modal-title">Borrar mensaje</div>
                    <div class="del-modal-body">Esta acción es permanente y no se puede deshacer.</div>
                    <div class="del-modal-actions">
                        <button class="del-btn del-btn--cancel">Cancelar</button>
                        <button class="del-btn del-btn--confirm">Borrar</button>
                    </div>
                </div>
            `;
            document.body.appendChild(_deleteModal);
            _deleteModal.querySelector('.del-modal-backdrop').addEventListener('click', hideDeleteConfirm);
            _deleteModal.querySelector('.del-btn--cancel').addEventListener('click', hideDeleteConfirm);
        }

        // Store current target on the confirm button
        const confirmBtn = _deleteModal.querySelector('.del-btn--confirm');
        confirmBtn.onclick = () => {
            hideDeleteConfirm();
            doDelete(msgId, channelId, msgEl);
        };

        _deleteModal.classList.add('del-modal--open');
        requestAnimationFrame(() => _deleteModal.classList.add('del-modal--visible'));
    }

    function hideDeleteConfirm() {
        if (!_deleteModal) return;
        _deleteModal.classList.remove('del-modal--visible');
        setTimeout(() => _deleteModal.classList.remove('del-modal--open'), 220);
    }

    function decorateMessage(msgEl) {
        if (!msgEl.dataset.msgId || msgEl.querySelector('.msg-actions')) return;
        const isOwn = msgEl.classList.contains('msg-me');
        const actions = document.createElement('div');
        actions.className = 'msg-actions';

        const reactBtn = document.createElement('button');
        reactBtn.className = 'msg-action-btn';
        reactBtn.title     = 'Reaccionar';
        reactBtn.innerHTML = '😊';
        reactBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openEmojiPicker(msgEl.dataset.msgId, currentChannelId, reactBtn);
        });
        actions.appendChild(reactBtn);

        if (isOwn) {
            const editBtn = document.createElement('button');
            editBtn.className = 'msg-action-btn';
            editBtn.title     = 'Editar';
            editBtn.innerHTML = '✏️';
            editBtn.addEventListener('click', (e) => { e.stopPropagation(); openEdit(msgEl); });
            actions.appendChild(editBtn);

            const delBtn = document.createElement('button');
            delBtn.className = 'msg-action-btn del';
            delBtn.title     = 'Borrar';
            delBtn.innerHTML = '🗑️';
            delBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                showDeleteConfirm(msgEl.dataset.msgId, currentChannelId, msgEl);
            });
            actions.appendChild(delBtn);
        }

        // Insert inside msg-header so they appear inline on the right
        const header = msgEl.querySelector('.msg-header');
        if (header) {
            header.appendChild(actions);
        } else {
            msgEl.appendChild(actions);
        }
    }

    // ═══════════════════════════════════════════════
    // @MENTION AUTOCOMPLETE
    // ═══════════════════════════════════════════════
    let acMembers = [];
    let acRoles   = [];
    let acLoaded  = false;

    // Lazily fetch members+roles once
    async function ensureAcData() {
        if (acLoaded) return;
        try {
            const r = await fetch('/api/members');
            if (!r.ok) return;
            const j = await r.json();
            acMembers = j.members || [];
            acRoles   = j.roles   || [];
            acLoaded  = true;
        } catch(e) { /* silent */ }
    }

    // Popup element (created once, reused)
    let acBox = null;
    let acItems = [];
    let acIdx = -1;
    let acTriggerPos = -1;   // caret position of the @ sign

    function getAcBox() {
        if (acBox) return acBox;
        acBox = document.createElement('div');
        acBox.id = 'ac-box';
        document.body.appendChild(acBox);
        return acBox;
    }

    function hideAc() {
        const box = getAcBox();
        box.classList.remove('ac-open');
        acItems = [];
        acIdx   = -1;
        acTriggerPos = -1;
    }

    function renderAc(matches) {
        const box = getAcBox();
        box.innerHTML = '';
        acItems = matches;
        acIdx   = -1;
        if (!matches.length) { box.classList.remove('ac-open'); return; }

        matches.forEach((item, i) => {
            const el = document.createElement('div');
            el.className = 'ac-item';
            el.dataset.index = i;

            if (item.type === 'user') {
                const avatar = document.createElement('div');
                avatar.className = 'ac-avatar';
                if (item.avatar) {
                    const img = document.createElement('img');
                    img.src = item.avatar;
                    img.className = 'ac-avatar-img';
                    avatar.appendChild(img);
                } else {
                    avatar.textContent = (item.display || item.username || '?')[0].toUpperCase();
                }
                const info = document.createElement('div');
                info.className = 'ac-info';
                const name = document.createElement('span');
                name.className = 'ac-name';
                name.textContent = item.display;
                const handle = document.createElement('span');
                handle.className = 'ac-handle';
                handle.textContent = '@' + item.username;
                info.appendChild(name);
                info.appendChild(handle);
                el.appendChild(avatar);
                el.appendChild(info);
            } else {
                const dot = document.createElement('span');
                dot.className = 'ac-role-dot';
                dot.style.background = item.color;
                dot.style.boxShadow = '0 0 6px ' + item.color + '88';
                const label = document.createElement('span');
                label.className = 'ac-role-name';
                label.style.color = item.color;
                label.textContent = '@' + item.name;
                const tag = document.createElement('span');
                tag.className = 'ac-role-tag';
                tag.textContent = 'ROLE';
                el.appendChild(dot);
                el.appendChild(label);
                el.appendChild(tag);
            }

            el.addEventListener('mouseenter', () => setAcIdx(i));
            el.addEventListener('mousedown', (e) => { e.preventDefault(); applyAc(i); });
            box.appendChild(el);
        });

        box.classList.add('ac-open');

        // Position above the composer — full width of composer
        const inputRect = msgInput.getBoundingClientRect();
        const boxW = Math.min(460, inputRect.width - 20);
        box.style.width   = boxW + 'px';
        box.style.bottom  = (window.innerHeight - inputRect.top + 10) + 'px';
        box.style.left    = inputRect.left + 'px';
        box.style.top     = 'auto';
    }

    function setAcIdx(i) {
        const box = getAcBox();
        const els = box.querySelectorAll('.ac-item');
        els.forEach(el => el.classList.remove('ac-item--active'));
        acIdx = i;
        if (i >= 0 && i < els.length) {
            els[i].classList.add('ac-item--active');
            els[i].scrollIntoView({ block: 'nearest' });
        }
    }

    function applyAc(i) {
        if (i < 0 || i >= acItems.length) return;
        const item = acItems[i];
        const val  = msgInput.value;
        const tag  = item.type === 'user'
            ? `<@${item.id}> `
            : `<@&${item.id}> `;

        // Replace from @ trigger pos to current caret
        const before = val.slice(0, acTriggerPos);
        const after  = val.slice(msgInput.selectionStart);
        msgInput.value = before + tag + after;

        // Move caret after inserted tag
        const newPos = before.length + tag.length;
        msgInput.setSelectionRange(newPos, newPos);
        hideAc();
        msgInput.focus();
    }

    // Wire up input events
    msgInput.addEventListener('input', async () => {
        const val    = msgInput.value;
        const caret  = msgInput.selectionStart;

        // Find nearest @ before caret that isn't preceded by a word char
        let atPos = -1;
        for (let i = caret - 1; i >= 0; i--) {
            if (val[i] === '@') { atPos = i; break; }
            if (val[i] === ' ' || val[i] === '\n') break;
        }

        if (atPos === -1) { hideAc(); return; }

        const query = val.slice(atPos + 1, caret).toLowerCase();

        // Don't show popup if query has a space (mention already complete)
        if (query.includes(' ') && query.length > 0) { hideAc(); return; }

        acTriggerPos = atPos;
        await ensureAcData();

        const MAX = 8;
        let matches = [];

        // Roles first (max 4)
        const roleMatches = acRoles
            .filter(r => r.name.toLowerCase().includes(query))
            .slice(0, 4)
            .map(r => ({ ...r, type: 'role' }));

        // Members (fill remaining slots)
        const memberMatches = acMembers
            .filter(m =>
                m.display.toLowerCase().includes(query) ||
                m.username.toLowerCase().includes(query)
            )
            .slice(0, MAX - roleMatches.length)
            .map(m => ({ ...m, type: 'user' }));

        matches = [...roleMatches, ...memberMatches];
        renderAc(matches);
    });

    msgInput.addEventListener('keydown', (e) => {
        if (!acItems.length) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setAcIdx(Math.min(acIdx + 1, acItems.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setAcIdx(Math.max(acIdx - 1, 0));
        } else if (e.key === 'Tab' || e.key === 'Enter') {
            if (acItems.length) {
                e.preventDefault();
                applyAc(acIdx >= 0 ? acIdx : 0);
            }
        } else if (e.key === 'Escape') {
            hideAc();
        }
    });

    // Never close on blur — only close on: Escape, click outside, or successful selection
    // (blur fires before mousedown/click on the popup, so it would always close too early)
    msgInput.addEventListener('blur', () => {
        // Do nothing — hideAc is called explicitly where needed
    });

    window.addEventListener('scroll', hideAc, true);

    // Close autocomplete when clicking anywhere outside the box and input
    document.addEventListener('pointerdown', (e) => {
        if (!acBox) return;
        if (!acBox.classList.contains('ac-open')) return;
        if (acBox.contains(e.target)) return;      // click inside box → keep open
        if (e.target === msgInput) return;          // click on input → keep open
        hideAc();
    }, true);

    window.sendMessage = async () => {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;

        const originalPlaceholder = msgInput.placeholder;
        // Create optimistic UI element so message appears immediately
        let optimisticNode = null;
        let optimisticClientId = String(Date.now()) + Math.floor(Math.random()*1000);
        try {
            const author = botName || 'BOT';
            optimisticNode = document.createElement('div');
            optimisticNode.className = 'message msg-me';
            optimisticNode.setAttribute('data-optimistic', '1');
            optimisticNode.dataset.clientId = optimisticClientId;
            optimisticNode.dataset.optimisticTs = String(Date.now());
            optimisticNode.innerHTML = `
                <div class="msg-header"><div class="msg-time">——:——</div>
                <div class="msg-author">${author}</div></div>
                <div class="msg-content">${formatLinks(content)}</div>
            `;
            chatFeed.appendChild(optimisticNode);
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch (e) { optimisticNode = null; }

        msgInput.value = '';
        msgInput.placeholder = "送信中...";
        msgInput.disabled = true;

        try {
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Server Reject");
            // If server returned message_id, replace optimistic node immediately to avoid duplication
            if (data && data.message_id) {
                try {
                    const realMsg = {
                        message_id: data.message_id,
                        author_id: data.author_id,
                        author_name: data.author_name,
                        content: content,
                        timestamp: data.timestamp
                    };

                    // find optimistic node by clientId and replace
                    const opt = chatFeed.querySelector(`[data-optimistic="1"][data-client-id="${optimisticClientId}"]`);
                    const message = document.createElement('div');
                    message.className = 'message';
                    if (botName && realMsg.author_id && String(realMsg.author_id) === String((window._botId || ''))) message.classList.add('msg-me');
                    else if (botName && realMsg.author_name === botName) message.classList.add('msg-me');

                    message.dataset.msgId = String(realMsg.message_id);
                    message.innerHTML = `
                        <div class="msg-header"><div class="msg-time">${formatTimestamp(realMsg.timestamp || '')}</div>
                        <div class="msg-author">${escapeHtml(realMsg.author_name || 'Unknown')}</div></div>
                        <div class="msg-content">${renderDiscordContent(realMsg.content || '')}</div>
                    `;
                    message.style.display = '';
                    message.style.visibility = 'visible';

                    decorateMessage(message);
                    if (opt) opt.replaceWith(message);
                    else chatFeed.appendChild(message);
                    chatFeed.scrollTop = chatFeed.scrollHeight;
                } catch (e) { /* ignore UI replace errors */ }
            }
            // also schedule a fetch to ensure DB-synced messages are present
            setTimeout(() => fetchMessages(false), 200);
        } catch(e) {
            console.error("Send error:", e); msgInput.style.borderColor = "var(--red)"; setTimeout(() => msgInput.style.borderColor = "", 2000);
            // remove optimistic node if exists and restore content
            if (optimisticNode && optimisticNode.parentNode) optimisticNode.remove();
            msgInput.value = content;
        } finally {
            msgInput.placeholder = originalPlaceholder;
            msgInput.disabled = false;
            msgInput.focus();
        }
    };

    // --- ESTADÍSTICAS ---
    window.generateStats = () => {
        let type = 'offensive';
        if (document.getElementById('dvg').value.trim() !== "") type = 'gk';

        const inputs = type === 'offensive' ? ['sht', 'dbl', 'stl', 'psn', 'dfd'] : ['dvg', 'biq', 'rfx', 'dtg'];
        let data = {}, sum = 0, count = 0;

        inputs.forEach(id => {
            let val = parseFloat(document.getElementById(id).value) || 0;
            if (val > 10) val = 10; if (val < 0) val = 0;
            data[id] = val; sum += val; count++;
        });

        const avg = count > 0 ? sum / count : 0;
        let rank = type === 'offensive' ? getOffensiveRank(avg) : getGKRank(avg);

        drawGraph(type, data, avg, rank);
        
        if(modal) modal.classList.add('active');
        document.getElementById('stats-drawer').classList.remove('active');
    };

    function getOffensiveRank(s) {
        if (s < 4.6) return "N/A";
        if (s <= 4.8) return "ROOKIE 🥉 - ⭐";
        if (s <= 5.1) return "ROOKIE 🥉 - ⭐⭐";
        if (s <= 5.4) return "ROOKIE 🥉 - ⭐⭐⭐";
        if (s <= 5.7) return "AMATEUR ⚽ - ⭐";
        if (s <= 6.0) return "AMATEUR ⚽ - ⭐⭐";
        if (s <= 6.3) return "AMATEUR ⚽ - ⭐⭐⭐";
        if (s <= 6.6) return "ELITE ⚡ - ⭐";
        if (s <= 6.9) return "ELITE ⚡ - ⭐⭐";
        if (s <= 7.2) return "ELITE ⚡ - ⭐⭐⭐";
        if (s <= 7.5) return "PRODIGY 🏅 - ⭐";
        if (s <= 7.8) return "PRODIGY 🏅 - ⭐⭐";
        if (s <= 8.1) return "PRODIGY 🏅 - ⭐⭐⭐";
        if (s <= 8.4) return "NEW GEN XI - ⭐";
        if (s <= 8.7) return "NEW GEN XI - ⭐⭐";
        if (s <= 9.0) return "NEW GEN XI - ⭐⭐⭐";
        if (s <= 9.3) return "WORLD CLASS 👑 - ⭐";
        if (s <= 9.6) return "WORLD CLASS 👑 - ⭐⭐";
        return "WORLD CLASS 👑 - ⭐⭐⭐";
    }

    function getGKRank(s) {
        if (s <= 6.9) return "D TIER";
        if (s <= 7.9) return "C TIER";
        if (s <= 8.4) return "B TIER";
        if (s <= 8.9) return "A TIER";
        if (s <= 9.4) return "S TIER";
        return "S+ TIER";
    }

    function drawGraph(type, data, avg, rank) {
        const W = 500, H = 500;
        ctx.clearRect(0, 0, W, H);

        // Background
        ctx.fillStyle = '#0F0E17';
        ctx.fillRect(0, 0, W, H);

        // Soft radial glow in center
        const isGK = type === 'gk';
        const mainCol  = isGK ? '#A78BFA' : '#F5A623';
        const fillCol  = isGK ? 'rgba(167,139,250,0.25)' : 'rgba(245,166,35,0.22)';
        const glowCol  = isGK ? 'rgba(167,139,250,0.55)' : 'rgba(245,166,35,0.50)';
        const secCol   = isGK ? '#26C9B8' : '#FF6B6B';

        const CX = 250, CY = 248, R = 132;
        const grd = ctx.createRadialGradient(CX, CY, 0, CX, CY, R * 1.3);
        grd.addColorStop(0, isGK ? 'rgba(167,139,250,0.06)' : 'rgba(245,166,35,0.06)');
        grd.addColorStop(1, 'transparent');
        ctx.fillStyle = grd;
        ctx.fillRect(0, 0, W, H);

        // Scanlines subtle
        ctx.save();
        for (let y = 0; y < H; y += 3) {
            ctx.fillStyle = 'rgba(0,0,0,0.08)';
            ctx.fillRect(0, y, W, 1);
        }
        ctx.restore();

        const keys = Object.keys(data);
        const total = keys.length;
        const step = (Math.PI * 2) / total;

        // Grid rings
        for (let l = 1; l <= 4; l++) {
            const rad = (R / 4) * l;
            const alpha = l === 4 ? 0.30 : 0.08;
            ctx.beginPath();
            if (isGK) {
                ctx.arc(CX, CY, rad, 0, Math.PI * 2);
            } else {
                for (let i = 0; i <= total; i++) {
                    const a = i * step - Math.PI / 2;
                    i === 0
                        ? ctx.moveTo(CX + Math.cos(a) * rad, CY + Math.sin(a) * rad)
                        : ctx.lineTo(CX + Math.cos(a) * rad, CY + Math.sin(a) * rad);
                }
            }
            ctx.strokeStyle = isGK ? `rgba(167,139,250,${alpha})` : `rgba(245,166,35,${alpha})`;
            ctx.lineWidth = l === 4 ? 1.5 : 0.8;
            ctx.stroke();
        }

        // Spokes
        if (!isGK) {
            keys.forEach((_, i) => {
                const a = i * step - Math.PI / 2;
                ctx.beginPath();
                ctx.moveTo(CX, CY);
                ctx.lineTo(CX + Math.cos(a) * R, CY + Math.sin(a) * R);
                ctx.strokeStyle = isGK ? 'rgba(167,139,250,0.10)' : 'rgba(245,166,35,0.10)';
                ctx.lineWidth = 0.8;
                ctx.stroke();
            });
        }

        // Data polygon
        ctx.beginPath();
        keys.forEach((k, i) => {
            const rad = (data[k] / 10) * R;
            const a = i * step - Math.PI / 2;
            i === 0
                ? ctx.moveTo(CX + Math.cos(a) * rad, CY + Math.sin(a) * rad)
                : ctx.lineTo(CX + Math.cos(a) * rad, CY + Math.sin(a) * rad);
        });
        ctx.closePath();
        ctx.shadowBlur = 22; ctx.shadowColor = glowCol;
        ctx.fillStyle = fillCol; ctx.fill();
        ctx.strokeStyle = mainCol; ctx.lineWidth = 2.5; ctx.stroke();
        ctx.shadowBlur = 0;

        // Vertex dots
        keys.forEach((k, i) => {
            const rad = (data[k] / 10) * R;
            const a = i * step - Math.PI / 2;
            const x = CX + Math.cos(a) * rad, y = CY + Math.sin(a) * rad;
            ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2);
            ctx.shadowBlur = 10; ctx.shadowColor = glowCol;
            ctx.fillStyle = mainCol + 'CC'; ctx.fill();
            ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2);
            ctx.fillStyle = '#FFFFFF'; ctx.fill();
            ctx.shadowBlur = 0;
        });

        // Labels
        keys.forEach((k, i) => {
            const a = i * step - Math.PI / 2;
            const lx = CX + Math.cos(a) * (R + 36);
            const ly = CY + Math.sin(a) * (R + 36);
            ctx.save();
            ctx.font = "700 15px 'Sora', sans-serif";
            ctx.fillStyle = mainCol;
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.shadowBlur = 6; ctx.shadowColor = glowCol;
            ctx.fillText(k.toUpperCase(), lx, ly);
            ctx.restore();
        });

        // Divider
        const grad = ctx.createLinearGradient(60, 0, 440, 0);
        grad.addColorStop(0, 'transparent');
        grad.addColorStop(0.3, mainCol + '66');
        grad.addColorStop(0.7, secCol + '66');
        grad.addColorStop(1, 'transparent');
        ctx.beginPath(); ctx.moveTo(60, 432); ctx.lineTo(440, 432);
        ctx.strokeStyle = grad; ctx.lineWidth = 1; ctx.stroke();

        // AVG
        ctx.save();
        ctx.font = "400 12px 'JetBrains Mono', monospace";
        ctx.fillStyle = '#6B6480'; ctx.textAlign = 'center';
        ctx.fillText('AVG  ' + avg.toFixed(2) + '  /  10', CX, 449);
        ctx.restore();

        // Rank
        ctx.save();
        ctx.shadowBlur = 20; ctx.shadowColor = glowCol;
        ctx.font = "800 24px 'Sora', sans-serif";
        ctx.fillStyle = mainCol; ctx.textAlign = 'center';
        ctx.fillText(rank, CX, 482);
        ctx.restore();

        try { window.latestStatsRank = String(rank || ''); } catch(e) {}
    }

    const closeBtn = document.getElementById('close-modal');
    if(closeBtn) closeBtn.onclick = () => {
        modal.classList.remove('active');
        document.getElementById('stats-drawer').classList.add('active');
    };

    const copyBtn = document.getElementById('copy-stats-btn');
    if(copyBtn) copyBtn.onclick = () => {
        canvas.toBlob(blob => {
            try {
                const item = new ClipboardItem({ "image/png": blob });
                navigator.clipboard.write([item]).then(() => {
                    const p = copyBtn.innerText; copyBtn.innerText = "SEALED";
                    setTimeout(() => copyBtn.innerText = p, 2000);
                });
            } catch (e) { alert("Right click to save"); }
        });
    };

    const copyRankBtn = document.getElementById('copy-rank-btn');
    if (copyRankBtn) copyRankBtn.onclick = async () => {
        try {
            const rankText = window.latestStatsRank || '';
            if (!rankText) {
                copyRankBtn.innerText = 'NO RANK';
                setTimeout(() => copyRankBtn.innerText = 'COPY RANK', 1400);
                return;
            }
            await navigator.clipboard.writeText(rankText);
            const prev = copyRankBtn.innerText;
            copyRankBtn.innerText = 'COPIED';
            setTimeout(() => copyRankBtn.innerText = prev, 1400);
        } catch (e) {
            alert('Copy failed: ' + (e && e.message ? e.message : e));
        }
    };

    // Close stats drawer or stats modal with Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' || e.key === 'Esc') {
            const drawer = document.getElementById('stats-drawer');
            const modalElem = document.getElementById('stats-modal');

            if (modalElem && modalElem.classList.contains('active')) {
                modalElem.classList.remove('active');
                if (drawer) drawer.classList.remove('active');
                return;
            }

            if (drawer && drawer.classList.contains('active')) {
                drawer.classList.remove('active');
            }
        }
    });
});
