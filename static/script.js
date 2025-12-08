document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');

    let currentChannelId = null;
    let isFetching = false; // ✨ MEJORA: Evitar peticiones solapadas

    // --- INIT ---
    fetchChannels();
    
    // ✨ MEJORA: Usar setTimeout recursivo es mejor que setInterval para redes lentas
    setInterval(() => {
        if (!isFetching) fetchMessages();
    }, 3000);

    // ✨ MEJORA: Enviar con tecla Enter
    msgInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') window.sendMessage();
    });

    // --- CHAT LOGIC ---
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            
            // Solo limpiar si hay canales nuevos para evitar parpadeo inicial innecesario
            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach(ch => {
                const btn = document.createElement('div');
                btn.className = 'channel-btn';
                btn.innerText = ch.channel_name; // Asegúrate que Python devuelve 'channel_name'
                btn.onclick = () => {
                    currentChannelId = ch.channel_id; // Asegúrate que Python devuelve 'channel_id'
                    document.querySelectorAll('.channel-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    chatFeed.innerHTML = '<div style="padding:20px; text-align:center; color:#888;">Cargando...</div>';
                    fetchMessages();
                };
                channelList.appendChild(btn);
            });
        } catch(e) { console.error("Chan Error", e); }
    }

    async function fetchMessages() {
        if (!currentChannelId) return;
        isFetching = true;

        try {
            const res = await fetch('/api/messages');
            const allMsgs = await res.json();
            // Invertimos porque el backend suele dar ORDER BY DESC (nuevo primero), 
            // pero en el chat queremos leer de arriba (viejo) a abajo (nuevo).
            const msgs = allMsgs.filter(m => m.channel_id == currentChannelId).reverse();
            
            // ✨ MEJORA: DETECCIÓN DE SCROLL INTELIGENTE
            // Solo bajamos el scroll si el usuario ya estaba abajo del todo (o si es la primera carga)
            const isScrolledToBottom = (chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight) < 100;

            chatFeed.innerHTML = '';
            msgs.forEach(msg => {
                const card = document.createElement('div');
                card.className = 'msg-card';
                card.innerHTML = `
                    <div class="msg-header">
                        <img src="${msg.author_avatar}" class="msg-avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                        <span>${msg.author_name}</span>
                        <span style="margin-left:auto; opacity:0.5; font-size:0.7rem;">${new Date(msg.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div class="msg-body">${formatMessage(msg.content)}</div>
                `;
                chatFeed.appendChild(card);
            });

            // Solo forzamos scroll si el usuario no estaba leyendo arriba
            if (isScrolledToBottom) {
                chatFeed.scrollTop = chatFeed.scrollHeight;
            }

        } catch(e) { console.error("Msg Error", e); }
        finally { isFetching = false; }
    }

    // ✨ MEJORA: Pequeña función para convertir enlaces en clickeables (opcional)
    function formatMessage(content) {
        if(!content) return "";
        // Simple regex para URLs
        return content.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" style="color:#00f2ff">$1</a>');
    }

    window.sendMessage = async () => {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;
        
        // ✨ MEJORA: Feedback visual inmediato
        const originalPlaceholder = msgInput.placeholder;
        msgInput.value = '';
        msgInput.placeholder = "Enviando...";
        msgInput.disabled = true;

        try {
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            if (!res.ok) throw new Error("Server Reject");
            
            setTimeout(fetchMessages, 500); // Refrescar rápido
        } catch(e) { 
            alert("ERROR SENDING MSG: " + e); 
            msgInput.value = content; // Devolver el texto si falló
        } finally {
            msgInput.placeholder = originalPlaceholder;
            msgInput.disabled = false;
            msgInput.focus();
        }
    };

    // --- STATS LOGIC (SIN CAMBIOS, ESTA PERFECTA) ---
    window.generateStats = () => {
        let type = 'offensive';
        if (document.getElementById('dvg') && document.getElementById('dvg').value !== "") {
            type = 'gk';
        }

        // Asegúrate de que estos IDs existan en tu HTML
        const inputs = type === 'offensive' 
            ? ['sht', 'dbl', 'stl', 'psn', 'dfd'] 
            : ['dvg', 'biq', 'rfx', 'dtg'];

        let data = {};
        let sum = 0;
        let count = 0;

        inputs.forEach(id => {
            const el = document.getElementById(id);
            if(!el) return; // Seguridad por si falta un input
            let val = parseFloat(el.value) || 0;
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
        ctx.clearRect(0,0,500,500);
        ctx.fillStyle = "#020205";
        ctx.fillRect(0,0,500,500);

        const cx = 250, cy = 230;
        const maxRadius = 130;
        const color = type === 'offensive' ? '#00f2ff' : '#ff0040';

        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.shadowBlur = 15;
        ctx.shadowColor = color;
        
        const keys = Object.keys(data);
        const total = keys.length;
        const angleStep = (Math.PI * 2) / total;

        // BASE
        ctx.beginPath();
        for(let i=1; i<=4; i++) {
            let r = (maxRadius/4)*i;
            ctx.moveTo(cx + r, cy);
            ctx.arc(cx, cy, r, 0, Math.PI*2);
        }
        ctx.strokeStyle = "rgba(255,255,255,0.1)";
        ctx.stroke();

        // DATOS
        if (type === 'gk') {
            keys.forEach((k, i) => {
                let val = data[k];
                let r = (val / 10) * maxRadius;
                let startAngle = i * angleStep - Math.PI/2;
                let endAngle = (i + 1) * angleStep - Math.PI/2;

                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.arc(cx, cy, r, startAngle, endAngle);
                ctx.lineTo(cx, cy);
                ctx.fillStyle = "rgba(255, 0, 64, 0.4)";
                ctx.fill();
                ctx.strokeStyle = color;
                ctx.stroke();
            });
        } else {
            ctx.beginPath();
            keys.forEach((k, i) => {
                let val = data[k];
                let r = (val / 10) * maxRadius;
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

        // TEXTO
        keys.forEach((k, i) => {
            let a = type === 'gk' 
                ? (i * angleStep + (angleStep/2)) - Math.PI/2 
                : i * angleStep - Math.PI/2;
            
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

        // RESULTADOS
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

    // MODAL
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
                alert("Error al copiar: Contexto no seguro o navegador incompatible.");
            }
        });
    };
});