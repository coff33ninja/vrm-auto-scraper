import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

// State
let scene, camera, renderer, controls;
let currentModel = null;
let currentVRM = null;
let models = [];
let clock = new THREE.Clock();
let lastModelCount = 0;
let refreshInterval = null;

// Supported preview formats
const PREVIEWABLE_FORMATS = ['vrm', 'glb', 'gltf'];
const ARCHIVE_FORMATS = ['zip', 'rar', '7z'];

// DOM elements
const container = document.getElementById('canvas-container');
const modelList = document.getElementById('model-list');
const statsEl = document.getElementById('stats');
const loadingEl = document.getElementById('loading');
const noModelEl = document.getElementById('no-model');
const controlsEl = document.getElementById('controls');
const searchInput = document.getElementById('search');

// Initialize Three.js scene
function initScene() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1a2e);

    camera = new THREE.PerspectiveCamera(
        35,
        container.clientWidth / container.clientHeight,
        0.1,
        1000
    );
    camera.position.set(0, 1.2, 3);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);

    // Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);

    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
    directionalLight.position.set(1, 2, 1);
    scene.add(directionalLight);

    const backLight = new THREE.DirectionalLight(0xffffff, 0.3);
    backLight.position.set(-1, 1, -1);
    scene.add(backLight);

    // Grid
    const gridHelper = new THREE.GridHelper(10, 10, 0x333333, 0x222222);
    scene.add(gridHelper);

    // Controls
    controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 1, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.update();

    // Handle resize
    window.addEventListener('resize', onWindowResize);

    // Start render loop
    animate();
}

function onWindowResize() {
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
}

function animate() {
    requestAnimationFrame(animate);
    
    const delta = clock.getDelta();
    
    // Update VRM animations if present
    if (currentVRM) {
        currentVRM.update(delta);
    }
    
    controls.update();
    renderer.render(scene, camera);
}

// Clear current model from scene
function clearCurrentModel() {
    if (currentModel) {
        scene.remove(currentModel);
        // Dispose of geometries and materials
        currentModel.traverse((child) => {
            if (child.geometry) child.geometry.dispose();
            if (child.material) {
                if (Array.isArray(child.material)) {
                    child.material.forEach(m => m.dispose());
                } else {
                    child.material.dispose();
                }
            }
        });
        currentModel = null;
    }
    if (currentVRM) {
        VRMUtils.deepDispose(currentVRM.scene);
        currentVRM = null;
    }
}

// Center camera on loaded model
function centerCameraOnModel(object) {
    const box = new THREE.Box3().setFromObject(object);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    
    const maxDim = Math.max(size.x, size.y, size.z);
    const distance = maxDim * 2;
    
    controls.target.copy(center);
    camera.position.set(center.x, center.y + size.y * 0.3, center.z + distance);
    controls.update();
}

// Load VRM model
async function loadVRM(url) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));

    const gltf = await loader.loadAsync(url);
    const vrm = gltf.userData.vrm;

    if (vrm) {
        VRMUtils.rotateVRM0(vrm);
        currentVRM = vrm;
        currentModel = vrm.scene;
        scene.add(vrm.scene);
        centerCameraOnModel(vrm.scene);
        return true;
    }
    return false;
}

// Load GLB/GLTF model
async function loadGLTF(url) {
    const loader = new GLTFLoader();
    const gltf = await loader.loadAsync(url);
    
    currentModel = gltf.scene;
    scene.add(gltf.scene);
    centerCameraOnModel(gltf.scene);
    return true;
}

// Load any supported 3D model
async function loadModel(filePath, fileType) {
    loadingEl.style.display = 'block';
    noModelEl.style.display = 'none';
    controlsEl.style.display = 'none';

    // Clear previous model
    clearCurrentModel();

    const url = `/models/${encodeURIComponent(filePath)}`;
    
    try {
        let success = false;
        
        if (fileType === 'vrm') {
            success = await loadVRM(url);
        } else if (fileType === 'glb' || fileType === 'gltf') {
            success = await loadGLTF(url);
        }
        
        if (success) {
            loadingEl.style.display = 'none';
            controlsEl.style.display = 'block';
        } else {
            throw new Error('Failed to load model');
        }
    } catch (error) {
        console.error('Error loading model:', error);
        loadingEl.style.display = 'none';
        noModelEl.innerHTML = `<h2>Error loading model</h2><p>${error.message}</p>`;
        noModelEl.style.display = 'block';
    }
}

