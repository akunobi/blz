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

    // --- INICIO ---
    // Fetch bot info used to mark bot-authored messages
    (async function fetchBotInfo(){
        try{
            const r = await fetch('/api/botinfo');
            if (r.ok){
                const j = await r.json();
                botName = j.name || null;
            }
        }catch(e){ console.warn('botinfo fetch failed', e); }
    })();
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

    // --- CANALES (CADENAS) ---
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();

            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach(ch => {
                const cName = ch.name || "Unknown";
                const cId = ch.id; 

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
                    fetchMessages();
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
                chatFeed.innerHTML = '<div class="seal-mark"><div class="mark-symbol">封</div><p>EMPTY VESSEL</p></div>';
            } else {
                msgs.forEach(msg => {
                    const fused = document.createElement('div');
                    fused.className = 'msg-fused';

                    // If this message was authored by the bot, mark it so CSS can align it to the right
                    if (botName && msg.author_name === botName) fused.classList.add('msg-me');

                    fused.innerHTML = `
                        <div class="msg-auth">${msg.author_name}</div>
                        <div class="msg-body">${formatLinks(msg.content)}</div>
                        <div class="msg-meta">${msg.timestamp || ''}</div>
                    `;
                    chatFeed.appendChild(fused);
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
            setTimeout(fetchMessages, 200);
        } catch(e) {
            alert("SEAL ERROR: " + e.message);
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