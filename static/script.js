document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');

    let currentChannelId = null;

    // --- 1. CARGA INICIAL ---
    fetchChannels();
    setInterval(fetchMessages, 3000);

    // --- 2. CANALES Y MENSAJES ---
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            
            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach(ch => {
                const btn = document.createElement('div');
                btn.className = 'channel-btn';
                btn.innerText = ch.channel_name;
                btn.onclick = () => {
                    currentChannelId = ch.channel_id;
                    document.querySelectorAll('.channel-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    fetchMessages();
                };
                channelList.appendChild(btn);
            });
        } catch(e) { console.error("Error Channels", e); }
    }

    async function fetchMessages() {
        if (!currentChannelId) return;
        try {
            const res = await fetch('/api/messages');
            const allMsgs = await res.json();
            const msgs = allMsgs.filter(m => m.channel_id == currentChannelId).reverse();
            
            chatFeed.innerHTML = '';
            msgs.forEach(msg => {
                const card = document.createElement('div');
                card.className = 'msg-card';
                card.innerHTML = `
                    <div class="msg-header">
                        <img src="${msg.author_avatar}" class="msg-avatar">
                        <span>${msg.author_name}</span>
                        <span style="margin-left:auto; opacity:0.5; font-size:0.7rem;">${new Date(msg.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div class="msg-body">${msg.content}</div>
                `;
                chatFeed.appendChild(card);
            });
            // SCROLL DOWN AUTOMÁTICO
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch(e) { console.error("Error Msgs", e); }
    }

    window.sendMessage = async () => {
        const content = msgInput.value;
        if (!content || !currentChannelId) return;
        try {
            await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            msgInput.value = '';
            setTimeout(fetchMessages, 500);
        } catch(e) { alert("ERROR TRANSMISIÓN"); }
    };

    // --- 3. UI STATS (PENTÁGONO VS CÍRCULO) ---
    window.generateStats = () => {
        // Lógica: Si hay algo escrito en GK (dvg), es GK. Si no, Ofensivo.
        let type = 'offensive';
        if (document.getElementById('dvg').value !== "") {
            type = 'gk';
        }

        const inputs = type === 'offensive' 
            ? ['sht', 'dbl', 'stl', 'psn', 'dfd'] 
            : ['dvg', 'biq', 'rfx', 'dtg'];

        let data = {};
        let sum = 0;
        let count = 0;

        inputs.forEach(id => {
            let val = parseFloat(document.getElementById(id).value) || 0;
            if (val > 100) val = 100;
            data[id] = val;
            sum += val;
            count++;
        });

        const avg = count > 0 ? sum / count : 0;
        const rank = getRankText(avg, type); // Tu funcion de rangos previa

        drawGraph(type, data, avg, rank);
        modal.style.display = 'flex';
    };

    function getRankText(avg, type) {
        let s = parseFloat((avg / 10).toFixed(1));
        if (type === 'offensive') {
            if (s <= 5.4) return "ROOKIE 🥉";
            if (s <= 7.2) return "ELITE ⚡";
            if (s <= 9.0) return "NEW GEN XI ⭐";
            return "WORLD CLASS 👑";
        } else {
            if (s <= 6.9) return "D TIER";
            if (s <= 8.4) return "B TIER";
            if (s <= 9.4) return "S TIER";
            return "S+ TIER";
        }
    }

    function drawGraph(type, data, avg, rank) {
        // Reset Canvas
        ctx.clearRect(0,0,500,500);
        ctx.fillStyle = "#020205";
        ctx.fillRect(0,0,500,500);

        const cx = 250, cy = 230, radius = 130;
        const color = type === 'offensive' ? '#00f2ff' : '#ff0040';

        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.shadowBlur = 15;
        ctx.shadowColor = color;
        
        const keys = Object.keys(data);
        const step = (Math.PI * 2) / keys.length;

        // 1. DIBUJAR BASE (Pentágono o Círculo)
        ctx.beginPath();
        if (type === 'gk') {
            // Círculos Concentricos (GK)
            for(let i=1; i<=4; i++) {
                ctx.moveTo(cx + (radius/4)*i, cy);
                ctx.arc(cx, cy, (radius/4)*i, 0, Math.PI*2);
            }
        } else {
            // Pentágono Grid (Offensive)
            for (let i = 1; i <= 4; i++) {
                let r = (radius / 4) * i;
                for (let j = 0; j < keys.length; j++) {
                    let a = j * step - Math.PI/2;
                    let x = cx + Math.cos(a) * r;
                    let y = cy + Math.sin(a) * r;
                    if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                }
                ctx.closePath();
            }
        }
        ctx.save();
        ctx.strokeStyle = "rgba(255,255,255,0.1)"; // Grid tenue
        ctx.stroke();
        ctx.restore();

        // 2. DIBUJAR FORMA DE DATOS
        ctx.beginPath();
        keys.forEach((k, i) => {
            let val = data[k];
            let r = (val / 100) * radius;
            let a = i * step - Math.PI/2;
            let x = cx + Math.cos(a) * r;
            let y = cy + Math.sin(a) * r;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            
            // Labels
            drawLabel(k.toUpperCase(), a, cx, cy, radius + 25);
        });
        ctx.closePath();
        ctx.fillStyle = type === 'offensive' ? "rgba(0, 242, 255, 0.2)" : "rgba(255, 0, 64, 0.2)";
        ctx.fill();
        ctx.stroke();

        // 3. TEXTOS
        ctx.shadowBlur = 0;
        ctx.fillStyle = "#fff";
        ctx.font = "20px Courier New";
        ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)}`, cx, 430);

        ctx.font = "bold 28px Impact";
        ctx.fillStyle = color;
        ctx.shadowBlur = 10;
        ctx.fillText(rank, cx, 465);
    }

    function drawLabel(text, angle, cx, cy, r) {
        let x = cx + Math.cos(angle) * r;
        let y = cy + Math.sin(angle) * r;
        ctx.save();
        ctx.fillStyle = "#fff";
        ctx.font = "bold 14px Consolas";
        ctx.textAlign = "center";
        ctx.fillText(text, x, y);
        ctx.restore();
    }

    // BOTONES MODAL
    document.getElementById('close-modal').onclick = () => modal.style.display = 'none';
    document.getElementById('copy-stats-btn').onclick = () => {
        canvas.toBlob(blob => {
            const item = new ClipboardItem({ "image/png": blob });
            navigator.clipboard.write([item]);
            alert("IMAGE COPIED TO CLIPBOARD");
        });
    };
});