// static/mobile.js — BLZ-T Mobile Frontend
(function () {
    "use strict";

    // ─── DOM ─────────────────────────────────────────────────────────────
    const feed = document.getElementById('m-feed');
    const msgInput = document.getElementById('m-msg-input');
    const sendBtn = document.getElementById('m-send-btn');
    const channelName = document.getElementById('m-channel-name');
    const channelList = document.getElementById('m-channel-list');
    const channelCount = document.getElementById('m-channel-count');
    const uptimeEl = document.getElementById('m-uptime');
    const toastContainer = document.getElementById('m-toast-container');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas ? canvas.getContext('2d') : null;

    // ─── STATE ───────────────────────────────────────────────────────────
    const state = {
        currentChannelId: null,
        channelMap: {},
        mentionCache: { users: {}, roles: {} },
        lastMessageId: {},
        botName: null,
        botId: null,
        isFetching: false,
        timerSec: 0,
        timerInterval: null,
        lastRank: null,
        pollInterval: null,
        // For the message grouping (consecutive from same author)
        lastAuthorId: null
    };

    // ─── HELPERS ─────────────────────────────────────────────────────────
    function escapeHtml(s) {
        return String(s ?? '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function formatTime(ts) {
        if (!ts) return '';
        try {
            const d = new Date(ts.replace(' ', 'T'));
            if (isNaN(d.getTime())) return '';
            const sameDay = d.toDateString() === new Date().toDateString();
            const time = d.toLocaleTimeString(navigator.language || 'en-US', {
                hour: '2-digit', minute: '2-digit', hour12: false
            });
            if (sameDay) return time;
            return d.toLocaleDateString(navigator.language || 'en-US', {
                day: '2-digit', month: '2-digit'
            }) + ' ' + time;
        } catch (_) { return ''; }
    }

    function showToast(msg, type) {
        const t = document.createElement('div');
        t.className = 'm-toast ' + (type ? 'm-toast-' + type : '');
        t.textContent = msg;
        toastContainer.appendChild(t);
        setTimeout(() => {
            t.classList.add('m-toast-leave');
            setTimeout(() => t.remove(), 220);
        }, 2800);
    }

    // ─── RENDER MESSAGES ─────────────────────────────────────────────────
    function renderContent(text) {
        if (!text) return '';
        let w = escapeHtml(text);
        w = w.replace(/&lt;@!?(\d+)&gt;/g, (_, id) => {
            const c = state.mentionCache.users[id];
            const display = (c && typeof c === 'object')
                ? (c.display || c.username || 'User')
                : 'User';
            return '<span class="mention mention-user">@' + escapeHtml(display) + '</span>';
        });
        w = w.replace(/&lt;@&amp;(\d+)&gt;/g, (_, id) => {
            const c = state.mentionCache.roles[id];
            const name = (c && typeof c === 'object') ? c.name : 'role';
            return '<span class="mention mention-role">@' + escapeHtml(name) + '</span>';
        });
        w = w.replace(/&lt;#(\d+)&gt;/g, (_, id) => {
            const name = state.channelMap[id];
            return '<span class="mention mention-channel">#' + escapeHtml(name || id) + '</span>';
        });
        w = w.replace(/&lt;t:(\d+)(?::([tTdDfFR]))?&gt;/g, (_, ts, fmt) => {
            try {
                const d = new Date(parseInt(ts) * 1000);
                const diff = Math.round((d.getTime() - Date.now()) / 1000);
                let str;
                if (fmt === 'R') {
                    const abs = Math.abs(diff);
                    const rel = abs < 60 ? abs + 's'
                              : abs < 3600 ? Math.floor(abs / 60) + 'm'
                              : abs < 86400 ? Math.floor(abs / 3600) + 'h'
                              : Math.floor(abs / 86400) + 'd';
                    str = diff > 0 ? 'in ' + rel : rel + ' ago';
                } else { str = d.toLocaleString(); }
                return '<span class="mention mention-ts">' + escapeHtml(str) + '</span>';
            } catch (_) { return ''; }
        });
        w = w.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
        w = w.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        w = w.replace(/\*(.+?)\*/g, '<em>$1</em>');
        w = w.replace(/`([^`]+?)`/g, '<code>$1</code>');
        w = w.replace(/\n/g, '<br>');
        return w;
    }

    // ─── EMBED RENDERER (mobile) ──────────────────────────────────────────────
    function renderEmbed(embed) {
        if (!embed || typeof embed !== 'object') return '';
        const colorHex = (typeof embed.color === 'number' && embed.color > 0)
            ? '#' + embed.color.toString(16).padStart(6, '0')
            : 'var(--amber, #F5A623)';

        let html = '<div class="m-msg-embed" style="border-left-color:' + colorHex + '">';

        if (embed.author && embed.author.name) {
            html += '<div class="m-embed-author">';
            if (embed.author.icon_url) {
                html += '<img class="m-embed-author-icon" src="' + escapeHtml(embed.author.icon_url) + '" alt="">';
            }
            const nameInner = embed.author.url
                ? '<a href="' + escapeHtml(embed.author.url) + '" target="_blank" rel="noopener">' + escapeHtml(embed.author.name) + '</a>'
                : escapeHtml(embed.author.name);
            html += '<span class="m-embed-author-name">' + nameInner + '</span>';
            html += '</div>';
        }

        if (embed.title) {
            const titleInner = embed.url
                ? '<a href="' + escapeHtml(embed.url) + '" target="_blank" rel="noopener">' + escapeHtml(embed.title) + '</a>'
                : escapeHtml(embed.title);
            html += '<div class="m-embed-title">' + titleInner + '</div>';
        }

        if (embed.description) {
            html += '<div class="m-embed-description">' + renderContent(embed.description) + '</div>';
        }

        if (Array.isArray(embed.fields) && embed.fields.length) {
            html += '<div class="m-embed-fields">';
            embed.fields.forEach(f => {
                if (!f) return;
                html += '<div class="m-embed-field">';
                if (f.name)  html += '<div class="m-embed-field-name">' + escapeHtml(f.name) + '</div>';
                if (f.value) html += '<div class="m-embed-field-value">' + renderContent(f.value) + '</div>';
                html += '</div>';
            });
            html += '</div>';
        }

        if (embed.image && embed.image.url) {
            html += '<img class="m-embed-image" src="' + escapeHtml(embed.image.url) + '" alt="">';
        } else if (embed.thumbnail && embed.thumbnail.url) {
            html += '<img class="m-embed-thumbnail" src="' + escapeHtml(embed.thumbnail.url) + '" alt="">';
        }

        const hasFooter = embed.footer && embed.footer.text;
        const hasTs = !!embed.timestamp;
        if (hasFooter || hasTs) {
            html += '<div class="m-embed-footer">';
            if (hasFooter && embed.footer.icon_url) {
                html += '<img class="m-embed-footer-icon" src="' + escapeHtml(embed.footer.icon_url) + '" alt="">';
            }
            if (hasFooter) {
                html += '<span>' + escapeHtml(embed.footer.text) + '</span>';
            }
            if (hasTs) {
                try {
                    const d = new Date(embed.timestamp);
                    if (!isNaN(d.getTime())) {
                        html += (hasFooter ? ' • ' : '') + '<span>' + d.toLocaleString() + '</span>';
                    }
                } catch (e) {}
            }
            html += '</div>';
        }

        html += '</div>';
        return html;
    }

    function renderEmbeds(embeds) {
        if (!Array.isArray(embeds) || !embeds.length) return '';
        return embeds.map(renderEmbed).join('');
    }

    function appendMessage(msg, isFresh) {
        if (feed.querySelector('[data-msg-id="' + CSS.escape(String(msg.message_id)) + '"]')) return;

        const isMe = !!(state.botId && msg.author_id && String(msg.author_id) === String(state.botId));
        const authorId = String(msg.author_id || msg.author_name || '?');
        // Group messages when the previous one in feed is from the same author within 3 minutes
        const prev = feed.lastElementChild;
        let grouped = false;
        if (prev && prev.classList && prev.classList.contains('m-msg')) {
            const prevAuthor = prev.dataset.authorKey;
            const prevTs = parseInt(prev.dataset.ts || '0', 10);
            const curTs = Date.parse((msg.timestamp || '').replace(' ', 'T')) || Date.now();
            if (prevAuthor === authorId && Math.abs(curTs - prevTs) < 3 * 60 * 1000) {
                grouped = true;
            }
        }

        const el = document.createElement('div');
        el.className = 'm-msg' + (isMe ? ' m-msg-me' : '') + (grouped ? ' m-msg-grouped' : '');
        el.dataset.msgId = String(msg.message_id);
        el.dataset.authorKey = authorId;
        el.dataset.ts = String(Date.parse((msg.timestamp || '').replace(' ', 'T')) || Date.now());

        const headerHtml = grouped ? '' :
            '<div class="m-msg-header">' +
                '<span class="m-msg-author">' + escapeHtml(msg.author_name || 'Unknown') + '</span>' +
                '<span class="m-msg-time">' + escapeHtml(formatTime(msg.timestamp)) + '</span>' +
            '</div>';

        el.innerHTML = headerHtml +
            '<div class="m-msg-content">' + renderContent(msg.content || '') + '</div>' +
            renderEmbeds(msg.embeds);

        feed.appendChild(el);
    }

    async function resolveMentions(msgs) {
        const userIds = new Set(), roleIds = new Set();
        const reU = /<@!?(\d+)>/g, reR = /<@&(\d+)>/g;
        msgs.forEach(m => {
            let x; const c = m.content || '';
            reU.lastIndex = 0; reR.lastIndex = 0;
            while ((x = reU.exec(c)) !== null) userIds.add(x[1]);
            while ((x = reR.exec(c)) !== null) roleIds.add(x[1]);
        });
        const toU = Array.from(userIds).filter(id => !state.mentionCache.users[id]);
        const toR = Array.from(roleIds).filter(id => !state.mentionCache.roles[id]);
        if (!toU.length && !toR.length) return;
        try {
            const r = await fetch('/api/mention_lookup', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ users: toU, roles: toR })
            });
            if (r.ok) {
                const j = await r.json();
                if (j.users) Object.assign(state.mentionCache.users, j.users);
                if (j.roles) Object.assign(state.mentionCache.roles, j.roles);
            }
        } catch (_) {}
    }

    async function fetchMessages(initial) {
        if (!state.currentChannelId || state.isFetching) return;
        state.isFetching = true;
        try {
            let url = '/api/messages?channel_id=' + encodeURIComponent(state.currentChannelId);
            if (!initial) {
                url += '&since_id=' + encodeURIComponent(state.lastMessageId[state.currentChannelId] || '0');
            } else {
                url += '&limit=200';
            }
            const res = await fetch(url);
            const msgs = await res.json();
            if (!Array.isArray(msgs)) return;
            await resolveMentions(msgs);

            const wasNearBottom = (feed.scrollHeight - feed.scrollTop - feed.clientHeight) < 120;

            if (initial) {
                feed.innerHTML = '';
                if (!msgs.length) {
                    feed.innerHTML =
                        '<div class="m-feed-empty">' +
                            '<div class="m-feed-empty-icon">📭</div>' +
                            '<div class="m-feed-empty-title">No messages yet</div>' +
                            '<div class="m-feed-empty-sub">Be the first to say something</div>' +
                        '</div>';
                } else {
                    msgs.forEach(m => appendMessage(m, false));
                }
                feed.scrollTop = feed.scrollHeight;
            } else if (msgs.length) {
                msgs.forEach(m => appendMessage(m, true));
                if (wasNearBottom) feed.scrollTop = feed.scrollHeight;
            }

            msgs.forEach(m => {
                if (m.message_id) {
                    const prev = state.lastMessageId[state.currentChannelId] || '0';
                    try {
                        if (BigInt(String(m.message_id)) > BigInt(prev)) {
                            state.lastMessageId[state.currentChannelId] = String(m.message_id);
                        }
                    } catch (_) {
                        state.lastMessageId[state.currentChannelId] = String(m.message_id);
                    }
                }
            });
        } catch (e) {
            console.warn('fetchMessages error', e);
        } finally {
            state.isFetching = false;
        }
    }

    // ─── CHANNELS ────────────────────────────────────────────────────────
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            if (!Array.isArray(channels) || !channels.length) {
                channelList.innerHTML = '<div class="m-mod-empty">No channels</div>';
                return;
            }
            channelList.innerHTML = '';
            channels.forEach((ch, idx) => {
                state.channelMap[ch.id] = ch.name;
                const el = document.createElement('button');
                el.className = 'm-channel-item';
                el.dataset.channelId = String(ch.id);
                el.innerHTML =
                    '<span class="m-channel-item-idx">' + String(idx + 1).padStart(2, '0') + '</span>' +
                    '<span class="m-channel-item-name">' + escapeHtml(ch.name || 'unknown') + '</span>';
                el.addEventListener('click', () => selectChannel(ch.id, ch.name));
                channelList.appendChild(el);
            });
            channelCount.textContent = channels.length;
        } catch (e) {
            console.warn('fetchChannels error', e);
        }
    }

    function selectChannel(id, name) {
        state.currentChannelId = id;
        channelName.textContent = name;
        document.querySelectorAll('.m-channel-item').forEach(el => {
            el.classList.toggle('m-channel-active', el.dataset.channelId === String(id));
        });
        feed.innerHTML =
            '<div class="m-feed-empty">' +
                '<div class="m-feed-empty-icon">⚡</div>' +
                '<div class="m-feed-empty-title">Loading…</div>' +
            '</div>';
        fetchMessages(true);
        closeChannels();
        resetTimer();
    }

    // ─── BOT INFO ────────────────────────────────────────────────────────
    async function fetchBotInfo() {
        try {
            const r = await fetch('/api/botinfo');
            if (r.ok) {
                const j = await r.json();
                state.botName = j.name || null;
                state.botId = j.id || null;
            }
        } catch (_) {}
    }

    // ─── TIMER ───────────────────────────────────────────────────────────
    function resetTimer() {
        state.timerSec = 0;
        if (state.timerInterval) clearInterval(state.timerInterval);
        if (uptimeEl) uptimeEl.textContent = '00:00';
        state.timerInterval = setInterval(() => {
            state.timerSec++;
            const m = Math.floor(state.timerSec / 60).toString().padStart(2, '0');
            const s = (state.timerSec % 60).toString().padStart(2, '0');
            if (uptimeEl) uptimeEl.textContent = m + ':' + s;
        }, 1000);
    }

    // ─── SEND MESSAGE ────────────────────────────────────────────────────
    async function sendMessage() {
        const content = msgInput.value.trim();
        if (!content) return;
        if (!state.currentChannelId) {
            showToast('Select a channel first', 'warn');
            return;
        }

        // /deadline shortcut
        if (content.startsWith('/deadline ')) {
            const targetId = content.slice('/deadline '.length).trim().replace(/[<@!>]/g, '').trim();
            if (targetId) {
                triggerDeadline(targetId);
                msgInput.value = '';
            } else {
                showToast('Usage: /deadline @User or /deadline USER_ID', 'warn');
            }
            return;
        }

        msgInput.disabled = true;
        sendBtn.disabled = true;
        try {
            const res = await fetch('/api/send', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: state.currentChannelId, content })
            });
            const data = await res.json();
            if (!res.ok || !data.success) throw new Error(data.error || 'Send failed');
            msgInput.value = '';
            fetchMessages(false);
        } catch (e) {
            showToast('Send failed: ' + e.message, 'error');
        } finally {
            msgInput.disabled = false;
            sendBtn.disabled = false;
            msgInput.focus();
        }
    }

    function triggerDeadline(targetId) {
        if (!state.currentChannelId) {
            showToast('Select a channel first', 'warn');
            return;
        }
        fetch('/api/deadline', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_id: targetId, channel_id: state.currentChannelId })
        })
        .then(r => r.json())
        .then(d => {
            if (d.error) showToast('Error: ' + d.error, 'error');
            else {
                showToast('Deadline sent', 'success');
                fetchMessages(false);
            }
        })
        .catch(e => showToast('Error: ' + e.message, 'error'));
    }

    // ─── DRAWER / SHEETS API ──────────────────────────────────────────
    function openDrawer(id) {
        const el = document.getElementById(id);
        if (el) el.classList.add('m-drawer-open');
    }
    function closeDrawer(id) {
        const el = document.getElementById(id);
        if (el) el.classList.remove('m-drawer-open');
    }
    function openSheet(id) {
        // Close menu sheet when opening a secondary sheet from it
        if (id !== 'm-menu-sheet') {
            document.getElementById('m-menu-sheet')?.classList.remove('m-sheet-open');
        }
        const el = document.getElementById(id);
        if (el) el.classList.add('m-sheet-open');
        if (id === 'm-mod-sheet') loadModPanel('warned');
    }
    function closeSheet(id) {
        const el = document.getElementById(id);
        if (el) el.classList.remove('m-sheet-open');
    }
    function closeAllSheets() {
        document.querySelectorAll('.m-sheet.m-sheet-open').forEach(el => el.classList.remove('m-sheet-open'));
        document.querySelectorAll('.m-drawer.m-drawer-open').forEach(el => el.classList.remove('m-drawer-open'));
    }

    // ─── LOGIN / MOD STATE ───────────────────────────────────────────
    function applyLoginState(loggedIn) {
        const modItem = document.getElementById('m-menu-mod');
        const loginLabel = document.getElementById('m-menu-login-label');
        if (modItem) modItem.style.display = loggedIn ? '' : 'none';
        if (loginLabel) loginLabel.textContent = loggedIn ? 'Logout' : 'Login';
    }

    function doLogin() {
        const user = document.getElementById('login-user').value.trim();
        const pass = document.getElementById('login-pass').value;
        if (user === 'admin' && pass === 'blzt2024') {
            localStorage.setItem('blzt_mod', '1');
            applyLoginState(true);
            document.getElementById('login-error').textContent = '';
            document.getElementById('login-user').value = '';
            document.getElementById('login-pass').value = '';
            closeSheet('m-login-sheet');
            showToast('Logged in', 'success');
        } else {
            document.getElementById('login-error').textContent = 'Incorrect credentials';
        }
    }

    // ─── MOD PANEL ───────────────────────────────────────────────────
    async function loadModPanel(tab) {
        document.querySelectorAll('.m-tab').forEach(t => {
            t.classList.toggle('m-tab-active', t.dataset.tab === tab);
        });
        const body = document.getElementById('m-mod-body');
        if (!body) return;
        body.innerHTML = '<div class="m-mod-loading">Loading…</div>';

        try {
            // Load stats bar
            try {
                const stats = await (await fetch('/api/stats')).json();
                const bar = document.getElementById('m-mod-stats-bar');
                if (bar) bar.innerHTML =
                    '<span class="m-stat-pill">💬 ' + stats.total_messages + ' msgs</span>' +
                    '<span class="m-stat-pill">⚠ ' + stats.total_warnings + ' warns</span>' +
                    '<span class="m-stat-pill">👤 ' + stats.unique_warned_users + ' users</span>' +
                    '<span class="m-stat-pill">🔨 ' + stats.total_mod_actions + ' actions</span>';
            } catch (_) {}

            if (tab === 'warned') {
                const data = await (await fetch('/api/mod/users')).json();
                if (data.error) throw new Error(data.error);
                if (!data.length) {
                    body.innerHTML = '<div class="m-mod-empty">No sanctioned users ✅</div>';
                    return;
                }
                body.innerHTML = data.map(u => {
                    const initial = escapeHtml((u.user_name || '?')[0].toUpperCase());
                    const lastAct = u.last_action ? u.last_action.action.toUpperCase() : null;
                    return (
                        '<div class="m-mod-user">' +
                            '<div class="m-mod-user-top">' +
                                '<div class="m-mod-user-avatar">' + initial + '</div>' +
                                '<div class="m-mod-user-info">' +
                                    '<div class="m-mod-user-name">' + escapeHtml(u.user_name || 'Unknown') + '</div>' +
                                    '<div class="m-mod-user-meta">ID: ' + escapeHtml(u.user_id) + '</div>' +
                                '</div>' +
                            '</div>' +
                            '<div class="m-mod-user-badges">' +
                                '<span class="m-badge-warn">⚠ ' + u.warn_count + ' warn' + (u.warn_count !== 1 ? 's' : '') + '</span>' +
                                (lastAct ? '<span class="m-badge-action">' + escapeHtml(lastAct) + '</span>' : '') +
                            '</div>' +
                            '<div class="m-mod-user-actions">' +
                                '<button class="m-mod-action" data-action="warn"    data-uid="' + escapeHtml(u.user_id) + '" data-uname="' + escapeHtml(u.user_name || '') + '" data-gid="' + escapeHtml(u.guild_id || '') + '">Warn</button>' +
                                '<button class="m-mod-action" data-action="timeout" data-uid="' + escapeHtml(u.user_id) + '" data-uname="' + escapeHtml(u.user_name || '') + '" data-gid="' + escapeHtml(u.guild_id || '') + '">Timeout</button>' +
                                '<button class="m-mod-action" data-action="kick"    data-uid="' + escapeHtml(u.user_id) + '" data-uname="' + escapeHtml(u.user_name || '') + '" data-gid="' + escapeHtml(u.guild_id || '') + '">Kick</button>' +
                                '<button class="m-mod-action" data-action="ban"     data-uid="' + escapeHtml(u.user_id) + '" data-uname="' + escapeHtml(u.user_name || '') + '" data-gid="' + escapeHtml(u.guild_id || '') + '">Ban</button>' +
                                '<button class="m-mod-action" data-action="clear"   data-uid="' + escapeHtml(u.user_id) + '" data-uname="' + escapeHtml(u.user_name || '') + '" data-gid="' + escapeHtml(u.guild_id || '') + '" style="grid-column: span 2;">Clear warns</button>' +
                            '</div>' +
                        '</div>'
                    );
                }).join('');

                body.querySelectorAll('.m-mod-action').forEach(btn => {
                    btn.addEventListener('click', () => {
                        modAction(btn.dataset.action, btn.dataset.uid, btn.dataset.uname, btn.dataset.gid);
                    });
                });
            } else {
                const data = await (await fetch('/api/mod/action_log')).json();
                if (data.error) throw new Error(data.error);
                if (!data.length) {
                    body.innerHTML = '<div class="m-mod-empty">No actions recorded</div>';
                    return;
                }
                body.innerHTML = data.map(a =>
                    '<div class="m-log-entry">' +
                        '<div class="m-log-row">' +
                            '<span class="m-log-user">' + escapeHtml(a.user_name || a.user_id) + '</span>' +
                            '<span class="m-log-action ' + escapeHtml(a.action) + '">' + escapeHtml(a.action) + '</span>' +
                            '<span class="m-log-time">' + escapeHtml((a.timestamp || '').slice(0, 16)) + '</span>' +
                        '</div>' +
                        '<div class="m-log-reason">' + escapeHtml(a.reason || '—') + '</div>' +
                        '<div class="m-log-mod">by ' + escapeHtml(a.moderator_name || '—') + '</div>' +
                    '</div>'
                ).join('');
            }
        } catch (e) {
            body.innerHTML = '<div class="m-mod-empty">Error: ' + escapeHtml(e.message) + '</div>';
        }
    }

    async function modAction(action, uid, uname, gid) {
        const labels = { warn: 'warn', timeout: 'timeout (24h)', kick: 'kick', ban: 'ban', clear: 'clear warns for' };
        let reason = 'Manual action from mobile panel';
        if (action !== 'clear') {
            const input = prompt('Reason for ' + (labels[action] || action) + ' on ' + uname + ':', reason);
            if (input === null) return;
            reason = input.trim() || reason;
        }
        if (!confirm((labels[action] || action) + ' ' + uname + '?')) return;

        try {
            const data = await (await fetch('/api/mod/action', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, user_id: uid, user_name: uname, guild_id: gid, reason })
            })).json();
            if (data.error) throw new Error(data.error);
            showToast(action.toUpperCase() + ' applied', 'success');
            setTimeout(() => loadModPanel('warned'), 500);
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        }
    }

    // ─── STATS GENERATION (same algorithm as desktop) ───────────────
    function generateStats() {
        let type = 'offensive';
        if (document.getElementById('dvg')?.value.trim() !== '') type = 'gk';
        const inputs = type === 'offensive'
            ? ['sht','dbl','stl','psn','dfd']
            : ['dvg','biq','rfx','dtg'];
        let data = {}, sum = 0;
        inputs.forEach(id => {
            let v = parseFloat(document.getElementById(id)?.value) || 0;
            v = Math.min(10, Math.max(0, v));
            data[id] = v;
            sum += v;
        });
        const avg = sum / inputs.length;
        const rank = type === 'offensive' ? getOffensiveRank(avg) : getGKRank(avg);
        state.lastRank = rank;
        drawGraph(type, data, avg, rank);
        closeSheet('m-metrics-sheet');
        openSheet('m-stats-sheet');
    }

    function getOffensiveRank(s) {
        if (s < 4.6) return 'N/A';
        const r = [[4.8,'ROOKIE 🥉 - ⭐'],[5.1,'ROOKIE 🥉 - ⭐⭐'],[5.4,'ROOKIE 🥉 - ⭐⭐⭐'],
                   [5.7,'AMATEUR ⚽ - ⭐'],[6.0,'AMATEUR ⚽ - ⭐⭐'],[6.3,'AMATEUR ⚽ - ⭐⭐⭐'],
                   [6.6,'ELITE ⚡ - ⭐'],[6.9,'ELITE ⚡ - ⭐⭐'],[7.2,'ELITE ⚡ - ⭐⭐⭐'],
                   [7.5,'PRODIGY 🏅 - ⭐'],[7.8,'PRODIGY 🏅 - ⭐⭐'],[8.1,'PRODIGY 🏅 - ⭐⭐⭐'],
                   [8.4,'NEW GEN XI - ⭐'],[8.7,'NEW GEN XI - ⭐⭐'],[9.0,'NEW GEN XI - ⭐⭐⭐'],
                   [9.3,'WORLD CLASS 👑 - ⭐'],[9.6,'WORLD CLASS 👑 - ⭐⭐'],[Infinity,'WORLD CLASS 👑 - ⭐⭐⭐']];
        return (r.find(([m]) => s <= m) || r[r.length - 1])[1];
    }
    function getGKRank(s) {
        if (s <= 6.9) return 'D TIER';
        if (s <= 7.9) return 'C TIER';
        if (s <= 8.4) return 'B TIER';
        if (s <= 8.9) return 'A TIER';
        if (s <= 9.4) return 'S TIER';
        return 'S+ TIER';
    }

    function drawGraph(type, data, avg, rank) {
        if (!ctx) return;
        const W = 500, H = 500, CX = 250, CY = 248, R = 132;
        const isGK = type === 'gk';
        const mainCol = isGK ? '#A78BFA' : '#F5A623';
        const fillCol = isGK ? 'rgba(167,139,250,0.25)' : 'rgba(245,166,35,0.22)';
        const glowCol = isGK ? 'rgba(167,139,250,0.55)' : 'rgba(245,166,35,0.50)';

        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = '#0F0E17'; ctx.fillRect(0, 0, W, H);
        const keys = Object.keys(data), step = (Math.PI * 2) / keys.length;

        for (let l = 1; l <= 4; l++) {
            const rad = (R / 4) * l;
            ctx.beginPath();
            keys.forEach((_, i) => {
                const a = i * step - Math.PI / 2;
                const x = CX + Math.cos(a) * rad, y = CY + Math.sin(a) * rad;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            });
            ctx.closePath();
            ctx.strokeStyle = isGK
                ? 'rgba(167,139,250,' + (l === 4 ? 0.3 : 0.08) + ')'
                : 'rgba(245,166,35,' + (l === 4 ? 0.3 : 0.08) + ')';
            ctx.lineWidth = l === 4 ? 1.5 : 0.8;
            ctx.stroke();
        }

        ctx.beginPath();
        keys.forEach((k, i) => {
            const rad = (data[k] / 10) * R;
            const a = i * step - Math.PI / 2;
            const x = CX + Math.cos(a) * rad, y = CY + Math.sin(a) * rad;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.closePath();
        ctx.shadowBlur = 22; ctx.shadowColor = glowCol;
        ctx.fillStyle = fillCol; ctx.fill();
        ctx.strokeStyle = mainCol; ctx.lineWidth = 2.5; ctx.stroke();
        ctx.shadowBlur = 0;

        keys.forEach((k, i) => {
            const rad = (data[k] / 10) * R;
            const a = i * step - Math.PI / 2;
            const x = CX + Math.cos(a) * rad, y = CY + Math.sin(a) * rad;
            ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2);
            ctx.shadowBlur = 10; ctx.shadowColor = glowCol;
            ctx.fillStyle = mainCol + 'CC'; ctx.fill();
            ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2);
            ctx.fillStyle = '#fff'; ctx.fill();
            ctx.shadowBlur = 0;
        });

        keys.forEach((k, i) => {
            const a = i * step - Math.PI / 2;
            const lx = CX + Math.cos(a) * (R + 36);
            const ly = CY + Math.sin(a) * (R + 36);
            ctx.font = "700 15px 'Sora',sans-serif";
            ctx.fillStyle = mainCol;
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.shadowBlur = 6; ctx.shadowColor = glowCol;
            ctx.fillText(k.toUpperCase(), lx, ly);
            ctx.shadowBlur = 0;
        });

        ctx.font = "400 12px 'JetBrains Mono',monospace";
        ctx.fillStyle = '#6B6480';
        ctx.textAlign = 'center';
        ctx.fillText('AVG  ' + avg.toFixed(2) + '  /  10', CX, 449);

        ctx.shadowBlur = 20; ctx.shadowColor = glowCol;
        ctx.font = "800 24px 'Sora',sans-serif";
        ctx.fillStyle = mainCol;
        ctx.fillText(rank, CX, 482);
        ctx.shadowBlur = 0;
    }

    function copyStats() {
        if (!canvas) { showToast('No stats yet', 'warn'); return; }
        if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
            canvas.toBlob(blob => {
                if (!blob) return;
                navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
                    .then(() => showToast('Image copied', 'success'))
                    .catch(() => downloadCanvas());
            });
        } else { downloadCanvas(); }
    }
    function downloadCanvas() {
        const a = document.createElement('a');
        a.download = 'blzt-stats.png';
        a.href = canvas.toDataURL('image/png');
        a.click();
        showToast('Image downloaded', 'info');
    }
    function copyRank() {
        if (!state.lastRank) { showToast('Generate stats first', 'warn'); return; }
        navigator.clipboard?.writeText(state.lastRank)
            .then(() => showToast('Rank copied', 'success'))
            .catch(() => showToast('Copy failed', 'error'));
    }

    // ─── SETTINGS: QUALITY ──────────────────────────────────────────
    function applyQuality(q) {
        document.body.dataset.quality = q;
        localStorage.setItem('blzt_quality', q);
        document.querySelectorAll('.m-quality-btn').forEach(b => {
            b.classList.toggle('m-quality-active', b.dataset.q === q);
        });
    }

    // ─── INIT ───────────────────────────────────────────────────────
    async function init() {
        applyQuality(localStorage.getItem('blzt_quality') || 'high');
        document.querySelectorAll('.m-quality-btn').forEach(btn => {
            btn.addEventListener('click', () => applyQuality(btn.dataset.q));
        });

        sendBtn.addEventListener('click', sendMessage);
        msgInput.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });

        document.querySelectorAll('.m-tab').forEach(t => {
            t.addEventListener('click', () => loadModPanel(t.dataset.tab));
        });

        document.getElementById('m-copy-stats-btn')?.addEventListener('click', copyStats);
        document.getElementById('m-copy-rank-btn')?.addEventListener('click', copyRank);

        // Login button toggles login <-> logout based on state
        const loginMenu = document.getElementById('m-menu-login');
        loginMenu.onclick = () => {
            if (localStorage.getItem('blzt_mod') === '1') {
                localStorage.removeItem('blzt_mod');
                applyLoginState(false);
                showToast('Logged out', 'info');
                closeSheet('m-menu-sheet');
            } else {
                openSheet('m-login-sheet');
            }
        };

        applyLoginState(localStorage.getItem('blzt_mod') === '1');

        await fetchBotInfo();
        await fetchChannels();
        resetTimer();

        // Poll for new messages — 1.2s is plenty for mobile, saves battery
        state.pollInterval = setInterval(() => {
            if (!state.isFetching && state.currentChannelId) fetchMessages(false);
        }, 1200);

        // Refetch channels and bot info periodically
        setInterval(fetchChannels, 60000);

        // Handle back button on Android / swipe down on iOS: close sheets first
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') closeAllSheets();
        });
    }

    // ─── EXPOSE PUBLIC API ──────────────────────────────────────────
    window.mobileApp = {
        openChannels: () => openDrawer('m-channels-drawer'),
        closeChannels: () => closeDrawer('m-channels-drawer'),
        openMenu: () => openSheet('m-menu-sheet'),
        closeMenu: () => closeSheet('m-menu-sheet'),
        openSheet, closeSheet, closeAllSheets,
        sendDeadline: () => {
            const el = document.getElementById('m-deadline-user');
            const id = (el?.value || '').trim().replace(/[<@!>]/g, '').trim();
            if (!id) { showToast('Enter a User ID', 'warn'); return; }
            triggerDeadline(id);
            if (el) el.value = '';
            closeSheet('m-commands-sheet');
        },
        doLogin,
        generateStats
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();