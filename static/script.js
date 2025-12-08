document.addEventListener('DOMContentLoaded', () => {
    console.log(">>> [SYSTEM]: EGOIST_SCRIPT_LOADED. INIT_CHAOS_PROTOCOL...");

    // --- VARIABLES GLOBALES ---
    let currentChannelId = null;
    const chatContainer = document.getElementById('chat-messages');
    const channelList = document.getElementById('channel-list');
    
    // --- ELEMENTOS STATS ---
    const statsModal = document.getElementById('stats-modal');
    const statsCanvas = document.getElementById('stats-canvas');
    const ctx = statsCanvas.getContext('2d');
    const copyBtn = document.getElementById('copy-stats-btn');
    const closeModalBtn = document.getElementById('close-modal');

    // =================================================================
    // 1. SISTEMA DE TICKETS (Igual que antes)
    // =================================================================

    setInterval(fetchMessages, 3000);
    fetchChannels();

    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            channelList.innerHTML = '';
            channels.forEach(ch => {
                const btn = document.createElement('div');
                btn.className = 'channel-item glitch-effect';
                btn.innerText = `# ${ch.channel_name}`;
                btn.onclick = () => {
                    currentChannelId = ch.channel_id;
                    document.querySelectorAll('.channel-item').forEach(el => el.classList.remove('active'));
                    btn.classList.add('active');
                    fetchMessages();
                };
                channelList.appendChild(btn);
            });
        } catch (e) {
            console.error(">>> [ERROR]: CANAL LINK SEVERED.", e);
        }
    }

    async function fetchMessages() {
        if (!currentChannelId) return;
        try {
            const res = await fetch('/api/messages'); 
            const allMessages = await res.json();
            const messages = allMessages.filter(m => m.channel_id == currentChannelId);

            chatContainer.innerHTML = '';
            messages.reverse().forEach(msg => {
                const msgDiv = document.createElement('div');
                msgDiv.className = 'message-card';
                msgDiv.innerHTML = `
                    <div class="msg-header">
                        <img src="${msg.author_avatar}" class="avatar-sm">
                        <span class="author-name">${msg.author_name}</span>
                        <span class="timestamp">${new Date(msg.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div class="msg-content">${msg.content}</div>
                `;
                chatContainer.appendChild(msgDiv);
            });
            chatContainer.scrollTop = chatContainer.scrollHeight;
        } catch (e) {
            console.error(">>> [ERROR]: DATA STREAM CORRUPTED.", e);
        }
    }

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
            fetchMessages();
        } catch (e) { alert(">>> [ERROR]: MESSAGE REJECTED."); }
    };

    // =================================================================
    // 2. LÓGICA DE RANGOS (NUEVO)
    // =================================================================

    function calculateOffensiveRank(avg) {
        // Normalizamos el promedio a 1 decimal
        const s = parseFloat(avg.toFixed(1)); 

        if (s < 4.6) return "N/A";

        // Rookie Strikers 🥉
        if (s >= 4.6 && s <= 5.4) {
            let stars = "⭐";
            if (s >= 4.8) stars = "⭐⭐";
            if (s >= 5.2) stars = "⭐⭐⭐";
            return `Rookie Strikers 🥉 - ${stars}`;
        }
        // Amateur Striker ⚽
        if (s >= 5.5 && s <= 6.3) {
            let stars = "⭐";
            if (s >= 5.8) stars = "⭐⭐";
            if (s >= 6.1) stars = "⭐⭐⭐";
            return `Amateur Striker ⚽ - ${stars}`;
        }
        // Elite ⚡
        if (s >= 6.4 && s <= 7.2) {
            let stars = "⭐";
            if (s >= 6.7) stars = "⭐⭐";
            if (s >= 7.0) stars = "⭐⭐⭐";
            return `Elite ⚡ - ${stars}`;
        }
        // Prodigy 🏅
        if (s >= 7.3 && s <= 8.1) {
            let stars = "⭐";
            if (s >= 7.6) stars = "⭐⭐";
            if (s >= 7.9) stars = "⭐⭐⭐";
            return `Prodigy 🏅 - ${stars}`;
        }
        // New Gen XI ⭐
        if (s >= 8.2 && s <= 9.0) {
            let stars = "⭐";
            if (s >= 8.5) stars = "⭐⭐";
            if (s >= 8.8) stars = "⭐⭐⭐";
            return `New Gen XI - ${stars}`; // El emoji de la clase ya es una estrella
        }
        // World Class 👑
        if (s >= 9.1) {
            let stars = "⭐";
            if (s >= 9.4) stars = "⭐⭐";
            if (s >= 9.7) stars = "⭐⭐⭐";
            return `World Class 👑 - ${stars}`;
        }
        return "Unranked";
    }

    function calculateGKRank(avg) {
        const s = parseFloat(avg.toFixed(1));
        
        if (s <= 6.9) return "D Tier";
        if (s >= 7.0 && s <= 7.9) return "C Tier";
        if (s >= 8.0 && s <= 8.4) return "B Tier";
        if (s >= 8.5 && s <= 8.9) return "A Tier";
        if (s >= 9.0 && s <= 9.4) return "S Tier";
        if (s >= 9.5) return "S+ Tier";
        
        return "N/A";
    }

    // =================================================================
    // 3. GENERADOR DE STATS Y CANVAS
    // =================================================================

    window.generateStats = () => {
        const offensiveInputs = ['sht', 'dbl', 'stl', 'psn', 'dfd'];
        const gkInputs = ['dvg', 'biq', 'rfx', 'dtg'];

        let type = null;
        let values = {};
        let sum = 0;
        let count = 0;

        // Detectar tipo y sumar valores
        if (document.getElementById('sht').value !== "") {
            type = 'offensive';
            offensiveInputs.forEach(id => {
                const val = parseFloat(document.getElementById(id).value) || 0;
                values[id] = val;
                sum += val;
                count++;
            });
        } else {
            type = 'gk';
            gkInputs.forEach(id => {
                const val = parseFloat(document.getElementById(id).value) || 0;
                values[id] = val;
                sum += val;
                count++;
            });
        }

        // Calcular Promedio y Rango
        // ASUMIMOS QUE EL INPUT ES 0-100. DIVIDIMOS POR 10 PARA LA TABLA DE RANGOS (0-10).
        // Si el usuario mete 0-10 directamente, quita el "/ 10".
        const rawAverage = sum / count; 
        const rankAverage = rawAverage / 10; 

        let rankText = "";
        if (type === 'offensive') {
            rankText = calculateOffensiveRank(rankAverage);
        } else {
            rankText = calculateGKRank(rankAverage);
        }

        drawChart(type, values, rankText, rawAverage);
        statsModal.style.display = 'flex';
    };

    function drawChart(type, data, rankText, rawAvg) {
        // Reset y Fondo
        ctx.clearRect(0, 0, statsCanvas.width, statsCanvas.height);
        const w = statsCanvas.width;
        const h = statsCanvas.height;
        const centerX = w / 2;
        const centerY = h / 2 - 20; // Subimos un poco el gráfico para dejar espacio al texto abajo
        const maxRadius = 110;

        ctx.fillStyle = "#050510"; 
        ctx.fillRect(0,0,w,h);

        // Estilos
        ctx.lineWidth = 2;
        // Offensive: Cyan BlueLock | GK: Magenta/Rojo Sangre
        const primaryColor = type === 'offensive' ? '#00f2ff' : '#ff004c'; 
        ctx.strokeStyle = primaryColor;
        ctx.shadowBlur = 15;
        ctx.shadowColor = primaryColor;
        
        const statsKeys = Object.keys(data);
        const totalStats = statsKeys.length;
        const angleStep = (Math.PI * 2) / totalStats;

        // 1. GRID DE FONDO
        ctx.beginPath();
        for (let level = 1; level <= 5; level++) {
            const r = (maxRadius / 5) * level;
            for (let i = 0; i < totalStats; i++) {
                const angle = i * angleStep - Math.PI / 2;
                const x = centerX + Math.cos(angle) * r;
                const y = centerY + Math.sin(angle) * r;
                
                if (type === 'gk') {
                    // GK: Círculos (Rinnegan style)
                    ctx.beginPath();
                    ctx.arc(centerX, centerY, r, 0, Math.PI * 2);
                    ctx.strokeStyle = `rgba(255, 0, 76, 0.15)`; // Rojo tenue
                    ctx.stroke();
                } else {
                    // Offensive: Pentágono
                    if (i === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                }
            }
            if (type === 'offensive') {
                ctx.closePath();
                ctx.strokeStyle = `rgba(0, 242, 255, 0.15)`;
                ctx.stroke();
            }
        }

        // 2. GRAFICO DE DATOS (EL EGO)
        ctx.beginPath();
        statsKeys.forEach((key, i) => {
            let val = data[key]; 
            // Clamp value 0-100
            if (val > 100) val = 100;
            if (val < 0) val = 0;

            const r = (val / 100) * maxRadius;
            const angle = i * angleStep - Math.PI / 2;
            const x = centerX + Math.cos(angle) * r;
            const y = centerY + Math.sin(angle) * r;

            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);

            // Vértices Brillantes
            drawGlitchPoint(x, y, primaryColor);
            
            // Labels (SHT, DBL...)
            drawLabel(key.toUpperCase(), angle, centerX, centerY, maxRadius + 25);
        });
        ctx.closePath();
        
        ctx.fillStyle = type === 'offensive' ? 'rgba(0, 242, 255, 0.25)' : 'rgba(255, 0, 76, 0.25)';
        ctx.fill();
        ctx.strokeStyle = primaryColor;
        ctx.stroke();

        // 3. TEXTO DE RANGO (PARTE INFERIOR)
        // Fondo semitransparente para el texto
        ctx.fillStyle = "rgba(0,0,0,0.5)";
        ctx.fillRect(0, h - 60, w, 60);

        // Texto del Promedio Numérico
        ctx.font = "bold 20px 'Courier New'";
        ctx.fillStyle = "#fff";
        ctx.textAlign = "center";
        ctx.shadowBlur = 0;
        ctx.fillText(`AVG: ${rawAvg.toFixed(1)} / 100`, centerX, h - 40);

        // Texto del RANGO FINAL
        ctx.font = "bold 18px 'Arial'"; // Arial para soportar mejor emojis
        ctx.fillStyle = primaryColor; // Color del texto igual al gráfico
        ctx.shadowBlur = 10;
        ctx.shadowColor = primaryColor;
        ctx.fillText(rankText.toUpperCase(), centerX, h - 15);
    }

    function drawGlitchPoint(x, y, color) {
        ctx.save();
        ctx.fillStyle = "#fff";
        ctx.shadowBlur = 20;
        ctx.shadowColor = color;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI*2);
        ctx.fill();
        ctx.restore();
    }

    function drawLabel(text, angle, cx, cy, radius) {
        const x = cx + Math.cos(angle) * radius;
        const y = cy + Math.sin(angle) * radius;
        ctx.font = "bold 12px 'Courier New'";
        ctx.fillStyle = "#ccc";
        ctx.textAlign = "center";
        ctx.shadowBlur = 0;
        ctx.fillText(text, x, y + 5);
    }

    // --- POPUP UTILS ---
    closeModalBtn.onclick = () => statsModal.style.display = 'none';

    copyBtn.onclick = () => {
        statsCanvas.toBlob(blob => {
            const item = new ClipboardItem({ "image/png": blob });
            navigator.clipboard.write([item]).then(() => {
                const originalText = copyBtn.innerText;
                copyBtn.innerText = "COPIED [EGO]";
                copyBtn.style.background = "#00ff00";
                copyBtn.style.color = "#000";
                setTimeout(() => {
                    copyBtn.innerText = originalText;
                    copyBtn.style.background = "";
                    copyBtn.style.color = "";
                }, 2000);
            });
        });
    };
});