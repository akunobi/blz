// ==========================================
// 1. CONFIGURACIÓN GLOBAL
// ==========================================
let currentTicketId = null;
let blzChartInstance = null; 

// ==========================================
// 2. INICIALIZACIÓN
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    
    // --- A. INICIO DE TICKETS ---
    loadTickets();
    setInterval(loadTickets, 3000); 
    setInterval(() => { if (currentTicketId) loadMessages(currentTicketId); }, 2000);

    const chatInput = document.getElementById('msg-input') || document.getElementById('chat-input');
    
    if(chatInput){
        // Forzar foco por si el CSS molesta
        chatInput.onclick = () => chatInput.focus();
        
        chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
    }

    const sendBtn = document.querySelector('.btn-send');
    if(sendBtn) {
        sendBtn.addEventListener('click', sendMessage);
    }

    // --- B. INICIO DE STATS (Restaurado) ---
    loadStats();
    
    const statInputs = document.querySelectorAll('.stat-input');
    statInputs.forEach(input => {
        input.addEventListener('input', calculateStats);
    });
    
    const notesArea = document.getElementById('notes-area');
    if(notesArea) {
        notesArea.addEventListener('input', saveStats);
    }
});

// ==========================================
// 3. NAVEGACIÓN
// ==========================================
function switchView(viewName) {
    document.querySelectorAll('.system-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById(`btn-view-${viewName}`);
    if(activeBtn) activeBtn.classList.add('active');

    const ticketsView = document.getElementById('view-tickets');
    const statsView = document.getElementById('view-qualifications'); 

    if(viewName === 'tickets') {
        if(ticketsView) ticketsView.classList.remove('hidden');
        if(statsView) statsView.classList.add('hidden');
    } else if (viewName === 'qualifications') {
        if(ticketsView) ticketsView.classList.add('hidden');
        if(statsView) statsView.classList.remove('hidden');
        if(blzChartInstance) blzChartInstance.update();
        else calculateStats(); 
    }
}

// ==========================================
// 4. LÓGICA DE TICKETS (CORREGIDO ID vs NOMBRE)
// ==========================================
function loadTickets() {
    const ticketView = document.getElementById('view-tickets');
    // Si usas pestañas y está oculta, no actualizamos para ahorrar recursos
    if (ticketView && ticketView.classList.contains('hidden')) return;

    fetch('/api/get_tickets')
    .then(r => r.json())
    .then(data => {
        const list = document.getElementById('ticket-list');
        if(!list) return;

        list.innerHTML = ''; 
        
        if (data.length === 0) {
            list.innerHTML = '<div style="padding:20px; color:#555; text-align:center; font-family:monospace;">NO SIGNALS DETECTED</div>';
            return;
        }

        data.forEach(ticket => {
            const div = document.createElement('div');
            div.className = `ticket-item ${currentTicketId === ticket.id ? 'active' : ''}`;
            
            // --- AQUÍ ESTÁ EL FIX ---
            // Si ticket.channel_name viene vacío o es null, creamos uno bonito
            let displayName = ticket.channel_name;
            if (!displayName || displayName === 'null' || displayName === 'undefined') {
                // Genera "ticket-pepito" limpiando espacios
                const cleanUser = ticket.user_name ? ticket.user_name.replace(/\s+/g, '').toLowerCase() : 'unknown';
                displayName = `ticket-${cleanUser}`;
            }
            // ------------------------

            div.onclick = () => selectTicket(ticket.id, displayName); 

            let statusBadge = ticket.status === 'open' 
                ? `<span class="badge" style="background:var(--neon-blue); box-shadow:0 0 5px var(--neon-blue); color:black; padding: 2px 5px; border-radius: 4px; font-size: 0.7em;">OP</span>` 
                : `<span class="badge" style="background:var(--text-grey); box-shadow:none; padding: 2px 5px; border-radius: 4px; font-size: 0.7em;">CL</span>`;

            div.innerHTML = `
                <div style="display:flex; flex-direction:column;">
                    <span class="ticket-name" style="font-size:0.9rem; font-family:monospace; color:var(--neon-blue); letter-spacing:1px; font-weight:bold;"># ${displayName}</span> 
                    <span style="font-size:0.75em; color:var(--text-grey); margin-top:4px;">User: <span style="color:white;">${ticket.user_name}</span></span>
                </div>
                ${statusBadge}
            `;
            list.appendChild(div);
        });
    })
    .catch(err => console.error("Error loading tickets:", err));
}

function selectTicket(id, name) {
    currentTicketId = id;
    const header = document.getElementById('chat-header-title');
    
    // Si el nombre llega vacío, mostramos el ID cortado como último recurso
    if(header) header.innerText = name || `Ticket ${id.substring(0,6)}...`;
    
    // Refrescamos la lista para pintar el borde naranja de "activo"
    loadTickets(); 
    loadMessages(id);
}

function loadMessages(ticketId) {
    const chatBody = document.getElementById('chat-body');
    if (!chatBody) return;

    fetch(`/api/get_messages/${ticketId}`)
    .then(r => r.json())
    .then(msgs => {
        chatBody.innerHTML = ''; 
        msgs.forEach(msg => {
            const div = document.createElement('div');
            // Ajusta 'BLZ-T' al nombre de tu bot si es diferente
            const isMe = (msg.sender === 'BLZ-T');
            div.className = `message ${isMe ? 'agent' : 'discord'}`; // Usamos agent/discord para coincidir con CSS típico
            
            // Fallback por si classes son sent/received
            if (!div.className.includes('agent') && !div.className.includes('discord')) {
                 div.className = `message ${isMe ? 'sent' : 'received'}`;
            }

            const timeString = new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            div.innerHTML = `
                <div class="msg-content">${msg.content}</div>
                <div class="msg-meta" style="font-size: 0.7em; opacity: 0.7; margin-top: 5px; text-align: ${isMe ? 'right' : 'left'}">
                    <span style="color:${isMe ? 'var(--neon-orange)' : 'var(--neon-blue)'}">${msg.sender}</span> • ${timeString}
                </div>
            `;
            chatBody.appendChild(div);
        });
        chatBody.scrollTop = chatBody.scrollHeight;
    });
}

function sendMessage() {
    const input = document.getElementById('msg-input') || document.getElementById('chat-input');
    if(!input) return;

    const content = input.value.trim();
    if (!content || !currentTicketId) return; 

    input.value = ''; 

    fetch('/api/send_message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ticket_id: currentTicketId,
            content: content
        })
    })
    .then(r => r.json())
    .then(data => {
        if(data.status === 'success') {
            loadMessages(currentTicketId); 
        } else {
            console.error("Error sending:", data);
        }
    });
}

