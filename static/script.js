// static/script.js — BLZ-T Frontend v2 (Bugs fixed + mejoras)
(function () {
    "use strict";

    // ─── DOM ──────────────────────────────────────────────────────────────────
    const channelList     = document.getElementById('channel-list');
    const chatFeed        = document.getElementById('chat-feed');
    const msgInput        = document.getElementById('msg-input');
    const sendBtn         = document.getElementById('send-btn');
    const chatChannelName = document.getElementById('chat-channel-name');
    const chatUptime      = document.getElementById('chat-uptime');
    const channelCount    = document.getElementById('channel-count');
    const canvas          = document.getElementById('stats-canvas');
    const ctx             = canvas ? canvas.getContext('2d') : null;

    // ─── ESTADO ──────────────────────────────────────────────────────────────
    window._currentChannelId = null;
    Object.defineProperty(window, 'currentChannelId', {
        get: () => window._currentChannelId,
        set: v => { window._currentChannelId = v; }
    });

    let isFetching      = false;
    let botName         = null;
    let channelMap      = {};
    let mentionCache    = { users: {}, roles: {} };
    let lastMessageId   = {};
    let timerInterval   = null;
    let acMembers = [], acRoles = [], acLoaded = false;
    let acBox = null, acItems = [], acIdx = -1, acTriggerPos = -1;

    // ─── INIT ─────────────────────────────────────────────────────────────────
    async function init() {
        createCustomCursor();
        await fetchBotInfo();
        fetchChannels();
        startTimer();
        setupEventListeners();
        setInterval(() => {
            if (!isFetching && currentChannelId) fetchMessages(false);
        }, 500);
        applyLoginState(localStorage.getItem('blzt_mod') === '1');
    }

    // ─── CURSOR ───────────────────────────────────────────────────────────────
    function createCustomCursor() {
        const cur = document.createElement('div');
        cur.id = 'custom-cursor';
        document.body.appendChild(cur);
        document.addEventListener('mousemove', e => {
            cur.style.left = e.clientX + 'px';
            cur.style.top  = e.clientY + 'px';
            const el   = document.elementFromPoint(e.clientX, e.clientY);
            const type = getCursorType(el);
            cur.classList.remove('is-pointer', 'is-text', 'is-notallowed');
            if (type === 'pointer')          cur.classList.add('is-pointer');
            else if (type === 'text')        cur.classList.add('is-text');
            else if (type === 'not-allowed') cur.classList.add('is-notallowed');
        });
        document.addEventListener('mouseleave', () => cur.style.opacity = '0');
        document.addEventListener('mouseenter', () => cur.style.opacity = '1');
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
                ['channel-item','metrics-pill','comp-btn','emoji-btn','ac-item',
                 'stats-btn','del-btn','modal-submit','brand-orb','msg-action-btn',
                 'modal-close','composer-send','settings-btn','cursor-upload-btn',
                 'cursor-reset-btn','quality-btn','mod-action-btn','cmd-send-btn',
                 'warn-badge-history','warn-history-btn'].some(c => node.classList.contains(c)))
                return 'pointer';
            node = node.parentElement;
        }
        return 'default';
    }

    // ─── BOT INFO ─────────────────────────────────────────────────────────────
    async function fetchBotInfo() {
        try {
            const r = await fetch('/api/botinfo');
            if (r.ok) {
                const j = await r.json();
                botName       = j.name || null;
                window._botId = j.id  || null;
            }
        } catch (e) { console.warn('botinfo fetch failed', e); }
    }

    // ─── EVENTOS ──────────────────────────────────────────────────────────────
    function setupEventListeners() {
        if (msgInput) {
            msgInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendMessage(); });
            msgInput.addEventListener('input',    handleAcInput);
            msgInput.addEventListener('keydown',  handleAcKeydown);
        }
        if (sendBtn) sendBtn.onclick = sendMessage;

        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') closeAllModals();
            // AltGr + 0 abre consola de logs
            if (e.code === 'Digit0' && e.getModifierState('AltGraph')) {
                e.preventDefault();
                openLogs();
            }
        });

        document.addEventListener('pointerdown', e => {
            if (!acBox || !acBox.classList.contains('ac-open')) return;
            if (!acBox.contains(e.target) && !msgInput?.contains(e.target)) hideAc();
        });
    }

    function closeAllModals() {
        ['stats-drawer','stats-modal','commands-panel','settings-panel',
         'mod-panel','login-modal','logs-panel','warn-history-modal']
            .forEach(id => document.getElementById(id)?.classList.remove('active'));
    }

    // ─── CANALES ──────────────────────────────────────────────────────────────
    async function fetchChannels() {
        try {
            const res      = await fetch('/api/channels');
            const channels = await res.json();
            if (!Array.isArray(channels) || !channels.length) return;

            channelList.innerHTML = '';
            const drawerList = document.getElementById('channel-drawer-list');
            if (drawerList) drawerList.innerHTML = '';

            channels.forEach((ch, idx) => {
                const cName = ch.name || 'Unknown';
                const cId   = ch.id;
                channelMap[cId] = cName;
                channelList.appendChild(createChannelElement(cName, cId, idx));
                if (drawerList) drawerList.appendChild(createChannelElement(cName, cId, idx, true));
            });

            if (channelCount) channelCount.textContent = channels.length;
        } catch (e) { console.error('fetchChannels error', e); }
    }

    function createChannelElement(name, id, idx, forDrawer = false) {
        const div  = document.createElement('div');
        div.className = 'channel-item';
        div.setAttribute('data-index', String(idx + 1).padStart(2, '0'));
        const span = document.createElement('span');
        span.className   = 'ch-name';
        span.textContent = name.toUpperCase();
        div.appendChild(span);
        div.onclick = () => {
            currentChannelId = id;
            document.querySelectorAll('.channel-item').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.channel-item').forEach(b => {
                if (b.querySelector('.ch-name')?.textContent === name.toUpperCase())
                    b.classList.add('active');
            });
            if (chatChannelName) chatChannelName.innerText = name.toUpperCase();
            resetTimer();
            chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">⚡</div><p class="feed-empty-title">Receiving signal...</p><p class="feed-empty-sub">信号受信中</p></div>';
            fetchMessages(true);
            if (forDrawer) document.getElementById('channel-drawer')?.classList.remove('open');
        };
        return div;
    }

    // ─── TIMER ────────────────────────────────────────────────────────────────
    function startTimer() { resetTimer(); }
    function resetTimer() {
        if (timerInterval) clearInterval(timerInterval);
        let seconds = 0;
        if (chatUptime) { chatUptime.innerText = '00:00'; chatUptime.style.color = 'var(--teal)'; }
        timerInterval = setInterval(() => {
            seconds++;
            const m = Math.floor(seconds / 60).toString().padStart(2, '0');
            const s = (seconds % 60).toString().padStart(2, '0');
            if (chatUptime) {
                chatUptime.innerText = `${m}:${s}`;
                chatUptime.style.color = seconds < 300 ? 'var(--teal)' : seconds < 900 ? 'var(--amber)' : 'var(--coral)';
            }
        }, 1000);
    }

    // ─── MENSAJES ─────────────────────────────────────────────────────────────
    async function fetchMessages(initial = false) {
        if (!currentChannelId) return;
        isFetching = true;

        try {
            const now = Date.now();
            document.querySelectorAll('[data-optimistic="1"]').forEach(n => {
                if ((now - parseInt(n.dataset.optimisticTs || 0)) > 5000) n.remove();
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

            const res  = await fetch(url);
            const msgs = await res.json();
            const nearBottom = (chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight) < 150;

            await resolveMentions(msgs);

            if (initial) {
                chatFeed.innerHTML = '';
                if (!msgs.length) {
                    chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">無</div><p class="feed-empty-title">No messages yet</p><p class="feed-empty-sub">信号なし</p></div>';
                } else {
                    msgs.forEach(msg => appendMessage(msg, false));
                }
                chatFeed.scrollTop = chatFeed.scrollHeight;
            } else {
                if (msgs.length) {
                    msgs.forEach(msg => appendMessage(msg, true));
                    chatFeed.scrollTop = chatFeed.scrollHeight;
                }
            }

            msgs.forEach(msg => {
                if (msg.message_id) {
                    const prev = lastMessageId[currentChannelId] || '0';
                    if (BigInt(String(msg.message_id)) > BigInt(prev))
                        lastMessageId[currentChannelId] = String(msg.message_id);
                }
            });
        } catch (e) {
            console.error('fetchMessages error', e);
        } finally {
            isFetching = false;
        }
    }

    // ─── RESOLUCIÓN DE MENCIONES ──────────────────────────────────────────────
    async function resolveMentions(msgs) {
        const userIds = new Set(), roleIds = new Set();
        const reU = /<@!?(\d+)>/g, reR = /<@&(\d+)>/g;
        msgs.forEach(m => {
            let x;
            const c = m.content || '';
            // Reset lastIndex before each use
            reU.lastIndex = 0; reR.lastIndex = 0;
            while ((x = reU.exec(c)) !== null) userIds.add(x[1]);
            while ((x = reR.exec(c)) !== null) roleIds.add(x[1]);
        });
        const toU = Array.from(userIds).filter(id => !mentionCache.users[id]);
        const toR = Array.from(roleIds).filter(id => !mentionCache.roles[id]);
        if (!toU.length && !toR.length) return;
        try {
            const r = await fetch('/api/mention_lookup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ users: toU, roles: toR })
            });
            if (r.ok) {
                const j = await r.json();
                if (j.users) Object.assign(mentionCache.users, j.users);
                if (j.roles) Object.assign(mentionCache.roles, j.roles);
            }
        } catch (e) {}
    }

    // ─── RENDERIZADO DE CONTENIDO DISCORD ────────────────────────────────────
    function renderDiscordContent(text) {
        if (!text) return '';

        // 1. Escapar todo HTML primero (previene XSS y el HTML en mensajes de spam)
        let w = escapeHtml(text);

        // 2. Menciones de usuario → span clickeable para deadline
        w = w.replace(/&lt;@!?(\d+)&gt;/g, (_, id) => {
            const cached = mentionCache.users[id];
            let display;
            if (cached && typeof cached === 'object') {
                display = cached.display || cached.username || `@${id}`;
            } else if (typeof cached === 'string') {
                display = cached;
            } else {
                display = `@User_${id.substring(0, 4)}`;
            }
            return `<span class="mention mention-user" title="ID: ${id}" style="cursor:pointer;" onclick="promptDeadline('${id}')">${escapeHtml(display)}</span>`;
        });

        // 3. Menciones de rol
        w = w.replace(/&lt;@&amp;(\d+)&gt;/g, (_, id) => {
            const cached = mentionCache.roles[id];
            let roleName = `role_${id}`, roleColor = '#a78bfa';
            if (cached && typeof cached === 'object') {
                roleName  = cached.name  || roleName;
                roleColor = cached.color || roleColor;
            } else if (typeof cached === 'string') {
                roleName = cached;
            }
            return `<span class="mention mention-role" style="color:${roleColor};background:${roleColor}22">${escapeHtml(roleName)}</span>`;
        });

        // 4. Menciones de canal
        w = w.replace(/&lt;#(\d+)&gt;/g, (_, id) => {
            const name = channelMap[id];
            return name
                ? `<span class="mention mention-channel">#${escapeHtml(name)}</span>`
                : `<span class="mention">#${id}</span>`;
        });

        // 5. Timestamps Discord <t:UNIX:R>
        w = w.replace(/&lt;t:(\d+)(?::([tTdDfFR]))?&gt;/g, (_, ts, fmt) => {
            try {
                const d   = new Date(parseInt(ts) * 1000);
                const now = Date.now();
                const diff = Math.round((d.getTime() - now) / 1000);
                let str;
                if (fmt === 'R') {
                    const abs = Math.abs(diff);
                    const rel = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.floor(abs/60)}m` : abs < 86400 ? `${Math.floor(abs/3600)}h` : `${Math.floor(abs/86400)}d`;
                    str = diff > 0 ? `in ${rel}` : `${rel} ago`;
                } else {
                    str = d.toLocaleString();
                }
                return `<span class="mention mention-ts" title="${d.toISOString()}">${str}</span>`;
            } catch (e) { return `&lt;t:${ts}&gt;`; }
        });

        // 6. Links
        w = w.replace(/(https?:\/\/[^\s<>"]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');

        // 7. Markdown básico
        w = w.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        w = w.replace(/\*(.+?)\*/g,     '<em>$1</em>');
        w = w.replace(/`(.+?)`/g,       '<code>$1</code>');
        w = w.replace(/\n/g,            '<br>');

        return w;
    }

    // ─── APPEND MESSAGE ───────────────────────────────────────────────────────
    function appendMessage(msg, isNew = false) {
        if (chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`)) return;

        const el = document.createElement('div');
        el.className = 'message';
        if (botName && ((msg.author_id && String(msg.author_id) === String(window._botId || '')) || msg.author_name === botName))
            el.classList.add('msg-me');

        el.dataset.msgId = String(msg.message_id);
        const rendered = renderDiscordContent(msg.content || '');
        el.innerHTML = `
            <div class="msg-header">
                <div class="msg-time">${formatTimestamp(msg.timestamp)}</div>
                <div class="msg-author">${escapeHtml(msg.author_name || 'Unknown')}</div>
            </div>
            <div class="msg-content">${rendered}</div>`;
        decorateMessage(el);
        chatFeed.appendChild(el);

        if (isNew) {
            requestAnimationFrame(() => {
                el.style.setProperty('--flash', '1');
                setTimeout(() => el.style.removeProperty('--flash'), 600);
            });
        }
    }

    function formatTimestamp(ts) {
        if (!ts) return '——';
        try {
            const d = new Date(ts.replace(' ', 'T'));
            if (isNaN(d.getTime())) return ts.slice(0, 16) || '——';
            const locale = navigator.language || 'es-ES';
            const time   = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false });
            const sameDay = d.toDateString() === new Date().toDateString();
            if (sameDay) return time;
            return d.toLocaleDateString(locale, { day: '2-digit', month: '2-digit' }) + ' ' + time;
        } catch (e) { return '——'; }
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function decorateMessage(msgEl) {
        if (!msgEl.dataset.msgId || msgEl.querySelector('.msg-actions')) return;
        const isOwn   = msgEl.classList.contains('msg-me');
        const actions = document.createElement('div');
        actions.className = 'msg-actions';
        const reactBtn = document.createElement('button');
        reactBtn.className = 'msg-action-btn'; reactBtn.innerHTML = '😊'; reactBtn.title = 'Reaccionar';
        actions.appendChild(reactBtn);
        if (isOwn) {
            const delBtn = document.createElement('button');
            delBtn.className = 'msg-action-btn del'; delBtn.innerHTML = '🗑️'; delBtn.title = 'Borrar';
            actions.appendChild(delBtn);
        }
        const header = msgEl.querySelector('.msg-header');
        (header || msgEl).appendChild(actions);
    }

    // ─── AUTOCOMPLETADO @ ─────────────────────────────────────────────────────
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
        const val   = msgInput.value;
        const caret = msgInput.selectionStart;
        // Buscar hacia atrás el @ más cercano sin espacios intermedios
        let atPos = -1;
        for (let i = caret - 1; i >= 0; i--) {
            if (val[i] === '@') { atPos = i; break; }
            // Solo rompemos si hay un espacio Y ya tenemos algún texto de query
            // (permitimos @ solo, o @texto, pero no @ texto con espacio en medio)
            if (val[i] === ' ' || val[i] === '\n') break;
        }
        if (atPos === -1) { hideAc(); return; }
        const query = val.slice(atPos + 1, caret).toLowerCase();
        // Si la query tiene un espacio interno (ej: "@foo bar"), cerrar
        if (query.includes(' ')) { hideAc(); return; }
        acTriggerPos = atPos;
        await ensureAcData();
        const roleMatches = acRoles
            .filter(r => !query || r.name.toLowerCase().includes(query))
            .slice(0, query ? 4 : 3).map(r => ({ ...r, type: 'role' }));
        const memberMatches = acMembers
            .filter(m => !query || m.display.toLowerCase().includes(query) || m.username.toLowerCase().includes(query))
            .slice(0, 10 - roleMatches.length).map(m => ({ ...m, type: 'user' }));
        const matches = [...roleMatches, ...memberMatches];
        if (!matches.length) { hideAc(); return; }
        renderAc(matches);
    }
    function handleAcKeydown(e) {
        if (!acItems.length) return;
        if (e.key === 'ArrowDown')  { e.preventDefault(); setAcIdx(Math.min(acIdx + 1, acItems.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setAcIdx(Math.max(acIdx - 1, 0)); }
        else if (e.key === 'Tab' || e.key === 'Enter') { e.preventDefault(); applyAc(acIdx >= 0 ? acIdx : 0); }
        else if (e.key === 'Escape') hideAc();
    }
    function renderAc(matches) {
        const box = getAcBox();
        box.innerHTML = '';
        acItems = matches; acIdx = -1;
        matches.forEach((item, i) => {
            const el = document.createElement('div');
            el.className = 'ac-item';
            el.dataset.index = i;
            if (item.type === 'user') {
                const av = document.createElement('div'); av.className = 'ac-avatar';
                if (item.avatar) {
                    const img = document.createElement('img');
                    img.src = item.avatar; img.className = 'ac-avatar-img';
                    av.appendChild(img);
                } else {
                    av.textContent = (item.display || item.username || '?')[0].toUpperCase();
                }
                const info = document.createElement('div'); info.className = 'ac-info';
                const nm   = document.createElement('span'); nm.className = 'ac-name'; nm.textContent = item.display;
                const hd   = document.createElement('span'); hd.className = 'ac-handle'; hd.textContent = '@' + item.username;
                info.appendChild(nm); info.appendChild(hd);
                el.appendChild(av); el.appendChild(info);
            } else {
                const dot   = document.createElement('span'); dot.className = 'ac-role-dot'; dot.style.background = item.color;
                const label = document.createElement('span'); label.className = 'ac-role-name'; label.style.color = item.color; label.textContent = '@' + item.name;
                const tag   = document.createElement('span'); tag.className = 'ac-role-tag'; tag.textContent = 'ROLE';
                el.appendChild(dot); el.appendChild(label); el.appendChild(tag);
            }
            el.addEventListener('mouseenter', () => setAcIdx(i));
            el.addEventListener('click', () => applyAc(i));
            box.appendChild(el);
        });
        box.classList.add('ac-open');
        const rect = msgInput.getBoundingClientRect();
        box.style.width  = Math.min(460, rect.width - 20) + 'px';
        box.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
        box.style.left   = rect.left + 'px';
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
        const val  = msgInput.value;
        const tag  = item.type === 'user' ? `<@${item.id}> ` : `<@&${item.id}> `;
        const before = val.slice(0, acTriggerPos);
        const after  = val.slice(msgInput.selectionStart);
        msgInput.value = before + tag + after;
        const newPos = before.length + tag.length;
        msgInput.setSelectionRange(newPos, newPos);
        hideAc(); msgInput.focus();
    }
    async function ensureAcData() {
        if (acLoaded) return;
        try {
            const r = await fetch('/api/members');
            if (r.ok) {
                const j = await r.json();
                acMembers = j.members || [];
                acRoles   = j.roles   || [];
                acLoaded  = true;
            }
        } catch (e) {}
    }

    // ─── ENVÍO DE MENSAJES + COMANDOS / ──────────────────────────────────────
    async function sendMessage() {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;

        // ── /done ─────────────────────────────────────────────────────────────
        if (content === '/done') {
            msgInput.value = '';
            showToast('Registrando EP…', 'info');
            try {
                const res  = await fetch('/api/done', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        channel_id: currentChannelId,
                        user_id: window._doneUserId || window._botId || '0'
                    })
                });
                const data = await res.json();
                if (data.error) showToast('❌ ' + data.error, 'error');
                else showToast('✅ ' + (data.text || 'EP registrado'), 'success');
                fetchMessages(false);
            } catch (e) {
                showToast('❌ Error: ' + e.message, 'error');
            }
            msgInput.disabled = false;
            msgInput.focus();
            return;
        }

        // ── /deadline ─────────────────────────────────────────────────────────
        if (content.startsWith('/deadline ')) {
            const rawTarget = content.slice('/deadline '.length).trim();
            const targetId  = rawTarget.replace(/[<@!>]/g, '').trim();
            if (targetId) {
                window.triggerWebDeadline(targetId);
                msgInput.value = '';
            } else {
                showToast('Uso: /deadline @Usuario o /deadline ID_USUARIO', 'warn');
            }
            return;
        }

        let optimisticNode = null;
        const optimisticClientId = String(Date.now()) + Math.floor(Math.random() * 1000);
        try {
            optimisticNode = document.createElement('div');
            optimisticNode.className = 'message msg-me';
            optimisticNode.setAttribute('data-optimistic', '1');
            optimisticNode.dataset.clientId     = optimisticClientId;
            optimisticNode.dataset.optimisticTs = String(Date.now());
            optimisticNode.innerHTML = `
                <div class="msg-header">
                    <div class="msg-time">——:——</div>
                    <div class="msg-author">${escapeHtml(botName || 'BOT')}</div>
                </div>
                <div class="msg-content">${renderDiscordContent(content)}</div>`;
            chatFeed.appendChild(optimisticNode);
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch (e) {}

        msgInput.value = '';
        msgInput.disabled = true;
        try {
            const res  = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Server Reject');
            if (data.message_id) {
                chatFeed.querySelector(`[data-client-id="${optimisticClientId}"]`)?.remove();
                fetchMessages(false);
            }
        } catch (e) {
            console.error('Send error:', e);
            if (optimisticNode?.parentNode) optimisticNode.remove();
            msgInput.value = content;
            showToast('Error al enviar: ' + e.message, 'error');
        } finally {
            msgInput.disabled = false;
            msgInput.focus();
        }
    }

    // ─── DEADLINE ─────────────────────────────────────────────────────────────
    window.promptDeadline = function (targetId) {
        const cached = mentionCache.users[targetId];
        const name   = (cached && typeof cached === 'object') ? cached.display : `ID ${targetId}`;
        if (confirm(`¿Enviar DEADLINE de 24h a ${name}?`))
            window.triggerWebDeadline(targetId);
    };

    window.triggerWebDeadline = function (targetId) {
        if (!currentChannelId) {
            showToast('Selecciona un canal primero', 'warn');
            return;
        }
        // Resolver nombre si está en caché
        const cached = mentionCache.users[targetId];
        const name   = (cached && typeof cached === 'object') ? cached.display : targetId;

        fetch('/api/deadline', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_id: targetId, channel_id: currentChannelId })
        })
        .then(r => r.json())
        .then(d => {
            showToast(`✅ Deadline enviado a ${name}`, 'success');
            fetchMessages(false);
        })
        .catch(err => showToast('Error: ' + err, 'error'));
    };

    // ─── LOGS PANEL ───────────────────────────────────────────────────────────
    let logsInterval = null;
    let logsUnlocked = false;

    function openLogs() {
        const panel = document.getElementById('logs-panel');
        if (!panel) return;
        panel.classList.add('active');
        if (!logsUnlocked) {
            document.getElementById('logs-auth').style.display  = 'flex';
            document.getElementById('logs-viewer').style.display = 'none';
            document.getElementById('logs-user').value = '';
            document.getElementById('logs-pass').value = '';
            document.getElementById('logs-error').textContent = '';
        } else {
            fetchLogs();
        }
    }

    window.openLogs  = openLogs;
    window.fetchLogs = fetchLogs;   // expuesto para el onchange del input de líneas

    window.closeLogs = function () {
        document.getElementById('logs-panel')?.classList.remove('active');
        clearInterval(logsInterval);
    };

    window.authLogs = function () {
        const user = document.getElementById('logs-user').value;
        const pass = document.getElementById('logs-pass').value;
        if (user === 'ogmhabas' && pass === 'blz-tadmin') {
            logsUnlocked = true;
            document.getElementById('logs-auth').style.display   = 'none';
            document.getElementById('logs-viewer').style.display = 'block';
            fetchLogs();
            clearInterval(logsInterval);
            logsInterval = setInterval(() => {
                if (document.getElementById('logs-panel')?.classList.contains('active'))
                    fetchLogs();
                else clearInterval(logsInterval);
            }, 3000);
        } else {
            document.getElementById('logs-error').textContent = '❌ Credenciales incorrectas';
        }
    };

    async function fetchLogs() {
        try {
            const linesInput = document.getElementById('logs-lines-input');
            const lines = linesInput ? (parseInt(linesInput.value) || 200) : 200;
            const res   = await fetch(`/api/logs?lines=${lines}`);
            const text  = await res.text();
            const pre   = document.getElementById('logs-content');
            if (pre) {
                pre.innerHTML = colorizeLogs(escapeHtml(text));
                const viewer = document.getElementById('logs-viewer');
                if (viewer) viewer.scrollTop = viewer.scrollHeight;
            }
        } catch (e) {
            const pre = document.getElementById('logs-content');
            if (pre) pre.textContent = 'Error cargando logs: ' + e.message;
        }
    }

    // Coloriza líneas según nivel de log
    function colorizeLogs(escapedText) {
        return escapedText.split('\n').map(line => {
            if (line.includes('[ERROR]') || line.includes('!!!'))
                return `<span style="color:#ff6b6b">${line}</span>`;
            if (line.includes('[WARNING]') || line.includes('[WARN]'))
                return `<span style="color:#f5a623">${line}</span>`;
            if (line.includes('[AUTOMOD]') || line.includes('AUTOMOD') || line.includes('SPAM'))
                return `<span style="color:#a78bfa">${line}</span>`;
            if (line.includes('[SLASH]') || line.includes('[DEADLINE]') || line.includes('>>>'))
                return `<span style="color:#26c9b8">${line}</span>`;
            if (line.includes('[HISTORY]') || line.includes('[SHEETS]'))
                return `<span style="color:#f5a623aa">${line}</span>`;
            return `<span style="color:#5a5575">${line}</span>`;
        }).join('\n');
    }

    // ─── LOGIN ────────────────────────────────────────────────────────────────
    window.toggleLogin = function () { document.getElementById('login-modal')?.classList.toggle('active'); };
    window.doLogin = function () {
        const user = document.getElementById('login-user')?.value.trim();
        const pass = document.getElementById('login-pass')?.value;
        if (user === 'admin' && pass === 'blzt2024') {
            localStorage.setItem('blzt_mod', '1');
            applyLoginState(true);
            toggleLogin();
            showToast('Sesión iniciada ✅', 'success');
        } else {
            document.getElementById('login-error').textContent = '❌ Credenciales incorrectas';
        }
    };
    function applyLoginState(loggedIn) {
        const btn  = document.getElementById('login-btn');
        const lbl  = document.getElementById('login-btn-label');
        const modB = document.getElementById('mod-btn');
        if (btn)  btn.classList.toggle('logged-in', loggedIn);
        if (lbl)  lbl.textContent = loggedIn ? 'Logout' : 'Login';
        if (modB) modB.style.display = loggedIn ? 'flex' : 'none';
        if (!loggedIn) document.getElementById('mod-panel')?.classList.remove('active');
    }

    // ─── MOD PANEL ────────────────────────────────────────────────────────────
    window.toggleMod = function () {
        const panel   = document.getElementById('mod-panel');
        if (!panel) return;
        const opening = !panel.classList.contains('active');
        panel.classList.toggle('active');
        if (opening) { loadModPanel('warned'); loadModStats(); }
    };
    window.switchModTab = function (tab) {
        document.querySelectorAll('.mod-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
        loadModPanel(tab);
    };

    async function loadModStats() {
        try {
            const r = await fetch('/api/stats');
            if (!r.ok) return;
            const s = await r.json();
            const el = document.getElementById('mod-stats-bar');
            if (el) el.innerHTML = `
                <span class="mod-stat">💬 ${s.total_messages} msgs</span>
                <span class="mod-stat">⚠ ${s.total_warnings} warns</span>
                <span class="mod-stat">👤 ${s.unique_warned_users} sancionados</span>
                <span class="mod-stat">🔨 ${s.total_mod_actions} acciones</span>`;
        } catch (e) {}
    }

    async function loadModPanel(tab = 'warned') {
        const body = document.getElementById('mod-body');
        if (!body) return;
        body.innerHTML = '<div class="mod-loading">Cargando...</div>';
        try {
            if (tab === 'warned') {
                const res  = await fetch('/api/mod/users');
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                renderWarnedUsers(data, body);
            } else {
                const res  = await fetch('/api/mod/action_log');
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                renderActionLog(data, body);
            }
        } catch (e) {
            body.innerHTML = `<div class="mod-empty">❌ Error: ${escapeHtml(e.message)}</div>`;
        }
    }

    function renderWarnedUsers(users, container) {
        if (!users.length) {
            container.innerHTML = '<div class="mod-empty">✅ Sin usuarios sancionados</div>';
            return;
        }
        container.innerHTML = '';
        users.forEach(u => {
            const card    = document.createElement('div');
            card.className = 'mod-user-card';
            const initial = (u.user_name || '?')[0].toUpperCase();
            const lastAct = u.last_action ? u.last_action.action.toUpperCase() : null;

            // Últimas warns con mensaje que las activó — siempre escapeHtml en contenido del mensaje
            const recentHtml = (u.recent_warnings || []).slice(0, 2).map(w => {
                const reason  = escapeHtml(w.reason || '—');
                const msgContent = w.message_content
                    ? `<div class="warn-msg-preview">
                           <span class="warn-msg-label">💬</span>
                           <span class="warn-msg-text">${escapeHtml(w.message_content).substring(0, 100)}${w.message_content.length > 100 ? '…' : ''}</span>
                       </div>`
                    : '';
                const link = w.message_link
                    ? `<a class="warn-msg-link" href="${escapeHtml(w.message_link)}" target="_blank">🔗</a>`
                    : '';
                return `<div class="warn-entry">
                    <span class="warn-entry-reason">${reason}</span>${link}
                    ${msgContent}
                </div>`;
            }).join('');

            card.innerHTML = `
                <div class="mod-user-avatar">${initial}</div>
                <div class="mod-user-info">
                    <div class="mod-user-name">${escapeHtml(u.user_name || 'Unknown')}</div>
                    <div class="mod-user-meta">ID: ${escapeHtml(u.user_id)}</div>
                    <div class="warn-badges">
                        <span class="warn-badge warn-badge-count">⚠ ${u.warn_count} warn${u.warn_count !== 1 ? 's' : ''}</span>
                        ${lastAct ? `<span class="warn-badge warn-badge-action">${lastAct}</span>` : ''}
                    </div>
                    ${recentHtml}
                    <button class="warn-history-btn" onclick="showWarnHistory('${escapeHtml(u.user_id)}','${escapeHtml(u.user_name||'Unknown')}')">
                        📋 Historial completo (${u.warn_count})
                    </button>
                </div>
                <div class="mod-user-actions">
                    <button class="mod-action-btn warn"    onclick="modAction('warn','${escapeHtml(u.user_id)}','${escapeHtml(u.user_name||'')}','${escapeHtml(u.guild_id||'')}',this)">Warn</button>
                    <button class="mod-action-btn timeout" onclick="modAction('timeout','${escapeHtml(u.user_id)}','${escapeHtml(u.user_name||'')}','${escapeHtml(u.guild_id||'')}',this)">Timeout</button>
                    <button class="mod-action-btn kick"    onclick="modAction('kick','${escapeHtml(u.user_id)}','${escapeHtml(u.user_name||'')}','${escapeHtml(u.guild_id||'')}',this)">Kick</button>
                    <button class="mod-action-btn ban"     onclick="modAction('ban','${escapeHtml(u.user_id)}','${escapeHtml(u.user_name||'')}','${escapeHtml(u.guild_id||'')}',this)">Ban</button>
                    <button class="mod-action-btn clear"   onclick="modAction('clear','${escapeHtml(u.user_id)}','${escapeHtml(u.user_name||'')}','${escapeHtml(u.guild_id||'')}',this)">Clear</button>
                </div>`;
            container.appendChild(card);
        });
    }

    function renderActionLog(actions, container) {
        if (!actions.length) {
            container.innerHTML = '<div class="mod-empty">Sin acciones registradas</div>';
            return;
        }
        const table = document.createElement('table');
        table.className = 'mod-log-table';
        table.innerHTML = `
            <thead><tr>
                <th>Usuario</th><th>Acción</th><th>Razón</th><th>Moderador</th><th>Fecha</th>
            </tr></thead>
            <tbody>${actions.map(a => `
                <tr>
                    <td>${escapeHtml(a.user_name || a.user_id)}</td>
                    <td><span class="log-action-badge ${escapeHtml(a.action)}">${escapeHtml(a.action)}</span></td>
                    <td title="${escapeHtml(a.reason||'')}">${escapeHtml((a.reason||'—').substring(0,60))}${(a.reason||'').length>60?'…':''}</td>
                    <td>${escapeHtml(a.moderator_name || '—')}</td>
                    <td>${a.timestamp ? a.timestamp.slice(0,16) : '—'}</td>
                </tr>`).join('')}
            </tbody>`;
        container.innerHTML = '';
        container.appendChild(table);
    }

    // ─── WARN HISTORY MODAL ───────────────────────────────────────────────────
    window.showWarnHistory = async function (userId, userName) {
        const modal = document.getElementById('warn-history-modal');
        if (!modal) return;
        modal.classList.add('active');
        const title   = document.getElementById('wh-title');
        const content = document.getElementById('wh-content');
        if (title)   title.textContent = `📋 Historial: ${userName}`;
        if (content) content.innerHTML = '<div class="mod-loading">Cargando...</div>';

        try {
            const r    = await fetch(`/api/mod/warn_history/${encodeURIComponent(userId)}`);
            const data = await r.json();
            if (data.error) throw new Error(data.error);
            if (!data.length) {
                content.innerHTML = '<div class="mod-empty">Sin advertencias registradas</div>';
                return;
            }
            content.innerHTML = data.map((w, i) => {
                // CRÍTICO: escapeHtml en message_content para evitar XSS y renderizado HTML no deseado
                const msgBlock = w.message_content
                    ? `<div class="wh-msg-content">
                           <span class="wh-msg-label">💬 Mensaje que activó la sanción:</span>
                           <pre class="wh-msg-pre">${escapeHtml(w.message_content)}</pre>
                       </div>`
                    : '<div class="wh-msg-content"><em style="color:var(--t2)">Sin mensaje guardado</em></div>';
                const link = w.message_link
                    ? `<a class="warn-msg-link" href="${escapeHtml(w.message_link)}" target="_blank" rel="noopener">🔗 Ver en Discord</a>`
                    : '';
                return `<div class="wh-entry">
                    <div class="wh-entry-header">
                        <span class="wh-num">#${data.length - i}</span>
                        <span class="wh-reason">${escapeHtml(w.reason || '—')}</span>
                        <span class="wh-mod">por ${escapeHtml(w.moderator_name || 'AutoMod')}</span>
                        <span class="wh-ts">${w.timestamp ? w.timestamp.slice(0,16) : '—'}</span>
                    </div>
                    ${msgBlock}${link}
                </div>`;
            }).join('');
        } catch (e) {
            if (content) content.innerHTML = `<div class="mod-empty">❌ Error: ${escapeHtml(e.message)}</div>`;
        }
    };

    window.closeWarnHistory = function () {
        document.getElementById('warn-history-modal')?.classList.remove('active');
    };

    // ─── ACCIONES DE MOD ──────────────────────────────────────────────────────
    window.modAction = async function (action, uid, uname, gid, btn) {
        const labels = {
            warn:'advertir a', timeout:'silenciar 24h a', kick:'expulsar a',
            ban:'banear a', clear:'limpiar warns de'
        };
        if (!confirm(`¿${labels[action] || action} ${uname}?`)) return;

        let reason = 'Manual action from web panel';
        if (action !== 'clear') {
            const input = prompt(`Razón para ${action} a ${uname}:`, reason);
            if (input === null) return;
            reason = input.trim() || reason;
        }

        if (btn) { btn.disabled = true; btn.textContent = '…'; }
        try {
            const res  = await fetch('/api/mod/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, user_id: uid, user_name: uname, guild_id: gid, reason })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            showToast(`✅ ${action.toUpperCase()} aplicado a ${uname}`, 'success');
            setTimeout(() => { loadModPanel('warned'); loadModStats(); }, 600);
        } catch (e) {
            showToast(`❌ Error: ${e.message}`, 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                const btnLabels = { warn:'Warn', timeout:'Timeout', kick:'Kick', ban:'Ban', clear:'Clear' };
                btn.textContent = btnLabels[action] || action;
            }
        }
    };

    // ─── TOAST ────────────────────────────────────────────────────────────────
    function showToast(message, type = 'info') {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
            document.body.appendChild(container);
        }
        const toast = document.createElement('div');
        const colors = { success:'#26c9b8', error:'#ff6b6b', warn:'#f5a623', info:'#a78bfa' };
        const c = colors[type] || colors.info;
        toast.style.cssText = `background:rgba(20,18,31,0.95);border:1px solid ${c};color:${c};padding:10px 16px;border-radius:8px;font-size:13px;font-family:var(--f-display,sans-serif);backdrop-filter:blur(10px);pointer-events:auto;max-width:320px;box-shadow:0 4px 20px rgba(0,0,0,0.4);transition:opacity 0.3s;`;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3500);
    }

    // ─── STATS ────────────────────────────────────────────────────────────────
    window.generateStats = function () {
        let type = 'offensive';
        if (document.getElementById('dvg')?.value.trim() !== '') type = 'gk';
        const inputs = type === 'offensive' ? ['sht','dbl','stl','psn','dfd'] : ['dvg','biq','rfx','dtg'];
        let data = {}, sum = 0;
        inputs.forEach(id => {
            let val = parseFloat(document.getElementById(id)?.value) || 0;
            val = Math.min(10, Math.max(0, val));
            data[id] = val; sum += val;
        });
        const avg  = sum / inputs.length;
        const rank = type === 'offensive' ? getOffensiveRank(avg) : getGKRank(avg);
        drawGraph(type, data, avg, rank);
        document.getElementById('stats-modal')?.classList.add('active');
        document.getElementById('stats-drawer')?.classList.remove('active');
    };

    function getOffensiveRank(s) {
        if (s < 4.6) return 'N/A';
        const ranks = [
            [4.8,'ROOKIE 🥉 - ⭐'],[5.1,'ROOKIE 🥉 - ⭐⭐'],[5.4,'ROOKIE 🥉 - ⭐⭐⭐'],
            [5.7,'AMATEUR ⚽ - ⭐'],[6.0,'AMATEUR ⚽ - ⭐⭐'],[6.3,'AMATEUR ⚽ - ⭐⭐⭐'],
            [6.6,'ELITE ⚡ - ⭐'],[6.9,'ELITE ⚡ - ⭐⭐'],[7.2,'ELITE ⚡ - ⭐⭐⭐'],
            [7.5,'PRODIGY 🏅 - ⭐'],[7.8,'PRODIGY 🏅 - ⭐⭐'],[8.1,'PRODIGY 🏅 - ⭐⭐⭐'],
            [8.4,'NEW GEN XI - ⭐'],[8.7,'NEW GEN XI - ⭐⭐'],[9.0,'NEW GEN XI - ⭐⭐⭐'],
            [9.3,'WORLD CLASS 👑 - ⭐'],[9.6,'WORLD CLASS 👑 - ⭐⭐'],[Infinity,'WORLD CLASS 👑 - ⭐⭐⭐']
        ];
        return (ranks.find(([max]) => s <= max) || ranks[ranks.length-1])[1];
    }
    function getGKRank(s) {
        if (s <= 6.9) return 'D TIER'; if (s <= 7.9) return 'C TIER';
        if (s <= 8.4) return 'B TIER'; if (s <= 8.9) return 'A TIER';
        if (s <= 9.4) return 'S TIER'; return 'S+ TIER';
    }
    function drawGraph(type, data, avg, rank) {
        if (!ctx) return;
        const W=500,H=500,CX=250,CY=248,R=132;
        const isGK=type==='gk', mainCol=isGK?'#A78BFA':'#F5A623',
              fillCol=isGK?'rgba(167,139,250,0.25)':'rgba(245,166,35,0.22)',
              glowCol=isGK?'rgba(167,139,250,0.55)':'rgba(245,166,35,0.50)';
        ctx.clearRect(0,0,W,H); ctx.fillStyle='#0F0E17'; ctx.fillRect(0,0,W,H);
        const grd=ctx.createRadialGradient(CX,CY,0,CX,CY,R*1.3);
        grd.addColorStop(0,isGK?'rgba(167,139,250,0.06)':'rgba(245,166,35,0.06)');
        grd.addColorStop(1,'transparent'); ctx.fillStyle=grd; ctx.fillRect(0,0,W,H);
        const keys=Object.keys(data),total=keys.length,step=(Math.PI*2)/total;
        for(let l=1;l<=4;l++){
            const rad=(R/4)*l; ctx.beginPath();
            if(isGK) ctx.arc(CX,CY,rad,0,Math.PI*2);
            else keys.forEach((_,i)=>{const a=i*step-Math.PI/2; i===0?ctx.moveTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad):ctx.lineTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad);});
            ctx.strokeStyle=isGK?`rgba(167,139,250,${l===4?0.3:0.08})`:`rgba(245,166,35,${l===4?0.3:0.08})`;
            ctx.lineWidth=l===4?1.5:0.8; ctx.stroke();
        }
        ctx.beginPath();
        keys.forEach((k,i)=>{const rad=(data[k]/10)*R,a=i*step-Math.PI/2; i===0?ctx.moveTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad):ctx.lineTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad);});
        ctx.closePath(); ctx.shadowBlur=22; ctx.shadowColor=glowCol;
        ctx.fillStyle=fillCol; ctx.fill(); ctx.strokeStyle=mainCol; ctx.lineWidth=2.5; ctx.stroke(); ctx.shadowBlur=0;
        keys.forEach((k,i)=>{const rad=(data[k]/10)*R,a=i*step-Math.PI/2,x=CX+Math.cos(a)*rad,y=CY+Math.sin(a)*rad;
            ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.shadowBlur=10;ctx.shadowColor=glowCol;ctx.fillStyle=mainCol+'CC';ctx.fill();
            ctx.beginPath();ctx.arc(x,y,2.5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.shadowBlur=0;});
        keys.forEach((k,i)=>{const a=i*step-Math.PI/2,lx=CX+Math.cos(a)*(R+36),ly=CY+Math.sin(a)*(R+36);
            ctx.font="700 15px 'Sora',sans-serif";ctx.fillStyle=mainCol;ctx.textAlign='center';ctx.textBaseline='middle';
            ctx.shadowBlur=6;ctx.shadowColor=glowCol;ctx.fillText(k.toUpperCase(),lx,ly);ctx.shadowBlur=0;});
        ctx.font="400 12px 'JetBrains Mono',monospace";ctx.fillStyle='#6B6480';ctx.textAlign='center';
        ctx.fillText('AVG  '+avg.toFixed(2)+'  /  10',CX,449);
        ctx.shadowBlur=20;ctx.shadowColor=glowCol;ctx.font="800 24px 'Sora',sans-serif";ctx.fillStyle=mainCol;
        ctx.fillText(rank,CX,482);ctx.shadowBlur=0;
    }

    // ─── SHORTCUTS GLOBALES ───────────────────────────────────────────────────
    window.toggleDrawer        = () => document.getElementById('stats-drawer')?.classList.toggle('active');
    window.toggleCommands      = () => document.getElementById('commands-panel')?.classList.toggle('active');
    window.toggleSettings      = () => document.getElementById('settings-panel')?.classList.toggle('active');
    window.toggleChannelDrawer = () => document.getElementById('channel-drawer')?.classList.toggle('open');

    // ─── ARRANQUE ─────────────────────────────────────────────────────────────
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
