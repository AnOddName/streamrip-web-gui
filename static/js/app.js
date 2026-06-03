// static/js/app.js

let currentTab = 'active';
let currentSearchType = 'album';
let currentPage = 1;
let itemsPerPage = 10;
let totalResults = 0;
let allSearchResults = [];


let eventSource = null;
let activeDownloads = new Map();
let downloadHistory = [];


function initializeSSE() {
    if (eventSource) {
        eventSource.close();
    }
    
    eventSource = new EventSource('/api/events');
    
    
    eventSource.onerror = function(error) {
        console.error('SSE error:', error);
        setTimeout(initializeSSE, 5000);
    };
    
    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        handleSSEMessage(data);
    };
}

function handleSSEMessage(data) {
    
    switch(data.type) {
        case 'download_started':
            handleDownloadStarted(data);
            break;
        case 'download_progress':
            handleDownloadProgress(data);
            break;
        case 'download_completed':
            handleDownloadCompleted(data);
            break;
        case 'download_error':
            handleDownloadError(data);
            break;
		case 'heartbeat':
			console.log('badump')
        default:
            console.log('Unknown SSE message type:', data.type);
    }
}

function handleDownloadStarted(data) {
    activeDownloads.set(data.id, {
        id: data.id,
        metadata: data.metadata,
        status: 'downloading',
        progress: 0,
        output: [],
        startTime: Date.now()
    });
    
    if (currentTab === 'active') {
        renderActiveDownloads();
    }
}

function handleDownloadError(data) {
    const download = activeDownloads.get(data.id);
    if (download) {
        download.status = 'error';
        download.error = data.error;
        updateDownloadElement(data.id, download);
        
        setTimeout(() => {
            downloadHistory.unshift({
                ...download,
                completedAt: Date.now()
            });
            activeDownloads.delete(data.id);
            
            if (currentTab === 'active') {
                renderActiveDownloads();
            } else if (currentTab === 'history') {
                renderDownloadHistory();
            }
        }, 2000);
    }
}

function statusLabel(status) {
    return status === 'skipped' ? 'already downloaded' : status;
}

function renderDownloadHistory() {
    const container = document.getElementById('downloadHistory');
    if (!container) return;

    if (downloadHistory.length === 0) {
        container.innerHTML = '<div class="empty-state">NO DOWNLOAD HISTORY</div>';
        return;
    }

    container.innerHTML = downloadHistory.map(item => {
        const isOk = item.status === 'completed' || item.status === 'skipped';
        const statusIcon = isOk ? '✓' : '✗';
        const statusClass = isOk ? 'success' : 'error';
        
        return `
        <div class="download-item ${item.status}">
            <div class="download-content">
                ${item.metadata?.album_art ? 
                    `<img src="${item.metadata.album_art}" class="download-album-art" onerror="this.style.display='none'">` : 
                    `<div class="download-album-art placeholder ${statusClass}">${statusIcon}</div>`
                }
                <div class="download-info">
                    <div class="download-title">${item.metadata?.title || 'Unknown'}</div>
                    <div class="download-artist">${item.metadata?.artist || 'Unknown Artist'}</div>
                    <div class="download-meta">
                        <span class="status-badge ${item.status}">${statusLabel(item.status)}</span>
                        ${item.metadata?.service ? 
                            `<span class="service-badge">${item.metadata.service.toUpperCase()}</span>` : ''}
                    </div>
                </div>
            </div>
        </div>
    `}).join('');
}


function handleDownloadProgress(data) {
    const download = activeDownloads.get(data.id);
    if (download) {
        download.latestOutput = data.output;
        download.allOutput = download.allOutput || [];
        download.allOutput.push(data.output);
    }
}

function handleDownloadCompleted(data) {
    const download = activeDownloads.get(data.id);
    if (download) {
        download.status = data.status;
        download.endTime = Date.now();
        download.output = data.output || download.allOutput.join('\n') || 'No output captured';
        updateDownloadElement(data.id, download);
        
        setTimeout(() => {
            downloadHistory.unshift({
                ...download,
                completedAt: Date.now()
            });
            
            if (downloadHistory.length > 50) {
                downloadHistory.pop();
            }
            
            activeDownloads.delete(data.id);
            
            if (currentTab === 'active') {
                renderActiveDownloads();
            } else if (currentTab === 'history') {
                renderDownloadHistory();
            }
        }, 3000); //3 seconds in tab
    }
}