// ==========================================
// 5. LÓGICA DE STATS Y GRÁFICO
// ==========================================

function calculateStats() {
    const ids = ['offense', 'shoot', 'dribble', 'pass', 'defense', 'speed'];
    let total = 0;
    let values = [];

    ids.forEach(id => {
        const el = document.getElementById(id);
        if(!el) return;
        let val = parseFloat(el.value) || 0;
        if (val > 100) val = 100; 
        values.push(val);
        total += val;
    });

    const avg = total / 6;
    const totalEl = document.getElementById('result-total');
    if(totalEl) totalEl.innerText = Math.round(avg);
    
    let rank = 'C';
    let tier = 'III';
    
    if (avg >= 90) { rank = 'S'; tier = 'WORLD 11'; }
    else if (avg >= 80) { rank = 'A'; tier = 'BLUE LOCK'; }
    else if (avg >= 70) { rank = 'B'; tier = 'U-20'; }
    
    const rankEl = document.getElementById('result-rank');
    if(rankEl) {
        rankEl.innerText = rank;
        rankEl.style.color = (rank === 'S' || rank === 'A') ? 'var(--neon-blue)' : 'white';
    }
    const tierEl = document.getElementById('result-tier');
    if(tierEl) tierEl.innerText = tier;

    updateChart(values);
    saveStats();
}

