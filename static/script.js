document.addEventListener('DOMContentLoaded', () => {
    console.log(">>> [SYSTEM]: CHAOS_SCRIPT INITIALIZED.");

    // --- VARIABLES ---
    let currentChannelId = null;
    const chatContainer = document.getElementById('chat-messages');
    const channelList = document.getElementById('channel-list');
    
    // Elementos Stats
    const statsModal = document.getElementById('stats-modal');
    const statsCanvas = document.getElementById('stats-canvas');
    const ctx = statsCanvas.getContext('2d');
    const copyBtn = document.getElementById('copy-stats-btn');
    const closeModalBtn = document.getElementById('close-modal');

    // =================================================================
    // 1. SISTEMA DE CANALES Y CHAT
    // =================================================================

    // Cargar canales al iniciar
    fetchChannels();
    // Actualizar mensajes cada 3 seg
    setInterval(fetchMessages, 3000);

    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            
            console.log(">>> [CHANNELS FOUND]:", channels); // Debug en consola
            channelList.innerHTML = '';

            if (channels.length === 0) {
                channelList.innerHTML = '<div style="padding:10px; text-align:right;">NO DATA...</div>';
            }

            channels.forEach(ch => {
                const btn = document.createElement('div');
                btn.className = 'channel-item';
                btn.innerText = `# ${ch.channel_name}`; // Solo nombre
                btn.onclick = () => {
                    currentChannelId = ch.channel_id;
                    // Estilos de activo
                    document.querySelectorAll('.channel-item').forEach(el => el.classList.remove('active'));
                    btn.classList.add('active');
                    fetchMessages();
                };
                channelList.appendChild(btn);
            });
        } catch (e) {
            console.error(">>> [ERROR FETCHING CHANNELS]:", e);
        }
    }

    async function fetchMessages() {
        if (!currentChannelId) return;
        try {
            const res = await fetch('/api/messages'); 
            const allMessages = await res.json();
            // Filtrar por canal seleccionado
            const messages = allMessages.filter(m => m.channel_id == currentChannelId);

            chatContainer.innerHTML = '';
            // Invertir orden para que el más nuevo esté abajo
            messages.reverse().forEach(msg => {
                const msgDiv = document.createElement('div');
                msgDiv.className = 'message-card';
                msgDiv.innerHTML = `
                    <div class="msg-header">
                        <img src="${msg.author_avatar}" class="avatar-sm">
                        <span class="author-name">${msg.author_name}</span>
                    </div>
                    <div class="msg-content">${msg.content}</div>
                    <span class="timestamp">${new Date(msg.timestamp).toLocaleTimeString()}</span>
                `;
                chatContainer.appendChild(msgDiv);
            });
            // Auto scroll al final
            chatContainer.scrollTop = chatContainer.scrollHeight;
        } catch (e) {
            console.error(">>> [ERROR STREAM]:", e);
        }
    }

    // Funcion global para el boton del HTML
    window.sendMessage = async () => {
        const input = document.getElementById('message-input');
        const content = input.value;
        if (!content || !currentChannelId) return;

        try {
            await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            input.value = '';
            setTimeout(fetchMessages, 500); // Recargar rápido
        } catch (e) { alert("ERROR SENDING"); }
    };

    // =================================================================
    // 2. SISTEMA DE STATS (FIXED)
    // =================================================================

    // Lógica de Rangos (Promedio)
    function calculateRankText(avg, type) {
        // avg viene en escala 0-100. Lo pasamos a 0-10 para comparar con tu tabla
        const s = parseFloat((avg / 10).toFixed(1)); 
        
        if (type === 'offensive') {
            if (s < 4.6) return "UNRANKED";
            if (s <= 5.4) return "ROOKIE 🥉";
            if (s <= 6.3) return "AMATEUR ⚽";
            if (s <= 7.2) return "ELITE ⚡";
            if (s <= 8.1) return "PRODIGY 🏅";
            if (s <= 9.0) return "NEW GEN XI ⭐";
            return "WORLD CLASS 👑";
        } else {
            // GK
            if (s <= 6.9) return "D TIER";
            if (s <= 7.9) return "C TIER";
            if (s <= 8.4) return "B TIER";
            if (s <= 8.9) return "A TIER";
            if (s <= 9.4) return "S TIER";
            return "S+ TIER";
        }
    }

    // ASIGNAR FUNCIÓN AL OBJETO WINDOW PARA QUE EL HTML LA VEA
    window.generateStats = () => {
        console.log(">>> BUTTON CLICKED: GENERATING STATS...");

        const offensiveIds = ['sht', 'dbl', 'stl', 'psn', 'dfd'];
        const gkIds = ['dvg', 'biq', 'rfx', 'dtg'];

        let type = 'offensive';
        // Si el primer campo de GK tiene algo, cambiamos a GK
        if (document.getElementById('dvg').value !== "") {
            type = 'gk';
        }

        let values = {};
        let sum = 0;
        let count = 0;
        const idsToUse = type === 'offensive' ? offensiveIds : gkIds;

        idsToUse.forEach(id => {
            let val = parseFloat(document.getElementById(id).value);
            if (isNaN(val)) val = 0; 
            values[id] = val;
            sum += val;
            count++;
        });

        const avg = count > 0 ? (sum / count) : 0;
        const rankText = calculateRankText(avg, type);

        drawChart(type, values, rankText, avg);
        statsModal.style.display = 'flex';
    };

    function drawChart(type, data, rankText, avg) {
        // Limpieza
        ctx.clearRect(0, 0, statsCanvas.width, statsCanvas.height);
        
        // Configuración
        const w = statsCanvas.width;
        const h = statsCanvas.height;
        const cx = w / 2;
        const cy = h / 2 - 30; // Subir un poco
        const maxRadius = 130;

        // Fondo Negro
        ctx.fillStyle = "#000";
        ctx.fillRect(0,0,w,h);

        // Color según tipo
        const color = type === 'offensive' ? '#00f2ff' : '#ff2a00';

        ctx.lineWidth = 3;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.shadowBlur = 15;
        ctx.shadowColor = color;

        const keys = Object.keys(data);
        const total = keys.length;
        const angleStep = (Math.PI * 2) / total;

        // 1. DIBUJAR GRID (Fondo)
        ctx.beginPath();
        for (let r = 20; r <= maxRadius; r += 25) {
            ctx.moveTo(cx + r, cy);
            ctx.arc(cx, cy, r, 0, Math.PI * 2);
        }
        ctx.strokeStyle = "rgba(255,255,255,0.1)";
        ctx.stroke();

        // 2. DIBUJAR DATOS
        ctx.beginPath();
        ctx.strokeStyle = color;
        keys.forEach((key, i) => {
            let val = data[key];
            if(val > 100) val = 100;
            
            const r = (val / 100) * maxRadius;
            const angle = i * angleStep - Math.PI / 2;
            const x = cx + Math.cos(angle) * r;
            const y = cy + Math.sin(angle) * r;

            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);

            // Labels
            const labelR = maxRadius + 30;
            const lx = cx + Math.cos(angle) * labelR;
            const ly = cy + Math.sin(angle) * labelR;
            ctx.font = "bold 16px Courier New";
            ctx.textAlign = "center";
            ctx.fillText(key.toUpperCase(), lx, ly);
        });
        ctx.closePath();
        ctx.fillStyle = type === 'offensive' ? "rgba(0, 242, 255, 0.2)" : "rgba(255, 42, 0, 0.2)";
        ctx.fill();
        ctx.stroke();

        // 3. TEXTO RESULTADO
        ctx.shadowBlur = 0;
        ctx.fillStyle = "#fff";
        ctx.font = "bold 20px Courier New";
        ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)}`, cx, h - 50);

        ctx.font = "bold 24px Impact";
        ctx.fillStyle = color;
        ctx.shadowBlur = 10;
        ctx.fillText(rankText, cx, h - 20);
    }

    // Eventos Modal
    closeModalBtn.onclick = () => statsModal.style.display = 'none';
    
    copyBtn.onclick = () => {
        statsCanvas.toBlob(blob => {
            const item = new ClipboardItem({ "image/png": blob });
            navigator.clipboard.write([item]).then(() => {
                const oldText = copyBtn.innerText;
                copyBtn.innerText = "COPIED!";
                setTimeout(() => copyBtn.innerText = oldText, 2000);
            });
        });
    };
});