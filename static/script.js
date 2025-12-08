document.addEventListener('DOMContentLoaded', () => {
    // --- REFERENCIAS DOM ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');
    
    let currentChannelId = null;
    let isFetching = false;

    // --- INICIO ---
    fetchChannels();
    setInterval(() => {
        if (!isFetching && currentChannelId) fetchMessages();
    }, 1000); // 1 segundo de refresh

    if(msgInput) {
        msgInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') window.sendMessage();
        });
        const sendBtn = document.getElementById('send-btn');
        if(sendBtn) sendBtn.onclick = window.sendMessage;
    }

    // --- LÓGICA DE CANALES ---
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();

            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach(ch => {
                const cName = ch.name || "Unknown";
                const cId = ch.id; 

                const btn = document.createElement('button');
                btn.className = 'channel-btn';
                btn.innerText = `> ${cName}`; 
                
                btn.onclick = () => {
                    currentChannelId = cId;
                    document.querySelectorAll('.channel-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    chatFeed.innerHTML = '<div style="padding:20px; text-align:center; opacity:0.5; color:var(--accent-dim);">/// SYNCING FREQUENCY...</div>';
                    fetchMessages();
                };
                
                channelList.appendChild(btn);
            });
        } catch(e) { 
            console.error("Chan Error", e);
            channelList.innerHTML = '<div style="color:var(--glow-pink); padding:10px;">[OFFLINE MODE]</div>';
        }
    }

    // --- LÓGICA DE MENSAJES ---
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
                chatFeed.innerHTML = '<div class="void-message"><span class="icon">✴</span><p>VOID SIGNAL</p></div>';
            } else {
                msgs.forEach(msg => {
                    const card = document.createElement('div');
                    card.className = 'msg-card';
                    card.innerHTML = `
                        <div class="msg-header">
                            <img src="${msg.author_avatar}" class="msg-avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                            <span>${msg.author_name}</span>
                            <span style="margin-left:auto; opacity:0.5; font-size:0.7rem;">${new Date(msg.timestamp).toLocaleTimeString()}</span>
                        </div>
                        <div class="msg-body">${formatLinks(msg.content)}</div>
                    `;
                    chatFeed.appendChild(card);
                });
            }

            if (isScrolledToBottom) {
                chatFeed.scrollTop = chatFeed.scrollHeight;
            }

        } catch(e) { console.error("Msg Error", e); }
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
        msgInput.placeholder = "/// TRANSMITTING...";
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
            alert("TRANSMISSION ERROR: " + e.message);
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
        const gkInput = document.getElementById('dvg');
        
        if (gkInput && gkInput.value.trim() !== "") type = 'gk';

        const inputs = type === 'offensive' 
            ? ['sht', 'dbl', 'stl', 'psn', 'dfd'] 
            : ['dvg', 'biq', 'rfx', 'dtg'];

        let data = {};
        let sum = 0;
        let count = 0;

        inputs.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            let val = parseFloat(el.value);
            if (isNaN(val)) val = 0; if (val > 10) val = 10; if (val < 0) val = 0;
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
        if (s <= 4.8) return "ROOKIE STRIKERS 🥉 - ⭐";
        if (s <= 5.1) return "ROOKIE STRIKERS 🥉 - ⭐⭐";
        if (s <= 5.4) return "ROOKIE STRIKERS 🥉 - ⭐⭐⭐";
        if (s <= 5.7) return "AMATEUR STRIKER ⚽ - ⭐";
        if (s <= 6.0) return "AMATEUR STRIKER ⚽ - ⭐⭐";
        if (s <= 6.3) return "AMATEUR STRIKER ⚽ - ⭐⭐⭐";
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
        ctx.fillStyle = "#0a0a0a"; 
        ctx.fillRect(0,0,500,500);

        const cx = 250, cy = 250;
        const maxRadius = 140;
        
        // Colores nuevos: Cyan vs Magenta
        const color = type === 'offensive' ? '#00f2ff' : '#ff0055'; 

        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.shadowBlur = 10;
        ctx.shadowColor = color;
        
        const keys = Object.keys(data);
        const total = keys.length;
        const angleStep = (Math.PI * 2) / total;

        ctx.beginPath();
        for(let level=1; level<=4; level++) {
            let r = (maxRadius/4)*level;
            
            if (type === 'gk') {
                ctx.moveTo(cx + r, cy);
                ctx.arc(cx, cy, r, 0, Math.PI * 2);
            } else {
                for(let i=0; i<=total; i++) {
                    let a = i * angleStep - Math.PI/2;
                    let x = cx + Math.cos(a) * r;
                    let y = cy + Math.sin(a) * r;
                    if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
                }
            }
        }
        ctx.strokeStyle = "rgba(255,255,255,0.1)"; 
        ctx.stroke();

        ctx.beginPath();
        keys.forEach((k, i) => {
            let val = data[k];
            let r = (val / 10) * maxRadius;
            let a = i * angleStep - Math.PI/2;
            let x = cx + Math.cos(a) * r;
            let y = cy + Math.sin(a) * r;
            if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
        });
        ctx.closePath();
        
        ctx.fillStyle = type === 'offensive' ? "rgba(0, 242, 255, 0.1)" : "rgba(255, 0, 85, 0.1)";
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.stroke();

        keys.forEach((k, i) => {
            let a = i * angleStep - Math.PI/2;
            let labelR = maxRadius + 35;
            let lx = cx + Math.cos(a) * labelR;
            let ly = cy + Math.sin(a) * labelR;
            
            ctx.save();
            ctx.fillStyle = "#fff";
            ctx.font = "12px 'Space Mono'"; 
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.shadowBlur = 0;
            ctx.fillText(k.toUpperCase(), lx, ly);
            ctx.restore();
        });

        ctx.shadowBlur = 0;
        ctx.fillStyle = "#fff";
        ctx.font = "12px 'Space Mono'"; 
        ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)} / 10`, cx, 440);

        ctx.font = "bold 24px 'Syncopate'"; 
        ctx.fillStyle = color;
        ctx.shadowBlur = 10;
        ctx.shadowColor = color;
        ctx.fillText(rank, cx, 475);
    }

    const closeBtn = document.getElementById('close-modal');
    if(closeBtn) closeBtn.onclick = () => modal.style.display = 'none';

    const copyBtn = document.getElementById('copy-stats-btn');
    if(copyBtn) copyBtn.onclick = () => {
        canvas.toBlob(blob => {
            try {
                const item = new ClipboardItem({ "image/png": blob });
                navigator.clipboard.write([item]).then(() => {
                    const prev = copyBtn.innerText;
                    copyBtn.innerText = "COPIED!";
                    setTimeout(() => copyBtn.innerText = prev, 2000);
                });
            } catch (e) {
                alert("Use click derecho -> Copiar imagen");
            }
        });
    };
});