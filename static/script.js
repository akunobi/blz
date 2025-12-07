// --- CONFIGURACIÓN GLOBAL ---
let currentTicketId = null;
let blzChartInstance = null; // Instancia del gráfico

// --- INICIALIZACIÓN ---
document.addEventListener('DOMContentLoaded', () => {
    // 1. Cargar datos guardados del evaluador
    loadStats();
    
    // 2. Listeners para inputs de estadísticas
    const statInputs = document.querySelectorAll('.stat-input');
    statInputs.forEach(input => {
        input.addEventListener('input', calculateStats);
    });
    
    // 3. Listener para guardar notas automáticamente
    const notesArea = document.getElementById('notes-area');
    if(notesArea) {
        notesArea.addEventListener('input', saveStats);
    }

    // 4. Iniciar polling de tickets
    loadTickets();
    setInterval(loadTickets, 3000); 
    setInterval(() => { if (currentTicketId) loadMessages(currentTicketId); }, 2000);
});

// --- LÓGICA DE NAVEGACIÓN (VISTAS) ---
function switchView(viewName) {
    // Actualizar botones del menú superior
    document.querySelectorAll('.system-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById(`btn-view-${viewName}`);
    if(activeBtn) activeBtn.classList.add('active');

    // Ocultar/Mostrar contenedores principales
    document.querySelectorAll('.content-view').forEach(div => div.classList.add('hidden'));
    const activeView = document.getElementById(`view-${viewName}`);
    if(activeView) activeView.classList.remove('hidden');

    // Manejar visibilidad de la barra lateral
    const ticketList = document.getElementById('ticket-list-container');
    
    // Si estamos en qualifications, ocultamos la lista para dar foco al panel central
    // Si prefieres que la lista se vea siempre, borra este if/else
    if (viewName === 'qualifications') {
        if(ticketList) ticketList.classList.add('hidden');
    } else {
        if(ticketList) ticketList.classList.remove('hidden');
    }
}

// --- LÓGICA DE TICKETS (BACKEND API) ---

function loadTickets() {
    // Si no estamos en la vista de tickets, opcionalmente podemos pausar la carga
    const ticketView = document.getElementById('view-tickets');
    if (ticketView && ticketView.classList.contains('hidden')) return;

    fetch('/api/get_tickets')
    .then(r => r.json())
    .then(data => {
        const list = document.getElementById('ticket-list');
        list.innerHTML = ''; // Limpiar lista
        
        if (data.length === 0) {
            list.innerHTML = '<div style="padding:20px; color:#555; text-align:center;">NO SIGNALS DETECTED</div>';
            return;
        }

        data.forEach(ticket => {
            const div = document.createElement('div');
            // Mantenemos la clase 'ticket-item' y 'active' original
            div.className = `ticket-item ${currentTicketId === ticket.id ? 'active' : ''}`;
            div.onclick = () => selectTicket(ticket.id, ticket.user_name); // Asegúrate que el backend envía user_name
            
            // Usamos 'status' para colorear o un badge si lo deseas
            let statusBadge = ticket.status === 'open' 
                ? `<span class="badge" style="background:var(--neon-blue); box-shadow:0 0 5px var(--neon-blue);">OP</span>` 
                : `<span class="badge" style="background:var(--text-grey); box-shadow:none;">CL</span>`;

            div.innerHTML = `
                <div style="display:flex; flex-direction:column;">
                    <span class="ticket-name" style="font-size:0.9rem;">${ticket.user_name}</span>
                    <span style="font-size:0.7em; color:var(--text-grey);">ID: ${ticket.id.substring(0,6)}...</span>
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
    document.getElementById('current-ticket-name').innerText = `TARGET ACQUIRED: ${name}`;
    
    // Habilitar controles
    document.getElementById('msg-input').disabled = false;
    document.getElementById('send-btn').disabled = false;

    loadMessages(id);
    loadTickets(); // Para actualizar estado activo visualmente
}

function loadMessages(id) {
    fetch(`/api/get_messages/${id}`)
    .then(r => r.json())
    .then(msgs => {
        const box = document.getElementById('chat-box');
        
        if(msgs.length === 0) {
            box.innerHTML = '<div class="empty-state">NO DATA LOGGED YET</div>';
            return;
        }
        
        box.innerHTML = ''; 
        msgs.forEach(m => {
            const div = document.createElement('div');
            // Diferenciamos mensajes del 'Soporte Web' vs Usuario
            const isAgent = m.sender === 'Soporte Web'; 
            
            div.className = `message ${isAgent ? 'agent' : 'discord'}`;
            div.innerHTML = `<small>${m.sender}</small><div class="msg-content">${m.content}</div>`;
            box.appendChild(div);
        });
        
        box.scrollTop = box.scrollHeight;
    });
}

function sendMessage() {
    if (!currentTicketId) return;
    const input = document.getElementById('msg-input');
    const text = input.value;
    if (!text) return;

    fetch('/api/send_message', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ ticket_id: currentTicketId, content: text })
    }).then(() => {
        input.value = '';
        loadMessages(currentTicketId);
    });
}

function completeTicket() {
    if (!currentTicketId) return;
    if(confirm('CONFIRM: Mark TRYOUT as COMPLETE? This will archive the data link.')) {
        // Aquí asumimos que tienes un endpoint para cerrar/archivar, si no, puedes quitar esta función
        // o simplemente limpiar la UI
        alert("Functionality to close ticket pending implementation in backend.");
    }
}

function handleEnter(e) { if (e.key === 'Enter') sendMessage(); }


// --- LÓGICA DE STATS / QUALIFICATIONS (INTACTA) ---

function calculateStats() {
    const offensiveIds = ['sht', 'dbl', 'stl', 'psn', 'dfd'];
    const gkIds = ['dvg', 'biq', 'rfx', 'dtg'];
    
    let sum = 0;
    let count = 0;
    let valuesObj = {};

    const processGroup = (ids) => {
        let localSum = 0;
        let localCount = 0;
        ids.forEach(id => {
            const el = document.getElementById(id);
            if(el && el.value !== '') {
                const val = parseFloat(el.value);
                if (!isNaN(val)) {
                    localSum += val;
                    localCount++;
                    valuesObj[id] = val;
                }
            }
        });
        return { sum: localSum, count: localCount };
    };

    const offData = processGroup(offensiveIds);
    const gkData = processGroup(gkIds);

    if (offData.count > 0) {
        sum = offData.sum;
        count = offData.count;
    } else if (gkData.count > 0) {
        sum = gkData.sum;
        count = gkData.count;
    }

    let average = count > 0 ? (sum / count).toFixed(1) : 0;
    
    const avgEl = document.getElementById('result-avg');
    if(avgEl) avgEl.innerText = average;
    
    updateRankAndTier(parseFloat(average));
    saveStats(valuesObj);
}

function updateRankAndTier(avg) {
    let titleHTML = `<span style="color:#555">N/A</span>`;
    let tier = "-";
    
    if (avg >= 9.5) tier = "S+";
    else if (avg >= 9.0) tier = "S";
    else if (avg >= 8.5) tier = "A";
    else if (avg >= 8.0) tier = "B";
    else if (avg >= 7.0) tier = "C";
    else if (avg >= 0.1) tier = "D";

    const getStars = (val, min, mid, high) => {
        if (val >= high) return "⭐⭐⭐";
        if (val >= mid) return "⭐⭐";
        return "⭐";
    };

    if (avg >= 9.1) {
        titleHTML = `<span style="color:#ffd700">World Class 👑 - ${getStars(avg, 9.1, 9.4, 9.7)}</span>`;
    } 
    else if (avg >= 8.2) {
        titleHTML = `<span style="color:#ffcc00">New Gen XI - ${getStars(avg, 8.2, 8.5, 8.8)}</span>`;
    } 
    else if (avg >= 7.3) {
        titleHTML = `<span style="color:#ff6600">Prodigy 🏅 - ${getStars(avg, 7.3, 7.6, 7.9)}</span>`;
    } 
    else if (avg >= 6.4) {
        titleHTML = `<span style="color:#00f0ff">Elite ⚡ - ${getStars(avg, 6.4, 6.7, 7.0)}</span>`;
    } 
    else if (avg >= 5.5) {
        titleHTML = `<span style="color:#a0a0a0">Amateur Striker ⚽ - ${getStars(avg, 5.5, 5.8, 6.1)}</span>`;
    } 
    else if (avg >= 4.6) {
        titleHTML = `<span style="color:#cd7f32">Rookie Strikers 🥉 - ${getStars(avg, 4.6, 4.9, 5.2)}</span>`;
    } 
    else {
        titleHTML = `<span style="color:#333">UNRANKED</span>`;
    }

    const rankEl = document.getElementById('result-rank');
    if(rankEl) rankEl.innerHTML = titleHTML;
    
    const tierEl = document.getElementById('result-tier');
    if(tierEl) {
        tierEl.innerText = tier;
        tierEl.style.color = (tier.startsWith("S") || tier === "A") ? "var(--neon-blue)" : 
                             (tier === "B" || tier === "C") ? "var(--neon-orange)" : "var(--text-grey)";
    }
}

function saveStats(valuesObj) {
    if(!valuesObj || valuesObj instanceof Event) {
        valuesObj = {};
        ['sht', 'dbl', 'stl', 'psn', 'dfd', 'dvg', 'biq', 'rfx', 'dtg'].forEach(id => {
            const el = document.getElementById(id);
            if(el) valuesObj[id] = el.value;
        });
    }
    
    const notesEl = document.getElementById('notes-area');
    const data = {
        stats: valuesObj,
        notes: notesEl ? notesEl.value : "",
        lastUpdated: new Date().toLocaleString()
    };
    
    localStorage.setItem('blz_player_stats', JSON.stringify(data));
    
    const indicator = document.getElementById('save-status');
    if(indicator) {
        indicator.innerText = "SAVING...";
        setTimeout(() => indicator.innerText = "DATA SAVED LOCALLY", 500);
    }
}

function loadStats() {
    const data = JSON.parse(localStorage.getItem('blz_player_stats'));
    if (data) {
        if(data.stats) {
            for (const [key, value] of Object.entries(data.stats)) {
                const el = document.getElementById(key);
                if(el) el.value = value;
            }
        }
        if(data.notes) {
            const notesEl = document.getElementById('notes-area');
            if(notesEl) notesEl.value = data.notes;
        }
        calculateStats();
    }
}

// --- GRÁFICOS (CHART.JS) & MODAL (INTACTO) ---

function openStatsModal() {
    const modal = document.getElementById('stats-modal');
    if(modal) {
        modal.classList.remove('hidden');
        renderChart();
    }
}

function closeStatsModal() {
    const modal = document.getElementById('stats-modal');
    if(modal) modal.classList.add('hidden');
}

function renderChart() {
    const ctx = document.getElementById('blzChart').getContext('2d');
    const getVal = (id) => parseFloat(document.getElementById(id).value) || 0;
    const isGK = (getVal('dvg') + getVal('biq') + getVal('rfx') + getVal('dtg')) > 0;
    
    if (blzChartInstance) {
        blzChartInstance.destroy();
    }

    Chart.defaults.font.family = "'Orbitron', sans-serif";
    Chart.defaults.color = '#fff';

    if (isGK) {
        const data = {
            labels: ['DIVING', 'BIQ', 'REFLEXES', 'DISTRIB.'],
            datasets: [{
                data: [getVal('dvg'), getVal('biq'), getVal('rfx'), getVal('dtg')],
                backgroundColor: [
                    'rgba(255, 102, 0, 0.7)',
                    'rgba(0, 240, 255, 0.7)',
                    'rgba(255, 255, 255, 0.7)',
                    'rgba(255, 102, 0, 0.4)'
                ],
                borderColor: '#0a0a0f',
                borderWidth: 2
            }]
        };

        blzChartInstance = new Chart(ctx, {
            type: 'polarArea',
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    r: {
                        grid: { color: 'rgba(255,255,255,0.1)' },
                        angleLines: { color: 'rgba(255,255,255,0.1)' },
                        suggestedMin: 0,
                        suggestedMax: 10,
                        ticks: { backdropColor: 'transparent', color: '#fff', z: 1 }
                    }
                },
                plugins: { legend: { position: 'bottom', labels: { color: '#fff' } } }
            }
        });

    } else {
        const data = {
            labels: ['SHOOTING', 'DRIBBLING', 'STEALING', 'PASSING', 'DEFENDING'],
            datasets: [{
                label: 'PLAYER STATS',
                data: [getVal('sht'), getVal('dbl'), getVal('stl'), getVal('psn'), getVal('dfd')],
                fill: true,
                backgroundColor: 'rgba(0, 240, 255, 0.2)',
                borderColor: '#00f0ff',
                pointBackgroundColor: '#fff',
                pointBorderColor: '#fff',
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: '#00f0ff',
                borderWidth: 2
            }]
        };

        blzChartInstance = new Chart(ctx, {
            type: 'radar',
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    r: {
                        angleLines: { color: 'rgba(255, 255, 255, 0.1)' },
                        grid: { color: 'rgba(255, 255, 255, 0.1)' },
                        pointLabels: { color: '#00f0ff', font: { size: 12, weight: 'bold' } },
                        suggestedMin: 0,
                        suggestedMax: 10,
                        ticks: { display: false }
                    }
                },
                plugins: { legend: { display: false } }
            }
        });
    }
}

async function copyChartToClipboard() {
    const canvas = document.getElementById('blzChart');
    canvas.toBlob(async (blob) => {
        try {
            const data = [new ClipboardItem({ [blob.type]: blob })];
            await navigator.clipboard.write(data);
            
            const btn = document.querySelector('.btn-copy');
            const originalText = btn.innerText;
            btn.innerText = "CHART COPIED!";
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
            console.error('Error copying image: ', err);
            alert('Clipboard access denied.');
        }
    });
}