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

    const chatInput = document.getElementById('chat-input');
    if(chatInput){
        chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
    }

    // --- B. INICIO DE STATS (Restaurado) ---
    // Cargar datos guardados si existen
    loadStats();
    
    // Listeners para los inputs de números (stats)
    const statInputs = document.querySelectorAll('.stat-input');
    statInputs.forEach(input => {
        input.addEventListener('input', calculateStats);
    });
    
    // Listener para guardar notas
    const notesArea = document.getElementById('notes-area');
    if(notesArea) {
        notesArea.addEventListener('input', saveStats);
    }
});

// ==========================================
// 3. NAVEGACIÓN
// ==========================================
function switchView(viewName) {
    // Botones
    document.querySelectorAll('.system-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById(`btn-view-${viewName}`);
    if(activeBtn) activeBtn.classList.add('active');

    // Contenedores
    const ticketsView = document.getElementById('view-tickets');
    const statsView = document.getElementById('view-qualifications'); // Asegúrate que en tu HTML el ID sea este

    if(viewName === 'tickets') {
        if(ticketsView) ticketsView.classList.remove('hidden');
        if(statsView) statsView.classList.add('hidden');
    } else if (viewName === 'qualifications') {
        if(ticketsView) ticketsView.classList.add('hidden');
        if(statsView) statsView.classList.remove('hidden');
        // Forzar repintado del gráfico al mostrar la pestaña
        if(blzChartInstance) blzChartInstance.update();
        else calculateStats(); // Crear si no existe
    }
}

// ==========================================
// 4. LÓGICA DE TICKETS (Backend conectado)
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
            list.innerHTML = '<div style="padding:20px; color:#555; text-align:center; font-family:monospace;">NO SIGNALS DETECTED</div>';
            return;
        }

        data.forEach(ticket => {
            const div = document.createElement('div');
            div.className = `ticket-item ${currentTicketId === ticket.id ? 'active' : ''}`;
            div.onclick = () => selectTicket(ticket.id, ticket.channel_name); 

            let statusBadge = ticket.status === 'open' 
                ? `<span class="badge" style="background:var(--neon-blue); box-shadow:0 0 5px var(--neon-blue); color:black;">OP</span>` 
                : `<span class="badge" style="background:var(--text-grey); box-shadow:none;">CL</span>`;

            div.innerHTML = `
                <div style="display:flex; flex-direction:column;">
                    <span class="ticket-name" style="font-size:0.85rem; font-family:monospace; color:var(--neon-blue); letter-spacing:1px;">${ticket.channel_name}</span> 
                    <span style="font-size:0.7em; color:var(--text-grey); margin-top:2px;">User: <span style="color:white;">${ticket.user_name}</span></span>
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
    if(header) header.innerText = name || id;
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
            div.className = `message ${isMe ? 'sent' : 'received'}`;
            const timeString = new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            div.innerHTML = `
                <div class="msg-content">${msg.content}</div>
                <div class="msg-meta">
                    <span style="color:${isMe ? 'var(--neon-blue)' : 'var(--neon-orange)'}">${msg.sender}</span> • ${timeString}
                </div>
            `;
            chatBody.appendChild(div);
        });
        chatBody.scrollTop = chatBody.scrollHeight;
    });
}

function sendMessage() {
    const input = document.getElementById('chat-input');
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
        if(data.status === 'success') loadMessages(currentTicketId); 
    });
}

// ==========================================
// 5. LÓGICA DE STATS Y GRÁFICO (Restaurada)
// ==========================================

function calculateStats() {
    // 1. Obtener valores de los inputs
    const ids = ['offense', 'shoot', 'dribble', 'pass', 'defense', 'speed'];
    let total = 0;
    let values = [];

    ids.forEach(id => {
        let val = parseFloat(document.getElementById(id).value) || 0;
        if (val > 100) val = 100; // Límite 100
        values.push(val);
        total += val;
    });

    // 2. Calcular media y actualizar DOM
    const avg = total / 6;
    document.getElementById('result-total').innerText = Math.round(avg);
    
    // Calcular Rango y Tier
    let rank = 'C';
    let tier = 'III';
    
    if (avg >= 90) { rank = 'S'; tier = 'WORLD 11'; }
    else if (avg >= 80) { rank = 'A'; tier = 'BLUE LOCK'; }
    else if (avg >= 70) { rank = 'B'; tier = 'U-20'; }
    
    const rankEl = document.getElementById('result-rank');
    if(rankEl) {
        rankEl.innerText = rank;
        // Cambio de color según rango
        rankEl.style.color = (rank === 'S' || rank === 'A') ? 'var(--neon-blue)' : 'white';
    }
    document.getElementById('result-tier').innerText = tier;

    // 3. Actualizar Gráfico
    updateChart(values);
    
    // 4. Guardar
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
                    backgroundColor: 'rgba(0, 240, 255, 0.2)', // Neon Blue transparente
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
                        suggestedMax: 100, // Escala de 0 a 100
                        ticks: { display: false } // Ocultar números del eje
                    }
                },
                plugins: { legend: { display: false } }
            }
        });
    }
}

// Función para guardar en LocalStorage (persistencia básica)
function saveStats() {
    const data = {
        inputs: {},
        notes: document.getElementById('notes-area')?.value || ''
    };
    
    ['offense', 'shoot', 'dribble', 'pass', 'defense', 'speed'].forEach(id => {
        data.inputs[id] = document.getElementById(id).value;
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
        // Recalcular gráfico con los datos cargados
        calculateStats();
    }
}

// ==========================================
// 6. FUNCIONES DEL MODAL Y COPIAR
// ==========================================

function openStatsModal() {
    const modal = document.getElementById('stats-modal');
    if(modal) modal.classList.remove('hidden');
    // Forzamos update del gráfico al abrir modal por si acaso
    if(blzChartInstance) blzChartInstance.update();
}

function closeStatsModal() {
    const modal = document.getElementById('stats-modal');
    if(modal) modal.classList.add('hidden');
}

// --- LA FUNCIÓN QUE PEDISTE ---
async function copyChartToClipboard() {
    const canvas = document.getElementById('blzChart');
    if(!canvas) return;

    // Convertir el canvas a un Blob (imagen)
    canvas.toBlob(async (blob) => {
        try {
            // Crear item para el portapapeles
            const data = [new ClipboardItem({ [blob.type]: blob })];
            // Escribir en portapapeles
            await navigator.clipboard.write(data);
            
            // Feedback visual en el botón
            const btn = document.querySelector('.btn-copy');
            const originalText = btn.innerText;
            
            btn.innerText = "COPIED!";
            btn.style.borderColor = "var(--neon-orange)";
            btn.style.color = "var(--neon-orange)";
            btn.style.boxShadow = "0 0 15px var(--neon-orange)";
            
            setTimeout(() => {
                btn.innerText = originalText;
                btn.style.borderColor = "white";
                btn.style.color = "white";
                btn.style.boxShadow = "none";
            }, 2000);
            
        } catch (err) {
            console.error("Error copiando:", err);
            alert("No se pudo copiar. Asegúrate de usar HTTPS o localhost.");
        }
    });
}