function updateChart(dataValues) {
    const ctx = document.getElementById('blzChart');
    if (!ctx) return;

    if (blzChartInstance) {
        blzChartInstance.data.datasets[0].data = dataValues;
        blzChartInstance.update();
    } else {
        blzChartInstance = new Chart(ctx, {
            type: 'radar',
            data: {
                labels: ['OFF', 'SHO', 'DRI', 'PAS', 'DEF', 'SPD'],
                datasets: [{
                    label: 'Player Stats',
                    data: dataValues,
                    backgroundColor: 'rgba(0, 240, 255, 0.2)', 
                    borderColor: '#00f0ff',
                    pointBackgroundColor: '#fff',
                    pointBorderColor: '#00f0ff',
                    borderWidth: 2
                }]
            },
            options: {
                scales: {
                    r: {
                        angleLines: { color: 'rgba(255, 255, 255, 0.1)' },
                        grid: { color: 'rgba(255, 255, 255, 0.1)' },
                        pointLabels: { color: '#00f0ff', font: { size: 12, weight: 'bold' } },
                        suggestedMin: 0,
                        suggestedMax: 100, 
                        ticks: { display: false } 
                    }
                },
                plugins: { legend: { display: false } }
            }
        });
    }
}

function saveStats() {
    const data = {
        inputs: {},
        notes: document.getElementById('notes-area')?.value || ''
    };
    
    ['offense', 'shoot', 'dribble', 'pass', 'defense', 'speed'].forEach(id => {
        const el = document.getElementById(id);
        if(el) data.inputs[id] = el.value;
    });

    localStorage.setItem('blz_stats_data', JSON.stringify(data));
    
    const indicator = document.getElementById('save-status');
    if(indicator) {
        indicator.innerText = "SAVING...";
        setTimeout(() => indicator.innerText = "DATA SAVED LOCALLY", 500);
    }
}

function loadStats() {
    const saved = localStorage.getItem('blz_stats_data');
    if (saved) {
        try {
            const data = JSON.parse(saved);
            if(data.inputs) {
                for (const [key, val] of Object.entries(data.inputs)) {
                    const el = document.getElementById(key);
                    if(el) el.value = val;
                }
            }
            if(data.notes) {
                const notes = document.getElementById('notes-area');
                if(notes) notes.value = data.notes;
            }
            calculateStats();
        } catch(e) { console.error("Error reading stats", e); }
    }
}

// ==========================================
// 6. MODALES Y COPY
// ==========================================
function openStatsModal() {
    const modal = document.getElementById('stats-modal');
    if(modal) modal.classList.remove('hidden');
    if(blzChartInstance) blzChartInstance.update();
}

function closeStatsModal() {
    const modal = document.getElementById('stats-modal');
    if(modal) modal.classList.add('hidden');
}

async function copyChartToClipboard() {
    const canvas = document.getElementById('blzChart');
    if(!canvas) return;

    canvas.toBlob(async (blob) => {
        try {
            const data = [new ClipboardItem({ [blob.type]: blob })];
            await navigator.clipboard.write(data);
            
            const btn = document.querySelector('.btn-copy');
            if(btn) {
                const originalText = btn.innerText;
                btn.innerText = "COPIED!";
                btn.style.borderColor = "var(--neon-orange)";
                btn.style.color = "var(--neon-orange)";
                
                setTimeout(() => {
                    btn.innerText = originalText;
                    btn.style.borderColor = "white";
                    btn.style.color = "white";
                }, 2000);
            }
        } catch (err) {
            console.error("Error copiando:", err);
            alert("No se pudo copiar (Requiere HTTPS o Localhost).");
        }
    });
}