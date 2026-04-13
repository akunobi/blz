/* static/script.js - BLZ-T Frontend Complete (Corregido) */
(function() {
    "use strict";

    // --- REFERENCIAS DOM ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const sendBtn = document.getElementById('send-btn');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas ? canvas.getContext('2d') : null;
    const chatChannelName = document.getElementById('chat-channel-name');
    const chatUptime = document.getElementById('chat-uptime');
    const channelCount = document.getElementById('channel-count');

    // --- ESTADO GLOBAL ---
    window._currentChannelId = null;
    Object.defineProperty(window, 'currentChannelId', {
        get: () => window._currentChannelId,
        set: v => { window._currentChannelId = v; }
    });
    let isFetching = false;
    let botName = null;
    window._botId = null;
    let channelMap = {};
    let mentionCache = { users: {}, roles: {} };
    let lastMessageId = {};
    let timerInterval = null;
    let acMembers = [], acRoles = [], acLoaded = false;
    let emojiPicker = null, emojiTargetMsgId = null, emojiTargetChannelId = null;
    let _toastTimer = null, _deleteModal = null;

    // --- INICIALIZACIÓN ---
    async function init() {
        createCustomCursor();
        await fetchBotInfo();
        fetchChannels();
        startTimer();
        setupEventListeners();
        setInterval(() => {
            if (!isFetching && currentChannelId) fetchMessages(false);
        }, 5000);
        applyLoginState(localStorage.getItem('blzt_mod') === '1');
    }

    function createCustomCursor() {
        const cur = document.createElement('div');
        cur.id = 'custom-cursor';
        document.body.appendChild(cur);
        document.addEventListener('mousemove', (e) => {
            cur.style.left = e.clientX + 'px';
            cur.style.top = e.clientY + 'px';
            const el = document.elementFromPoint(e.clientX, e.clientY);
            const type = getCursorType(el);
            cur.className = '';
            if (type === 'pointer') cur.classList.add('is-pointer');
            else if (type === 'text') cur.classList.add('is-text');
            else if (type === 'not-allowed') cur.classList.add('is-notallowed');
        });
        document.addEventListener('mouseleave', () => { cur.style.opacity = '0'; });
        document.addEventListener('mouseenter', () => { cur.style.opacity = '1'; });
    }

    function getCursorType(el) {
        if (!el) return 'default';
        let node = el;
        while (node && node !== document.body) {
            const tag = node.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || node.isContentEditable) return 'text';
            if (node.disabled || node.hasAttribute('disabled')) return 'not-allowed';
            if (tag === 'BUTTON' || tag === 'A' || tag === 'LABEL' ||
                node.getAttribute('role') === 'button' ||
                node.classList.contains('channel-item') ||
                node.classList.contains('metrics-pill') ||
                node.classList.contains('cmds-btn') ||
                node.classList.contains('emoji-btn') ||
                node.classList.contains('settings-btn') ||
                node.classList.contains('del-btn') ||
                node.classList.contains('modal-submit') ||
                node.classList.contains('brand-btn') ||
                node.classList.contains('msg-action-btn') ||
                node.classList.contains('modal-close') ||
                node.classList.contains('composer-send') ||
                node.classList.contains('login-btn') ||
                node.classList.contains('mod-btn') ||
                node.classList.contains('cursor-upload-btn') ||
                node.classList.contains('cursor-reset-btn') ||
                node.classList.contains('quality-btn')) return 'pointer';
            node = node.parentElement;
        }
        return 'default';
    }

    async function fetchBotInfo() {
        try {
            const r = await fetch('/api/botinfo');
            if (r.ok) {
                const j = await r.json();
                botName = j.name || null;
                window._botId = j.id || null;
            }
        } catch (e) { console.warn('botinfo fetch failed', e); }
    }

    function setupEventListeners() {
        if (msgInput) {
            msgInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') sendMessage(); });
        }
        if (sendBtn) sendBtn.onclick = sendMessage;

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeAllModals();
            if (e.code === 'Digit0' && e.getModifierState('AltGraph')) {
                e.preventDefault();
                openLogs();
            }
        });

        msgInput?.addEventListener('input', handleAcInput);
        msgInput?.addEventListener('keydown', handleAcKeydown);
        document.addEventListener('pointerdown', (e) => {
            if (!acBox || !acBox.classList.contains('ac-open')) return;
            if (!acBox.contains(e.target) && !msgInput.contains(e.target)) hideAc();
        });
    }

    function closeAllModals() {
        document.getElementById('stats-drawer')?.classList.remove('active');
        document.getElementById('stats-modal')?.classList.remove('active');
        document.getElementById('commands-panel')?.classList.remove('active');
        document.getElementById('settings-panel')?.classList.remove('active');
        document.getElementById('mod-panel')?.classList.remove('active');
        document.getElementById('login-modal')?.classList.remove('active');
        document.getElementById('logs-panel')?.classList.remove('active');
    }

    // ---------- CANALES ----------
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            if (!channels.length) return;
            channelList.innerHTML = '';
            const drawerList = document.getElementById('channel-drawer-list');
            if (drawerList) drawerList.innerHTML = '';
            channels.forEach((ch, idx) => {
                const cName = ch.name || "Unknown";
                const cId = ch.id;
                channelMap[cId] = cName;
                const link = createChannelElement(cName, cId, idx);
                channelList.appendChild(link);
                if (drawerList) {
                    const drawerLink = createChannelElement(cName, cId, idx, true);
                    drawerList.appendChild(drawerLink);
                }
            });
            if (channelCount) channelCount.textContent = channels.length;
        } catch (e) { console.error('Error fetching channels:', e); }
    }

    function createChannelElement(name, id, idx, forDrawer = false) {
        const div = document.createElement('div');
        div.className = 'channel-item';
        div.setAttribute('data-index', String(idx + 1).padStart(2, '0'));
        const nameSpan = document.createElement('span');
        nameSpan.className = 'ch-name';
        nameSpan.textContent = name.toUpperCase();
        div.appendChild(nameSpan);
        div.onclick = () => {
            currentChannelId = id;
            document.querySelectorAll('.channel-item').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.channel-item').forEach(b => {
                if (b.querySelector('.ch-name')?.textContent === name.toUpperCase()) b.classList.add('active');
            });
            if (chatChannelName) chatChannelName.innerText = name.toUpperCase();
            resetTimer();
            chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">⚡</div><p class="feed-empty-title">Receiving signal...</p><p class="feed-empty-sub">信号受信中</p></div>';
            fetchMessages(true);
            if (forDrawer) document.getElementById('channel-drawer')?.classList.remove('open');
        };
        return div;
    }

    function startTimer() { resetTimer(); }
    function resetTimer() {
        if (timerInterval) clearInterval(timerInterval);
        let seconds = 0;
        if (chatUptime) {
            chatUptime.innerText = "00:00";
            chatUptime.style.color = 'var(--teal)';
        }
        timerInterval = setInterval(() => {
            seconds++;
            const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
            const secs = (seconds % 60).toString().padStart(2, '0');
            if (chatUptime) {
                chatUptime.innerText = `${mins}:${secs}`;
                if (seconds < 300) chatUptime.style.color = 'var(--teal)';
                else if (seconds < 900) chatUptime.style.color = 'var(--amber)';
                else chatUptime.style.color = 'var(--coral)';
            }
        }, 1000);
    }

    // ---------- MENSAJES ----------
    async function fetchMessages(initial = false) {
        if (!currentChannelId) return;
        isFetching = true;
        try {
            const now = Date.now();
            document.querySelectorAll('[data-optimistic="1"]').forEach(n => {
                const ts = parseInt(n.dataset.optimisticTs || '0', 10);
                if (ts && (now - ts) > 5000) n.remove();
            });
        } catch (e) {}
        if (!botName) await fetchBotInfo();
        try {
            let url = `/api/messages?channel_id=${encodeURIComponent(currentChannelId)}`;
            if (!initial) {
                const since = lastMessageId[currentChannelId] || '0';
                url += `&since_id=${encodeURIComponent(since)}`;
            } else {
                url += `&limit=1000`;
            }
            const res = await fetch(url);
            const msgs = await res.json();
            const isScrolledToBottom = (chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight) < 150;
            await resolveMentions(msgs);
            if (initial) {
                chatFeed.innerHTML = '';
                if (!msgs.length) {
                    chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">無</div><p class="feed-empty-title">No messages yet</p><p class="feed-empty-sub">信号なし</p></div>';
                } else {
                    msgs.forEach(msg => appendMessage(msg, false));
                }
                if (isScrolledToBottom) chatFeed.scrollTop = chatFeed.scrollHeight;
            } else {
                if (msgs.length) {
                    msgs.forEach(msg => appendMessage(msg, true));
                    chatFeed.scrollTop = chatFeed.scrollHeight;
                }
            }
            msgs.forEach(msg => {
                if (msg.message_id) {
                    const prev = lastMessageId[currentChannelId] || '0';
                    lastMessageId[currentChannelId] = BigInt(String(msg.message_id)) > BigInt(prev) ? String(msg.message_id) : prev;
                }
            });
            enrichUnresolvedMentions();
        } catch (e) {
            console.error('Error fetching messages:', e);
        } finally {
            isFetching = false;
        }
    }

    async function resolveMentions(msgs) {
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
        if (usersToLookup.length + rolesToLookup.length > 0) {
            try {
                const r = await fetch('/api/mention_lookup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ users: usersToLookup, roles: rolesToLookup })
                });
                if (r.ok) {
                    const j = await r.json();
                    if (j.users) Object.assign(mentionCache.users, j.users);
                    if (j.roles) Object.assign(mentionCache.roles, j.roles);
                }
            } catch (e) {}
        }
    }

    function appendMessage(msg, isNew = false) {
        const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
        if (existing) return;
        const message = document.createElement('div');
        message.className = 'message';
        if (botName && msg.author_id && String(msg.author_id) === String(window._botId || '')) message.classList.add('msg-me');
        else if (botName && msg.author_name === botName) message.classList.add('msg-me');
        message.dataset.msgId = String(msg.message_id);
        const rendered = renderDiscordContent(msg.content || '');
        message.innerHTML = `
            <div class="msg-header">
                <div class="msg-time">${formatTimestamp(msg.timestamp)}</div>
                <div class="msg-author">${escapeHtml(msg.author_name || 'Unknown')}</div>
            </div>
            <div class="msg-content">${rendered}</div>
        `;
        renderComponents(message, msg.components);
        decorateMessage(message);
        chatFeed.appendChild(message);
        if (isNew) {
            requestAnimationFrame(() => {
                message.style.setProperty('--flash', '1');
                setTimeout(() => message.style.removeProperty('--flash'), 600);
            });
        }
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
        let working = escapeHtml(text);
        working = working.replace(/&lt;@!?(\d+)&gt;/g, (m, id) => {
            const resolved = mentionCache.users[id];
            if (resolved) {
                const display = (typeof resolved === 'string') ? resolved : (resolved.display || `@${id}`);
                return `<span class="mention mention-user">${escapeHtml(display)}</span>`;
            }
            return `<span class="mention">@${id}</span>`;
        });
        working = working.replace(/&lt;@&amp;(\d+)&gt;/g, (m, id) => {
            const resolved = mentionCache.roles[id];
            return resolved ? `<span class="mention mention-role">${escapeHtml(resolved)}</span>` : `<span class="mention">@role:${id}</span>`;
        });
        working = working.replace(/&lt;#(\d+)&gt;/g, (m, id) => {
            const name = channelMap[id];
            return name ? `<span class="mention mention-channel">#${escapeHtml(name)}</span>` : `<span class="mention">#${id}</span>`;
        });
        working = formatLinks(working);
        working = working.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        working = working.replace(/\*(.+?)\*/g, '<em>$1</em>');
        working = working.replace(/`(.+?)`/g, '<code>$1</code>');
        return working;
    }

    function enrichUnresolvedMentions() {}

    function renderComponents(msgEl, componentsJson) {
        if (!componentsJson) return;
        // Implementación básica de componentes (puedes expandir)
    }

    function decorateMessage(msgEl) {
        if (!msgEl.dataset.msgId || msgEl.querySelector('.msg-actions')) return;
        const isOwn = msgEl.classList.contains('msg-me');
        const actions = document.createElement('div');
        actions.className = 'msg-actions';
        const reactBtn = document.createElement('button');
        reactBtn.className = 'msg-action-btn';
        reactBtn.innerHTML = '😊';
        reactBtn.title = 'Reaccionar';
        reactBtn.onclick = (e) => { e.stopPropagation(); openEmojiPicker(msgEl.dataset.msgId, currentChannelId, reactBtn); };
        actions.appendChild(reactBtn);
        if (isOwn) {
            const editBtn = document.createElement('button');
            editBtn.className = 'msg-action-btn';
            editBtn.innerHTML = '✏️';
            editBtn.title = 'Editar';
            editBtn.onclick = (e) => { e.stopPropagation(); openEdit(msgEl); };
            actions.appendChild(editBtn);
            const delBtn = document.createElement('button');
            delBtn.className = 'msg-action-btn del';
            delBtn.innerHTML = '🗑️';
            delBtn.title = 'Borrar';
            delBtn.onclick = (e) => { e.stopPropagation(); showDeleteConfirm(msgEl.dataset.msgId, currentChannelId, msgEl); };
            actions.appendChild(delBtn);
        }
        const header = msgEl.querySelector('.msg-header');
        if (header) header.appendChild(actions);
        else msgEl.appendChild(actions);
    }

    // ---------- AUTOCOMPLETADO ----------
    let acBox = null, acItems = [], acIdx = -1, acTriggerPos = -1;
    function getAcBox() {
        if (acBox) return acBox;
        acBox = document.createElement('div');
        acBox.id = 'ac-box';
        acBox.setAttribute('role', 'listbox');
        document.body.appendChild(acBox);
        return acBox;
    }
    function hideAc() {
        getAcBox().classList.remove('ac-open');
        acItems = []; acIdx = -1; acTriggerPos = -1;
    }
    async function handleAcInput() {
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
        if (!matches.length) { hideAc(); return; }
        renderAc(matches);
    }
    function handleAcKeydown(e) {
        if (!acItems.length) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); setAcIdx(Math.min(acIdx + 1, acItems.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setAcIdx(Math.max(acIdx - 1, 0)); }
        else if (e.key === 'Tab' || e.key === 'Enter') { e.preventDefault(); applyAc(acIdx >= 0 ? acIdx : 0); }
        else if (e.key === 'Escape') hideAc();
    }
    function renderAc(matches) {
        const box = getAcBox();
        box.innerHTML = '';
        acItems = matches;
        acIdx = -1;
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
            el.addEventListener('click', () => applyAc(i));
            box.appendChild(el);
        });
        box.classList.add('ac-open');
        const rect = msgInput.getBoundingClientRect();
        box.style.width = Math.min(460, rect.width - 20) + 'px';
        box.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
        box.style.left = rect.left + 'px';
    }
    function setAcIdx(i) {
        const els = getAcBox().querySelectorAll('.ac-item');
        els.forEach(el => el.classList.remove('ac-item--active'));
        acIdx = i;
        if (i >= 0 && i < els.length) els[i].classList.add('ac-item--active');
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
    async function ensureAcData() {
        if (acLoaded) return;
        try {
            const r = await fetch('/api/members');
            if (r.ok) {
                const j = await r.json();
                acMembers = j.members || [];
                acRoles = j.roles || [];
                acLoaded = true;
            }
        } catch (e) {}
    }

    // ---------- ENVÍO DE MENSAJES ----------
    async function sendMessage() {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;
        const originalPlaceholder = msgInput.placeholder;
        let optimisticNode = null;
        let optimisticClientId = String(Date.now()) + Math.floor(Math.random()*1000);
        try {
            optimisticNode = document.createElement('div');
            optimisticNode.className = 'message msg-me';
            optimisticNode.setAttribute('data-optimistic', '1');
            optimisticNode.dataset.clientId = optimisticClientId;
            optimisticNode.dataset.optimisticTs = String(Date.now());
            optimisticNode.innerHTML = `<div class="msg-header"><div class="msg-time">——:——</div><div class="msg-author">${botName || 'BOT'}</div></div><div class="msg-content">${formatLinks(content)}</div>`;
            chatFeed.appendChild(optimisticNode);
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch (e) {}
        msgInput.value = '';
        msgInput.placeholder = 'Sending...';
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
                const opt = chatFeed.querySelector(`[data-client-id="${optimisticClientId}"]`);
                if (opt) opt.remove();
                fetchMessages(false);
            }
        } catch (e) {
            console.error('send error:', e);
            if (optimisticNode && optimisticNode.parentNode) optimisticNode.remove();
            msgInput.value = content;
        } finally {
            msgInput.placeholder = originalPlaceholder;
            msgInput.disabled = false;
            msgInput.focus();
        }
    }

    // --- PANEL DE LOGS (AltGr+0) ---
    window.openLogs = function() {
        const panel = document.getElementById('logs-panel');
        if (!panel) return;
        panel.classList.add('active');
        document.getElementById('logs-auth').style.display = 'flex';
        document.getElementById('logs-viewer').style.display = 'none';
        document.getElementById('logs-user').value = '';
        document.getElementById('logs-pass').value = '';
        document.getElementById('logs-error').textContent = '';
    };
    window.closeLogs = function() {
        document.getElementById('logs-panel')?.classList.remove('active');
    };
    window.authLogs = function() {
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
            document.getElementById('logs-content').textContent = "Error al cargar logs";
        }
    }

    // --- LOGIN ---
    window.toggleLogin = function() {
        document.getElementById('login-modal')?.classList.toggle('active');
    };
    window.doLogin = function() {
        const user = document.getElementById('login-user')?.value.trim();
        const pass = document.getElementById('login-pass')?.value;
        if (user === 'admin' && pass === 'blz2024') {
            localStorage.setItem('blzt_mod', '1');
            applyLoginState(true);
            toggleLogin();
        } else {
            document.getElementById('login-error').textContent = 'Invalid credentials';
        }
    };
    function applyLoginState(loggedIn) {
        const btn = document.getElementById('login-btn');
        const label = document.getElementById('login-btn-label');
        const modB = document.getElementById('mod-btn');
        if (btn) btn.classList.toggle('logged-in', loggedIn);
        if (label) label.textContent = loggedIn ? 'Mod' : 'Login';
        if (modB) modB.style.display = loggedIn ? 'flex' : 'none';
        if (!loggedIn) document.getElementById('mod-panel')?.classList.remove('active');
    }

    // --- STATS (simplificado) ---
    window.generateStats = function() {
        alert('Stats generation (demo)');
        document.getElementById('stats-modal').classList.add('active');
    };
    window.toggleDrawer = () => document.getElementById('stats-drawer')?.classList.toggle('active');
    window.toggleCommands = () => document.getElementById('commands-panel')?.classList.toggle('active');
    window.toggleSettings = () => document.getElementById('settings-panel')?.classList.toggle('active');
    window.toggleChannelDrawer = () => document.getElementById('channel-drawer')?.classList.toggle('open');
    window.toggleMod = async () => {
        const panel = document.getElementById('mod-panel');
        panel.classList.toggle('active');
        if (panel.classList.contains('active')) await fetchModData();
    };

    // --- COMMANDS: DEADLINE ---
    window.sendDeadlineCommand = async () => {
        const userInput = document.getElementById('deadline-user');
        const selectedUserId = userInput?.dataset.selectedUserId;
        if (!selectedUserId) {
            showToast('Please select a user', 'error');
            return;
        }
        const sendBtn = document.getElementById('deadline-send');
        sendBtn.disabled = true;
        sendBtn.innerHTML = 'Sending...';
        try {
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    channel_id: currentChannelId,
                    content: `/deadline <@${selectedUserId}>`
                })
            });
            const data = await res.json();
            if (data.status === 'OK') {
                showToast('Deadline command sent', 'success');
                toggleCommands();
            } else {
                throw new Error(data.error || 'Failed');
            }
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        } finally {
            sendBtn.disabled = false;
            sendBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2L15 22 11 13 2 9 22 2Z"/></svg> Send Command`;
        }
    };

    // --- MOD PANEL ---
    async function fetchModData() {
        const body = document.getElementById('mod-body');
        body.innerHTML = '<div class="mod-loading">Loading...</div>';
        try {
            const [usersRes, actionsRes] = await Promise.all([
                fetch('/api/mod/users'),
                fetch('/api/mod/actions')
            ]);
            const users = await usersRes.json();
            const actions = await actionsRes.json();
            renderModPanel(users, actions);
        } catch (e) {
            body.innerHTML = '<div class="mod-empty">Error loading data</div>';
        }
    }

    function renderModPanel(users, actions) {
        const body = document.getElementById('mod-body');
        const activeTab = document.querySelector('.mod-tab.active')?.dataset.tab || 'warned';
        if (activeTab === 'warned') {
            renderWarnedUsers(users, body);
        } else {
            renderActionLog(actions, body);
        }
    }

    function renderWarnedUsers(users, container) {
        if (!users.length) {
            container.innerHTML = '<div class="mod-empty">No warned users</div>';
            return;
        }
        container.innerHTML = '';
        users.forEach(u => {
            const card = document.createElement('div');
            card.className = 'mod-user-card';
            const initial = (u.user_name || '?')[0].toUpperCase();
            const lastAction = u.last_action ? u.last_action.action.toUpperCase() : null;
            card.innerHTML = `
                <div class="mod-user-avatar">${initial}</div>
                <div class="mod-user-info">
                    <div class="mod-user-name">${escapeHtml(u.user_name || 'Unknown')}</div>
                    <div class="mod-user-meta">${escapeHtml(u.user_id)}</div>
                    <div class="warn-badges">
                        <span class="warn-badge warn-badge-count">⚠ ${u.warn_count} warn${u.warn_count !== 1 ? 's' : ''}</span>
                        ${lastAction ? `<span class="warn-badge warn-badge-action">${lastAction}</span>` : ''}
                        <span class="warn-badge warn-badge-history" onclick="showWarnHistory(this, '${escapeHtml(JSON.stringify(u)).replace(/'/g, "\\'")}')">📋 History</span>
                    </div>
                </div>
                <div class="mod-user-actions">
                    <button class="mod-action-btn warn" onclick="modAction('warn','${u.user_id}','${escapeHtml(u.user_name)}','${u.guild_id}',this)">Warn</button>
                    <button class="mod-action-btn timeout" onclick="modAction('timeout','${u.user_id}','${escapeHtml(u.user_name)}','${u.guild_id}',this)">Timeout</button>
                    <button class="mod-action-btn kick" onclick="modAction('kick','${u.user_id}','${escapeHtml(u.user_name)}','${u.guild_id}',this)">Kick</button>
                    <button class="mod-action-btn ban" onclick="modAction('ban','${u.user_id}','${escapeHtml(u.user_name)}','${u.guild_id}',this)">Ban</button>
                    <button class="mod-action-btn clear" onclick="modAction('clear','${u.user_id}','${escapeHtml(u.user_name)}','${u.guild_id}',this)">Clear Warns</button>
                </div>
            `;
            container.appendChild(card);
        });
    }

    function renderActionLog(actions, container) {
        if (!actions.length) {
            container.innerHTML = '<div class="mod-empty">No actions yet</div>';
            return;
        }
        const table = document.createElement('table');
        table.className = 'mod-log-table';
        table.innerHTML = `
            <thead><tr><th>User</th><th>Action</th><th>Reason</th><th>Moderator</th><th>Date</th></tr></thead>
            <tbody>${actions.map(a => `
                <tr>
                    <td>${escapeHtml(a.user_name || a.user_id)}</td>
                    <td><span class="log-action-badge ${a.action}">${a.action}</span></td>
                    <td>${escapeHtml(a.reason || '—')}</td>
                    <td>${escapeHtml(a.moderator_name || '—')}</td>
                    <td>${a.timestamp ? a.timestamp.slice(0,16) : '—'}</td>
                </tr>`).join('')}
            </tbody>`;
        container.innerHTML = '';
        container.appendChild(table);
    }

    window.switchModTab = (tab) => {
        document.querySelectorAll('.mod-tab').forEach(t => t.classList.remove('active'));
        document.querySelector(`.mod-tab[data-tab="${tab}"]`).classList.add('active');
        fetchModData();
    };

    window.modAction = async (action, uid, uname, gid, btn) => {
        const reason = prompt(`Reason for ${action}?`);
        if (reason === null) return;
        let endpoint = '';
        let body = { user_id: uid, guild_id: gid, reason };
        if (action === 'warn') endpoint = '/api/mod/warn';
        else if (action === 'timeout') {
            const duration = prompt('Timeout duration in seconds (e.g., 3600 for 1h):', '3600');
            if (!duration) return;
            body.duration_seconds = parseInt(duration);
            endpoint = '/api/mod/timeout';
        } else if (action === 'kick') endpoint = '/api/mod/kick';
        else if (action === 'ban') {
            const days = prompt('Delete message days (0-7):', '0');
            if (days === null) return;
            body.delete_days = parseInt(days);
            endpoint = '/api/mod/ban';
        } else if (action === 'clear') {
            if (!confirm(`Clear all warnings for ${uname}?`)) return;
            endpoint = '/api/mod/clear_warns';
            body = { user_id: uid, guild_id: gid };
        }
        try {
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (res.ok) {
                showToast(`${action} executed successfully`, 'success');
                fetchModData();
            } else {
                throw new Error(data.error);
            }
        } catch (e) {
            showToast(`Error: ${e.message}`, 'error');
        }
    };

    window.showWarnHistory = (el, userDataStr) => {
        try {
            const user = JSON.parse(userDataStr.replace(/&quot;/g, '"'));
            const modal = document.createElement('div');
            modal.id = 'warn-history-modal';
            modal.className = 'wh-open wh-visible';
            modal.innerHTML = `
                <div class="wh-backdrop" onclick="this.parentElement.remove()"></div>
                <div class="wh-card">
                    <div class="wh-header">
                        <span class="wh-title">Warnings for ${escapeHtml(user.user_name)}</span>
                        <button class="wh-close" onclick="this.closest('#warn-history-modal').remove()">✕</button>
                    </div>
                    <div class="wh-body">
                        ${user.warnings.map((w, i) => `
                            <div class="wh-warn-item">
                                <span class="wh-warn-num">#${i+1}</span>
                                <div class="wh-warn-body">
                                    <div class="wh-warn-reason">${escapeHtml(w.reason)}</div>
                                    ${w.message_content ? `<div class="wh-warn-message">${escapeHtml(w.message_content)}</div>` : ''}
                                    <div class="wh-warn-meta">
                                        <span class="wh-warn-mod">by ${escapeHtml(w.moderator_name)}</span>
                                        <span class="wh-warn-time">${w.timestamp ? new Date(w.timestamp).toLocaleString() : ''}</span>
                                        ${w.message_link ? `<a href="${w.message_link}" target="_blank" class="wh-warn-link">Jump to message</a>` : ''}
                                    </div>
                                </div>
                            </div>
                        `).join('') || '<div class="wh-empty">No warnings</div>'}
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        } catch (e) {
            console.error(e);
            alert('Could not display warnings');
        }
    };

    // --- TOAST ---
    function showToast(msg, type = 'info') {
        if (_toastTimer) clearTimeout(_toastTimer);
        let toast = document.getElementById('blz-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'blz-toast';
            toast.className = 'blz-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.className = `blz-toast blz-toast--${type} blz-toast--show`;
        _toastTimer = setTimeout(() => toast.classList.remove('blz-toast--show'), 3000);
    }

    // --- OTROS ---
    window.openEmojiPicker = (msgId, channelId, btn) => { /* implementar si es necesario */ };
    window.openEdit = (msgEl) => { /* implementar si es necesario */ };
    window.showDeleteConfirm = (msgId, channelId, msgEl) => { /* implementar si es necesario */ };

    // Iniciar
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
