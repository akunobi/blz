document.addEventListener('DOMContentLoaded', () => {
    // --- REFERENCIAS ---
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
    }, 1000); 

    if(msgInput) {
        msgInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') window.sendMessage();
        });
        document.getElementById('send-btn').onclick = window.sendMessage;
    }

    // --- CANALES (ORBITALES) ---
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();

            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach(ch => {
                const cName = ch.name || "Unknown";
                const cId = ch.id; 

                // Creamos un NODO en lugar de un botón simple
                const node = document.createElement('div');
                node.className = 'channel-node';
                node.innerText = cName.toUpperCase(); // Todo en mayúsculas estilo técnico
                
                node.onclick = () => {
                    currentChannelId = cId;
                    document.querySelectorAll('.channel-node').forEach(b => b.classList.remove('active'));
                    node.classList.add('active');
                    chatFeed.innerHTML = '<div class="system-msg"><span class="pulse-icon">⚡</span>ESTABLISHING LINK...</div>';
                    fetchMessages();
                };
                
                channelList.appendChild(node);
            });
        } catch(e) { 
            console.error("Error", e);
            channelList.innerHTML = '<div style="color:var(--neon-blue); padding:20px; text-align:right;">[OFFLINE]</div>';
        }
    }

    // --- MENSAJES (BLOQUES) ---
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
                chatFeed.innerHTML = '<div class="system-msg">NO DATA FRAGMENTS</div>';
            } else {
                msgs.forEach(msg => {
                    const block = document.createElement('div');
                    block.className = 'msg-block';
                    // Avatar opcional, aquí solo nombre y texto para limpieza
                    block.innerHTML = `
                        <div class="msg-author">${msg.author_name}</div>
                        <div class="msg-content">${formatLinks(msg.content)}</div>
                    `;
                    chatFeed.appendChild(block);
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
        msgInput.placeholder = "TRANSMITTING...";
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

    // --- GRÁFICOS (ESTILO NEÓN) ---
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

    // (Tus funciones de rango getOffensiveRank y getGKRank van aquí, iguales que antes)
    function getOffensiveRank(s) {
        if (s < 4.6) return "N/A";
        if (s <= 4.8) return "ROOKIE 🥉 - ⭐";
        // ... (resto de tus rangos) ...
        return "WORLD CLASS 👑 - ⭐⭐⭐";
    }
    function getGKRank(s) {
        if (s <= 6.9) return "D TIER";
        // ... (resto de tus rangos) ...
        return "S+ TIER";
    }

    function drawGraph(type, data, avg, rank) {
        ctx.clearRect(0,0,500,500);
        ctx.fillStyle = "#020205"; ctx.fillRect(0,0,500,500); // Fondo negro profundo
        
        const cx = 250, cy = 250, r = 140;
        const color = type === 'offensive' ? '#00f2ff' : '#0066ff'; // Cian vs Azul Eléctrico

        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.shadowBlur = 20; ctx.shadowColor = color;
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
        ctx.strokeStyle = "rgba(0, 242, 255, 0.2)"; 
        ctx.stroke();

        ctx.beginPath();
        keys.forEach((k, i) => {
            let val = data[k], rad = (val/10)*r, a = i*angleStep-Math.PI/2;
            let x=cx+Math.cos(a)*rad, y=cy+Math.sin(a)*rad;
            i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
        });
        ctx.closePath();
        ctx.fillStyle = type==='offensive'?"rgba(0, 242, 255, 0.3)":"rgba(0, 102, 255, 0.3)";
        ctx.fill(); ctx.stroke();

        keys.forEach((k, i) => {
            let a = i*angleStep-Math.PI/2, labelR = r+40, x=cx+Math.cos(a)*labelR, y=cy+Math.sin(a)*labelR;
            ctx.save(); ctx.fillStyle = "#fff"; ctx.font = "bold 16px 'Orbitron'"; 
            ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.shadowBlur = 0;
            ctx.fillText(k.toUpperCase(), x, y); ctx.restore();
        });

        ctx.shadowBlur = 0; ctx.fillStyle = "#fff"; ctx.font = "14px 'Rajdhani'"; ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)} / 10`, cx, 440);
        ctx.font = "bold 24px 'Orbitron'"; ctx.fillStyle = color; ctx.shadowBlur = 10; ctx.shadowColor = color;
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
                    const p = copyBtn.innerText; copyBtn.innerText = "SAVED!";
                    setTimeout(() => copyBtn.innerText = p, 2000);
                });
            } catch (e) { alert("Right click to save"); }
        });
    };
});