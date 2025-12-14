document.addEventListener('DOMContentLoaded', () => {
    // --- REFERENCIAS ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');
    
    // HUD
    const hudPanel = document.getElementById('ticket-hud');
    const hudName = document.getElementById('hud-name');
    const hudTime = document.getElementById('hud-time');
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
    }, 1000);

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
            
            channels.forEach(ch => {
                const cName = ch.name || "Unknown";
                const cId = ch.id; 
                channelMap[cId] = cName;

                // Creamos el ESLABÓN DE LA CADENA
                const link = document.createElement('div');
                link.className = 'chain-link';
                link.innerText = cName.toUpperCase(); 
                
                link.onclick = () => {
                    currentChannelId = cId;
                    document.querySelectorAll('.chain-link').forEach(b => b.classList.remove('active'));
                    link.classList.add('active');
                    
                    // HUD UPDATE
                    hudPanel.classList.add('visible');
                    hudName.innerText = cName.toUpperCase();
                    startTimer();

                    chatFeed.innerHTML = '<div style="text-align:center; padding-top:50px; color:var(--lock-blue); font-family:\'Orbitron\'">BREAKING SEAL...</div>';
                    fetchMessages(true);
                };
                
                channelList.appendChild(link);
            });
        } catch(e) { console.error(e); }
    }

    function startTimer() {
        if (timerInterval) clearInterval(timerInterval);
        let seconds = 0;
        hudTime.innerText = "00:00";
        timerInterval = setInterval(() => {
            seconds++;
            const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
            const secs = (seconds % 60).toString().padStart(2, '0');
            hudTime.innerText = `${mins}:${secs}`;
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
                    chatFeed.innerHTML = '<div class="seal-mark"><div class="mark-symbol">封</div><p>EMPTY VESSEL</p></div>';
                } else {
                    msgs.forEach(msg => {
                        const fused = document.createElement('div');
                        fused.className = 'msg-fused';

                        if (botName && msg.author_id && String(msg.author_id) === String((window._botId || ''))) fused.classList.add('msg-me');
                        else if (botName && msg.author_name === botName) fused.classList.add('msg-me');

                        const rendered = renderDiscordContent(msg.content || '');

                        fused.innerHTML = `
                                <div class="msg-auth">${escapeHtml(msg.author_name || 'Unknown')}</div>
                                <div class="msg-body">${rendered}</div>
                                <div class="msg-meta">${msg.timestamp || ''}</div>
                            `;
                        // dedupe: skip if message with same id already present
                        if (msg.message_id) {
                            const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
                            if (existing) return; // skip this message
                            fused.dataset.msgId = String(msg.message_id);
                        }
                        chatFeed.appendChild(fused);

                        if (msg.message_id) lastMessageId[currentChannelId] = Math.max(lastMessageId[currentChannelId] || 0, Number(msg.message_id));
                    });
                }

                if (isScrolledToBottom) chatFeed.scrollTop = chatFeed.scrollHeight;
            } else {
                // incremental append
                if (msgs && msgs.length > 0) {
                    msgs.forEach(msg => {
                        const fused = document.createElement('div');
                        fused.className = 'msg-fused';

                        if (botName && msg.author_id && String(msg.author_id) === String((window._botId || ''))) fused.classList.add('msg-me');
                        else if (botName && msg.author_name === botName) fused.classList.add('msg-me');

                        const rendered = renderDiscordContent(msg.content || '');
                        fused.innerHTML = `
                            <div class="msg-auth">${escapeHtml(msg.author_name || 'Unknown')}</div>
                            <div class="msg-body">${rendered}</div>
                            <div class="msg-meta">${msg.timestamp || ''}</div>
                        `;
                        // dedupe before appending
                        if (msg.message_id) {
                            const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
                            if (existing) return;
                            fused.dataset.msgId = String(msg.message_id);
                        }
                        // dedupe before appending
                        if (msg.message_id) {
                            const existing = chatFeed.querySelector(`[data-msg-id="${msg.message_id}"]`);
                            if (existing) return;
                            fused.dataset.msgId = String(msg.message_id);
                        }
                        chatFeed.appendChild(fused);
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
        try {
            const author = botName || 'BOT';
            optimisticNode = document.createElement('div');
            optimisticNode.className = 'msg-fused msg-me';
            optimisticNode.setAttribute('data-optimistic', '1');
            optimisticNode.dataset.optimisticTs = String(Date.now());
            optimisticNode.innerHTML = `
                <div class="msg-auth">${author}</div>
                <div class="msg-body">${formatLinks(content)}</div>
                <div class="msg-meta">SENDING...</div>
            `;
            chatFeed.appendChild(optimisticNode);
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch (e) { optimisticNode = null; }

        msgInput.value = '';
        msgInput.placeholder = "ROARING...";
        msgInput.disabled = true;

        try {
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Server Reject");
            // after successful send, refresh messages shortly (real message will arrive via bot->DB)
            setTimeout(fetchMessages, 200);
        } catch(e) {
            alert("SEAL ERROR: " + e.message);
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
        
        if(modal) modal.style.display = 'flex';
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
        ctx.clearRect(0,0,500,500);
        
        // FONDO NEGRO Y FUEGO
        ctx.fillStyle = "#000"; ctx.fillRect(0,0,500,500);
        
        const cx = 250, cy = 250, r = 140;
        
        // COLOR: Si es ofensivo = Cian (Blue Lock), si es GK = Naranja (Kurama)
        const color = type === 'offensive' ? '#00f2ff' : '#ff5e00';

        ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.shadowBlur = 20; ctx.shadowColor = color;
        const keys = Object.keys(data), total = keys.length, angleStep = (Math.PI * 2) / total;

        ctx.beginPath();
        for(let l=1; l<=4; l++) {
            let rad = (r/4)*l;
            if (type === 'gk') { ctx.moveTo(cx+rad, cy); ctx.arc(cx, cy, rad, 0, Math.PI*2); } 
            else {
                for(let i=0; i<=total; i++) {
                    let a = i*angleStep-Math.PI/2, x=cx+Math.cos(a)*rad, y=cy+Math.sin(a)*rad;
                    i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
                }
            }
        }
        ctx.strokeStyle = "#333"; ctx.stroke();

        ctx.beginPath();
        keys.forEach((k, i) => {
            let val = data[k], rad = (val/10)*r, a = i*angleStep-Math.PI/2;
            let x=cx+Math.cos(a)*rad, y=cy+Math.sin(a)*rad;
            i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
        });
        ctx.closePath();
        // Relleno transparente
        ctx.fillStyle = type==='offensive'?"rgba(0,242,255,0.4)":"rgba(255, 94, 0, 0.4)"; 
        ctx.fill(); 
        ctx.strokeStyle = "#fff"; ctx.stroke();

        keys.forEach((k, i) => {
            let a = i*angleStep-Math.PI/2, labelR = r+40, x=cx+Math.cos(a)*labelR, y=cy+Math.sin(a)*labelR;
            ctx.save(); ctx.fillStyle = "#fff"; ctx.font = "bold 20px 'Bebas Neue'"; 
            ctx.textAlign = "center"; ctx.textBaseline = "middle";
            ctx.fillText(k.toUpperCase(), x, y); ctx.restore();
        });

        ctx.fillStyle = "#fff"; ctx.font = "16px 'Orbitron'"; ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)} / 10`, cx, 440);
        ctx.font = "bold 30px 'Bebas Neue'"; ctx.fillStyle = color;
        ctx.fillText(rank, cx, 480);

        // expose latest rank string to other UI handlers for copy
        try { window.latestStatsRank = String(rank || ''); } catch (e) {}
    }

    const closeBtn = document.getElementById('close-modal');
    if(closeBtn) closeBtn.onclick = () => {
        modal.style.display = 'none';
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
            const modal = document.getElementById('stats-modal');

            if (modal && modal.style.display === 'flex') {
                modal.style.display = 'none';
                if (drawer) drawer.classList.remove('active');
                return;
            }

            if (drawer && drawer.classList.contains('active')) {
                drawer.classList.remove('active');
            }
        }
    });
});