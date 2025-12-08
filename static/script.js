document.addEventListener('DOMContentLoaded', () => {
    // --- REFERENCIAS ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');
    
    // HUD REFERENCIAS
    const hudPanel = document.getElementById('ticket-hud');
    const hudName = document.getElementById('hud-name');
    const hudDate = document.getElementById('hud-date');
    const hudTime = document.getElementById('hud-time');
    let timerInterval;

    let currentChannelId = null;
    let isFetching = false;

    // --- INICIO ---
    fetchChannels();
    setInterval(() => {
        if (!isFetching && currentChannelId) fetchMessages();
    }, 1000);

    if(msgInput) {
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
            
            channels.forEach(ch => {
                const cName = ch.name || "Unknown";
                const cId = ch.id; 

                const shard = document.createElement('div');
                shard.className = 'channel-shard';
                shard.innerText = `${cName.toUpperCase()}`; 
                
                shard.onclick = () => {
                    currentChannelId = cId;
                    document.querySelectorAll('.channel-shard').forEach(b => b.classList.remove('active'));
                    shard.classList.add('active');
                    
                    // ACTUALIZAR HUD
                    hudPanel.classList.add('visible');
                    hudName.innerText = cName.toUpperCase();
                    
                    const now = new Date();
                    hudDate.innerText = now.toLocaleDateString();
                    startTimer(); 

                    chatFeed.innerHTML = '<div style="text-align:center; margin-top:50px; color:var(--bl-cyan); font-family:\'Koulen\'">SYNCING DATA...</div>';
                    fetchMessages();
                };
                
                channelList.appendChild(shard);
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

    // --- MENSAJES ---
    async function fetchMessages() {
        if (!currentChannelId) return;
        isFetching = true;

        try {
            const res = await fetch('/api/messages');
            const allMsgs = await res.json();
            const msgs = allMsgs.filter(m => m.channel_id === currentChannelId).reverse();
            const isScrolledToBottom = (chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight) < 150;

            chatFeed.innerHTML = '';
            
            if (msgs.length === 0) {
                chatFeed.innerHTML = '<div class="initial-lock"><div class="lock-icon">👁‍🗨</div><p>VOID</p></div>';
            } else {
                msgs.forEach(msg => {
                    const frag = document.createElement('div');
                    frag.className = 'msg-fragment';
                    frag.innerHTML = `
                        <div class="msg-meta">${msg.author_name}</div>
                        <div class="msg-text">${formatLinks(msg.content)}</div>
                    `;
                    chatFeed.appendChild(frag);
                });
            }

            if (isScrolledToBottom) chatFeed.scrollTop = chatFeed.scrollHeight;

        } catch(e) { console.error(e); }
        finally { isFetching = false; }
    }

    function formatLinks(text) {
        if (!text) return "";
        return text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
    }

    window.sendMessage = async () => {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;

        const originalPlaceholder = msgInput.placeholder;
        msgInput.value = '';
        msgInput.placeholder = "SYNCING...";
        msgInput.disabled = true;

        try {
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Server Reject");
            setTimeout(fetchMessages, 200);
        } catch(e) {
            alert("ERROR: " + e.message);
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
        // Cerramos el drawer al mostrar el modal (para que se vea bien)
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
        ctx.fillStyle = "#000"; ctx.fillRect(0,0,500,500);
        const cx = 250, cy = 250, r = 140;
        const color = type === 'offensive' ? '#00f2ff' : '#0066ff';

        ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.shadowBlur = 0;
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
        ctx.fillStyle = type==='offensive'?"rgba(0,242,255,0.5)":"rgba(0,102,255,0.5)"; 
        ctx.fill(); 
        ctx.strokeStyle = "#fff"; ctx.stroke();

        keys.forEach((k, i) => {
            let a = i*angleStep-Math.PI/2, labelR = r+40, x=cx+Math.cos(a)*labelR, y=cy+Math.sin(a)*labelR;
            ctx.save(); ctx.fillStyle = "#fff"; ctx.font = "bold 20px 'Koulen'"; 
            ctx.textAlign = "center"; ctx.textBaseline = "middle";
            ctx.fillText(k.toUpperCase(), x, y); ctx.restore();
        });

        ctx.fillStyle = "#fff"; ctx.font = "16px 'Rajdhani'"; ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)} / 10`, cx, 440);
        ctx.font = "bold 30px 'Koulen'"; ctx.fillStyle = color;
        ctx.fillText(rank, cx, 480);
    }

    // --- AQUÍ ESTÁ EL CAMBIO ---
    const closeBtn = document.getElementById('close-modal');
    if(closeBtn) closeBtn.onclick = () => {
        // 1. Cierra el Modal
        modal.style.display = 'none';
        // 2. Abre de nuevo el Drawer de Stats
        document.getElementById('stats-drawer').classList.add('active');
    };

    const copyBtn = document.getElementById('copy-stats-btn');
    if(copyBtn) copyBtn.onclick = () => {
        canvas.toBlob(blob => {
            try {
                const item = new ClipboardItem({ "image/png": blob });
                navigator.clipboard.write([item]).then(() => {
                    const p = copyBtn.innerText; copyBtn.innerText = "CAPTURED";
                    setTimeout(() => copyBtn.innerText = p, 2000);
                });
            } catch (e) { alert("Right click to save"); }
        });
    };
});