const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// CHANGED: Wire the script directly to the automated variable injected by deploy.sh
const API_BASE_URL = window.ENV_CONFIG.API_URL;

/// UX Helpers to avoid confusion between upload states
function clearUrlInput() {
    const urlField = document.getElementById('urlInput');
    if (urlField && urlField.value !== "") {
        urlField.value = "";
        updateStatus('📸 Local image selected. Ready to process.', 'DEFAULT');
    }
}

function clearFileInput() {
    const fileField = document.getElementById('fileInput');
    if (fileField && fileField.value !== "") {
        fileField.value = ""; // Reset the native file selector
        updateStatus('🌐 Web URL provided. Ready to process.', 'DEFAULT');
    }
}

function updateStatus(message, state = 'DEFAULT') {
    const statusBox = document.getElementById('status');
    if (!statusBox) return;

    // 1. Update the text string inside the element
    statusBox.innerText = message;

    // 2. Adjust colors and icons depending on what the pipeline is doing
    switch (state) {
        case 'PROCESSING':
            statusBox.style.color = '#1e293b';       // Dark charcoal text
            statusBox.style.backgroundColor = '#eff6ff'; // Light blue background
            statusBox.style.borderColor = '#bfdbfe';     // Soft blue border
            break;
            
        case 'COMPLETE':
            statusBox.style.color = '#15803d';       // Deep green text
            statusBox.style.backgroundColor = '#f0fdf4'; // Soft green background
            statusBox.style.borderColor = '#bbf7d0';     // Green border
            break;
            
        case 'ERROR':
        case 'FAILED': // Combined FAILED case to handle your catch block fallback cleanly
            statusBox.style.color = '#b91c1c';       // Bright red text
            statusBox.style.backgroundColor = '#fef2f2'; // Soft red background
            statusBox.style.borderColor = '#fecaca';     // Red border
            break;
            
        case 'DEFAULT':
        default:
            statusBox.style.color = '#64748b';       // Original muted gray text
            statusBox.style.backgroundColor = '#f8fafc'; // Default off-white background
            statusBox.style.borderColor = '#e2e8f0';     // Default border
            break;
    }
}


async function processImage(event) {
    if (event) event.preventDefault();

    const fileInput = document.getElementById('fileInput').files[0];
    const urlInput = document.getElementById('urlInput').value.trim();
    const statusText = document.getElementById('status');
    const resultImage = document.getElementById('resultImage');

    statusText.innerText = "Processing image via AWS...";

    // Geolocation
    let city = "Unknown";
    let state = "Unknown";
    let country = "Unknown";

    let payload = {};
    let image_id = "unknown ID";

    if (fileInput) {
        // Convert local file to Base64 string
        const base64String = await toBase64(fileInput);
        payload = {userCity: city, userState: state, userCountry: country, type: "local", image: base64String.split(',')[1] }; // Strip metadata header
    } else if (urlInput) {
        payload = {
            type: "url",
            userCity: city, 
            userState: state, 
            userCountry: country, 
            image: urlInput
        };
    } else {
        updateStatus("Please select a file or enter a web URL.", "DEFAULT");
        return;
    }

    try {
        // CHANGED: Pointing to the global dynamic API_BASE_URL context
        const initial_response = await fetch(API_BASE_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const initial_data = await initial_response.json();
        image_id = initial_data.imageId;
        
        // check that image ID is returned. If not error
        if (!image_id || initial_data.error) {
            updateStatus(`Error submitting image: ${initial_data.error}`, "ERROR");
            return;
        } 

        // 2. Check in database for progress updates
        updateStatus(`Job ${image_id} submitted for processing.`, "PROCESSING");
        
        // CHANGED: Fixed path formatting. It handles trailing slashes cleanly out of API Gateway
        const cleanBaseUrl = API_BASE_URL.replace(/\/$/, "");
        const statusUrl = `${cleanBaseUrl}/${image_id}`;

        // RECOMMENDED: Switch from a path variable to a URL parameter string
        // const statusUrl = `${cleanBaseUrl}/status?imageId=${encodeURIComponent(image_id)}`;


        const status_response = await fetch(statusUrl);
        if (!status_response.ok) {
            // FIXED: Standardized undefined 'statusResponse' syntax variable bug to local 'status_response'
            updateStatus(
                `Initial GET status check failed with status: ${status_response.status}. ${status_response}`, 
                "ERROR"
            );
            return;
        }

        let poll_data = await status_response.json();
        let poll_status = String(poll_data.status).toUpperCase();
        let poll_message = String(poll_data.message);

        // 3. Polling Logic (While loop based strictly on the GET response data)
        const startTime = Date.now();
        const maxWaitTime = 300000; // 5 minute timeout
        const pollInterval = 10000; // Poll every 10 seconds
        let counter = 0;

        while (poll_status !== 'COMPLETE' && poll_status !== 'FAILED') {
            counter++;
            if (Date.now() - startTime > maxWaitTime) {
                updateStatus(
                    `Processing timed out after 5 mins. FAILED.`,
                    'ERROR'
                );
                return;
            }
            // update browser status
            updateStatus(
                `Query ${counter}: Status: ${poll_status} | Message: ${poll_message}`, 
                'PROCESSING'
            );
            
            // Wait 10 seconds before checking again
            await delay(pollInterval);

            // Repeated GET request to refresh pollData
            const repeat_response = await fetch(statusUrl);
            if (!repeat_response.ok) {
                updateStatus(`Processing failed. Network error.`, 'ERROR');
                return;
            }
            poll_data = await repeat_response.json();
            poll_status = String(poll_data.status).toUpperCase();
            poll_message = String(poll_data.message);
        }
        
        // Evaluation after loop termination
        if (poll_status === 'COMPLETE' && poll_data.s3OutputUrl) {
            updateStatus(
                `Processing succeeded. ${poll_data.message || ""}`, 
                'COMPLETE'
            );
            // Hook an event listener to the image element first
            resultImage.onload = function() {
                // This code ONLY fires when the S3 download is 100% complete
                resultImage.style.display = "block";
            };
            // 2. Set the image source to kick off the background network fetch
            resultImage.src = `${poll_data.s3OutputUrl}`;
            updateStatus(
                `Processing succeeded. ${poll_data.message || ""} Output image is displayed below.`,
                'COMPLETE'
            );
        } else {
            // FIXED: Variable string fallback reference error from poll_status.message to poll_data.message
            updateStatus(
                `Error processing image. Final message ${poll_data.message || 'No description'} : Image_id ${image_id}`, 
                'ERROR'
            );
        }

    } catch (err) {
        // FIXED: Maps to the new unified style selector rule in updateStatus switch tree
        updateStatus(
                `Error processing image. Final state ${err}. Image_id ${image_id}`, 
                "FAILED"
            );
        console.error(err);
    }
}

const toBase64 = file => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result);
    reader.onerror = error => reject(error);
});