// Fetch models from API
async function fetchModels() {
    try {
        const response = await fetch('/api/models');
        models = await response.json();
        renderModelList(models);
        updateStats();
    } catch (error) {
        console.error('Error fetching models:', error);
        modelList.innerHTML = '<p style="padding: 20px; color: #e94560;">Error loading models</p>';
    }
}

// Get icon for file type
function getFileIcon(fileType) {
    if (fileType === 'vrm') return '🎭';
    if (fileType === 'glb' || fileType === 'gltf') return '🎨';
    if (fileType === 'fbx') return '📐';
    if (fileType === 'blend') return '🎬';
    if (ARCHIVE_FORMATS.includes(fileType)) return '📦';
    return '📄';
}

// Check if file type can be previewed
function canPreview(fileType) {
    return PREVIEWABLE_FORMATS.includes(fileType);
}

// Render model list
function renderModelList(modelsToRender) {
    modelList.innerHTML = modelsToRender.map(model => {
        const previewable = canPreview(model.file_type);
        const icon = getFileIcon(model.file_type);
        
        return `
        <div class="model-card ${previewable ? '' : 'no-preview'}" data-id="${model.id}" data-path="${model.file_path}" data-type="${model.file_type}">
            ${model.thumbnail_path 
                ? `<img class="model-thumb" src="/thumbnails/${model.thumbnail_path}" alt="${model.name}" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                   <div class="model-thumb fallback-icon" style="display:none;align-items:center;justify-content:center;font-size:2rem;">${icon}</div>`
                : `<div class="model-thumb" style="display:flex;align-items:center;justify-content:center;font-size:2rem;">${icon}</div>`
            }
            <div class="model-info">
                <div class="model-name" title="${model.name}">${model.name}</div>
                <div class="model-artist">by ${model.artist || 'Unknown'}</div>
                <div class="model-meta">
                    ${model.source} • ${model.file_type.toUpperCase()} • ${formatSize(model.size_bytes)}
                    ${previewable ? '' : ' • No preview'}
                </div>
            </div>
        </div>
    `}).join('');

    // Add click handlers
    document.querySelectorAll('.model-card').forEach(card => {
        card.addEventListener('click', () => {
            const fileType = card.dataset.type;
            const filePath = card.dataset.path;
            
            if (canPreview(fileType)) {
                document.querySelectorAll('.model-card').forEach(c => c.classList.remove('active'));
                card.classList.add('active');
                loadModel(filePath, fileType);
            } else {
                const msg = ARCHIVE_FORMATS.includes(fileType) 
                    ? `${fileType.toUpperCase()} archive - check extracted folder for 3D files`
                    : `${fileType.toUpperCase()} files need conversion to VRM/GLB for preview`;
                showNotification(msg);
            }
        });
    });
}

// Update stats
function updateStats() {
    const totalSize = models.reduce((sum, m) => sum + m.size_bytes, 0);
    const previewableCount = models.filter(m => canPreview(m.file_type)).length;
    statsEl.textContent = `${models.length} models (${previewableCount} previewable) • ${formatSize(totalSize)}`;
}

// Format file size
function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// Search functionality
searchInput.addEventListener('input', (e) => {
    const query = e.target.value.toLowerCase();
    const filtered = models.filter(m => 
        m.name.toLowerCase().includes(query) ||
        (m.artist && m.artist.toLowerCase().includes(query)) ||
        m.source.toLowerCase().includes(query) ||
        m.file_type.toLowerCase().includes(query)
    );
    renderModelList(filtered);
});

// Check for new models periodically
async function checkForNewModels() {
    try {
        const response = await fetch('/api/count');
        const data = await response.json();
        
        if (data.count !== lastModelCount) {
            console.log(`Model count changed: ${lastModelCount} -> ${data.count}`);
            lastModelCount = data.count;
            await fetchModels();
            showNotification(`${data.count} models available`);
        }
    } catch (error) {
        console.error('Error checking for new models:', error);
    }
}

// Show notification toast
function showNotification(message) {
    const existing = document.querySelector('.notification');
    if (existing) existing.remove();
    
    const notification = document.createElement('div');
    notification.className = 'notification';
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: #4ecca3;
        color: #1a1a2e;
        padding: 12px 20px;
        border-radius: 8px;
        font-weight: bold;
        z-index: 1000;
        animation: slideIn 0.3s ease;
        max-width: 400px;
    `;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transition = 'opacity 0.3s';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Start auto-refresh polling (every 10 seconds)
function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(checkForNewModels, 10000);
    console.log('Auto-refresh enabled (10s interval)');
}

// Initialize
initScene();
fetchModels().then(() => {
    lastModelCount = models.length;
    startAutoRefresh();
});
