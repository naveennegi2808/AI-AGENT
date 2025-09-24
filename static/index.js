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

// Handle LinkedIn Image Upload
async function handleImageUpload() {
    const fileInput = document.getElementById('linkedin-image-file');
    const uploadStatus = document.getElementById('upload-status');
    const file = fileInput.files[0];

    if (!file) {
        alert('Please select an image file first.');
        return;
    }

    // Validate file type
    if (!file.type.startsWith('image/')) {
        alert('Please select a valid image file (PNG, JPG, GIF).');
        return;
    }

    // Show uploading status
    uploadStatus.textContent = 'Uploading image...';
    uploadStatus.className = '';
    uploadStatus.style.display = 'block';

    const formData = new FormData();
    formData.append('image', file);

    try {
        const response = await fetch('/upload/linkedin/image', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (result.success) {
            uploadStatus.textContent = '✅ Image uploaded successfully! You can now ask the agent to post on LinkedIn.';
            uploadStatus.className = 'success';
            // Refresh the page to show the updated state
            setTimeout(() => {
                window.location.reload();
            }, 1500);
        } else {
            uploadStatus.textContent = '❌ Upload failed: ' + (result.error || 'Unknown error');
            uploadStatus.className = 'error';
        }
    } catch (error) {
        uploadStatus.textContent = '❌ Upload failed: ' + error.message;
        uploadStatus.className = 'error';
        console.error('Upload error:', error);
    }
}

// LinkedIn Image Upload Functionality (Legacy - for drag & drop if needed)
const imageInput = document.getElementById('imageInput');
const uploadArea = document.getElementById('uploadArea');
const uploadStatus = document.getElementById('uploadStatus');
const clearImageBtn = document.getElementById('clearImageBtn');
const imageUploadPanel = document.getElementById('imageUploadPanel');

// Handle image input change
if (imageInput) {
    imageInput.addEventListener('change', function(e) {
        const file = e.target.files[0];
        if (file) {
            uploadImageToLinkedIn(file);
        }
    });
}

// Handle drag and drop
if (uploadArea) {
    uploadArea.addEventListener('click', function() {
        imageInput.click();
    });

    uploadArea.addEventListener('dragover', function(e) {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });

    uploadArea.addEventListener('dragleave', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
    });

    uploadArea.addEventListener('drop', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const file = files[0];
            if (file.type.startsWith('image/')) {
                uploadImageToLinkedIn(file);
            } else {
                alert('Please select an image file.');
            }
        }
    });
}

// Clear uploaded image
if (clearImageBtn) {
    clearImageBtn.addEventListener('click', function() {
        // Clear the session storage of uploaded image
        fetch('/upload/linkedin/image', {
            method: 'DELETE'
        }).then(() => {
            uploadStatus.style.display = 'none';
            uploadArea.style.display = 'block';
            imageInput.value = '';
        }).catch(err => {
            console.log('Could not clear image:', err);
            // Still hide the status even if API call fails
            uploadStatus.style.display = 'none';
            uploadArea.style.display = 'block';
            imageInput.value = '';
        });
    });
}

// Upload image to LinkedIn (Legacy function)
async function uploadImageToLinkedIn(file) {
    const formData = new FormData();
    formData.append('image', file);

    // Show uploading state
    if (uploadArea) {
        uploadArea.classList.add('uploading');
    }
    
    try {
        const response = await fetch('/upload/linkedin/image', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();
        
        if (uploadArea) {
            uploadArea.classList.remove('uploading');
        }

        if (result.success) {
            // Show success status
            if (uploadArea) uploadArea.style.display = 'none';
            if (uploadStatus) uploadStatus.style.display = 'flex';
        } else {
            alert('Upload failed: ' + (result.error || 'Unknown error'));
        }
    } catch (error) {
        if (uploadArea) {
            uploadArea.classList.remove('uploading');
        }
        alert('Upload failed: ' + error.message);
        console.error('Upload error:', error);
    }
}

// Check service status and update badges
async function updateServiceStatus() {
    try {
        // Check Google services
        const googleResponse = await fetch('/status');
        const googleData = await googleResponse.json();
        const googleBadge = document.getElementById('googleStatus');
        
        if (googleData.status === 'authorized') {
            googleBadge.className = 'status-badge status-connected';
            googleBadge.innerHTML = '<i class="fas fa-circle" style="font-size: 8px;"></i><span>Connected</span>';
        }

        // Check LinkedIn services
        const linkedinResponse = await fetch('/linkedin-status');
        const linkedinData = await linkedinResponse.json();
        const linkedinBadge = document.getElementById('linkedinStatus');
        
        if (linkedinData.status === 'authorized') {
            linkedinBadge.className = 'status-badge status-connected';
            linkedinBadge.innerHTML = '<i class="fas fa-circle" style="font-size: 8px;"></i><span>Connected</span>';
            
            // Show image upload panel when LinkedIn is connected
            if (imageUploadPanel) {
                imageUploadPanel.style.display = 'block';
            }
        } else {
            // Hide image upload panel when LinkedIn is not connected
            if (imageUploadPanel) {
                imageUploadPanel.style.display = 'none';
            }
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

// Refresh status every 30 seconds
setInterval(updateServiceStatus, 30000);