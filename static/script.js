// static/script.js - BLZ-T Frontend con Panel de Logs y Slash Commands UI

document.addEventListener('DOMContentLoaded', () => {
    // --- REFERENCIAS ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas ? canvas.getContext('2d') : null;

    const chatChannelName = document.getElementById('chat-channel-name');
    const chatUptime = document.getElementById('chat-uptime');
    let timerInterval;

    window._currentChannelId = null;
    Object.defineProperty(window, 'currentChannelId', {
        get: () => window._currentChannelId,
        set: v => { window._currentChannelId = v; }
    });

    let isFetching = false;
    let botName = null;
    let channelMap = {};
    let mentionCache = { users: {}, roles: {} };
    let lastMessageId = {};

    // --- INICIALIZACIÓN ---
    (async function fetchBotInfo() {
        try {
            const r = await fetch('/api/botinfo');
            if (r.ok) {
                const j = await r.json();
                botName = j.name || null;
                window._botId = j.id || null;
            }
        } catch (e) { console.warn('botinfo fetch failed', e); }
    })();

    fetchChannels();
    setInterval(() => {
        if (!isFetching && currentChannelId) fetchMessages(false);
    }, 500);

    if (msgInput) {
        msgInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') window.sendMessage();
        });
        document.getElementById('send-btn').onclick = window.sendMessage;
    }

    // --- CANALES ---
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
            const countEl = document.getElementById('channel-count');
            if (countEl) countEl.textContent = channels.length;
        } catch (e) { console.error(e); }
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
            if (seconds < 300) chatUptime.style.color = 'var(--teal)';
            else if (seconds < 900) chatUptime.style.color = 'var(--amber)';
            else chatUptime.style.color = 'var(--coral)';
        }, 1000);
    }

    // --- MENSAJES ---
    async function fetchMessages(initial = false) {
        if (!currentChannelId) return;
        isFetching = true;
        try {
            const now = Date.now();
            document.querySelectorAll('[data-optimistic="1"]').forEach(n => {
                const ts = parseInt(n.dataset.optimisticTs || '0', 10);
                if (ts && (now - ts) > 5000) n.remove();
            });
        } catch (e) { }

        if (!botName) {
            try {
                const r = await fetch('/api/botinfo');
                if (r.ok) { const j = await r.json(); botName = j.name || null; }
            } catch (e) { }
        }

        try {
            let res;
            if (initial) {
                res = await fetch(`/api/messages?channel_id=${encodeURIComponent(currentChannelId)}&limit=1000`);
            } else {
                const since = encodeURIComponent(lastMessageId[currentChannelId] || 0);
                res = await fetch(`/api/messages?channel_id=${encodeURIComponent(currentChannelId)}&since_id=${since}`);
            }
            const msgs = await res.json();
            const isScrolledToBottom = (chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight) < 150;

            const userIds = new Set();
            const roleIds = new Set();
            const mentionUserRe = /<@!?(\d+)>/g;
            const mentionRoleRe = /<@&(\d+)>/g;
            msgs.forEach(m => {
                let m1; while ((m1 = mentionUserRe.exec(m.content || '')) !== null) userIds.add(m1[1]);
                let m2; while ((m2 = mentionRoleRe.exec(m.content || '')) !== null) roleIds.add(m2[1]);
            });

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
                        message.style.display = '';
                        message.style.visibility = 'visible';
                        if (msg.message_id) {
                            const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
                            if (existing) return;
                            message.dataset.msgId = String(msg.message_id);
                        }
                        renderComponents(message, msg.components || null);
                        decorateMessage(message);
                        chatFeed.appendChild(message);
                        if (msg.message_id) {
                            const prev = lastMessageId[currentChannelId] || '0';
                            lastMessageId[currentChannelId] = BigInt(String(msg.message_id)) > BigInt(prev) ? String(msg.message_id) : prev;
                        }
                    });
                }
                if (isScrolledToBottom) chatFeed.scrollTop = chatFeed.scrollHeight;
            } else {
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
                        if (msg.message_id) {
                            const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
                            if (existing) return;
                            message.dataset.msgId = String(msg.message_id);
                        }
                        renderComponents(message, msg.components || null);
                        decorateMessage(message);
                        chatFeed.appendChild(message);
                        chatFeed.scrollTop = chatFeed.scrollHeight;
                        requestAnimationFrame(() => {
                            message.style.setProperty('--flash', '1');
                            setTimeout(() => message.style.removeProperty('--flash'), 600);
                        });
                        if (msg.message_id) {
                            const prev = lastMessageId[currentChannelId] || '0';
                            lastMessageId[currentChannelId] = BigInt(String(msg.message_id)) > BigInt(prev) ? String(msg.message_id) : prev;
                        }
                    });
                }
            }
            try { enrichUnresolvedMentions(); } catch (e) { }
        } catch (e) { console.error(e); }
        finally { isFetching = false; }
    }

    function formatLinks(text) {
        if (!text) return "";
        return text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
    }

    function escapeHtml(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function formatTimestamp(ts) {
        if (!ts) return '——';
        try {
            const normalised = ts.replace(' ', 'T');
            const d = new Date(normalised);
            if (isNaN(d.getTime())) return ts.slice(0, 16) || '——';
            const now = new Date();
            const isToday = d.getDate() === now.getDate() && d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
            const locale = navigator.language || 'es-ES';
            const timeStr = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false });
            if (isToday) return timeStr;
            const dateStr = d.toLocaleDateString(locale, { day: '2-digit', month: '2-digit' });
            return `${dateStr} ${timeStr}`;
        } catch (e) { return '——'; }
    }

    function renderDiscordContent(text) {
        if (!text) return '';
        const codeBlockRe = /```([\s\S]*?)```/g;
        const codeBlocks = [];
        const textWithoutCode = text.replace(codeBlockRe, (m, p1) => {
            const idx = codeBlocks.push(p1) - 1;
            return `@@CODEBLOCK${idx}@@`;
        });
        let working = textWithoutCode;
        working = working.replace(/<#(\d+)>/g, (m, id) => `@@CH_${id}@@`);
        working = working.replace(/<@!?(\d+)>/g, (m, id) => `@@MU_${id}@@`);
        working = working.replace(/<@&(\d+)>/g, (m, id) => `@@MR_${id}@@`);
        working = escapeHtml(working);
        working = working.replace(/`([^`]+)`/g, '<code>$1</code>');
        working = working.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        working = working.replace(/__([^_]+)__/g, '<u>$1</u>');
        working = working.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        working = working.replace(/_([^_]+)_/g, '<em>$1</em>');
        working = working.replace(/~~([^~]+)~~/g, '<del>$1</del>');
        working = working.replace(/\|\|([^|]+)\|\|/g, '<span class="spoiler">$1</span>');
        working = formatLinks(working);
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
        working = working.replace(/@@CODEBLOCK(\d+)@@/g, (m, idx) => {
            const src = codeBlocks[Number(idx)] || '';
            return `<pre><code>${escapeHtml(src)}</code></pre>`;
        });
        return working;
    }

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

    // --- COMPONENTES DE DISCORD (botones) ---
    const BTN_STYLE = { 1: 'btn-primary', 2: 'btn-secondary', 3: 'btn-success', 4: 'btn-danger', 5: 'btn-link' };
    function renderComponents(msgEl, componentsJson) {
        if (!componentsJson) return;
        let rows;
        try { rows = JSON.parse(componentsJson); } catch (e) { return; }
        if (!rows || !rows.length) return;
        const container = document.createElement('div');
        container.className = 'msg-components';
        rows.forEach(row => {
            if (!row.components || !row.components.length) return;
            const rowEl = document.createElement('div');
            rowEl.className = 'comp-row';
            row.components.forEach(comp => {
                if (comp.type !== 2) return;
                const btn = document.createElement('button');
                const styleClass = BTN_STYLE[comp.style] || 'btn-secondary';
                btn.className = 'comp-btn ' + styleClass;
                if (comp.disabled) btn.disabled = true;
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
                if (comp.style === 5 && comp.url) {
                    btn.onclick = () => window.open(comp.url, '_blank');
                } else if (comp.custom_id) {
                    btn.title = 'Pulsa este botón en Discord';
                    btn.classList.add('comp-btn--readonly');
                    btn.setAttribute('aria-disabled', 'true');
                    btn.onclick = (e) => { e.preventDefault(); showToast('Abre Discord para pulsar este botón', 'info'); };
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

    // --- ACCIONES DE MENSAJE (editar/borrar/reaccionar) ---
    const EMOJI_LIST = ['👍','👎','❤️','🔥','⚡','😂','😭','😤','💀','👀','✅','❌','🎯','⚔️','🛡️','🏆','💪','🤝','🫡','💯','🗡️','🔴','🔵','⭐','💥','🙏','😮','🤔','😈','🥶'];
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
                const targetId = emojiTargetMsgId;
                const targetCh = emojiTargetChannelId;
                closeEmojiPicker();
                if (targetId && targetCh) doReact(targetId, targetCh, emoji, btn);
            });
            grid.appendChild(btn);
        });
        document.body.appendChild(emojiPicker);
        document.addEventListener('click', (ev) => {
            if (!emojiPicker || !emojiPicker.classList.contains('open')) return;
            if (!emojiPicker.contains(ev.target) && !ev.target.closest('.msg-action-btn')) closeEmojiPicker();
        }, true);
        return emojiPicker;
    }

    function openEmojiPicker(msgId, channelId, anchorEl) {
        emojiTargetMsgId = msgId;
        emojiTargetChannelId = channelId;
        const picker = getEmojiPicker();
        const rect = anchorEl.getBoundingClientRect();
        const pickerH = 220, pickerW = 280;
        const spaceAbove = rect.top;
        const spaceBelow = window.innerHeight - rect.bottom;
        picker.style.left = Math.min(Math.max(8, rect.left), window.innerWidth - pickerW - 8) + 'px';
        if (spaceAbove > pickerH || spaceAbove > spaceBelow) {
            picker.style.top = 'auto';
            picker.style.bottom = (window.innerHeight - rect.top + 6) + 'px';
        } else {
            picker.style.bottom = 'auto';
            picker.style.top = (rect.bottom + 6) + 'px';
        }
        picker.classList.add('open');
    }

    function closeEmojiPicker() {
        if (emojiPicker) emojiPicker.classList.remove('open');
        emojiTargetMsgId = null;
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
                msgEl.style.opacity = '0';
                msgEl.style.maxHeight = '0';
                msgEl.style.overflow = 'hidden';
                msgEl.style.padding = '0';
                setTimeout(() => msgEl.remove(), 220);
            } else {
                msgEl.style.opacity = '1';
                showMsgError(msgEl, j.error || 'Error al borrar');
            }
        } catch (e) { msgEl.style.opacity = '1'; showMsgError(msgEl, 'Error de red'); }
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
        } catch (e) { showMsgError(msgEl, 'Error de red'); }
    }

    async function doReact(msgId, channelId, emoji, originBtn) {
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
                if (reactBtn) reactBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 13s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>';
                showToast(j.error || 'Error al reaccionar', 'error');
            }
        } catch (e) {
            if (reactBtn) reactBtn.textContent = '😊';
            showToast('Error de red', 'error');
        }
    }

    function openEdit(msgEl) {
        if (msgEl.querySelector('.msg-edit-wrap')) return;
        const contentEl = msgEl.querySelector('.msg-content');
        if (!contentEl) return;
        const rawText = contentEl.innerText || '';
        const wrap = document.createElement('div');
        wrap.className = 'msg-edit-wrap';
        const ta = document.createElement('textarea');
        ta.className = 'msg-edit-input';
        ta.value = rawText.replace(' (editado)', '');
        ta.rows = Math.max(1, Math.min(6, (rawText.match(/\n/g) || []).length + 1));
        const saveBtn = document.createElement('button');
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
        const chId = currentChannelId;
        saveBtn.onclick = () => { const v = ta.value.trim(); if (v) doEdit(msgId, chId, msgEl, v); else cancelEdit(msgEl); };
        cancelBtn.onclick = () => cancelEdit(msgEl);
        ta.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveBtn.click(); }
            else if (e.key === 'Escape') cancelEdit(msgEl);
        });
    }

    function cancelEdit(msgEl) { const w = msgEl.querySelector('.msg-edit-wrap'); if (w) w.remove(); }

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

    let _deleteModal = null;
    function showDeleteConfirm(msgId, channelId, msgEl) {
        if (!_deleteModal) {
            _deleteModal = document.createElement('div');
            _deleteModal.id = 'delete-modal';
            _deleteModal.innerHTML = `
                <div class="del-modal-backdrop"></div>
                <div class="del-modal-card">
                    <div class="del-modal-icon"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--coral)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg></div>
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
        const confirmBtn = _deleteModal.querySelector('.del-btn--confirm');
        confirmBtn.onclick = () => { hideDeleteConfirm(); doDelete(msgId, channelId, msgEl); };
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
        reactBtn.title = 'Reaccionar';
        reactBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 13s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>';
        reactBtn.addEventListener('click', (e) => { e.stopPropagation(); openEmojiPicker(msgEl.dataset.msgId, currentChannelId, reactBtn); });
        actions.appendChild(reactBtn);
        if (isOwn) {
            const editBtn = document.createElement('button');
            editBtn.className = 'msg-action-btn';
            editBtn.title = 'Editar';
            editBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
            editBtn.addEventListener('click', (e) => { e.stopPropagation(); openEdit(msgEl); });
            actions.appendChild(editBtn);
            const delBtn = document.createElement('button');
            delBtn.className = 'msg-action-btn del';
            delBtn.title = 'Borrar';
            delBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
            delBtn.addEventListener('click', (e) => { e.stopPropagation(); showDeleteConfirm(msgEl.dataset.msgId, currentChannelId, msgEl); });
            actions.appendChild(delBtn);
        }
        const header = msgEl.querySelector('.msg-header');
        if (header) header.appendChild(actions);
        else msgEl.appendChild(actions);
    }

    // --- AUTOCOMPLETADO DE MENCIONES ---
    let acMembers = [], acRoles = [], acLoaded = false;
    async function ensureAcData() {
        if (acLoaded && (acMembers.length > 0 || acRoles.length > 0)) return;
        try {
            const r = await fetch('/api/members');
            if (!r.ok) return;
            const j = await r.json();
            acMembers = j.members || [];
            acRoles = j.roles || [];
            acLoaded = acMembers.length > 0 || acRoles.length > 0;
        } catch (e) { }
    }

    let acBox = null, acItems = [], acIdx = -1, acTriggerPos = -1;
    function getAcBox() {
        if (acBox) return acBox;
        acBox = document.createElement('div');
        acBox.id = 'ac-box';
        acBox.setAttribute('role', 'listbox');
        acBox.addEventListener('mousedown', (e) => e.preventDefault());
        document.body.appendChild(acBox);
        return acBox;
    }
    function hideAc() {
        const box = getAcBox();
        box.classList.remove('ac-open');
        acItems = []; acIdx = -1; acTriggerPos = -1;
    }
    function renderAc(matches) {
        const box = getAcBox();
        box.innerHTML = '';
        acItems = matches;
        acIdx = -1;
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
                } else avatar.textContent = (item.display || item.username || '?')[0].toUpperCase();
                const info = document.createElement('div');
                info.className = 'ac-info';
                const name = document.createElement('span');
                name.className = 'ac-name';
                name.textContent = item.display;
                const handle = document.createElement('span');
                handle.className = 'ac-handle';
                handle.textContent = '@' + item.username;
                info.appendChild(name); info.appendChild(handle);
                el.appendChild(avatar); el.appendChild(info);
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
                el.appendChild(dot); el.appendChild(label); el.appendChild(tag);
            }
            el.addEventListener('mouseenter', () => setAcIdx(i));
            el.addEventListener('mousedown', (e) => { e.preventDefault(); e.stopPropagation(); });
            el.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); applyAc(i); });
            box.appendChild(el);
        });
        box.classList.add('ac-open');
        const inputRect = msgInput.getBoundingClientRect();
        const boxW = Math.min(460, inputRect.width - 20);
        box.style.width = boxW + 'px';
        box.style.bottom = (window.innerHeight - inputRect.top + 10) + 'px';
        box.style.left = inputRect.left + 'px';
        box.style.top = 'auto';
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
        const val = msgInput.value;
        const tag = item.type === 'user' ? `<@${item.id}> ` : `<@&${item.id}> `;
        const before = val.slice(0, acTriggerPos);
        const after = val.slice(msgInput.selectionStart);
        msgInput.value = before + tag + after;
        const newPos = before.length + tag.length;
        msgInput.setSelectionRange(newPos, newPos);
        hideAc();
        msgInput.focus();
    }

    msgInput.addEventListener('input', async () => {
        const val = msgInput.value;
        const caret = msgInput.selectionStart;
        let atPos = -1;
        for (let i = caret - 1; i >= 0; i--) {
            if (val[i] === '@') { atPos = i; break; }
            if (val[i] === ' ' || val[i] === '\n') break;
        }
        if (atPos === -1) { hideAc(); return; }
        const query = val.slice(atPos + 1, caret).toLowerCase();
        if (query.includes(' ') && query.length > 0) { hideAc(); return; }
        acTriggerPos = atPos;
        await ensureAcData();
        const MAX = 10;
        let matches = [];
        const roleMatches = acRoles.filter(r => !query || r.name.toLowerCase().includes(query)).slice(0, query ? 4 : 3).map(r => ({ ...r, type: 'role' }));
        const memberMatches = acMembers.filter(m => !query || m.display.toLowerCase().includes(query) || m.username.toLowerCase().includes(query)).slice(0, MAX - roleMatches.length).map(m => ({ ...m, type: 'user' }));
        matches = [...roleMatches, ...memberMatches];
        if (!matches.length && query.length > 0) { hideAc(); return; }
        if (!matches.length) { hideAc(); return; }
        renderAc(matches);
    });

    msgInput.addEventListener('keydown', (e) => {
        if (!acItems.length) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); setAcIdx(Math.min(acIdx + 1, acItems.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setAcIdx(Math.max(acIdx - 1, 0)); }
        else if (e.key === 'Tab' || e.key === 'Enter') { if (acItems.length) { e.preventDefault(); applyAc(acIdx >= 0 ? acIdx : 0); } }
        else if (e.key === 'Escape') hideAc();
    });

    document.addEventListener('pointerdown', (e) => {
        if (!acBox || !acBox.classList.contains('ac-open')) return;
        if (acBox.contains(e.target)) return;
        if (msgInput.contains(e.target)) return;
        hideAc();
    });

    // --- ENVIAR MENSAJE ---
    window.sendMessage = async () => {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;
        const originalPlaceholder = msgInput.placeholder;
        let optimisticNode = null;
        let optimisticClientId = String(Date.now()) + Math.floor(Math.random() * 1000);
        try {
            const author = botName || 'BOT';
            optimisticNode = document.createElement('div');
            optimisticNode.className = 'message msg-me';
            optimisticNode.setAttribute('data-optimistic', '1');
            optimisticNode.dataset.clientId = optimisticClientId;
            optimisticNode.dataset.optimisticTs = String(Date.now());
            optimisticNode.innerHTML = `<div class="msg-header"><div class="msg-time">——:——</div><div class="msg-author">${author}</div></div><div class="msg-content">${formatLinks(content)}</div>`;
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
            if (data && data.message_id) {
                try {
                    const realMsg = { message_id: data.message_id, author_id: data.author_id, author_name: data.author_name, content: content, timestamp: data.timestamp };
                    const opt = chatFeed.querySelector(`[data-optimistic="1"][data-client-id="${optimisticClientId}"]`);
                    const message = document.createElement('div');
                    message.className = 'message';
                    if (botName && realMsg.author_id && String(realMsg.author_id) === String((window._botId || ''))) message.classList.add('msg-me');
                    else if (botName && realMsg.author_name === botName) message.classList.add('msg-me');
                    message.dataset.msgId = String(realMsg.message_id);
                    message.innerHTML = `<div class="msg-header"><div class="msg-time">${formatTimestamp(realMsg.timestamp || '')}</div><div class="msg-author">${escapeHtml(realMsg.author_name || 'Unknown')}</div></div><div class="msg-content">${renderDiscordContent(realMsg.content || '')}</div>`;
                    message.style.display = '';
                    message.style.visibility = 'visible';
                    decorateMessage(message);
                    if (opt) opt.replaceWith(message);
                    else chatFeed.appendChild(message);
                    chatFeed.scrollTop = chatFeed.scrollHeight;
                } catch (e) { }
            }
            setTimeout(() => fetchMessages(false), 200);
        } catch (e) {
            console.error("Send error:", e);
            msgInput.style.borderColor = "var(--red)";
            setTimeout(() => msgInput.style.borderColor = "", 2000);
            if (optimisticNode && optimisticNode.parentNode) optimisticNode.remove();
            msgInput.value = content;
        } finally {
            msgInput.placeholder = originalPlaceholder;
            msgInput.disabled = false;
            msgInput.focus();
        }
    };

    // --- ESTADÍSTICAS (sin cambios) ---
    window.generateStats = () => { /* ... código existente ... */ };
    function getOffensiveRank(s) { /* ... */ }
    function getGKRank(s) { /* ... */ }
    function drawGraph(type, data, avg, rank) { /* ... */ }

    // --- COMMANDS PANEL ---
    window.toggleCommands = function () { document.getElementById('commands-panel')?.classList.toggle('active'); };
    let deadlineUser = null;
    const deadlineInput = document.getElementById('deadline-user');
    const deadlineAc = document.getElementById('deadline-ac');
    const deadlinePreview = document.getElementById('deadline-preview');
    function updateDeadlinePreview() {
        if (!deadlinePreview) return;
        const name = deadlineUser ? (deadlineUser.display || deadlineUser.username || deadlineUser.name) : '...';
        deadlinePreview.innerHTML = '<code>!deadline <span class="cmd-preview-user">' + name + '</span></code>';
    }
    function buildCmdAcItem(item) {
        const el = document.createElement('div');
        el.className = 'ac-item';
        if (item.type === 'user') {
            const av = document.createElement('div');
            av.className = 'ac-avatar';
            if (item.avatar) {
                const img = document.createElement('img');
                img.src = item.avatar; img.className = 'ac-avatar-img';
                av.appendChild(img);
            } else av.textContent = (item.display || item.username || '?')[0].toUpperCase();
            const info = document.createElement('div');
            info.className = 'ac-info';
            const n = document.createElement('span'); n.className = 'ac-name'; n.textContent = item.display;
            const h = document.createElement('span'); h.className = 'ac-handle'; h.textContent = '@' + item.username;
            info.appendChild(n); info.appendChild(h);
            el.appendChild(av); el.appendChild(info);
        }
        return el;
    }
    if (deadlineInput && deadlineAc) {
        deadlineAc.addEventListener('mousedown', e => e.preventDefault());
        deadlineInput.addEventListener('input', async () => {
            const q = deadlineInput.value.trim().toLowerCase();
            deadlineUser = null;
            updateDeadlinePreview();
            if (!q) { deadlineAc.classList.remove('open'); deadlineAc.innerHTML = ''; return; }
            await ensureAcData();
            const matches = acMembers.filter(m => m.display.toLowerCase().includes(q) || m.username.toLowerCase().includes(q)).slice(0, 8).map(m => ({ ...m, type: 'user' }));
            deadlineAc.innerHTML = '';
            if (!matches.length) { deadlineAc.classList.remove('open'); return; }
            matches.forEach(item => {
                const el = buildCmdAcItem(item);
                el.addEventListener('mousedown', e => e.preventDefault());
                el.addEventListener('click', () => {
                    deadlineUser = item;
                    deadlineInput.value = item.display || item.username;
                    deadlineAc.classList.remove('open');
                    deadlineAc.innerHTML = '';
                    updateDeadlinePreview();
                    deadlineInput.focus();
                });
                deadlineAc.appendChild(el);
            });
            deadlineAc.classList.add('open');
        });
        deadlineInput.addEventListener('blur', () => { setTimeout(() => deadlineAc.classList.remove('open'), 180); });
    }
    window.sendDeadlineCommand = async function () {
        if (!currentChannelId) { showToast('Selecciona un canal primero', 'error'); return; }
        const username = deadlineUser ? (deadlineUser.username || deadlineUser.display) : (deadlineInput?.value?.trim());
        if (!username) { showToast('Selecciona un usuario', 'error'); return; }
        const btn = document.getElementById('deadline-send');
        if (btn) btn.disabled = true;
        try {
            const res = await fetch('/api/deadline', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, username: username })
            });
            const data = await res.json();
            if (res.ok) {
                showToast('Deadline enviado', 'success');
                document.getElementById('commands-panel')?.classList.remove('active');
                if (deadlineInput) deadlineInput.value = '';
                deadlineUser = null;
                updateDeadlinePreview();
            } else showToast(data.error || 'Error al enviar', 'error');
        } catch (e) { showToast('Error de red', 'error'); }
        finally { if (btn) btn.disabled = false; }
    };

    // --- CUSTOM CURSOR ---
    (function () {
        const cur = document.createElement('div');
        cur.id = 'custom-cursor';
        document.body.appendChild(cur);
        function getCursorType(el) {
            if (!el) return 'default';
            let node = el;
            while (node && node !== document.body) {
                const tag = node.tagName;
                if (tag === 'INPUT' || tag === 'TEXTAREA' || node.isContentEditable) return 'text';
                if (node.disabled || node.hasAttribute('disabled')) return 'not-allowed';
                if (tag === 'BUTTON' || tag === 'A' || tag === 'LABEL' || node.getAttribute('role') === 'button' || node.getAttribute('tabindex') !== null && node.getAttribute('tabindex') >= 0 || node.classList.contains('channel-item') || node.classList.contains('metrics-pill') || node.classList.contains('comp-btn') || node.classList.contains('emoji-btn') || node.classList.contains('ac-item') || node.classList.contains('stats-btn') || node.classList.contains('del-btn') || node.classList.contains('modal-submit') || node.classList.contains('brand-orb') || node.classList.contains('msg-action-btn') || node.classList.contains('modal-close') || node.classList.contains('stats-close') || node.classList.contains('mp-close') || node.classList.contains('composer-send') || node.classList.contains('settings-btn') || node.classList.contains('cursor-upload-btn') || node.classList.contains('cursor-reset-btn') || node.classList.contains('quality-btn') || node.classList.contains('settings-close')) return 'pointer';
                node = node.parentElement;
            }
            return 'default';
        }
        document.addEventListener('mousemove', (e) => {
            cur.style.left = e.clientX + 'px';
            cur.style.top = e.clientY + 'px';
            const el = document.elementFromPoint(e.clientX, e.clientY);
            const type = getCursorType(el);
            cur.classList.remove('is-pointer', 'is-text', 'is-notallowed');
            if (type === 'pointer') cur.classList.add('is-pointer');
            else if (type === 'text') cur.classList.add('is-text');
            else if (type === 'not-allowed') cur.classList.add('is-notallowed');
        });
        document.addEventListener('mouseleave', () => { cur.style.opacity = '0'; });
        document.addEventListener('mouseenter', () => { cur.style.opacity = '1'; });
    })();

    // --- SETTINGS PANEL ---
    (function () {
        const STORAGE_KEY = 'blzt_settings';
        function loadSettings() { try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch (e) { return {}; } }
        function saveSettings(data) { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch (e) { } }
        function applyQuality(q) {
            document.documentElement.classList.remove('q-low', 'q-medium', 'q-high');
            document.documentElement.classList.add('q-' + q);
            document.querySelectorAll('.quality-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.q === q));
        }
        function applyCursors(cursors) {
            let css = '';
            if (cursors.normal) css += `* { cursor: url('${cursors.normal}') 0 0, auto !important; }\n`;
            if (cursors.pointer) css += `button:not([disabled]), a, label, [role="button"], .channel-item, .metrics-pill, .comp-btn:not([disabled]), .emoji-btn, .ac-item, .stats-btn, .del-btn, .del-btn--cancel, .del-btn--confirm, .modal-submit, .brand-orb, .msg-action-btn, .modal-close, .stats-close, .mp-close, .composer-send, .settings-btn, .cursor-upload-btn, .cursor-reset-btn, .quality-btn { cursor: url('${cursors.pointer}') 0 0, pointer !important; }\n`;
            if (cursors.text) css += `html body input, html body textarea, html body [contenteditable] { cursor: url('${cursors.text}') 0 0, text !important; }\n`;
            if (cursors.pointer || cursors.normal) css += `#custom-cursor { display: none !important; }\n`;
            let styleEl = document.getElementById('blzt-cursor-style');
            if (!styleEl) { styleEl = document.createElement('style'); styleEl.id = 'blzt-cursor-style'; document.head.appendChild(styleEl); }
            styleEl.textContent = css;
        }
        function updatePreview(type, dataUrl) {
            const prev = document.getElementById('prev-' + type);
            if (!prev) return;
            prev.innerHTML = dataUrl ? `<img src="${dataUrl}" alt="cursor">` : `<span class="cursor-upload-placeholder">${type === 'normal' ? '↖' : type === 'pointer' ? '☝' : 'I'}</span>`;
        }
        function normalizeImageToPng(file, size) {
            return new Promise((resolve, reject) => {
                const url = URL.createObjectURL(file);
                const img = new Image();
                img.onload = () => {
                    const canvas = document.createElement('canvas');
                    canvas.width = size; canvas.height = size;
                    const ctx = canvas.getContext('2d');
                    const scale = Math.min(size / img.naturalWidth, size / img.naturalHeight);
                    const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
                    const x = (size - w) / 2, y = (size - h) / 2;
                    ctx.clearRect(0, 0, size, size);
                    ctx.drawImage(img, x, y, w, h);
                    URL.revokeObjectURL(url);
                    resolve(canvas.toDataURL('image/png'));
                };
                img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Image load failed')); };
                img.src = url;
            });
        }
        function init() {
            const s = loadSettings();
            applyQuality(s.quality || 'high');
            if (s.cursors && Object.keys(s.cursors).length) {
                applyCursors(s.cursors);
                Object.entries(s.cursors).forEach(([type, url]) => updatePreview(type, url));
            }
            document.querySelectorAll('.quality-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const q = btn.dataset.q;
                    applyQuality(q);
                    const s = loadSettings();
                    s.quality = q;
                    saveSettings(s);
                });
            });
            ['normal', 'pointer', 'text'].forEach(type => {
                const input = document.getElementById('cursor-' + type);
                if (!input) return;
                input.addEventListener('change', (e) => {
                    const file = e.target.files[0];
                    if (!file) return;
                    normalizeImageToPng(file, 64).then(pngDataUrl => {
                        const s = loadSettings();
                        s.cursors = s.cursors || {};
                        s.cursors[type] = pngDataUrl;
                        saveSettings(s);
                        applyCursors(s.cursors);
                        updatePreview(type, pngDataUrl);
                    }).catch(err => { console.warn('Cursor conversion failed:', err); showToast('Could not read cursor file', 'error'); });
                    input.value = '';
                });
            });
            document.querySelectorAll('.cursor-reset-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const type = btn.dataset.type;
                    const s = loadSettings();
                    s.cursors = s.cursors || {};
                    delete s.cursors[type];
                    saveSettings(s);
                    applyCursors(s.cursors);
                    updatePreview(type, null);
                });
            });
        }
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
        else init();
        window.toggleSettings = function () { document.getElementById('settings-panel').classList.toggle('active'); };
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') document.getElementById('settings-panel')?.classList.remove('active'); });
    })();

    // --- MOBILE CHANNEL DRAWER ---
    window.toggleChannelDrawer = function () {
        const drawer = document.getElementById('channel-drawer');
        if (!drawer) return;
        const isOpen = drawer.classList.toggle('open');
        if (isOpen) {
            const drawerList = document.getElementById('channel-drawer-list');
            const mainList = document.getElementById('channel-list');
            if (!drawerList || !mainList) return;
            drawerList.innerHTML = '';
            mainList.querySelectorAll('.channel-item').forEach(item => {
                const el = document.createElement('div');
                el.className = 'channel-item' + (item.classList.contains('active') ? ' active' : '');
                el.setAttribute('data-index', item.getAttribute('data-index') || '');
                const nameSpan = document.createElement('span');
                nameSpan.className = 'ch-name';
                nameSpan.textContent = item.querySelector('.ch-name')?.textContent || '';
                el.appendChild(nameSpan);
                el.addEventListener('click', () => { drawer.classList.remove('open'); item.click(); });
                drawerList.appendChild(el);
            });
        }
    };
    (function () {
        let startY = 0;
        document.addEventListener('touchstart', e => { const sheet = document.querySelector('.channel-drawer-sheet'); if (sheet && sheet.contains(e.target)) startY = e.touches[0].clientY; }, { passive: true });
        document.addEventListener('touchend', e => { if (!startY) return; if (e.changedTouches[0].clientY - startY > 60) document.getElementById('channel-drawer')?.classList.remove('open'); startY = 0; }, { passive: true });
    })();

    // --- LOGIN + MOD PANEL (sin cambios relevantes) ---
    // ... (código existente de login y mod panel)

    // --- NUEVO: PANEL DE LOGS (Ctrl+AltGr+0) ---
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.altKey && e.code === 'Digit0') {
            e.preventDefault();
            openLogs();
        }
    });

    function openLogs() {
        const panel = document.getElementById('logs-panel');
        if (!panel) return;
        panel.classList.add('active');
        document.getElementById('logs-auth').style.display = 'flex';
        document.getElementById('logs-viewer').style.display = 'none';
        document.getElementById('logs-user').value = '';
        document.getElementById('logs-pass').value = '';
        document.getElementById('logs-error').textContent = '';
    }

    window.closeLogs = function () {
        document.getElementById('logs-panel').classList.remove('active');
    };

    window.authLogs = function () {
        const user = document.getElementById('logs-user').value;
        const pass = document.getElementById('logs-pass').value;
        if (user === 'ogmhabas' && pass === 'blz-tadmin') {
            document.getElementById('logs-auth').style.display = 'none';
            document.getElementById('logs-viewer').style.display = 'block';
            fetchLogs();
        } else {
            document.getElementById('logs-error').textContent = 'Invalid credentials';
        }
    };

    async function fetchLogs() {
        try {
            const res = await fetch('/api/logs?lines=200');
            const text = await res.text();
            document.getElementById('logs-content').textContent = text;
        } catch (e) {
            document.getElementById('logs-content').textContent = 'Error al cargar logs';
        }
    }

    // Exponer funciones globales necesarias
    window.toggleDrawer = () => document.getElementById('stats-drawer').classList.toggle('active');
    window.toggleLogin = () => { /* ... */ };
    window.doLogin = () => { /* ... */ };
    window.toggleMod = () => { /* ... */ };
    window.switchModTab = () => { /* ... */ };
    window.modAction = () => { /* ... */ };
    window.applyTimeout = () => { /* ... */ };
    window.showWarnHistory = () => { /* ... */ };
});
