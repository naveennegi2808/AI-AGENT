
// Auto-resize textarea
const messageInput = document.getElementById('messageInput');
messageInput.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = this.scrollHeight + 'px';
});

// Handle Enter key (Submit on Enter, new line on Shift+Enter)
messageInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('chatForm').submit();
    }
});

// Auto-scroll to bottom of messages
const chatMessages = document.getElementById('chatMessages');
if (chatMessages) {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Form submission handling
const chatForm = document.getElementById('chatForm');
const sendBtn = document.getElementById('sendBtn');

chatForm.addEventListener('submit', function () {
    sendBtn.disabled = true;
    sendBtn.innerHTML = `
                <div class="loading">
                    <div class="loading-dots">
                        <div class="loading-dot"></div>
                        <div class="loading-dot"></div>
                        <div class="loading-dot"></div>
                    </div>
                    <span>Sending...</span>
                </div>
            `;
});

// Check service status and update badges
async function updateServiceStatus() {
    try {
        // Check Google services
        const googleResponse = await fetch('/status');
        const googleData = await googleResponse.json();

        const googleBadge = document.querySelector('.service-card:first-child .status-badge');
        if (googleData.status === 'authorized') {
            googleBadge.className = 'status-badge status-connected';
            googleBadge.innerHTML = '<i class="fas fa-circle" style="font-size: 8px;"></i><span>Connected</span>';
        }

        // Check Instagram status
        const instagramResponse = await fetch('/instagram/status');
        const instagramData = await instagramResponse.json();

        const instagramBadge = document.querySelector('.service-card:last-child .status-badge');
        if (instagramData.status === 'authorized') {
            instagramBadge.className = 'status-badge status-connected';
            instagramBadge.innerHTML = '<i class="fas fa-circle" style="font-size: 8px;"></i><span>Connected</span>';
        }
    } catch (error) {
        console.log('Could not check service status:', error);
    }
}

// Update status on page load
updateServiceStatus();

// Focus on input when page loads
window.addEventListener('load', function () {
    messageInput.focus();
});
