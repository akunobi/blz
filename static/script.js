// ==========================================
// 1. CONFIGURACIÓN GLOBAL
// ==========================================
let currentTicketId = null;
let blzChartInstance = null; 

// ==========================================
// 2. INICIALIZACIÓN
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    
    // --- TICKETS ---
    loadTickets();
    setInterval(loadTickets, 3000); 
    setInterval(() => { if (currentTicketId) loadMessages(currentTicketId); }, 2000);

    // --- CHAT INPUT FIX ---
    const chatInput = document.getElementById('msg-input');
    const sendBtn = document.querySelector('.btn-send');

    if(chatInput){
        // Forzamos que se pueda escribir al hacer clic
        chatInput.onclick = () => { chatInput.focus(); };
        chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
    }
    if(sendBtn) sendBtn.addEventListener('click', sendMessage);

    // --- STATS SYSTEM ---
    loadStats(); // Cargar datos guardados
    
    // Asignar listeners a los inputs numéricos
    const statInputs = document.querySelectorAll('.stat-input');
    statInputs.forEach(input => {
        input.addEventListener('input', calculateStats);
        input.addEventListener('change', calculateStats); // Doble seguridad
    });
    
    // Calcular una vez al inicio por si hay datos
    setTimeout(calculateStats, 500);
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
        
        // Renderizar gráfico al cambiar pestaña
        setTimeout(() => {
            calculateStats();
            if(blzChartInstance) blzChartInstance.update();
        }, 100);
    }
}

// ==========================================
// 4. TICKETS & CHAT
// ==========================================
function loadTickets() {
    const ticketView = document.getElementById('view-tickets');
    if (ticketView && ticketView.classList.contains('hidden')) return;

    fetch('/api/get_tickets')
    .then(r => r.json())
    .then(data => {
        const list = document.getElementById('ticket-list');
        if(!list) return;

        list.innerHTML = ''; 
        if (data.length === 0) {
            list.innerHTML = '<div style="padding:20px; color:#555; text-align:center;">NO SIGNALS</div>';
            return;
        }

        data.forEach(ticket => {
            const div = document.createElement('div');
            div.className = `ticket-item ${currentTicketId === ticket.id ? 'active' : ''}`;
            
            // Generador de nombres si falla el backend
            let displayName = ticket.channel_name;
            if (!displayName || displayName === 'null') {
                const cleanUser = ticket.user_name ? ticket.user_name.replace(/\s+/g, '').toLowerCase() : 'unknown';
                displayName = `ticket-${cleanUser}`;
            }

            div.onclick = () => selectTicket(ticket.id, displayName); 

            let statusBadge = ticket.status === 'open' 
                ? `<span style="color:var(--neon-blue); font-weight:bold; border:1px solid var(--neon-blue); padding:2px 6px; border-radius:4px; font-size:0.7em;">OP</span>` 
                : `<span style="color:#666; border:1px solid #444; padding:2px 6px; border-radius:4px; font-size:0.7em;">CL</span>`;

            div.innerHTML = `
                <div style="display:flex; flex-direction:column;">
                    <span style="color:var(--neon-blue); font-weight:bold;"># ${displayName}</span> 
                    <span style="font-size:0.75em; color:#888;">User: ${ticket.user_name}</span>
                </div>
                ${statusBadge}
            `;
            list.appendChild(div);
        });
    });
}

function selectTicket(id, name) {
    currentTicketId = id;
    const header = document.getElementById('chat-header-title');
    if(header) header.innerText = name;
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
            const isMe = (msg.sender === 'BLZ-T');
            div.className = `message ${isMe ? 'agent' : 'discord'}`;
            div.innerHTML = `<div>${msg.content}</div><div style="font-size:0.6em; opacity:0.6; margin-top:5px;">${msg.sender}</div>`;
            chatBody.appendChild(div);
        });
        chatBody.scrollTop = chatBody.scrollHeight;
    });
}

function sendMessage() {
    const input = document.getElementById('msg-input');
    if(!input) return;

    const content = input.value.trim();
    if (!content || !currentTicketId) return; 

    input.value = ''; 
    input.focus(); // Mantener foco para escribir rápido

    fetch('/api/send_message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticket_id: currentTicketId, content: content })
    })
    .then(r => r.json())
    .then(d => { if(d.status === 'success') loadMessages(currentTicketId); });
}

// ==========================================
// 5. STATS SYSTEM (CORREGIDO)
// ==========================================
function calculateStats() {
    const ids = ['offense', 'shoot', 'dribble', 'pass', 'defense', 'speed'];
    let total = 0;
    let values = [];

    // Recoger valores
    ids.forEach(id => {
        const el = document.getElementById(id);
        let val = 0;
        if(el) val = parseFloat(el.value) || 0;
        if (val > 99) val = 99; // Cap en 99 como FIFA/Blue Lock
        values.push(val);
        total += val;
    });

    const avg = Math.round(total / 6);
    
    // Actualizar UI - Total
    const totalEl = document.getElementById('result-total');
    if(totalEl) totalEl.innerText = avg;

    // --- LÓGICA DE RANGOS (AS SOLICITADO) ---
    let rankTitle = 'B ⭐';     // Default
    let rankTier = 'ACADEMY';   // Default
    let rankColor = 'white';

    if (avg >= 90) {
        rankTitle = 'S ⭐⭐⭐⭐⭐';
        rankTier = 'WORLD 11 🌏';
        rankColor = 'var(--neon-blue)'; // Azul Neón
    } else if (avg >= 85) {
        rankTitle = 'S ⭐⭐⭐⭐';
        rankTier = 'NEO EGOIST ⚔️';
        rankColor = '#00ccff';
    } else if (avg >= 80) {
        rankTitle = 'A ⭐⭐⭐';
        rankTier = 'BLUE LOCK ⛓️';
        rankColor = 'var(--neon-orange)'; // Naranja
    } else if (avg >= 70) {
        rankTitle = 'B ⭐⭐';
        rankTier = 'U-20 🇯🇵';
        rankColor = '#ffff00'; // Amarillo
    }

    // Actualizar DOM Rangos
    const rankEl = document.getElementById('result-rank');
    const tierEl = document.getElementById('result-tier');
    
    if(rankEl) {
        rankEl.innerText = rankTitle;
        rankEl.style.color = rankColor;
    }
    if(tierEl) {
        tierEl.innerText = rankTier;
        // Animación suave si es World 11
        tierEl.style.textShadow = (avg >= 90) ? "0 0 15px var(--neon-blue)" : "none";
    }

    // Actualizar Gráfico y Guardar
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
                    label: 'STATS',
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
                        pointLabels: { color: '#00f0ff', font: { size: 10 } },
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
    const data = { inputs: {} };
    ['offense', 'shoot', 'dribble', 'pass', 'defense', 'speed'].forEach(id => {
        const el = document.getElementById(id);
        if(el) data.inputs[id] = el.value;
    });
    localStorage.setItem('blz_stats_data', JSON.stringify(data));
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
            calculateStats();
        } catch(e) { console.error(e); }
    }
}

// Funciones modales
function openStatsModal() {
    document.getElementById('stats-modal')?.classList.remove('hidden');
    if(blzChartInstance) blzChartInstance.update();
}
function closeStatsModal() {
    document.getElementById('stats-modal')?.classList.add('hidden');
}
async function copyChartToClipboard() {
    alert("Función de copiado lista (requiere HTTPS/Localhost)");
}