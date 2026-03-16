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

<<<<<<< HEAD
                    chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-kanji">⚡</div><div class="feed-empty-text">SIGNAL INCOMING...</div></div>';
=======
                    chatFeed.innerHTML = '<div class="stream-init"><div class="init-symbol" style="opacity:0.5">âš¡</div><p class="init-text">SIGNAL INCOMING...</p></div>';
>>>>>>> 7081ee4b2ca8bbb32ba7446705dd1cff79a43424
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
                    chatFeed.innerHTML = '<div class="feed-empty"><div class="feed-empty-kanji">刀</div><div class="feed-empty-text">NO SIGNAL</div></div>';
                } else {
                    msgs.forEach(msg => {
                        const message = document.createElement('div');
                        message.className = 'message';

                        if (botName && msg.author_id && String(msg.author_id) === String((window._botId || ''))) message.classList.add('msg-me');
                        else if (botName && msg.author_name === botName) message.classList.add('msg-me');

                        const rendered = renderDiscordContent(msg.content || '');

                        message.innerHTML = `
                                <div class="msg-time">${formatTimestamp(msg.timestamp || '')}</div>
                                <div class="msg-author">${escapeHtml(msg.author_name || 'Unknown')}</div>
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
                        chatFeed.appendChild(message);

                        if (msg.message_id) lastMessageId[currentChannelId] = Math.max(lastMessageId[currentChannelId] || 0, Number(msg.message_id));
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
                            <div class="msg-time">${formatTimestamp(msg.timestamp || '')}</div>
                            <div class="msg-author">${escapeHtml(msg.author_name || 'Unknown')}</div>
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
                        chatFeed.appendChild(message);
                        chatFeed.scrollTop = chatFeed.scrollHeight;

                        if (msg.message_id) lastMessageId[currentChannelId] = Math.max(lastMessageId[currentChannelId] || 0, Number(msg.message_id));
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

    // Format ISO timestamp to HH:MM
    function formatTimestamp(ts) {
        if (!ts) return '——:——';
        try {
            const d = new Date(ts);
            if (isNaN(d.getTime())) return ts.slice(0, 5) || '——:——';
            return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', hour12: false });
        } catch (e) { return '——:——'; }
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
                <div class="msg-time">——:——</div>
                <div class="msg-author">${author}</div>
                <div class="msg-content">${formatLinks(content)}</div>
            `;
            chatFeed.appendChild(optimisticNode);
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch (e) { optimisticNode = null; }

        msgInput.value = '';
<<<<<<< HEAD
        msgInput.placeholder = "送信中...";
=======
        msgInput.placeholder = "é€ä¿¡ä¸­...";
>>>>>>> 7081ee4b2ca8bbb32ba7446705dd1cff79a43424
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
                        <div class="msg-time">${formatTimestamp(realMsg.timestamp || '')}</div>
                        <div class="msg-author">${escapeHtml(realMsg.author_name || 'Unknown')}</div>
                        <div class="msg-content">${renderDiscordContent(realMsg.content || '')}</div>
                    `;
                    message.style.display = '';
                    message.style.visibility = 'visible';

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

    // --- ESTADÃSTICAS ---
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
        if (s <= 4.8) return "ROOKIE ðŸ¥‰ - â­";
        if (s <= 5.1) return "ROOKIE ðŸ¥‰ - â­â­";
        if (s <= 5.4) return "ROOKIE ðŸ¥‰ - â­â­â­";
        if (s <= 5.7) return "AMATEUR âš½ - â­";
        if (s <= 6.0) return "AMATEUR âš½ - â­â­";
        if (s <= 6.3) return "AMATEUR âš½ - â­â­â­";
        if (s <= 6.6) return "ELITE âš¡ - â­";
        if (s <= 6.9) return "ELITE âš¡ - â­â­";
        if (s <= 7.2) return "ELITE âš¡ - â­â­â­";
        if (s <= 7.5) return "PRODIGY ðŸ… - â­";
        if (s <= 7.8) return "PRODIGY ðŸ… - â­â­";
        if (s <= 8.1) return "PRODIGY ðŸ… - â­â­â­";
        if (s <= 8.4) return "NEW GEN XI - â­";
        if (s <= 8.7) return "NEW GEN XI - â­â­";
        if (s <= 9.0) return "NEW GEN XI - â­â­â­";
        if (s <= 9.3) return "WORLD CLASS ðŸ‘‘ - â­";
        if (s <= 9.6) return "WORLD CLASS ðŸ‘‘ - â­â­";
        return "WORLD CLASS ðŸ‘‘ - â­â­â­";
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
<<<<<<< HEAD
        const W = 500, H = 500;
        ctx.clearRect(0, 0, W, H);

        // ── Pure black bg ──
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, W, H);

        // ── Scanline texture ──
        ctx.save();
        for (let y = 0; y < H; y += 3) {
            ctx.fillStyle = 'rgba(0,0,0,0.25)';
            ctx.fillRect(0, y, W, 1);
        }
        ctx.restore();

        const CX = 250, CY = 248, R = 138;
        const isGK = type === 'gk';
        const mainColor  = isGK ? '#E86000' : '#00D4FF';
        const fillColor  = isGK ? 'rgba(232,96,0,0.30)' : 'rgba(0,212,255,0.28)';
        const glowColor  = isGK ? 'rgba(232,96,0,0.55)' : 'rgba(0,212,255,0.55)';

        const keys = Object.keys(data);
        const total = keys.length;
        const step = (Math.PI * 2) / total;

        // ── Grid rings ──
        for (let l = 1; l <= 4; l++) {
            const rad = (R / 4) * l;
            ctx.beginPath();
            if (isGK) {
                ctx.arc(CX, CY, rad, 0, Math.PI * 2);
            } else {
                for (let i = 0; i <= total; i++) {
                    const a = i * step - Math.PI / 2;
                    const x = CX + Math.cos(a) * rad;
                    const y = CY + Math.sin(a) * rad;
                    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
                }
            }
            ctx.strokeStyle = l === 4
                ? 'rgba(207,10,44,0.40)'
                : 'rgba(207,10,44,0.14)';
            ctx.lineWidth = l === 4 ? 1.5 : 0.8;
            ctx.stroke();
        }

        // ── Spokes ──
        if (!isGK) {
            keys.forEach((_, i) => {
                const a = i * step - Math.PI / 2;
                ctx.beginPath();
                ctx.moveTo(CX, CY);
                ctx.lineTo(CX + Math.cos(a) * R, CY + Math.sin(a) * R);
                ctx.strokeStyle = 'rgba(207,10,44,0.18)';
                ctx.lineWidth = 0.8;
=======
        ctx.clearRect(0,0,500,500);

        // Background: near-black with subtle grid
        ctx.fillStyle = "#050307";
        ctx.fillRect(0,0,500,500);

        // Washi grid overlay
        ctx.save();
        ctx.strokeStyle = "rgba(196,154,26,0.06)";
        ctx.lineWidth = 1;
        for(let x=0; x<=500; x+=40) { ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,500); ctx.stroke(); }
        for(let y=0; y<=500; y+=40) { ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(500,y); ctx.stroke(); }
        ctx.restore();

        const cx = 250, cy = 245, r = 140;
        const color = type === 'offensive' ? '#00d4ff' : '#e85500';
        const colorDim = type === 'offensive' ? 'rgba(0,212,255,0.12)' : 'rgba(232,85,0,0.12)';

        const keys = Object.keys(data), total = keys.length, angleStep = (Math.PI * 2) / total;

        // Draw grid rings
        for(let l=1; l<=4; l++) {
            let rad = (r/4)*l;
            ctx.beginPath();
            ctx.shadowBlur = 0;
            if (type === 'gk') {
                ctx.arc(cx, cy, rad, 0, Math.PI*2);
            } else {
                for(let i=0; i<=total; i++) {
                    let a = i*angleStep-Math.PI/2, x=cx+Math.cos(a)*rad, y=cy+Math.sin(a)*rad;
                    i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
                }
            }
            ctx.strokeStyle = l===4 ? "rgba(196,154,26,0.30)" : "rgba(196,154,26,0.12)";
            ctx.lineWidth = l===4 ? 1.5 : 1;
            ctx.stroke();
        }

        // Draw axis spokes
        if (type !== 'gk') {
            keys.forEach((k, i) => {
                let a = i*angleStep-Math.PI/2;
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.lineTo(cx+Math.cos(a)*r, cy+Math.sin(a)*r);
                ctx.strokeStyle = "rgba(196,154,26,0.18)";
                ctx.lineWidth = 1;
>>>>>>> 7081ee4b2ca8bbb32ba7446705dd1cff79a43424
                ctx.stroke();
            });
        }

<<<<<<< HEAD
        // ── Stat polygon ──
=======
        // Draw filled shape
>>>>>>> 7081ee4b2ca8bbb32ba7446705dd1cff79a43424
        ctx.beginPath();
        keys.forEach((k, i) => {
            const rad = (data[k] / 10) * R;
            const a = i * step - Math.PI / 2;
            const x = CX + Math.cos(a) * rad;
            const y = CY + Math.sin(a) * rad;
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        ctx.closePath();

<<<<<<< HEAD
        ctx.shadowBlur = 20;
        ctx.shadowColor = glowColor;
        ctx.fillStyle = fillColor;
        ctx.fill();
        ctx.strokeStyle = mainColor;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.shadowBlur = 0;

        // ── Vertex dots ──
        keys.forEach((k, i) => {
            const rad = (data[k] / 10) * R;
            const a = i * step - Math.PI / 2;
            const x = CX + Math.cos(a) * rad;
            const y = CY + Math.sin(a) * rad;
            ctx.beginPath();
            ctx.arc(x, y, 3.5, 0, Math.PI * 2);
            ctx.shadowBlur = 10;
            ctx.shadowColor = glowColor;
            ctx.fillStyle = '#FFFFFF';
=======
        // Glow fill
        ctx.shadowBlur = 28;
        ctx.shadowColor = color;
        ctx.fillStyle = type==='offensive'?"rgba(0,212,255,0.35)":"rgba(232,85,0,0.35)";
        ctx.fill();

        // Stroke shape
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.stroke();
        ctx.shadowBlur = 0;

        // Dot on each vertex
        keys.forEach((k, i) => {
            let val = data[k], rad = (val/10)*r, a = i*angleStep-Math.PI/2;
            let x=cx+Math.cos(a)*rad, y=cy+Math.sin(a)*rad;
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI*2);
            ctx.fillStyle = "#fff";
            ctx.shadowBlur = 8;
            ctx.shadowColor = color;
>>>>>>> 7081ee4b2ca8bbb32ba7446705dd1cff79a43424
            ctx.fill();
            ctx.shadowBlur = 0;
        });

<<<<<<< HEAD
        // ── Labels ──
        keys.forEach((k, i) => {
            const a = i * step - Math.PI / 2;
            const lx = CX + Math.cos(a) * (R + 34);
            const ly = CY + Math.sin(a) * (R + 34);
            ctx.save();
            ctx.font = "900 17px 'Zen Kaku Gothic New', sans-serif";
            ctx.fillStyle = '#B89B3C';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(k.toUpperCase(), lx, ly);
            ctx.restore();
        });

        // ── Divider line ──
        ctx.beginPath();
        ctx.moveTo(40, 430);
        ctx.lineTo(460, 430);
        ctx.strokeStyle = 'rgba(207,10,44,0.30)';
        ctx.lineWidth = 1;
        ctx.stroke();

        // ── AVG ──
        ctx.save();
        ctx.font = "500 13px 'DM Mono', monospace";
        ctx.fillStyle = '#6A5E52';
        ctx.textAlign = 'center';
        ctx.fillText('AVG  ' + avg.toFixed(2) + ' / 10', CX, 448);
        ctx.restore();

        // ── Rank ──
        ctx.save();
        ctx.shadowBlur = 18;
        ctx.shadowColor = glowColor;
        ctx.font = "900 24px 'Zen Kaku Gothic New', sans-serif";
        ctx.fillStyle = mainColor;
        ctx.textAlign = 'center';
        ctx.fillText(rank, CX, 482);
=======
        // Labels
        keys.forEach((k, i) => {
            let a = i*angleStep-Math.PI/2, labelR = r+38;
            let x=cx+Math.cos(a)*labelR, y=cy+Math.sin(a)*labelR;
            ctx.save();
            ctx.fillStyle = "#c49a1a";
            ctx.font = "bold 18px 'Dela Gothic One', sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(k.toUpperCase(), x, y);
            ctx.restore();
        });

        // AVG text
        ctx.save();
        ctx.fillStyle = "#b8a898";
        ctx.font = "500 14px 'Noto Sans JP', sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("AVG  " + avg.toFixed(1) + " / 10", cx, 438);
        ctx.restore();

        // Rank text with shadow
        ctx.save();
        ctx.shadowBlur = 22;
        ctx.shadowColor = color;
        ctx.font = "bold 26px 'Dela Gothic One', sans-serif";
        ctx.fillStyle = color;
        ctx.textAlign = "center";
        ctx.fillText(rank, cx, 478);
>>>>>>> 7081ee4b2ca8bbb32ba7446705dd1cff79a43424
        ctx.restore();

        try { window.latestStatsRank = String(rank || ''); } catch (e) {}
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
