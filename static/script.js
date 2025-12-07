// ==========================================
// 1. CONFIGURACIÓN GLOBAL
// ==========================================
let currentTicketId = null;
let blzChartInstance = null; // Para el gráfico de estadísticas

// ==========================================
// 2. INICIALIZACIÓN (Al cargar la página)
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    
    // A. Iniciar el sistema de Tickets
    loadTickets();
    // Actualizar lista de tickets cada 3 segundos
    setInterval(loadTickets, 3000); 
    
    // Actualizar chat cada 2 segundos (solo si hay uno abierto)
    setInterval(() => { 
        if (currentTicketId) loadMessages(currentTicketId); 
    }, 2000);

    // B. Listener para enviar con "Enter"
    const chatInput = document.getElementById('chat-input');
    if(chatInput){
        chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
    }

    // C. Inicializar Stats (Si existen en el HTML)
    // Intentamos cargar estadísticas guardadas si el usuario ya las usó
    if(typeof loadStats === 'function') loadStats();
});

// ==========================================
// 3. NAVEGACIÓN (Vistas Tickets vs Stats)
// ==========================================
function switchView(viewName) {
    // 1. Gestionar botones del menú
    document.querySelectorAll('.system-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById(`btn-view-${viewName}`);
    if(activeBtn) activeBtn.classList.add('active');

    // 2. Gestionar contenedores de contenido
    // Ocultamos todos los contenidos principales
    const ticketsView = document.getElementById('view-tickets');
    const statsView = document.getElementById('view-qualifications'); // Asumo que tu ID es este por el contexto anterior

    if(viewName === 'tickets') {
        if(ticketsView) ticketsView.classList.remove('hidden');
        if(statsView) statsView.classList.add('hidden');
    } else if (viewName === 'qualifications') {
        if(ticketsView) ticketsView.classList.add('hidden');
        if(statsView) statsView.classList.remove('hidden');
        // Redibujar gráfico si es necesario
        if(blzChartInstance) blzChartInstance.update();
    }
}

// ==========================================
// 4. LÓGICA DE TICKETS
// ==========================================

function loadTickets() {
    // Si no estamos en la vista de tickets, no hacemos peticiones para ahorrar recursos
    const ticketView = document.getElementById('view-tickets');
    if (ticketView && ticketView.classList.contains('hidden')) return;

    fetch('/api/get_tickets')
    .then(r => r.json())
    .then(data => {
        const list = document.getElementById('ticket-list');
        if(!list) return;

        // Guardamos el scroll actual para que no salte al actualizar
        // const currentScroll = list.scrollTop; 
        
        list.innerHTML = ''; // Limpiamos la lista
        
        if (data.length === 0) {
            list.innerHTML = '<div style="padding:20px; color:#555; text-align:center; font-family:monospace;">NO SIGNALS DETECTED</div>';
            return;
        }

        data.forEach(ticket => {
            const div = document.createElement('div');
            // Añadimos clase 'active' si es el ticket que estamos viendo ahora mismo
            div.className = `ticket-item ${currentTicketId === ticket.id ? 'active' : ''}`;
            
            // Al hacer clic, cargamos ese chat
            div.onclick = () => selectTicket(ticket.id, ticket.channel_name); 

            // Badge de estado (Open/Closed)
            let statusBadge = ticket.status === 'open' 
                ? `<span class="badge" style="background:var(--neon-blue); box-shadow:0 0 5px var(--neon-blue); color:black;">OP</span>` 
                : `<span class="badge" style="background:var(--text-grey); box-shadow:none;">CL</span>`;

            // HTML interno del item de la lista
            div.innerHTML = `
                <div style="display:flex; flex-direction:column;">
                    <span class="ticket-name" style="font-size:0.85rem; font-family:monospace; color:var(--neon-blue); letter-spacing:1px;">
                        ${ticket.channel_name}
                    </span> 
                    <span style="font-size:0.7em; color:var(--text-grey); margin-top:2px;">
                        User: <span style="color:white;">${ticket.user_name}</span>
                    </span>
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
    
    // Actualizar título de la cabecera del chat
    const header = document.getElementById('chat-header-title');
    if(header) header.innerText = name || id;
    
    // Forzar recarga visual de la lista para marcar el activo
    loadTickets(); 
    
    // Cargar mensajes inmediatamente
    loadMessages(id);
}

// ==========================================
// 5. LÓGICA DE MENSAJES (CHAT)
// ==========================================

function loadMessages(ticketId) {
    const chatBody = document.getElementById('chat-body');
    if (!chatBody) return;

    fetch(`/api/get_messages/${ticketId}`)
    .then(r => r.json())
    .then(msgs => {
        chatBody.innerHTML = ''; // Limpiar chat actual
        
        msgs.forEach(msg => {
            const div = document.createElement('div');
            
            // DETECTAR SI SOY YO (BLZ-T) O EL USUARIO
            // Si el sender es "BLZ-T", el mensaje va a la derecha (sent)
            const isMe = (msg.sender === 'BLZ-T');
            
            div.className = `message ${isMe ? 'sent' : 'received'}`;
            
            // Construcción del HTML del mensaje
            // Formato de hora simple
            const timeString = new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

            div.innerHTML = `
                <div class="msg-content">${msg.content}</div>
                <div class="msg-meta">
                    <span style="color:${isMe ? 'var(--neon-blue)' : 'var(--neon-orange)'}">${msg.sender}</span> 
                    • ${timeString}
                </div>
            `;
            chatBody.appendChild(div);
        });
        
        // Auto-scroll hacia abajo para ver el último mensaje
        chatBody.scrollTop = chatBody.scrollHeight;
    })
    .catch(err => console.error("Error loading messages:", err));
}

function sendMessage() {
    const input = document.getElementById('chat-input');
    const content = input.value.trim();
    
    if (!content || !currentTicketId) return; // No enviar si vacío o sin ticket seleccionado

    input.value = ''; // Limpiar input visualmente rápido

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
            loadMessages(currentTicketId); // Recargar chat para ver mi mensaje
        } else {
            console.error("Error sending:", data);
            alert("Error enviando mensaje. Revisa la consola.");
        }
    })
    .catch(err => console.error("Fetch error:", err));
}

// ==========================================
// 6. FUNCIONES DE ESTADÍSTICAS (MOCKUP/PLACEHOLDER)
// ==========================================
// Estas funciones mantienen viva la pestaña de "Stats" 
// si es que la estás usando, basada en tu código anterior.

function calculateStats() {
    // Lógica original de cálculo de stats (si la necesitas)
    // Aquí iría el código para actualizar el gráfico Chart.js
    console.log("Calculando stats...");
}

function loadStats() {
    // Lógica para cargar stats de localStorage
    console.log("Cargando stats...");
}

function copyChartToClipboard() {
    // Lógica para copiar gráfico
    alert("Función de copiar gráfico pendiente de implementar completamente.");
}