function updateDownloadElement(id, download) {
    const element = document.querySelector(`[data-download-id="${id}"]`);
    if (!element) {
        renderActiveDownloads();
        return;
    }
    
    const statusBadge = element.querySelector('.status-badge');
    if (statusBadge) {
        statusBadge.textContent = statusLabel(download.status);
        statusBadge.className = `status-badge ${download.status}`;
    }
    
    if (element.classList.contains('expanded')) {
        const outputEl = element.querySelector('.download-output');
        if (outputEl) {
            outputEl.textContent = download.output;
            outputEl.scrollTop = outputEl.scrollHeight;
        }
    }
}
function renderActiveDownloads() {
    const container = document.getElementById('activeDownloads');
    
    if (activeDownloads.size === 0) {
        container.innerHTML = '<div class="empty-state">NO ACTIVE DOWNLOADS</div>';
        return;
    }
    
    container.innerHTML = Array.from(activeDownloads.values()).map(item => `
        <div class="download-item ${item.status}" data-download-id="${item.id}">
            <div class="download-content">
                ${item.metadata.album_art ? 
                    `<img src="${item.metadata.album_art}" class="download-album-art">` : 
                    `<div class="download-album-art placeholder">▶</div>`
                }
                <div class="download-info">
                    <div class="download-title">${item.metadata.title || 'Unknown'}</div>
                    <div class="download-artist">${item.metadata.artist || 'Unknown Artist'}</div>
                    <div class="download-meta">
                        <span class="status-badge ${item.status}">${statusLabel(item.status)}</span>
                        ${item.metadata.service ? 
                            `<span class="service-badge">${item.metadata.service.toUpperCase()}</span>` : ''}
                    </div>
                    ${item.output ? `<a class="toggle-output" onclick="toggleOutput('${item.id}')">SHOW OUTPUT</a>` : ''}
                </div>
                <div class="download-spinner"></div>
            </div>
            ${item.output ? `
                <div class="download-output">
                    ${item.output}
                </div>
            ` : ''}
        </div>
    `).join('');
}


function toggleOutput(id) {
    const item = document.querySelector(`.download-item[data-download-id="${id}"]`);
    if (item) {
        item.classList.toggle('expanded');
        const toggleBtn = item.querySelector('.toggle-output');
        if (toggleBtn) {
            toggleBtn.textContent = item.classList.contains('expanded') ? 'HIDE OUTPUT' : 'SHOW OUTPUT';
        }
    }
}


function switchTab(tab, element) {
    currentTab = tab;
    
    if (element && !element.classList.contains('search-header')) {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        element.classList.add('active');
    }
    
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.getElementById(tab + 'Tab').classList.add('active');
    
    if (tab === 'active') {
        renderActiveDownloads(); 
    } else if (tab === 'history') {
        renderDownloadHistory(); 
    } else if (tab === 'config') {
        loadConfig();
    } else if (tab === 'files') {
        loadFiles();
    }
}

async function startDownload() {
    const url = document.getElementById('urlInput').value.trim();
    const quality = document.getElementById('qualitySelect').value;
    
    if (!url) {
        alert('Please enter a URL');
        return;
    }
    
    const btn = document.getElementById('downloadBtn');
    btn.disabled = true;
    
    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, quality: parseInt(quality) })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            document.getElementById('urlInput').value = '';
            document.querySelector('.tab').click();
        } else {
            alert(data.error || 'Failed to start download');
        }
    } catch (error) {
        alert('Error: ' + error.message);
    } finally {
        btn.disabled = false;
    }
}


async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const data = await response.json();
        document.getElementById('configEditor').value = data.config || '';
    } catch (error) {
        alert('Failed to load config: ' + error.message);
    }
}

