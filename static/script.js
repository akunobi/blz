document.addEventListener('DOMContentLoaded', () => {
    // --- REFERENCIAS DOM ---
    const channelList = document.getElementById('channel-list');
    const chatFeed = document.getElementById('chat-feed');
    const msgInput = document.getElementById('msg-input');
    const modal = document.getElementById('stats-modal');
    const canvas = document.getElementById('stats-canvas');
    const ctx = canvas.getContext('2d');
    
    // Estado
    let currentChannelId = null;
    let isFetching = false;

    // --- INICIO ---
    // Cargar canales al iniciar
    fetchChannels();
    
    // Loop de mensajes (cada 3s)
    setInterval(() => {
        if (!isFetching && currentChannelId) fetchMessages();
    }, 3000);

    // Enviar con Enter
    if(msgInput) {
        msgInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') window.sendMessage();
        });
    }

    // --- LÓGICA DE CANALES (AQUÍ ESTABA EL ERROR) ---
    async function fetchChannels() {
        try {
            const res = await fetch('/api/channels');
            const channels = await res.json();

            // Limpiamos la lista solo si hay datos nuevos
            if (channels.length > 0) channelList.innerHTML = '';
            
            channels.forEach(ch => {
                // CORRECCIÓN: Busca 'name' O 'channel_name', y 'id' O 'channel_id'
                const cName = ch.name || ch.channel_name || "Unknown Channel";
                const cId = ch.id || ch.channel_id;

                const btn = document.createElement('button');
                btn.className = 'channel-btn';
                btn.innerText = `# ${cName}`; 
                
                btn.onclick = () => {
                    // Actualizar ID actual
                    currentChannelId = cId;
                    
                    // Actualizar visual (clase active)
                    document.querySelectorAll('.channel-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    
                    // Mostrar carga y pedir mensajes
                    chatFeed.innerHTML = '<div style="padding:20px; text-align:center; opacity:0.5;">/// DECRYPTING DATA...</div>';
                    fetchMessages();
                };
                
                channelList.appendChild(btn);
            });
        } catch(e) { 
            console.error("Chan Error", e);
            channelList.innerHTML = '<div style="color:red; padding:10px;">OFFLINE MODE</div>';
        }
    }

    // --- LÓGICA DE MENSAJES ---
    async function fetchMessages() {
        if (!currentChannelId) return;
        isFetching = true;

        try {
            const res = await fetch('/api/messages');
            const allMsgs = await res.json();
            
            // Filtramos por el canal seleccionado
            // Usamos '==' para que no importe si es string o int
            const msgs = allMsgs.filter(m => m.channel_id == currentChannelId).reverse();
            
            // Detectar si el usuario está leyendo mensajes viejos (scroll arriba)
            const isScrolledToBottom = (chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight) < 150;

            chatFeed.innerHTML = '';
            
            if (msgs.length === 0) {
                chatFeed.innerHTML = '<div class="empty-state"><p>NO DATA FOUND</p></div>';
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

            // Mantener scroll abajo si corresponde
            if (isScrolledToBottom) {
                chatFeed.scrollTop = chatFeed.scrollHeight;
            }

        } catch(e) { console.error("Msg Error", e); }
        finally { isFetching = false; }
    }

    // Convertir URLs en links clickeables
    function formatLinks(text) {
        if (!text) return "";
        return text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
    }

    // --- ENVIAR MENSAJES ---
    window.sendMessage = async () => {
        const content = msgInput.value.trim();
        if (!content || !currentChannelId) return;

        // Feedback visual
        msgInput.value = '';
        msgInput.placeholder = "/// SENDING...";
        msgInput.disabled = true;

        try {
            const res = await fetch('/api/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannelId, content: content })
            });
            
            if (!res.ok) throw new Error("Server Reject");
            
            // Refrescar rápido
            setTimeout(fetchMessages, 500);
            
        } catch(e) {
            alert("ERROR: " + e);
            msgInput.value = content; // Devolver texto si falla
        } finally {
            msgInput.placeholder = "ENTER COMMAND / MESSAGE...";
            msgInput.disabled = false;
            msgInput.focus();
        }
    };

    // --- ESTADÍSTICAS (GRÁFICOS) ---
    // Esta función detecta si rellenaste los datos de Ofensiva o Portero
    window.generateStats = () => {
        // Detectar tipo chequeando si el input DVG (portero) tiene algo
        let type = 'offensive';
        const gkInput = document.getElementById('dvg');
        if (gkInput && gkInput.value !== "") {
            type = 'gk';
        }

        // IDs según el tipo
        const inputs = type === 'offensive' 
            ? ['sht', 'dbl', 'stl', 'psn', 'dfd'] 
            : ['dvg', 'biq', 'rfx', 'dtg'];

        let data = {};
        let sum = 0;
        let count = 0;
        let valid = true;

        inputs.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            
            let val = parseFloat(el.value);
            if (isNaN(val)) val = 0; // Si está vacío cuenta como 0
            
            // Límites 0-100 o 0-10 (adaptable)
            if (val > 10) val = 10; 
            if (val < 0) val = 0;
            
            data[id] = val;
            sum += val;
            count++;
        });

        // Calcular promedio y rango
        const avg = count > 0 ? sum / count : 0;
        const rank = getRankText(avg);

        // Dibujar
        drawGraph(type, data, avg, rank);
        
        // Mostrar Modal
        if(modal) modal.style.display = 'flex';
        
        // Cerrar Drawer automáticamente (opcional)
        const drawer = document.getElementById('stats-drawer');
        if(drawer) drawer.classList.remove('active');
    };

    function getRankText(s) {
        // Asumiendo escala 0-10
        if (s < 5) return "UNRANKED";
        if (s < 7) return "ROOKIE";
        if (s < 8) return "ELITE";
        if (s < 9) return "PRODIGY";
        if (s < 9.5) return "WORLD CLASS";
        return "MASTER EGOIST";
    }

    function drawGraph(type, data, avg, rank) {
        // Limpiar canvas
        ctx.clearRect(0,0,500,500);
        
        // Fondo negro puro
        ctx.fillStyle = "#050505";
        ctx.fillRect(0,0,500,500);

        const cx = 250, cy = 250;
        const maxRadius = 140;
        const color = type === 'offensive' ? '#00f2ff' : '#ff0040'; // Cyan o Rojo

        // Configuración lineas
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.shadowBlur = 15;
        ctx.shadowColor = color;
        
        const keys = Object.keys(data);
        const total = keys.length;
        const angleStep = (Math.PI * 2) / total;

        // 1. DIBUJAR BASE (La "telaraña" de fondo)
        ctx.beginPath();
        for(let level=1; level<=4; level++) {
            let r = (maxRadius/4)*level;
            // Dibujamos polígono o círculo según tipo
            for(let i=0; i<=total; i++) {
                let a = i * angleStep - Math.PI/2;
                let x = cx + Math.cos(a) * r;
                let y = cy + Math.sin(a) * r;
                if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
            }
        }
        ctx.strokeStyle = "rgba(255,255,255,0.1)"; // Base gris oscura
        ctx.shadowBlur = 0;
        ctx.stroke();

        // 2. DIBUJAR DATOS (La forma rellena)
        ctx.beginPath();
        keys.forEach((k, i) => {
            let val = data[k];
            let r = (val / 10) * maxRadius; // Normalizar 0-10 a Radio
            let a = i * angleStep - Math.PI/2;
            let x = cx + Math.cos(a) * r;
            let y = cy + Math.sin(a) * r;
            
            if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
        });
        ctx.closePath();
        
        // Relleno con transparencia
        ctx.fillStyle = type === 'offensive' ? "rgba(0, 242, 255, 0.2)" : "rgba(255, 0, 64, 0.2)";
        ctx.fill();
        
        // Borde brillante
        ctx.strokeStyle = color;
        ctx.shadowBlur = 20;
        ctx.shadowColor = color;
        ctx.stroke();

        // 3. ETIQUETAS (Texto SHT, DBL...)
        keys.forEach((k, i) => {
            let a = i * angleStep - Math.PI/2;
            let labelR = maxRadius + 35;
            let lx = cx + Math.cos(a) * labelR;
            let ly = cy + Math.sin(a) * labelR;
            
            ctx.save();
            ctx.fillStyle = "#fff";
            ctx.font = "bold 16px Courier New";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.shadowBlur = 0;
            ctx.fillText(k.toUpperCase(), lx, ly);
            ctx.restore();
        });

        // 4. RANGO EN EL CENTRO (Opcional, o abajo)
        ctx.shadowBlur = 0;
        ctx.fillStyle = "#fff";
        ctx.font = "14px Courier New";
        ctx.textAlign = "center";
        ctx.fillText(`AVG: ${avg.toFixed(1)}`, cx, 450);

        ctx.font = "bold 24px Impact";
        ctx.fillStyle = color;
        ctx.fillText(rank, cx, 480);
    }

    // --- CONTROLES DEL MODAL ---
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