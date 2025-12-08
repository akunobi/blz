document.addEventListener('DOMContentLoaded', () => {
    console.log(">>> SYSTEM: INITIALIZED.");

    let currentChannelId = null;
    const chatFeed = document.getElementById('chat-feed');
    const channelList = document.getElementById('channel-list');
    
    // UI ELEMENTS
    const statsModal = document.getElementById('stats-modal');
    const closeModalBtn = document.getElementById('close-modal');
    const copyBtn = document.getElementById('copy-stats-btn');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');

    // ===========================================
    // 1. CHAT LOGIC
    // ===========================================
    fetchChannels();
    setInterval(fetchMessages, 3000); // Auto refresh chat

    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            
            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach(ch => {
                const btn = document.createElement('div');
                btn.className = 'channel-btn';
                btn.innerText = `# ${ch.channel_name}`;
                btn.onclick = () => {
                    currentChannelId = ch.channel_id;
                    document.querySelectorAll('.channel-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    fetchMessages();
                };
                channelList.appendChild(btn);
            });
        } catch(e) { console.error("Link Error", e); }
    }

    async function fetchMessages() {
        if (!currentChannelId) return;
        try {
            const res = await fetch('/api/messages');
            const allMsgs = await res.json();
            // Filtramos y ordenamos
            const msgs = allMsgs.filter(m => m.channel_id == currentChannelId).reverse();
            
            chatFeed.innerHTML = '';
            
            msgs.forEach(msg => {
                // Crear tarjeta de mensaje
                const card = document.createElement('div');
                card.className = 'msg-card';
                
                // AQUÍ SE VE DE QUIÉN ES EL MENSAJE
                card.innerHTML = `
                    <div class="msg-header">
                        <img src="${msg.author_avatar}" class="msg-avatar">
                        <span class="msg-author">${msg.author_name}</span>
                    </div>
                    <div class="msg-body">${msg.content}</div>
                    <div class="msg-time">${new Date(msg.timestamp).toLocaleTimeString()}</div>
                `;
                chatFeed.appendChild(card);
            });
            // Auto scroll down
            chatFeed.scrollTop = chatFeed.scrollHeight;

        } catch(e) { console.error("Stream Error", e); }
    }

    // ENVIAR MENSAJE
    window.sendMessage = async () => {
        const input = document.getElementById('msg-input');
        const content = input.value;
        if (!content || !currentChannelId) return;

        try {
            await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            input.value = '';
            setTimeout(fetchMessages, 500); // Refresco rápido
        } catch(e) { alert("ERROR DE TRANSMISIÓN"); }
    };

    // Permitir enviar con la tecla Enter
    document.getElementById('msg-input').addEventListener("keypress", function(event) {
        if (event.key === "Enter") {
            window.sendMessage();
        }
    });

    // ===========================================
    // 2. STATS & POPUP LOGIC
    // ===========================================
    
    // Cerrar Modal
    closeModalBtn.onclick = () => {
        statsModal.style.display = 'none';
    };

    // Copiar Imagen
    copyBtn.onclick = () => {
        canvas.toBlob(blob => {
            const item = new ClipboardItem({ "image/png": blob });
            navigator.clipboard.write([item]).then(() => {
                const oldText = copyBtn.innerText;
                copyBtn.innerText = "COPIED [MEMORY]";
                copyBtn.style.color = "#00f2ff";
                setTimeout(() => {
                    copyBtn.innerText = oldText;
                    copyBtn.style.color = "";
                }, 2000);
            });
        });
    };

    // Función de cálculo de rangos
    function getRank(avg, type) {
        let s = parseFloat((avg / 10).toFixed(1));
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

    window.generateStats = () => {
        // Detectar si es ofensivo o portero
        let type = 'offensive';
        const gkVal = document.getElementById('dvg').value;
        if (gkVal && gkVal !== "") type = 'gk';

        const inputs = type === 'offensive' 
            ? ['sht', 'dbl', 'stl', 'psn', 'dfd'] 
            : ['dvg', 'biq', 'rfx', 'dtg'];

        let data = {};
        let sum = 0;
        let count = 0;

        inputs.forEach(id => {
            let val = parseFloat(document.getElementById(id).value) || 0;
            if(val > 100) val = 100;
            data[id] = val;
            sum += val;
            count++;
        });

        const avg = count > 0 ? sum / count : 0;
        const rank = getRank(avg, type);

        drawCanvas(type, data, avg, rank);
        
        // MOSTRAR EL POPUP (FLEX)
        statsModal.style.display = 'flex';
    };

    function drawCanvas(type, data, avg, rank) {
        // Reset
        ctx.clearRect(0,0,500,500);
        ctx.fillStyle = "#020205"; // Fondo oscuro casi negro
        ctx.fillRect(0,0,500,500);

        const cx = 250, cy = 230;
        const radius = 130;
        const color = type === 'offensive' ? '#00f2ff' : '#ff2a00';

        // Estilos
        ctx.lineWidth = 3;
        ctx.strokeStyle = color;
        ctx.shadowBlur = 20;
        ctx.shadowColor = color;

        const keys = Object.keys(data);
        const step = (Math.PI * 2) / keys.length;

        // 1. Grid (Fondo)
        ctx.beginPath();
        for (let i = 1; i <= 4; i++) {
            let r = (radius / 4) * i;
            if (type === 'gk') {
                ctx.moveTo(cx + r, cy);
                ctx.arc(cx, cy, r, 0, Math.PI*2);
            } else {
                // Pentágono
                for (let j = 0; j < keys.length; j++) {
                    let a = j * step - Math.PI/2;
                    let x = cx + Math.cos(a) * r;
                    let y = cy + Math.sin(a) * r;
                    if(j===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
                }
                ctx.closePath();
            }
        }
        ctx.save();
        ctx.strokeStyle = "rgba(255,255,255,0.1)";
        ctx.shadowBlur = 0;
        ctx.stroke();
        ctx.restore();

        // 2. Data Shape
        ctx.beginPath();
        keys.forEach((k, i) => {
            let val = data[k];
            let r = (val / 100) * radius;
            let a = i * step - Math.PI/2;
            let x = cx + Math.cos(a) * r;
            let y = cy + Math.sin(a) * r;
            if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
            
            // Labels
            ctx.save();
            ctx.fillStyle = "#fff";
            ctx.font = "bold 14px Consolas";
            ctx.textAlign = "center";
            ctx.shadowBlur = 0;
            let lx = cx + Math.cos(a) * (radius + 25);
            let ly = cy + Math.sin(a) * (radius + 25);
            ctx.fillText(k.toUpperCase(), lx, ly);
            ctx.restore();
        });
        ctx.closePath();
        ctx.fillStyle = type === 'offensive' ? "rgba(0, 242, 255, 0.2)" : "rgba(255, 42, 0, 0.2)";
        ctx.fill();
        ctx.stroke();

        // 3. Texts
        ctx.fillStyle = "#fff";
        ctx.textAlign = "center";
        ctx.shadowBlur = 0;
        ctx.font = "bold 20px Consolas";
        ctx.fillText(`AVG: ${avg.toFixed(1)}`, cx, 430);

        ctx.fillStyle = color;
        ctx.font = "bold 26px Impact";
        ctx.shadowBlur = 10;
        ctx.shadowColor = color;
        ctx.fillText(rank, cx, 460);
    }
});