async function saveConfig() {
    const config = document.getElementById('configEditor').value;
    
    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config })
        });
        
        if (response.ok) {
            alert('Config saved successfully');
        } else {
            const data = await response.json();
            alert('Failed to save config: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

async function loadFiles() {
    try {
        const response = await fetch('/api/browse');
        const files = await response.json();
        
        const container = document.getElementById('fileList');
        
        if (files.length === 0) {
            container.innerHTML = '<div class="empty-state">NO FILES FOUND</div>';
            return;
        }
        
        container.innerHTML = files.map(file => `
            <div class="file-item">
                <div class="file-name">${file.name}</div>
                <div class="file-meta">
                    ${(file.size / 1024 / 1024).toFixed(2)} MB • 
                    ${new Date(file.modified * 1000).toLocaleDateString()}
                </div>
            </div>
        `).join('');
    } catch (error) {
        alert('Failed to load files: ' + error.message);
    }
}

function setSearchType(type, element) {
    currentSearchType = type;
    document.querySelectorAll('.search-type-btn').forEach(btn => btn.classList.remove('active'));
    element.classList.add('active');
}

async function searchMusic() {
    const query = document.getElementById('searchInput').value.trim();
    const source = document.getElementById('searchSource').value;
    
    if (!query) {
        alert('Please enter a search query');
        return;
    }
    
    const resultsDiv = document.getElementById('searchResults');
    resultsDiv.innerHTML = '<div class="empty-state">SEARCHING ' + source.toUpperCase() + '...</div>';
    
    currentPage = 1;
    allSearchResults = [];
    
    try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                query: query,
                type: currentSearchType,
                source: source
            })
        });
        
        const data = await response.json();
        
        // error handling new
        if (!response.ok) {
            const errorMsg = data.error || 'Search failed';
            let errorHtml = `<div class="error-state">
                <div class="error-title">⚠ SEARCH ERROR</div>
                <div class="error-message">${escapeHtml(errorMsg)}</div>`;
            
            if (data.debug_info) {
                errorHtml += `<div class="error-details">`;
                
                if (data.debug_info.return_code !== undefined) {
                    errorHtml += `<div class="error-detail-line">Return Code: ${data.debug_info.return_code}</div>`;
                }
                
                if (data.debug_info.stdout_preview) {
                    errorHtml += `<details class="error-traceback">
                        <summary>▼ Show Technical Details</summary>
                        <pre>${escapeHtml(data.debug_info.stdout_preview)}</pre>
                    </details>`;
                }
                
                errorHtml += `</div>`;
            }
            
            errorHtml += `</div>`;
            resultsDiv.innerHTML = errorHtml;
            updatePaginationControls();
            return;
        }
        
        if (data.message) {
            resultsDiv.innerHTML = `<div class="empty-state">${data.message.toUpperCase()}</div>`;
            updatePaginationControls();
        } else if (data.results && data.results.length > 0) {
            allSearchResults = data.results;
            totalResults = data.results.length;
            displayCurrentPage();
        } else {
            resultsDiv.innerHTML = '<div class="empty-state">NO RESULTS FOUND ON ' + source.toUpperCase() + '</div>';
            updatePaginationControls();
        }
    } catch (error) {
        console.error('Search error:', error);
        resultsDiv.innerHTML = `<div class="error-state">
            <div class="error-title">⚠ CONNECTION ERROR</div>
            <div class="error-message">${escapeHtml(error.message)}</div>
        </div>`;
        updatePaginationControls();
    }
}

// Helper function to escape HTML
function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function displayCurrentPage() {
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = Math.min(startIndex + itemsPerPage, totalResults);
    const pageResults = allSearchResults.slice(startIndex, endIndex);
    
    const resultsDiv = document.getElementById('searchResults');
    
    if (pageResults.length === 0) {
        resultsDiv.innerHTML = '<div class="empty-state">NO RESULTS FOUND</div>';
        return;
    }
    
    resultsDiv.innerHTML = pageResults.map(result => `
        <div class="search-result-item" data-id="${result.id}" data-source="${result.service}" data-type="${result.type}">
            <div class="result-album-art placeholder" id="art-${result.id}">▶</div>
            <div class="result-info">
                <span class="result-service">${result.service}</span>
                ${result.title ? `<div class="result-title">${result.title}</div>` : ''}
                <div class="result-artist">${result.artist || result.desc}</div>
                ${result.id ? `<div class="result-id">ID: ${result.id} (${result.type})</div>` : ''}
            </div>
            ${result.url ? `
                <button class="result-download-btn" onclick="downloadFromUrl('${result.url}')">
                    DOWNLOAD
                </button>
            ` : `
                <button class="result-download-btn" disabled style="opacity: 0.3;">
                    NO URL
                </button>
            `}
        </div>
    `).join('');
    
    updatePaginationControls();
    loadAlbumArtForVisibleItems();
}
function updatePaginationControls() {
    const totalPages = Math.ceil(totalResults / itemsPerPage);
    document.getElementById('pageInfo').textContent = `Page ${currentPage} of ${totalPages}`;
    document.getElementById('resultsCount').textContent = `${totalResults} results`;
    
    document.getElementById('prevPage').disabled = currentPage <= 1;
    document.getElementById('nextPage').disabled = currentPage >= totalPages;
}

function changePage(direction) {
    const totalPages = Math.ceil(totalResults / itemsPerPage);
    const newPage = currentPage + direction;
    
    if (newPage >= 1 && newPage <= totalPages) {
        currentPage = newPage;
        displayCurrentPage();
    }
}

