// static/script.js — BLZ-T Frontend v3
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

    // ─── STATE ────────────────────────────────────────────────────────────────
    window._currentChannelId = null;
    Object.defineProperty(window, 'currentChannelId', {
        get: () => window._currentChannelId,
        set: v => { window._currentChannelId = v; }
    });

    let isFetching    = false;
    let botName       = null;
    let channelMap    = {};
    let mentionCache  = { users: {}, roles: {} };
    let lastMessageId = {};
    let timerInterval = null;
    let acMembers = [], acRoles = [], acLoaded = false, acLoadedAt = 0;
    const AC_CACHE_MS = 180000;
    let acBox = null, acItems = [], acIdx = -1, acTriggerPos = -1;

    // ─── INIT ─────────────────────────────────────────────────────────────────
    async function init() {
        createCustomCursor();
        await fetchBotInfo();
        fetchChannels();
        startTimer();
        setupEventListeners();
        initSettings();
        setInterval(() => {
            if (!isFetching && currentChannelId) fetchMessages(false);
        }, 500);
        document.getElementById('copy-stats-btn')?.addEventListener('click', function () {
            if (!canvas) { showToast('No stats generated yet', 'warn'); return; }
            if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
                canvas.toBlob(blob => {
                    if (!blob) return;
                    navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
                        .then(() => showToast('✅ Image copied!', 'success'))
                        .catch(() => downloadCanvasImage());
                });
            } else { downloadCanvasImage(); }
        });
        document.getElementById('copy-rank-btn')?.addEventListener('click', function () {
            const rank = window._lastRank;
            if (!rank) { showToast('Generate stats first', 'warn'); return; }
            navigator.clipboard?.writeText(rank)
                .then(() => showToast('✅ Rank copied: ' + rank, 'success'))
                .catch(() => showToast('❌ Copy failed', 'error'));
        });
        function downloadCanvasImage() {
            const a = document.createElement('a');
            a.download = 'blzt-stats.png';
            a.href = canvas.toDataURL('image/png');
            a.click();
            showToast('📥 Stats image downloaded!', 'info');
        }
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
                 'modal-close','composer-send','settings-btn','warn-history-btn',
                 'cursor-reset-btn','quality-btn','mod-action-btn','cmd-send-btn'].some(c => node.classList.contains(c)))
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
                window._botId = j.id   || null;
            }
        } catch (e) { console.warn('botinfo fetch failed', e); }
    }

    // ─── EVENTS ───────────────────────────────────────────────────────────────
    function setupEventListeners() {
        if (msgInput) {
            msgInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendMessage(); });
            msgInput.addEventListener('input',    handleAcInput);
            msgInput.addEventListener('keydown',  handleAcKeydown);
        }
        if (sendBtn) sendBtn.onclick = sendMessage;
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') closeAllModals();
            if (e.code === 'Digit0' && e.getModifierState('AltGraph')) { e.preventDefault(); openLogs(); }
        });
        document.addEventListener('pointerdown', e => {
            if (!acBox || !acBox.classList.contains('ac-open')) return;
            if (!acBox.contains(e.target) && !msgInput?.contains(e.target)) hideAc();
        });
    }

    function closeAllModals() {
        ['stats-drawer','stats-modal','commands-panel','settings-panel','mod-panel','login-modal','logs-panel']
            .forEach(id => document.getElementById(id)?.classList.remove('active'));
        const wh = document.getElementById('warn-history-modal');
        if (wh) wh.classList.remove('wh-visible', 'wh-open');
    }

    // ─── CHANNELS ─────────────────────────────────────────────────────────────
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
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
        const div = document.createElement('div');
        div.className = 'channel-item';
        div.setAttribute('data-index', String(idx + 1).padStart(2, '0'));
        div.dataset.channelId = String(id);
        const span = document.createElement('span');
        span.className = 'ch-name';
        span.textContent = name.toUpperCase();
        div.appendChild(span);
        div.onclick = () => {
            currentChannelId = id;
            document.querySelectorAll('.channel-item').forEach(b => {
                b.classList.toggle('active', b.dataset.channelId === String(id));
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
                chatUptime.innerText = m + ':' + s;
                chatUptime.style.color = seconds < 300 ? 'var(--teal)' : seconds < 900 ? 'var(--amber)' : 'var(--coral)';
            }
        }, 1000);
    }

    // ─── MESSAGES ─────────────────────────────────────────────────────────────
    async function fetchMessages(initial) {
        if (!currentChannelId) return;
        isFetching = true;
        try {
            document.querySelectorAll('[data-optimistic="1"]').forEach(n => {
                if ((Date.now() - parseInt(n.dataset.optimisticTs || 0)) > 5000) n.remove();
            });
        } catch (e) {}
        if (!botName) await fetchBotInfo();
        try {
            let url = '/api/messages?channel_id=' + encodeURIComponent(currentChannelId);
            if (!initial) {
                url += '&since_id=' + encodeURIComponent(lastMessageId[currentChannelId] || '0');
            } else {
                url += '&limit=1000';
            }
            const res  = await fetch(url);
            const msgs = await res.json();
            await resolveMentions(msgs);
            if (initial) {
                chatFeed.innerHTML = '';
                if (!msgs.length) {
                    chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">無</div><p class="feed-empty-title">No messages yet</p><p class="feed-empty-sub">信号なし</p></div>';
                } else {
                    msgs.forEach(msg => appendMessage(msg, false));
                }
                chatFeed.scrollTop = chatFeed.scrollHeight;
            } else if (msgs.length) {
                msgs.forEach(msg => appendMessage(msg, true));
                chatFeed.scrollTop = chatFeed.scrollHeight;
            }
            msgs.forEach(msg => {
                if (msg.message_id) {
                    const prev = lastMessageId[currentChannelId] || '0';
                    if (BigInt(String(msg.message_id)) > BigInt(prev))
                        lastMessageId[currentChannelId] = String(msg.message_id);
                }
            });
        } catch (e) { console.error('fetchMessages error', e); }
        finally { isFetching = false; }
    }

    // ─── MENTION RESOLUTION ───────────────────────────────────────────────────
    async function resolveMentions(msgs) {
        const userIds = new Set(), roleIds = new Set();
        const reU = /<@!?(\d+)>/g, reR = /<@&(\d+)>/g;
        msgs.forEach(m => {
            let x; const c = m.content || '';
            reU.lastIndex = 0; reR.lastIndex = 0;
            while ((x = reU.exec(c)) !== null) userIds.add(x[1]);
            while ((x = reR.exec(c)) !== null) roleIds.add(x[1]);
        });
        const toU = Array.from(userIds).filter(id => !mentionCache.users[id]);
        const toR = Array.from(roleIds).filter(id => !mentionCache.roles[id]);
        if (!toU.length && !toR.length) return;
        try {
            const r = await fetch('/api/mention_lookup', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ users: toU, roles: toR })
            });
            if (r.ok) {
                const j = await r.json();
                if (j.users) Object.assign(mentionCache.users, j.users);
                if (j.roles) Object.assign(mentionCache.roles, j.roles);
            }
        } catch (e) {}
    }

    // ─── DISCORD CONTENT RENDERING ────────────────────────────────────────────
    function renderDiscordContent(text) {
        if (!text) return '';
        let w = escapeHtml(text);
        // User mentions
        w = w.replace(/&lt;@!?(\d+)&gt;/g, (_, id) => {
            const c = mentionCache.users[id];
            const display = (c && typeof c === 'object') ? (c.display || c.username || '@' + id) : (typeof c === 'string' ? c : '@User_' + id.substring(0, 4));
            return '<span class="mention mention-user" title="ID: ' + id + '" style="cursor:pointer;" onclick="promptDeadline(\'' + id + '\')">' + escapeHtml(display) + '</span>';
        });
        // Role mentions
        w = w.replace(/&lt;@&amp;(\d+)&gt;/g, (_, id) => {
            const c = mentionCache.roles[id];
            const roleName  = (c && typeof c === 'object') ? (c.name  || 'role_' + id) : 'role_' + id;
            const roleColor = (c && typeof c === 'object') ? (c.color || '#a78bfa') : '#a78bfa';
            return '<span class="mention mention-role" style="color:' + roleColor + ';background:' + roleColor + '22">' + escapeHtml(roleName) + '</span>';
        });
        // Channel mentions
        w = w.replace(/&lt;#(\d+)&gt;/g, (_, id) => {
            const name = channelMap[id];
            return name ? '<span class="mention mention-channel">#' + escapeHtml(name) + '</span>' : '<span class="mention">#' + id + '</span>';
        });
        // Discord timestamps
        w = w.replace(/&lt;t:(\d+)(?::([tTdDfFR]))?&gt;/g, (_, ts, fmt) => {
            try {
                const d = new Date(parseInt(ts) * 1000);
                const diff = Math.round((d.getTime() - Date.now()) / 1000);
                let str;
                if (fmt === 'R') {
                    const abs = Math.abs(diff);
                    const rel = abs < 60 ? abs + 's' : abs < 3600 ? Math.floor(abs/60) + 'm' : abs < 86400 ? Math.floor(abs/3600) + 'h' : Math.floor(abs/86400) + 'd';
                    str = diff > 0 ? 'in ' + rel : rel + ' ago';
                } else { str = d.toLocaleString(); }
                return '<span class="mention mention-ts" title="' + d.toISOString() + '">' + str + '</span>';
            } catch (e) { return '&lt;t:' + ts + '&gt;'; }
        });
        // Links
        w = w.replace(/(https?:\/\/[^\s<>"]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
        // Basic markdown
        w = w.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        w = w.replace(/\*(.+?)\*/g,     '<em>$1</em>');
        w = w.replace(/`(.+?)`/g,       '<code>$1</code>');
        w = w.replace(/\n/g,            '<br>');
        return w;
    }

    // ─── EMBED RENDERER ───────────────────────────────────────────────────────
    function renderEmbed(embed) {
        if (!embed || typeof embed !== 'object') return '';
        const colorHex = (typeof embed.color === 'number' && embed.color > 0)
            ? '#' + embed.color.toString(16).padStart(6, '0')
            : 'var(--amber)';

        let html = '<div class="msg-embed" style="border-left-color:' + colorHex + '">';

        // Author
        if (embed.author && embed.author.name) {
            html += '<div class="embed-author">';
            if (embed.author.icon_url) {
                html += '<img class="embed-author-icon" src="' + escapeHtml(embed.author.icon_url) + '" alt="">';
            }
            const nameInner = embed.author.url
                ? '<a href="' + escapeHtml(embed.author.url) + '" target="_blank" rel="noopener">' + escapeHtml(embed.author.name) + '</a>'
                : escapeHtml(embed.author.name);
            html += '<span class="embed-author-name">' + nameInner + '</span>';
            html += '</div>';
        }

        // Title
        if (embed.title) {
            const titleInner = embed.url
                ? '<a href="' + escapeHtml(embed.url) + '" target="_blank" rel="noopener">' + escapeHtml(embed.title) + '</a>'
                : escapeHtml(embed.title);
            html += '<div class="embed-title">' + titleInner + '</div>';
        }

        // Body container with optional thumbnail on the right
        html += '<div class="embed-body">';
        html += '<div class="embed-body-main">';

        // Description (markdown via renderDiscordContent)
        if (embed.description) {
            html += '<div class="embed-description">' + renderDiscordContent(embed.description) + '</div>';
        }

        // Fields
        if (Array.isArray(embed.fields) && embed.fields.length) {
            html += '<div class="embed-fields">';
            embed.fields.forEach(f => {
                if (!f) return;
                const inlineCls = f.inline ? ' embed-field-inline' : '';
                html += '<div class="embed-field' + inlineCls + '">';
                if (f.name)  html += '<div class="embed-field-name">' + escapeHtml(f.name) + '</div>';
                if (f.value) html += '<div class="embed-field-value">' + renderDiscordContent(f.value) + '</div>';
                html += '</div>';
            });
            html += '</div>';
        }

        html += '</div>'; // .embed-body-main

        // Thumbnail
        if (embed.thumbnail && embed.thumbnail.url) {
            html += '<img class="embed-thumbnail" src="' + escapeHtml(embed.thumbnail.url) + '" alt="">';
        }

        html += '</div>'; // .embed-body

        // Image
        if (embed.image && embed.image.url) {
            html += '<img class="embed-image" src="' + escapeHtml(embed.image.url) + '" alt="">';
        }

        // Footer + timestamp
        const hasFooter = embed.footer && embed.footer.text;
        const hasTs = !!embed.timestamp;
        if (hasFooter || hasTs) {
            html += '<div class="embed-footer">';
            if (hasFooter && embed.footer.icon_url) {
                html += '<img class="embed-footer-icon" src="' + escapeHtml(embed.footer.icon_url) + '" alt="">';
            }
            if (hasFooter) {
                html += '<span class="embed-footer-text">' + escapeHtml(embed.footer.text) + '</span>';
            }
            if (hasFooter && hasTs) html += '<span class="embed-footer-sep">•</span>';
            if (hasTs) {
                try {
                    const d = new Date(embed.timestamp);
                    if (!isNaN(d.getTime())) {
                        html += '<span class="embed-footer-ts" title="' + d.toISOString() + '">' + d.toLocaleString() + '</span>';
                    }
                } catch (e) {}
            }
            html += '</div>';
        }

        html += '</div>'; // .msg-embed
        return html;
    }

    function renderEmbeds(embeds) {
        if (!Array.isArray(embeds) || !embeds.length) return '';
        return embeds.map(renderEmbed).join('');
    }

    // ─── APPEND MESSAGE ───────────────────────────────────────────────────────
    function appendMessage(msg, isNew) {
        if (chatFeed.querySelector('[data-msg-id="' + msg.message_id + '"]')) return;
        const el = document.createElement('div');
        el.className = 'message';
        // Identify bot's own messages for styling
        if (botName && (
            (msg.author_id && String(msg.author_id) === String(window._botId || '')) ||
            msg.author_name === botName
        )) el.classList.add('msg-me');

        el.dataset.msgId     = String(msg.message_id);
        el.dataset.rawContent = msg.content || '';   // raw text for inline edit

        el.innerHTML =
            '<div class="msg-header">' +
                '<div class="msg-time">' + formatTimestamp(msg.timestamp) + '</div>' +
                '<div class="msg-author">' + escapeHtml(msg.author_name || 'Unknown') + '</div>' +
            '</div>' +
            '<div class="msg-content">' + renderDiscordContent(msg.content || '') + '</div>' +
            renderEmbeds(msg.embeds);

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
            const locale  = navigator.language || 'en-US';
            const time    = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false });
            const sameDay = d.toDateString() === new Date().toDateString();
            if (sameDay) return time;
            return d.toLocaleDateString(locale, { day: '2-digit', month: '2-digit' }) + ' ' + time;
        } catch (e) { return '——'; }
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    // ─── MESSAGE ACTIONS ──────────────────────────────────────────────────────
    function decorateMessage(msgEl) {
        if (!msgEl.dataset.msgId || msgEl.querySelector('.msg-actions')) return;
        const isOwn = msgEl.classList.contains('msg-me');
        const msgId = msgEl.dataset.msgId;
        const actions = document.createElement('div');
        actions.className = 'msg-actions';

        // React — all messages
        const reactBtn = document.createElement('button');
        reactBtn.className = 'msg-action-btn';
        reactBtn.innerHTML = '😊';
        reactBtn.title = 'React';
        reactBtn.addEventListener('click', (e) => { e.stopPropagation(); openEmojiPicker(reactBtn, msgId); });
        actions.appendChild(reactBtn);

        // Edit — own messages only
        if (isOwn) {
            const editBtn = document.createElement('button');
            editBtn.className = 'msg-action-btn';
            editBtn.innerHTML = '✏️';
            editBtn.title = 'Edit';
            editBtn.addEventListener('click', () => startInlineEdit(msgEl, msgId));
            actions.appendChild(editBtn);
        }

        // Delete — all messages
        const delBtn = document.createElement('button');
        delBtn.className = 'msg-action-btn del';
        delBtn.innerHTML = '🗑️';
        delBtn.title = 'Delete';
        delBtn.addEventListener('click', () => {
            if (!confirm('Delete this message?')) return;
            delBtn.disabled = true; delBtn.textContent = '…';
            fetch('/api/delete', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, message_id: msgId })
            })
            .then(r => r.json())
            .then(d => {
                if (d.success) {
                    msgEl.style.transition = 'opacity 200ms';
                    msgEl.style.opacity = '0';
                    setTimeout(() => msgEl.remove(), 210);
                    showToast('🗑️ Message deleted', 'success');
                } else {
                    showToast('❌ ' + (d.error || 'Delete failed'), 'error');
                    delBtn.disabled = false; delBtn.innerHTML = '🗑️';
                }
            })
            .catch(err => { showToast('❌ ' + err.message, 'error'); delBtn.disabled = false; delBtn.innerHTML = '🗑️'; });
        });
        actions.appendChild(delBtn);

        const header = msgEl.querySelector('.msg-header');
        (header || msgEl).appendChild(actions);
    }

    // ─── EMOJI PICKER ─────────────────────────────────────────────────────────
    // Uses a click-based approach — no pointerdown/mousedown race conditions
    function openEmojiPicker(anchor, msgId) {
        // Toggle: if same picker is already open, close it
        const existing = document.getElementById('emoji-picker-popup');
        if (existing) { existing.remove(); return; }

        const EMOJIS = ['👍','❤️','😂','😮','😢','🔥','✅','👀','💯','🎉','🤔','😎'];
        const picker = document.createElement('div');
        picker.id = 'emoji-picker-popup';
        Object.assign(picker.style, {
            position: 'fixed', zIndex: '9999',
            background: 'var(--bg2)', border: '1px solid var(--border-hi)',
            borderRadius: '14px', padding: '10px',
            display: 'flex', gap: '4px', flexWrap: 'wrap', maxWidth: '220px',
            boxShadow: '0 12px 40px rgba(0,0,0,0.7)'
        });

        EMOJIS.forEach(em => {
            const btn = document.createElement('button');
            btn.textContent = em;
            Object.assign(btn.style, {
                background: 'none', border: 'none', cursor: 'pointer',
                fontSize: '1.3rem', borderRadius: '8px', padding: '5px',
                width: '36px', height: '36px',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'background 100ms'
            });
            btn.addEventListener('mouseover', () => { btn.style.background = 'var(--lift)'; });
            btn.addEventListener('mouseout',  () => { btn.style.background = 'none'; });
            // Use click (not mousedown) — simpler and reliable
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                picker.remove();
                document.removeEventListener('click', outsideDismiss, true);
                const chanId = currentChannelId;
                if (!chanId) { showToast('⚠ No channel selected', 'warn'); return; }
                fetch('/api/react', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channel_id: chanId, message_id: msgId, emoji: em })
                })
                .then(r => r.json())
                .then(d => {
                    if (d.success) showToast('✅ Reacted with ' + em, 'success');
                    else showToast('❌ ' + (d.error || 'Reaction failed'), 'error');
                })
                .catch(err => showToast('❌ ' + err.message, 'error'));
            });
            picker.appendChild(btn);
        });

        document.body.appendChild(picker);

        // Position above the anchor button
        const rect = anchor.getBoundingClientRect();
        picker.style.left   = Math.min(rect.left, window.innerWidth - 230) + 'px';
        picker.style.bottom = (window.innerHeight - rect.top + 8) + 'px';

        // Dismiss when clicking outside — registered on next tick so opening click doesn't dismiss
        function outsideDismiss(e) {
            if (!picker.contains(e.target) && e.target !== anchor) {
                picker.remove();
                document.removeEventListener('click', outsideDismiss, true);
            }
        }
        setTimeout(() => document.addEventListener('click', outsideDismiss, true), 0);
    }

    // ─── INLINE EDIT ──────────────────────────────────────────────────────────
    function startInlineEdit(msgEl, msgId) {
        if (msgEl.querySelector('.msg-edit-wrap')) return;
        // Read the raw (unencoded) text stored on the message element
        const originalText = msgEl.dataset.rawContent || '';

        const wrap = document.createElement('div');
        wrap.className = 'msg-edit-wrap';

        const input = document.createElement('textarea');
        input.className = 'msg-edit-input';
        input.value = originalText;  // plain text, no HTML entities

        const saveBtn = document.createElement('button');
        saveBtn.className = 'msg-edit-save';
        saveBtn.textContent = 'Save';

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'msg-edit-cancel';
        cancelBtn.textContent = 'Cancel';

        cancelBtn.addEventListener('click', () => wrap.remove());

        saveBtn.addEventListener('click', () => {
            const newContent = input.value.trim();
            if (!newContent) { showToast('⚠ Message cannot be empty', 'warn'); return; }
            if (newContent === originalText) { wrap.remove(); return; }
            saveBtn.disabled = true; saveBtn.textContent = '…';
            fetch('/api/edit', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, message_id: msgId, content: newContent })
            })
            .then(r => r.json())
            .then(d => {
                if (d.success) {
                    const cd = msgEl.querySelector('.msg-content');
                    if (cd) cd.innerHTML = renderDiscordContent(newContent);
                    msgEl.dataset.rawContent = newContent;  // update stored raw text
                    wrap.remove();
                    showToast('✅ Message edited', 'success');
                } else {
                    showToast('❌ ' + (d.error || 'Edit failed'), 'error');
                    saveBtn.disabled = false; saveBtn.textContent = 'Save';
                }
            })
            .catch(err => { showToast('❌ ' + err.message, 'error'); saveBtn.disabled = false; saveBtn.textContent = 'Save'; });
        });

        input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveBtn.click(); }
            if (e.key === 'Escape') cancelBtn.click();
        });

        wrap.appendChild(input); wrap.appendChild(saveBtn); wrap.appendChild(cancelBtn);
        msgEl.appendChild(wrap);
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
    }

    // ─── @ AUTOCOMPLETE ───────────────────────────────────────────────────────
    function getAcBox() {
        if (acBox) return acBox;
        acBox = document.createElement('div');
        acBox.id = 'ac-box';
        acBox.setAttribute('role', 'listbox');
        document.body.appendChild(acBox);
        return acBox;
    }
    function hideAc() { getAcBox().classList.remove('ac-open'); acItems = []; acIdx = -1; acTriggerPos = -1; }
    async function handleAcInput() {
        const val = msgInput.value, caret = msgInput.selectionStart;
        let atPos = -1;
        for (let i = caret - 1; i >= 0; i--) {
            if (val[i] === '@') { atPos = i; break; }
            if (val[i] === ' ' || val[i] === '\n') break;
        }
        if (atPos === -1) { hideAc(); return; }
        const query = val.slice(atPos + 1, caret).toLowerCase();
        if (query.includes(' ')) { hideAc(); return; }
        acTriggerPos = atPos;
        await ensureAcData();
        const roleMatches = acRoles.filter(r => !query || r.name.toLowerCase().includes(query)).slice(0, query ? 4 : 3).map(r => ({ ...r, type: 'role' }));
        const memberMatches = acMembers.filter(m => !query || m.display.toLowerCase().includes(query) || m.username.toLowerCase().includes(query)).slice(0, 10 - roleMatches.length).map(m => ({ ...m, type: 'user' }));
        const matches = [...roleMatches, ...memberMatches];
        if (!matches.length) { hideAc(); return; }
        renderAc(matches);
    }
    function handleAcKeydown(e) {
        if (!acItems.length) return;
        if (e.key === 'ArrowDown')  { e.preventDefault(); setAcIdx(Math.min(acIdx + 1, acItems.length - 1)); }
        else if (e.key === 'ArrowUp')  { e.preventDefault(); setAcIdx(Math.max(acIdx - 1, 0)); }
        else if (e.key === 'Tab' || e.key === 'Enter') { e.preventDefault(); applyAc(acIdx >= 0 ? acIdx : 0); }
        else if (e.key === 'Escape') hideAc();
    }
    function renderAc(matches) {
        const box = getAcBox(); box.innerHTML = ''; acItems = matches; acIdx = -1;
        matches.forEach((item, i) => {
            const el = document.createElement('div'); el.className = 'ac-item'; el.dataset.index = i;
            if (item.type === 'user') {
                const av = document.createElement('div'); av.className = 'ac-avatar';
                if (item.avatar) { const img = document.createElement('img'); img.src = item.avatar; img.className = 'ac-avatar-img'; av.appendChild(img); }
                else { av.textContent = (item.display || item.username || '?')[0].toUpperCase(); }
                const info = document.createElement('div'); info.className = 'ac-info';
                const nm = document.createElement('span'); nm.className = 'ac-name'; nm.textContent = item.display;
                const hd = document.createElement('span'); hd.className = 'ac-handle'; hd.textContent = '@' + item.username;
                info.appendChild(nm); info.appendChild(hd); el.appendChild(av); el.appendChild(info);
            } else {
                const dot = document.createElement('span'); dot.className = 'ac-role-dot'; dot.style.background = item.color;
                const label = document.createElement('span'); label.className = 'ac-role-name'; label.style.color = item.color; label.textContent = '@' + item.name;
                const tag = document.createElement('span'); tag.className = 'ac-role-tag'; tag.textContent = 'ROLE';
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
        const item = acItems[i], val = msgInput.value;
        const tag = item.type === 'user' ? '<@' + item.id + '> ' : '<@&' + item.id + '> ';
        const before = val.slice(0, acTriggerPos), after = val.slice(msgInput.selectionStart);
        msgInput.value = before + tag + after;
        const newPos = before.length + tag.length;
        msgInput.setSelectionRange(newPos, newPos);
        hideAc(); msgInput.focus();
    }
    async function ensureAcData(forceRefresh) {
        const now = Date.now();
        if (acLoaded && !forceRefresh && (now - acLoadedAt) < AC_CACHE_MS) return;
        try {
            const r = await fetch('/api/members');
            if (r.ok) {
                const j = await r.json();
                acMembers = j.members || []; acRoles = j.roles || [];
                acLoaded = true; acLoadedAt = now;
            }
        } catch (e) { console.warn('ensureAcData error', e); }
    }

    // ─── SEND MESSAGE ─────────────────────────────────────────────────────────
    async function sendMessage() {
        const content = msgInput.value.trim();
        if (!content) return;
        if (!currentChannelId) { showToast('⚠ Select a channel first', 'warn'); return; }

        if (content.startsWith('/deadline ')) {
            const targetId = content.slice('/deadline '.length).trim().replace(/[<@!>]/g, '').trim();
            if (targetId) { window.triggerWebDeadline(targetId); msgInput.value = ''; }
            else showToast('Usage: /deadline @User or /deadline USER_ID', 'warn');
            return;
        }

        let optimisticNode = null;
        const optimisticClientId = String(Date.now()) + Math.floor(Math.random() * 1000);
        try {
            optimisticNode = document.createElement('div');
            optimisticNode.className = 'message msg-me';
            optimisticNode.setAttribute('data-optimistic', '1');
            optimisticNode.dataset.clientId = optimisticClientId;
            optimisticNode.dataset.optimisticTs = String(Date.now());
            optimisticNode.innerHTML = '<div class="msg-header"><div class="msg-time">——:——</div><div class="msg-author">' + escapeHtml(botName || 'BOT') + '</div></div><div class="msg-content">' + renderDiscordContent(content) + '</div>';
            chatFeed.appendChild(optimisticNode);
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch (e) {}

        msgInput.value = ''; msgInput.disabled = true;
        try {
            const res  = await fetch('/api/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ channel_id: currentChannelId, content }) });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Server rejected');
            if (data.message_id) {
                chatFeed.querySelector('[data-client-id="' + optimisticClientId + '"]')?.remove();
                fetchMessages(false);
            }
        } catch (e) {
            if (optimisticNode?.parentNode) optimisticNode.remove();
            msgInput.value = content;
            showToast('❌ Send failed: ' + e.message, 'error');
        } finally { msgInput.disabled = false; msgInput.focus(); }
    }

    // ─── DEADLINE ─────────────────────────────────────────────────────────────
    window.promptDeadline = function (targetId) {
        const c = mentionCache.users[targetId];
        const name = (c && typeof c === 'object') ? c.display : 'ID ' + targetId;
        if (confirm('Send a 24h deadline to ' + name + '?')) window.triggerWebDeadline(targetId);
    };
    window.triggerWebDeadline = function (targetId) {
        if (!currentChannelId) { showToast('⚠ Select a channel first', 'warn'); return; }
        const c = mentionCache.users[targetId];
        const name = (c && typeof c === 'object') ? c.display : targetId;
        fetch('/api/deadline', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_id: targetId, channel_id: currentChannelId }) })
            .then(r => r.json())
            .then(() => { showToast('✅ Deadline sent to ' + name, 'success'); fetchMessages(false); })
            .catch(err => showToast('❌ Error: ' + err, 'error'));
    };

    // ─── LOGS PANEL ───────────────────────────────────────────────────────────
    let logsInterval = null, logsUnlocked = false;
    function openLogs() {
        const panel = document.getElementById('logs-panel');
        if (!panel) return;
        panel.classList.add('active');
        if (!logsUnlocked) {
            document.getElementById('logs-auth').style.display   = 'flex';
            document.getElementById('logs-viewer').style.display = 'none';
            document.getElementById('logs-user').value = '';
            document.getElementById('logs-pass').value = '';
            document.getElementById('logs-error').textContent = '';
        } else { fetchLogs(); }
    }
    window.openLogs  = openLogs;
    window.fetchLogs = fetchLogs;
    window.closeLogs = function () { document.getElementById('logs-panel')?.classList.remove('active'); clearInterval(logsInterval); };
    window.authLogs  = function () {
        const user = document.getElementById('logs-user').value;
        const pass = document.getElementById('logs-pass').value;
        if (user === 'ogmhabas' && pass === 'blz-tadmin') {
            logsUnlocked = true;
            document.getElementById('logs-auth').style.display   = 'none';
            document.getElementById('logs-viewer').style.display = 'block';
            fetchLogs();
            clearInterval(logsInterval);
            logsInterval = setInterval(() => { if (document.getElementById('logs-panel')?.classList.contains('active')) fetchLogs(); else clearInterval(logsInterval); }, 3000);
        } else { document.getElementById('logs-error').textContent = '❌ Incorrect credentials'; }
    };
    async function fetchLogs() {
        try {
            const lines = parseInt(document.getElementById('logs-lines-input')?.value) || 200;
            const text  = await (await fetch('/api/logs?lines=' + lines)).text();
            const pre   = document.getElementById('logs-content');
            const scrollBox = document.getElementById('logs-scroll');
            if (!pre) return;
            // Only auto-scroll if user was already near the bottom
            const nearBottom = scrollBox
                ? (scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight) < 80
                : true;
            pre.innerHTML = colorizeLogs(escapeHtml(text));
            if (scrollBox && nearBottom) scrollBox.scrollTop = scrollBox.scrollHeight;
        } catch (e) { const pre = document.getElementById('logs-content'); if (pre) pre.textContent = 'Error loading logs: ' + e.message; }
    }
    function colorizeLogs(t) {
        return t.split('\n').map(line => {
            if (line.includes('[ERROR]') || line.includes('!!!'))       return '<span style="color:#ff6b6b">' + line + '</span>';
            if (line.includes('[WARNING]') || line.includes('[WARN]'))  return '<span style="color:#f5a623">' + line + '</span>';
            if (line.includes('[AUTOMOD]') || line.includes('SPAM'))    return '<span style="color:#a78bfa">' + line + '</span>';
            if (line.includes('[SLASH]') || line.includes('>>>'))       return '<span style="color:#26c9b8">' + line + '</span>';
            if (line.includes('[HISTORY]') || line.includes('[SHEETS]')) return '<span style="color:#f5a623aa">' + line + '</span>';
            return '<span style="color:#5a5575">' + line + '</span>';
        }).join('\n');
    }

    // ─── LOGIN ────────────────────────────────────────────────────────────────
    window.toggleLogin = function () {
        // If already logged in, this button acts as a logout
        if (localStorage.getItem('blzt_mod') === '1') {
            localStorage.removeItem('blzt_mod');
            applyLoginState(false);
            showToast('👋 Logged out', 'info');
            return;
        }
        document.getElementById('login-modal')?.classList.toggle('active');
    };
    window.doLogin = function () {
        const user = document.getElementById('login-user')?.value.trim();
        const pass = document.getElementById('login-pass')?.value;
        if (user === 'admin' && pass === 'blzt2024') {
            localStorage.setItem('blzt_mod', '1');
            applyLoginState(true);
            document.getElementById('login-modal')?.classList.remove('active');
            showToast('✅ Logged in', 'success');
        } else { document.getElementById('login-error').textContent = '❌ Incorrect credentials'; }
    };
    function applyLoginState(loggedIn) {
        const btn = document.getElementById('login-btn'), lbl = document.getElementById('login-btn-label'), modB = document.getElementById('mod-btn');
        if (btn)  btn.classList.toggle('logged-in', loggedIn);
        if (lbl)  lbl.textContent = loggedIn ? 'Logout' : 'Login';
        if (modB) modB.style.display = loggedIn ? 'flex' : 'none';
        if (!loggedIn) document.getElementById('mod-panel')?.classList.remove('active');
    }

    // ─── MOD PANEL ────────────────────────────────────────────────────────────
    window.toggleMod = function () {
        const panel = document.getElementById('mod-panel'); if (!panel) return;
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
            const s = await (await fetch('/api/stats')).json();
            const el = document.getElementById('mod-stats-bar');
            if (el) el.innerHTML =
                '<span class="mod-stat">💬 ' + s.total_messages + ' msgs</span>' +
                '<span class="mod-stat">⚠ ' + s.total_warnings + ' warns</span>' +
                '<span class="mod-stat">👤 ' + s.unique_warned_users + ' sanctioned</span>' +
                '<span class="mod-stat">🔨 ' + s.total_mod_actions + ' actions</span>';
        } catch (e) {}
    }
    async function loadModPanel(tab) {
        const body = document.getElementById('mod-body'); if (!body) return;
        body.innerHTML = '<div class="mod-loading">Loading...</div>';
        try {
            if (tab === 'warned') { const data = await (await fetch('/api/mod/users')).json(); if (data.error) throw new Error(data.error); renderWarnedUsers(data, body); }
            else { const data = await (await fetch('/api/mod/action_log')).json(); if (data.error) throw new Error(data.error); renderActionLog(data, body); }
        } catch (e) { body.innerHTML = '<div class="mod-empty">❌ Error: ' + escapeHtml(e.message) + '</div>'; }
    }
    function renderWarnedUsers(users, container) {
        if (!users.length) { container.innerHTML = '<div class="mod-empty">✅ No sanctioned users</div>'; return; }
        container.innerHTML = '';
        users.forEach(u => {
            const card = document.createElement('div'); card.className = 'mod-user-card';
            const initial = (u.user_name || '?')[0].toUpperCase();
            const lastAct = u.last_action ? u.last_action.action.toUpperCase() : null;
            const recentHtml = (u.recent_warnings || []).slice(0, 2).map(w => {
                const reason = escapeHtml(w.reason || '—');
                const mc = w.message_content ? '<div class="warn-msg-preview"><span class="warn-msg-label">💬</span><span class="warn-msg-text">' + escapeHtml(w.message_content).substring(0, 100) + (w.message_content.length > 100 ? '…' : '') + '</span></div>' : '';
                const lnk = w.message_link ? '<a class="warn-msg-link" href="' + escapeHtml(w.message_link) + '" target="_blank">🔗</a>' : '';
                return '<div class="warn-entry"><span class="warn-entry-reason">' + reason + '</span>' + lnk + mc + '</div>';
            }).join('');
            card.innerHTML =
                '<div class="mod-user-avatar">' + initial + '</div>' +
                '<div class="mod-user-info">' +
                    '<div class="mod-user-name">' + escapeHtml(u.user_name || 'Unknown') + '</div>' +
                    '<div class="mod-user-meta">ID: ' + escapeHtml(u.user_id) + '</div>' +
                    '<div class="warn-badges"><span class="warn-badge warn-badge-count">⚠ ' + u.warn_count + ' warn' + (u.warn_count !== 1 ? 's' : '') + '</span>' + (lastAct ? '<span class="warn-badge warn-badge-action">' + lastAct + '</span>' : '') + '</div>' +
                    recentHtml +
                    '<button class="warn-history-btn js-warn-hist">📋 Full history (' + u.warn_count + ')</button>' +
                '</div>' +
                '<div class="mod-user-actions">' +
                    '<button class="mod-action-btn warn"    data-action="warn">Warn</button>' +
                    '<button class="mod-action-btn timeout" data-action="timeout">Timeout</button>' +
                    '<button class="mod-action-btn kick"    data-action="kick">Kick</button>' +
                    '<button class="mod-action-btn ban"     data-action="ban">Ban</button>' +
                    '<button class="mod-action-btn clear"   data-action="clear">Clear</button>' +
                '</div>';
            card.querySelector('.js-warn-hist').addEventListener('click', () => showWarnHistory(u.user_id, u.user_name || 'Unknown'));
            card.querySelectorAll('.mod-action-btn[data-action]').forEach(btn => {
                btn.addEventListener('click', () => modAction(btn.dataset.action, u.user_id, u.user_name || '', u.guild_id || '', btn));
            });
            container.appendChild(card);
        });
    }
    function renderActionLog(actions, container) {
        if (!actions.length) { container.innerHTML = '<div class="mod-empty">No actions recorded</div>'; return; }
        const table = document.createElement('table'); table.className = 'mod-log-table';
        table.innerHTML = '<thead><tr><th>User</th><th>Action</th><th>Reason</th><th>Moderator</th><th>Date</th></tr></thead><tbody>' +
            actions.map(a => '<tr><td>' + escapeHtml(a.user_name || a.user_id) + '</td><td><span class="log-action-badge ' + escapeHtml(a.action) + '">' + escapeHtml(a.action) + '</span></td><td title="' + escapeHtml(a.reason||'') + '">' + escapeHtml((a.reason||'—').substring(0,60)) + ((a.reason||'').length>60?'…':'') + '</td><td>' + escapeHtml(a.moderator_name||'—') + '</td><td>' + (a.timestamp?a.timestamp.slice(0,16):'—') + '</td></tr>').join('') +
            '</tbody>';
        container.innerHTML = ''; container.appendChild(table);
    }

    // ─── WARN HISTORY MODAL ───────────────────────────────────────────────────
    window.showWarnHistory = async function (userId, userName) {
        const modal = document.getElementById('warn-history-modal'); if (!modal) return;
        modal.classList.add('wh-open');
        requestAnimationFrame(() => modal.classList.add('wh-visible'));
        const title = document.getElementById('wh-title'), content = document.getElementById('wh-content');
        if (title) title.textContent = '📋 History: ' + userName;
        if (content) content.innerHTML = '<div class="mod-loading">Loading...</div>';
        try {
            const data = await (await fetch('/api/mod/warn_history/' + encodeURIComponent(userId))).json();
            if (data.error) throw new Error(data.error);
            if (!data.length) { content.innerHTML = '<div class="mod-empty">No warnings on record</div>'; return; }
            content.innerHTML = data.map((w, i) => {
                const msgBlock = w.message_content
                    ? '<div class="wh-msg-content"><span class="wh-msg-label">💬 Message that triggered the action:</span><pre class="wh-msg-pre">' + escapeHtml(w.message_content) + '</pre></div>'
                    : '<div class="wh-msg-content"><em style="color:var(--t2)">No message saved</em></div>';
                const link = w.message_link ? '<a class="warn-msg-link" href="' + escapeHtml(w.message_link) + '" target="_blank" rel="noopener">🔗 View in Discord</a>' : '';
                return '<div class="wh-entry"><div class="wh-entry-header"><span class="wh-num">#' + (data.length - i) + '</span><span class="wh-reason">' + escapeHtml(w.reason||'—') + '</span><span class="wh-mod">by ' + escapeHtml(w.moderator_name||'AutoMod') + '</span><span class="wh-ts">' + (w.timestamp?w.timestamp.slice(0,16):'—') + '</span></div>' + msgBlock + link + '</div>';
            }).join('');
        } catch (e) { if (content) content.innerHTML = '<div class="mod-empty">❌ Error: ' + escapeHtml(e.message) + '</div>'; }
    };
    window.closeWarnHistory = function () {
        const modal = document.getElementById('warn-history-modal'); if (!modal) return;
        modal.classList.remove('wh-visible');
        setTimeout(() => modal.classList.remove('wh-open'), 280);
    };

    // ─── MOD ACTIONS ──────────────────────────────────────────────────────────
    window.modAction = async function (action, uid, uname, gid, btn) {
        const labels = { warn:'warn', timeout:'timeout (24h)', kick:'kick', ban:'ban', clear:'clear warns for' };
        let reason = 'Manual action from web panel';
        if (action === 'clear') {
            if (!confirm('Clear all warns for ' + uname + '?')) return;
        } else {
            const input = prompt('Reason for ' + (labels[action]||action) + ' on ' + uname + ':', reason);
            if (input === null) return;
            reason = input.trim() || reason;
            if (!confirm((labels[action]||action) + ' ' + uname + '?\nReason: ' + reason)) return;
        }
        if (btn) { btn.disabled = true; btn.textContent = '…'; }
        try {
            const data = await (await fetch('/api/mod/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, user_id: uid, user_name: uname, guild_id: gid, reason }) })).json();
            if (data.error) throw new Error(data.error);
            showToast('✅ ' + action.toUpperCase() + ' applied to ' + uname, 'success');
            setTimeout(() => { loadModPanel('warned'); loadModStats(); }, 600);
        } catch (e) { showToast('❌ Error: ' + e.message, 'error'); }
        finally { if (btn) { btn.disabled = false; btn.textContent = { warn:'Warn', timeout:'Timeout', kick:'Kick', ban:'Ban', clear:'Clear' }[action] || action; } }
    };

    // ─── TOAST ────────────────────────────────────────────────────────────────
    function showToast(message, type) {
        type = type || 'info';
        let c = document.getElementById('toast-container');
        if (!c) { c = document.createElement('div'); c.id = 'toast-container'; c.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;'; document.body.appendChild(c); }
        const t = document.createElement('div');
        const col = { success:'#26c9b8', error:'#ff6b6b', warn:'#f5a623', info:'#a78bfa' }[type] || '#a78bfa';
        t.style.cssText = 'background:rgba(20,18,31,0.95);border:1px solid ' + col + ';color:' + col + ';padding:10px 16px;border-radius:8px;font-size:13px;font-family:var(--f-display,sans-serif);backdrop-filter:blur(10px);pointer-events:auto;max-width:320px;box-shadow:0 4px 20px rgba(0,0,0,0.4);transition:opacity 0.3s;';
        t.textContent = message; c.appendChild(t);
        setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3500);
    }

    // ─── STATS ────────────────────────────────────────────────────────────────
    window.generateStats = function () {
        let type = 'offensive';
        if (document.getElementById('dvg')?.value.trim() !== '') type = 'gk';
        const inputs = type === 'offensive' ? ['sht','dbl','stl','psn','dfd'] : ['dvg','biq','rfx','dtg'];
        let data = {}, sum = 0;
        inputs.forEach(id => { let v = parseFloat(document.getElementById(id)?.value)||0; v=Math.min(10,Math.max(0,v)); data[id]=v; sum+=v; });
        const avg=sum/inputs.length, rank=type==='offensive'?getOffensiveRank(avg):getGKRank(avg);
        window._lastRank=rank; drawGraph(type,data,avg,rank);
        document.getElementById('stats-modal')?.classList.add('active');
        document.getElementById('stats-drawer')?.classList.remove('active');
    };
    function getOffensiveRank(s) {
        if (s < 4.6) return 'N/A';
        const r=[[4.8,'ROOKIE 🥉 - ⭐'],[5.1,'ROOKIE 🥉 - ⭐⭐'],[5.4,'ROOKIE 🥉 - ⭐⭐⭐'],[5.7,'AMATEUR ⚽ - ⭐'],[6.0,'AMATEUR ⚽ - ⭐⭐'],[6.3,'AMATEUR ⚽ - ⭐⭐⭐'],[6.6,'ELITE ⚡ - ⭐'],[6.9,'ELITE ⚡ - ⭐⭐'],[7.2,'ELITE ⚡ - ⭐⭐⭐'],[7.5,'PRODIGY 🏅 - ⭐'],[7.8,'PRODIGY 🏅 - ⭐⭐'],[8.1,'PRODIGY 🏅 - ⭐⭐⭐'],[8.4,'NEW GEN XI - ⭐'],[8.7,'NEW GEN XI - ⭐⭐'],[9.0,'NEW GEN XI - ⭐⭐⭐'],[9.3,'WORLD CLASS 👑 - ⭐'],[9.6,'WORLD CLASS 👑 - ⭐⭐'],[Infinity,'WORLD CLASS 👑 - ⭐⭐⭐']];
        return (r.find(([m])=>s<=m)||r[r.length-1])[1];
    }
    function getGKRank(s) { if(s<=6.9)return'D TIER';if(s<=7.9)return'C TIER';if(s<=8.4)return'B TIER';if(s<=8.9)return'A TIER';if(s<=9.4)return'S TIER';return'S+ TIER'; }
    function drawGraph(type,data,avg,rank){
        if(!ctx)return;
        const W=500,H=500,CX=250,CY=248,R=132,isGK=type==='gk',mainCol=isGK?'#A78BFA':'#F5A623',fillCol=isGK?'rgba(167,139,250,0.25)':'rgba(245,166,35,0.22)',glowCol=isGK?'rgba(167,139,250,0.55)':'rgba(245,166,35,0.50)';
        ctx.clearRect(0,0,W,H);ctx.fillStyle='#0F0E17';ctx.fillRect(0,0,W,H);
        const grd=ctx.createRadialGradient(CX,CY,0,CX,CY,R*1.3);grd.addColorStop(0,isGK?'rgba(167,139,250,0.06)':'rgba(245,166,35,0.06)');grd.addColorStop(1,'transparent');ctx.fillStyle=grd;ctx.fillRect(0,0,W,H);
        const keys=Object.keys(data),step=(Math.PI*2)/keys.length;
        for(let l=1;l<=4;l++){const rad=(R/4)*l;ctx.beginPath();if(isGK)ctx.arc(CX,CY,rad,0,Math.PI*2);else keys.forEach((_,i)=>{const a=i*step-Math.PI/2;i===0?ctx.moveTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad):ctx.lineTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad);});ctx.strokeStyle=isGK?'rgba(167,139,250,'+(l===4?0.3:0.08)+')':'rgba(245,166,35,'+(l===4?0.3:0.08)+')';ctx.lineWidth=l===4?1.5:0.8;ctx.stroke();}
        ctx.beginPath();keys.forEach((k,i)=>{const rad=(data[k]/10)*R,a=i*step-Math.PI/2;i===0?ctx.moveTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad):ctx.lineTo(CX+Math.cos(a)*rad,CY+Math.sin(a)*rad);});ctx.closePath();ctx.shadowBlur=22;ctx.shadowColor=glowCol;ctx.fillStyle=fillCol;ctx.fill();ctx.strokeStyle=mainCol;ctx.lineWidth=2.5;ctx.stroke();ctx.shadowBlur=0;
        keys.forEach((k,i)=>{const rad=(data[k]/10)*R,a=i*step-Math.PI/2,x=CX+Math.cos(a)*rad,y=CY+Math.sin(a)*rad;ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.shadowBlur=10;ctx.shadowColor=glowCol;ctx.fillStyle=mainCol+'CC';ctx.fill();ctx.beginPath();ctx.arc(x,y,2.5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.shadowBlur=0;});
        keys.forEach((k,i)=>{const a=i*step-Math.PI/2,lx=CX+Math.cos(a)*(R+36),ly=CY+Math.sin(a)*(R+36);ctx.font="700 15px 'Sora',sans-serif";ctx.fillStyle=mainCol;ctx.textAlign='center';ctx.textBaseline='middle';ctx.shadowBlur=6;ctx.shadowColor=glowCol;ctx.fillText(k.toUpperCase(),lx,ly);ctx.shadowBlur=0;});
        ctx.font="400 12px 'JetBrains Mono',monospace";ctx.fillStyle='#6B6480';ctx.textAlign='center';ctx.fillText('AVG  '+avg.toFixed(2)+'  /  10',CX,449);
        ctx.shadowBlur=20;ctx.shadowColor=glowCol;ctx.font="800 24px 'Sora',sans-serif";ctx.fillStyle=mainCol;ctx.fillText(rank,CX,482);ctx.shadowBlur=0;
    }

    // ─── GLOBAL SHORTCUTS ─────────────────────────────────────────────────────
    window.toggleDrawer        = () => document.getElementById('stats-drawer')?.classList.toggle('active');
    window.toggleCommands      = () => document.getElementById('commands-panel')?.classList.toggle('active');
    window.toggleChannelDrawer = () => document.getElementById('channel-drawer')?.classList.toggle('open');

    // ─── SETTINGS ─────────────────────────────────────────────────────────────
    function applyQuality(q) {
        document.body.dataset.quality = q;
        localStorage.setItem('blzt_quality', q);
        document.querySelectorAll('.quality-btn').forEach(b => b.classList.toggle('active', b.dataset.q === q));
    }
    function initSettings() {
        applyQuality(localStorage.getItem('blzt_quality') || 'high');
        document.querySelectorAll('.quality-btn').forEach(btn => btn.addEventListener('click', () => applyQuality(btn.dataset.q)));
    }
    window.toggleSettings = function () { document.getElementById('settings-panel')?.classList.toggle('active'); };

    // ─── BOOT ─────────────────────────────────────────────────────────────────
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();