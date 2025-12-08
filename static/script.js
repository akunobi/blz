document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');

    let currentChannelId = null;

    // --- INIT ---
    fetchChannels();
    setInterval(fetchMessages, 3000);

    // --- CHAT LOGIC ---
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
        } catch(e) { console.error("Chan Error", e); }
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
            chatFeed.scrollTop = chatFeed.scrollHeight;
        } catch(e) { console.error("Msg Error", e); }
    }

    window.sendMessage = async () => {
        const content = msgInput.value;
        if (!content || !currentChannelId) return;
        try {
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            if (!res.ok) throw new Error("Server Reject");
            msgInput.value = '';
            setTimeout(fetchMessages, 500);
        } catch(e) { alert("ERROR SENDING MSG: " + e); }
    };

    // --- STATS LOGIC (0-10 SCALE) ---
    window.generateStats = () => {
        let type = 'offensive';
        // Si hay valor en el primer campo de GK, asumimos GK
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
            // Obtener valor (puede ser decimal, ej: 8.5)
            let val = parseFloat(document.getElementById(id).value) || 0;
            // Clamping 0-10
            if (val > 10) val = 10;
            if (val < 0) val = 0;
            
            data[id] = val;
            sum += val;
            count++;
        });

        const avg = count > 0 ? sum / count : 0;
        const rank = getRankText(avg, type);

        drawGraph(type, data, avg, rank);
        modal.style.display = 'flex';
    };

    function getRankText(s, type) {
        // s ya viene en escala 0-10
        if (type === 'offensive') {
            if (s < 4.6) return "UNRANKED";
            if (s <= 5.4) return "ROOKIE 🥉";
            if (s <= 6.3) return "AMATEUR ⚽";
            if (s <= 7.2) return "ELITE ⚡";
            if (s <= 8.1) return "PRODIGY 🏅";
            if (s <= 9.0) return "NEW GEN XI ⭐";
            return "WORLD CLASS 👑";
        } else {
            if (s <= 6.9) return "D TIER";
            if (s <= 7.9) return "C TIER";
            if (s <= 8.4) return "B TIER";
            if (s <= 8.9) return "A TIER";
            if (s <= 9.4) return "S TIER";
            return "S+ TIER";
        }
    }

    function drawGraph(type, data, avg, rank) {
        // Reset
        ctx.clearRect(0,0,500,500);
        ctx.fillStyle = "#020205";
        ctx.fillRect(0,0,500,500);

        const cx = 250, cy = 230;
        const maxRadius = 130; // Radio máximo
        const color = type === 'offensive' ? '#00f2ff' : '#ff0040';

        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.shadowBlur = 15;
        ctx.shadowColor = color;
        
        const keys = Object.keys(data);
        const total = keys.length;
        const angleStep = (Math.PI * 2) / total;

        // --- DIBUJAR BASE ---
        ctx.beginPath();
        // Círculos de guía (al 25%, 50%, 75%, 100% de 10)
        for(let i=1; i<=4; i++) {
            let r = (maxRadius/4)*i;
            ctx.moveTo(cx + r, cy);
            ctx.arc(cx, cy, r, 0, Math.PI*2);
        }
        ctx.strokeStyle = "rgba(255,255,255,0.1)";
        ctx.stroke();

        // --- DIBUJAR DATOS ---
        
        if (type === 'gk') {
            // === GK: SECTORES (Pie Slices / Polar Area) ===
            keys.forEach((k, i) => {
                let val = data[k];
                let r = (val / 10) * maxRadius; // Escala sobre 10
                
                // Angulo inicio y fin del sector
                let startAngle = i * angleStep - Math.PI/2;
                let endAngle = (i + 1) * angleStep - Math.PI/2;

                ctx.beginPath();
                ctx.moveTo(cx, cy); // Centro
                ctx.arc(cx, cy, r, startAngle, endAngle); // Arco externo
                ctx.lineTo(cx, cy); // Volver al centro
                ctx.fillStyle = "rgba(255, 0, 64, 0.4)"; // Relleno solido
                ctx.fill();
                ctx.strokeStyle = color;
                ctx.stroke();
            });

        } else {
            // === OFFENSIVE: PENTAGONO ===
            ctx.beginPath();
            keys.forEach((k, i) => {
                let val = data[k];
                let r = (val / 10) * maxRadius; // Escala sobre 10
                let a = i * angleStep - Math.PI/2;
                let x = cx + Math.cos(a) * r;
                let y = cy + Math.sin(a) * r;
                
                if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
            });
            ctx.closePath();
            ctx.fillStyle = "rgba(0, 242, 255, 0.2)";
            ctx.fill();
            ctx.stroke();
        }

        // --- ETIQUETAS ---
        keys.forEach((k, i) => {
            // Posicion etiqueta fija afuera
            let a = type === 'gk' 
                ? (i * angleStep + (angleStep/2)) - Math.PI/2 // GK: Centrado en el sector
                : i * angleStep - Math.PI/2; // Off: En el vértice
            
            let labelR = maxRadius + 30;
            let lx = cx + Math.cos(a) * labelR;
            let ly = cy + Math.sin(a) * labelR;
            
            ctx.save();
            ctx.fillStyle = "#fff";
            ctx.font = "bold 16px Consolas";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.shadowBlur = 0;
            ctx.fillText(k.toUpperCase(), lx, ly);
            ctx.restore();
        });

        // --- RESULTADOS TEXTO ---
        ctx.shadowBlur = 0;
        ctx.fillStyle = "#fff";
        ctx.font = "20px Courier New";
        ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)} / 10`, cx, 430);

        ctx.font = "bold 28px Impact";
        ctx.fillStyle = color;
        ctx.shadowBlur = 10;
        ctx.fillText(rank, cx, 465);
    }

    // --- MODAL CONTROLS ---
    document.getElementById('close-modal').onclick = () => modal.style.display = 'none';
    
    document.getElementById('copy-stats-btn').onclick = () => {
        canvas.toBlob(blob => {
            // Clipboard API requiere contexto seguro (https o localhost)
            try {
                const item = new ClipboardItem({ "image/png": blob });
                navigator.clipboard.write([item]).then(() => {
                    const btn = document.getElementById('copy-stats-btn');
                    const prev = btn.innerText;
                    btn.innerText = "COPIED!";
                    setTimeout(() => btn.innerText = prev, 2000);
                }).catch(err => alert("Clipboard Error: " + err));
            } catch (e) {
                alert("Browser not supporting direct clipboard write. Right click image to save.");
            }
        });
    };
});