document.getElementById('searchSource').addEventListener('change', function(e) {
    const source = e.target.value;
    const albumBtn = document.querySelector('.search-type-btn[onclick*="album"]');
    const artistBtn = document.querySelector('.search-type-btn[onclick*="artist"]');
    const trackBtn = document.querySelector('.search-type-btn[onclick*="track"]');
    
    if (source === 'soundcloud') {
        if (currentSearchType === 'album' || currentSearchType === 'artist') {
            trackBtn.click();
        }
        
        albumBtn.style.opacity = '0.3';
        albumBtn.style.pointerEvents = 'none';
        albumBtn.title = 'Not available on SoundCloud';
        
        artistBtn.style.opacity = '0.3';
        artistBtn.style.pointerEvents = 'none';
        artistBtn.title = 'Not available on SoundCloud';
    } else {
        albumBtn.style.opacity = '1';
        albumBtn.style.pointerEvents = 'auto';
        albumBtn.title = '';
        
        artistBtn.style.opacity = '1';
        artistBtn.style.pointerEvents = 'auto';
        artistBtn.title = '';
    }
});

async function loadAlbumArtForVisibleItems() {
    const visibleItems = document.querySelectorAll('.search-result-item');
    
    for (const item of visibleItems) {
        const itemId = item.dataset.id;
        const source = item.dataset.source;
        const type = item.dataset.type;
        
        if (!itemId) continue;
        
        try {
            const response = await fetch(`/api/album-art?source=${source}&type=${type}&id=${encodeURIComponent(itemId)}`);
            const data = await response.json();
            if (data.album_art) {
                const artElement = document.getElementById(`art-${itemId}`);
                if (artElement) {
                    artElement.classList.remove('placeholder');
                    artElement.innerHTML = `<img src="${data.album_art}" alt="Album art" class="result-album-art" onerror="this.parentElement.classList.add('placeholder'); this.parentElement.innerHTML='▶'">`;
                }
            } else {
                const artElement = document.getElementById(`art-${itemId}`);
                if (artElement && artElement.classList.contains('placeholder')) {
                    artElement.classList.add('loaded');
                    if (type === 'artist') {
                        artElement.innerHTML = '👤';
                    } else if (type === 'track') {
                        artElement.innerHTML = '🎵';
                    } else {
                        artElement.innerHTML = '▶';
                    }
                }
            }

            if (data.release_type || data.tracks_count || data.year) {
                const infoEl = item.querySelector('.result-info');
                if (infoEl) {
                    const existing = infoEl.querySelector('.result-extra-meta');
                    if (existing) existing.remove();

                    const parts = [];
                    if (data.release_type) parts.push(data.release_type.toUpperCase());
                    if (data.year) parts.push(data.year);
                    if (data.tracks_count) parts.push(`${data.tracks_count} tracks`);


                    const meta = document.createElement('div');
                    meta.className = 'result-extra-meta';
                    meta.textContent = parts.join(' · ');
                    const resultId = infoEl.querySelector('.result-id');
                    if (resultId) {
                        infoEl.insertBefore(meta, resultId);
                    } else {
                        infoEl.appendChild(meta); // fallback if no result-id exists
                    }
                }
            }

        } catch (error) {
            console.error('Error loading album art:', error);
            const artElement = document.getElementById(`art-${itemId}`);
            if (artElement && artElement.classList.contains('placeholder')) {
                if (type === 'artist') {
                    artElement.innerHTML = '👤';
                } else if (type === 'track') {
                    artElement.innerHTML = '🎵';
                } else {
                    artElement.innerHTML = '▶';
                }
            }
        }
    }
}
async function downloadFromUrl(url) {
    const quality = document.getElementById('qualitySelect').value;
    
    const searchResults = document.querySelectorAll('.search-result-item');
    let metadata = {};
    
    searchResults.forEach(item => {
        const btn = item.querySelector('.result-download-btn');
        if (btn && btn.onclick && btn.onclick.toString().includes(url)) {
            const serviceEl = item.querySelector('.result-service');
            const titleEl = item.querySelector('.result-title');
            const artistEl = item.querySelector('.result-artist');
            const artImg = item.querySelector('.result-album-art img');
            
            metadata = {
                title: titleEl?.textContent || '',
                artist: artistEl?.textContent || '',
                service: serviceEl?.textContent?.toLowerCase() || '',
                album_art: artImg?.src || ''
            };
        }
    });
    
    switchTab('active');
    
    try {
        const response = await fetch('/api/download-from-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                url: url,
                quality: parseInt(quality),
                ...metadata
            })
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            alert('Failed to start download: ' + (data.error || 'Unknown error'));
        }
        
    } catch (error) {
        alert('Error: ' + error.message);
    }
}


window.addEventListener('load', () => {
    initializeSSE();
});

window.addEventListener('beforeunload', () => {
    if (eventSource) {
        eventSource.close();
    }
});

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch((err) => {
        console.log('Service worker registration failed:', err);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('urlInput');
    if (urlInput) {
        urlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                startDownload();
            }
        });
    }
    
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                searchMusic();
            }
        });
    }
});
