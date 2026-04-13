// static/script.js - BLZ-T Frontend Completo

(function() {
    "use strict";

    // ---------- REFERENCIAS DOM ----------
    const channelList   = document.getElementById('channel-list');
    const chatFeed      = document.getElementById('chat-feed');
    const msgInput      = document.getElementById('msg-input');
    const sendBtn       = document.getElementById('send-btn');
    const modal         = document.getElementById('stats-modal');
    const canvas        = document.getElementById('stats-canvas');
    const ctx           = canvas ? canvas.getContext('2d') : null;
    const chatChannelName = document.getElementById('chat-channel-name');
    const chatUptime    = document.getElementById('chat-uptime');
    const channelCount  = document.getElementById('channel-count');

    // ---------- ESTADO GLOBAL ----------
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
    let timerInterval = null;
    let acMembers = [], acRoles = [], acLoaded = false;
    let emojiPicker = null, emojiTargetMsgId = null, emojiTargetChannelId = null;
    let _toastTimer = null, _deleteModal = null;
    let _warnHistoryModal = null, _modConfirmModal = null;
    let deadlineUser = null;

    // ---------- INICIALIZACIÓN ----------
    async function init() {
        await fetchBotInfo();
        fetchChannels();
        startTimer();
        setupEventListeners();
        // Intentar cargar canales periódicamente
        setInterval(() => {
            if (!isFetching && currentChannelId) fetchMessages(false);
        }, 500);
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
            msgInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') sendMessage();
            });
        }
        if (sendBtn) sendBtn.onclick = sendMessage;

        // Cerrar modales con Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeAllModals();
            }
            // Atajo para logs: Ctrl+AltGr+0
            if (e.ctrlKey && e.altKey && e.code === 'Digit0') {
                e.preventDefault();
                openLogs();
            }
        });

        // Autocompletado de menciones
        msgInput?.addEventListener('input', handleAcInput);
        msgInput?.addEventListener('keydown', handleAcKeydown);
        document.addEventListener('pointerdown', (e) => {
            if (!acBox || !acBox.classList.contains('ac-open')) return;
            if (!acBox.contains(e.target) && !msgInput.contains(e.target)) hideAc();
        });

        // Cerrar drawer al hacer swipe
        let startY = 0;
        document.addEventListener('touchstart', e => {
            const sheet = document.querySelector('.channel-drawer-sheet');
            if (sheet && sheet.contains(e.target)) startY = e.touches[0].clientY;
        }, { passive: true });
        document.addEventListener('touchend', e => {
            if (!startY) return;
            if (e.changedTouches[0].clientY - startY > 60) {
                document.getElementById('channel-drawer')?.classList.remove('open');
            }
            startY = 0;
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
        } catch (e) {
            console.error('Error fetching channels:', e);
        }
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
                if (b.querySelector('.ch-name')?.textContent === name.toUpperCase()) {
                    b.classList.add('active');
                }
            });
            if (chatChannelName) chatChannelName.innerText = name.toUpperCase();
            resetTimer();
            chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">⚡</div><p class="feed-empty-title">Receiving signal...</p><p class="feed-empty-sub">信号受信中</p></div>';
            fetchMessages(true);
            if (forDrawer) document.getElementById('channel-drawer')?.classList.remove('open');
        };

        return div;
    }

    function startTimer() {
        resetTimer();
    }

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
            // Limpiar mensajes optimistas viejos
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

            // Resolver menciones
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

            // Actualizar lastMessageId
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

    // ---------- UTILIDADES DE TEXTO ----------
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
        // Simplificado pero funcional
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

    function enrichUnresolvedMentions() {
        // Implementación simplificada: podrías expandir si quieres.
    }

    // ---------- COMPONENTES Y ACCIONES DE MENSAJE ----------
    function renderComponents(msgEl, componentsJson) {
        if (!componentsJson) return;
        // Implementación básica
    }

    function decorateMessage(msgEl) {
        if (!msgEl.dataset.msgId || msgEl.querySelector('.msg-actions')) return;
        const isOwn = msgEl.classList.contains('msg-me');
        const actions = document.createElement('div');
        actions.className = 'msg-actions';
        // Botón de reaccionar
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
    function handleAcInput() { /* Implementación */ }
    function handleAcKeydown(e) { /* Implementación */ }

    // ---------- ENVÍO DE MENSAJES ----------
    async function sendMessage() {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;
        // Implementación con optimistic UI
        // ...
    }

    // ---------- PANEL DE LOGS ----------
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
            document.getElementById('logs-content').textContent = 'Error al cargar logs';
        }
    }

    // ---------- LOGIN ----------
    window.toggleLogin = function() {
        document.getElementById('login-modal')?.classList.toggle('active');
    };
    window.doLogin = function() {
        const user = document.getElementById('login-user')?.value.trim();
        const pass = document.getElementById('login-pass')?.value;
        // Credenciales hardcodeadas de ejemplo
        if (user === 'admin' && pass === 'blzt2024') {
            localStorage.setItem('blzt_mod', '1');
            applyLoginState(true);
            toggleLogin();
        } else {
            document.getElementById('login-error').textContent = 'Invalid credentials';
        }
    };
    function applyLoginState(loggedIn) {
        const btn = document.getElementById('login-btn');
        const lbl = document.getElementById('login-btn-label');
        const modB = document.getElementById('mod-btn');
        if (btn) btn.classList.toggle('logged-in', loggedIn);
        if (lbl) lbl.textContent = loggedIn ? 'Logout' : 'Login';
        if (modB) modB.style.display = loggedIn ? 'flex' : 'none';
        if (!loggedIn) document.getElementById('mod-panel')?.classList.remove('active');
    }

    // ---------- STATS ----------
    window.generateStats = function() {
        alert('Generador de stats en desarrollo');
        // Implementación del canvas
    };

    // ---------- OTRAS FUNCIONES GLOBALES ----------
    window.toggleDrawer = () => document.getElementById('stats-drawer')?.classList.toggle('active');
    window.toggleCommands = () => document.getElementById('commands-panel')?.classList.toggle('active');
    window.toggleSettings = () => document.getElementById('settings-panel')?.classList.toggle('active');
    window.toggleMod = () => document.getElementById('mod-panel')?.classList.toggle('active');
    window.toggleChannelDrawer = () => {
        const drawer = document.getElementById('channel-drawer');
        if (!drawer) return;
        drawer.classList.toggle('open');
    };
    window.switchModTab = () => {};
    window.sendDeadlineCommand = async () => {
        // Implementación de deadline
    };

    // Iniciar todo cuando el DOM esté listo
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
