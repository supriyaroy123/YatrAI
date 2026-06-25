/* ═══════════════════════════════════════════════════════════════
   YatrAI — Frontend Logic
   Leaflet map, API calls, result rendering, theme toggle
   ═══════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────────
let map;
let routeLayer = null;
let markersLayer = null;
let selectedVehicle = 'Car';
let lastPredictionData = null;     // cache to re-predict on vehicle change
let hasPredicted = false;          // track if user has predicted at least once
let currentTileLayer = null;       // track map tiles for theme swap

// ── DOM References ───────────────────────────────────────────────
const originInput = document.getElementById('origin-input');
const destInput = document.getElementById('destination-input');
const predictBtn = document.getElementById('predict-btn');
const loadingOverlay = document.getElementById('loading-overlay');
const resultsSection = document.getElementById('results-section');
const explanationSection = document.getElementById('explanation-section');
const weatherSection = document.getElementById('weather-section');
const errorToast = document.getElementById('error-toast');
const errorToastMsg = document.getElementById('error-toast-msg');

// ── Initialize ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initMap();
    initVehicleSelector();
    initKeyboardShortcuts();
    initDepartureTime();
    initAutoPredictOnChanges();
});

// ── Theme Toggle (Dark / Light) ──────────────────────────────────
function initTheme() {
    let saved = localStorage.getItem('yatrai-theme');
    if (!saved) {
        saved = 'dark';
        localStorage.setItem('yatrai-theme', 'dark');
    }
    document.documentElement.setAttribute('data-theme', saved);
    const btn = document.getElementById('theme-toggle');
    if (btn) {
        btn.addEventListener('click', toggleTheme);
        updateThemeIcon();
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('yatrai-theme', next);
    updateThemeIcon();
    updateMapTiles();
}

function updateThemeIcon() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    btn.innerHTML = isLight ? '🌙' : '☀️';
    btn.title = isLight ? 'Switch to Dark Mode' : 'Switch to Light Mode';
}

function getMapTileUrl() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    return isLight
        ? 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'
        : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
}

function updateMapTiles() {
    if (!map) return;
    if (currentTileLayer) {
        map.removeLayer(currentTileLayer);
    }
    currentTileLayer = L.tileLayer(getMapTileUrl(), {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 19,
    }).addTo(map);
}

// ── Initialize Map ───────────────────────────────────────────────
function initMap() {
    map = L.map('map', {
        center: [22.5, 78.5],
        zoom: 5,
        zoomControl: true,
        attributionControl: true,
    });

    currentTileLayer = L.tileLayer(getMapTileUrl(), {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 19,
    }).addTo(map);

    routeLayer = L.layerGroup().addTo(map);
    markersLayer = L.layerGroup().addTo(map);
}

// ── Vehicle Selector ─────────────────────────────────────────────
function initVehicleSelector() {
    const buttons = document.querySelectorAll('.vehicle-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            selectedVehicle = btn.dataset.vehicle;

            // Auto re-predict if a prediction was already done
            if (hasPredicted) {
                handlePredict();
            }
        });
    });
}

// ── Departure Time Initialization ────────────────────────────────
function initDepartureTime() {
    const departureTimeInput = document.getElementById('departure-time');
    if (departureTimeInput && !departureTimeInput.value) {
        const now = new Date();
        const hours = String(now.getHours()).padStart(2, '0');
        const minutes = String(now.getMinutes()).padStart(2, '0');
        departureTimeInput.value = `${hours}:${minutes}`;
        
        departureTimeInput.addEventListener('change', () => {
            if (hasPredicted) {
                handlePredict();
            }
        });
    }
}

// ── Auto Predict on Inputs Changes ──────────────────────────────
function initAutoPredictOnChanges() {
    const inputs = [originInput, destInput];
    inputs.forEach(input => {
        if (input) {
            input.addEventListener('change', () => {
                if (hasPredicted) {
                    handlePredict();
                }
            });
        }
    });

    const customMileageInput = document.getElementById('custom-mileage-input');
    if (customMileageInput) {
        customMileageInput.addEventListener('change', () => {
            if (hasPredicted) {
                handlePredict();
            }
        });
        customMileageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                handlePredict();
            }
        });
    }
}

// ── Fuel Mode Toggle ──────────────────────────────────────────────
window.toggleFuelMode = function(mode) {
    const customGroup = document.getElementById('custom-mileage-group');
    if (customGroup) {
        if (mode === 'custom') {
            customGroup.classList.remove('hidden');
            const customInput = document.getElementById('custom-mileage-input');
            if (customInput) customInput.focus();
        } else {
            customGroup.classList.add('hidden');
        }
    }

    if (hasPredicted) {
        handlePredict();
    }
};

// ── Keyboard Shortcuts ───────────────────────────────────────────
function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.target === originInput || e.target === destInput)) {
            handlePredict();
        }
    });
}

// ── Main Predict Handler ─────────────────────────────────────────
async function handlePredict() {
    const origin = originInput.value.trim();
    const destination = destInput.value.trim();
    const departureTimeInput = document.getElementById('departure-time');
    const departureTime = departureTimeInput ? departureTimeInput.value : '';

    if (!origin || !destination) {
        showToast('Please enter both origin and destination');
        return;
    }

    // Get Fuel Mode inputs
    const fuelModeEl = document.querySelector('input[name="fuel-mode"]:checked');
    const fuelMode = fuelModeEl ? fuelModeEl.value : 'average';
    const customMileageInput = document.getElementById('custom-mileage-input');
    const customMileageVal = customMileageInput ? parseFloat(customMileageInput.value) : null;

    if (fuelMode === 'custom') {
        if (isNaN(customMileageVal) || customMileageVal <= 0) {
            showToast('Please enter a valid mileage (km/L)');
            if (customMileageInput) customMileageInput.focus();
            return;
        }
    }

    showLoading(true);

    try {
        const response = await fetch('/predict', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                origin: origin,
                destination: destination,
                vehicle_type: selectedVehicle,
                departure_time: departureTime,
                fuel_mode: fuelMode,
                custom_mileage: fuelMode === 'custom' ? customMileageVal : null
            }),
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || `Server error: ${response.status}`);
        }

        const data = await response.json();
        lastPredictionData = data;
        hasPredicted = true;

        updateResults(data);
        updateMap(data);
        updateExplanation(data);
        updateWeather(data.weather);

        // Scroll to results
        setTimeout(() => {
            resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 300);

    } catch (err) {
        showToast(err.message || 'Failed to get prediction. Please try again.');
    } finally {
        showLoading(false);
    }
}

// ── Update Results Cards ─────────────────────────────────────────
function updateResults(data) {
    resultsSection.classList.remove('hidden');

    // Route summary
    const summary = document.getElementById('route-summary');
    summary.innerHTML = `
        <strong>${data.origin.name}</strong> → <strong>${data.destination.name}</strong>
        &nbsp;·&nbsp; ${data.route.distance_km} km &nbsp;·&nbsp; ${data.vehicle_type}
    `;

    // Congestion card
    const congLevel = data.congestion.level;
    const congCard = document.getElementById('congestion-card');
    const congValue = document.getElementById('congestion-level');

    const congestionLabels = {
        'Free-flow': 'Smooth Traffic',
        'Moderate': 'Moderate Traffic',
        'Heavy': 'Heavy Traffic',
        'Gridlock': 'Stalled / Gridlock'
    };
    const friendlyCongestion = congestionLabels[congLevel] || congLevel;

    const congClass = getSeverityClass(congLevel);
    congValue.textContent = friendlyCongestion;
    congValue.className = 'card-value ' + congClass;
    congCard.className = 'result-card border-' + congLevel.toLowerCase().replace('-', '');

    const congColor = getSeverityColor(congLevel);
    document.getElementById('congestion-icon-bg').style.background = `${congColor}22`;

    // ETA card
    const etaValue = document.getElementById('eta-value');
    const etaDetail = document.getElementById('eta-detail');
    etaValue.textContent = formatTime(data.travel_time.eta_minutes);
    etaDetail.innerHTML = `
        <div style="margin-bottom: 4px;"><strong>Departure:</strong> ${data.travel_time.departure_time} &nbsp;·&nbsp; <strong>Arrival:</strong> ${data.travel_time.arrival_time}</div>
        <div style="opacity: 0.8;">Typical: ${formatTime(data.travel_time.base_minutes)} · Delay: +${formatTime(data.travel_time.delay_minutes)}</div>
    `;

    // Accident risk card
    const riskLevel = data.accident_risk.level;
    const riskCard = document.getElementById('risk-card');
    const riskValue = document.getElementById('risk-level');
    const riskReasons = document.getElementById('risk-reasons');

    const riskLabels = {
        'Low': 'Safe Conditions',
        'Medium': 'Caution Advised',
        'High': 'High Risk / Alert'
    };
    const friendlyRisk = riskLabels[riskLevel] || riskLevel;

    const riskClass = getSeverityClass(riskLevel);
    riskValue.textContent = friendlyRisk;
    riskValue.className = 'card-value ' + riskClass;
    riskCard.className = 'result-card border-' + riskLevel.toLowerCase();

    const riskColor = getSeverityColor(riskLevel);
    document.getElementById('risk-icon-bg').style.background = `${riskColor}22`;

    // Risk reasons
    riskReasons.innerHTML = '';
    if (data.accident_risk.reasons) {
        data.accident_risk.reasons.forEach(reason => {
            const tag = document.createElement('span');
            tag.className = 'risk-tag';
            tag.textContent = reason;
            riskReasons.appendChild(tag);
        });
    }

    // AQI card
    const aqiValue = document.getElementById('aqi-value');
    const aqiCategory = document.getElementById('aqi-category');
    const aqiCard = document.getElementById('aqi-card');

    if (data.aqi.aqi >= 0) {
        aqiValue.textContent = data.aqi.aqi;
        aqiCategory.textContent = data.aqi.category;
        aqiValue.style.color = data.aqi.color;
        aqiCard.style.borderLeftColor = data.aqi.color;
        document.getElementById('aqi-icon-bg').style.background = `${data.aqi.color}22`;
    } else {
        aqiValue.textContent = 'N/A';
        aqiCategory.textContent = 'AQI data unavailable';
    }

    // Fuel Cost Estimation Card
    const fuelCard = document.getElementById('fuel-analytics-card');
    if (data.fuel_estimation && fuelCard) {
        fuelCard.classList.remove('hidden');

        document.getElementById('fuel-distance-val').textContent = `${data.fuel_estimation.distance_km} km`;
        document.getElementById('fuel-vehicle-val').textContent = data.fuel_estimation.vehicle_type;
        document.getElementById('fuel-mileage-val').textContent = `${data.fuel_estimation.mileage_used} km/L`;
        document.getElementById('fuel-needed-val').textContent = `${data.fuel_estimation.fuel_needed_liters} L`;
        document.getElementById('fuel-cost-val').textContent = `₹${data.fuel_estimation.fuel_cost_rupees}`;
        
        const trafficImpactVal = document.getElementById('fuel-traffic-impact-val');
        const trafficImpactCard = document.getElementById('fuel-traffic-impact-card');
        const trafficImpactPct = data.fuel_estimation.traffic_impact_percent;
        
        trafficImpactVal.textContent = `+${trafficImpactPct}%`;
        
        if (trafficImpactPct === 0) {
            trafficImpactVal.textContent = '0% (None)';
            trafficImpactVal.style.color = 'var(--color-green)';
            trafficImpactCard.style.borderColor = 'var(--border-glass)';
        } else if (trafficImpactPct <= 10) {
            trafficImpactVal.style.color = 'var(--color-yellow)';
            trafficImpactCard.style.borderColor = 'rgba(255, 234, 0, 0.2)';
        } else if (trafficImpactPct <= 25) {
            trafficImpactVal.style.color = 'var(--color-orange)';
            trafficImpactCard.style.borderColor = 'rgba(255, 145, 0, 0.2)';
        } else {
            trafficImpactVal.style.color = 'var(--color-red)';
            trafficImpactCard.style.borderColor = 'rgba(255, 23, 68, 0.2)';
        }

        const vehIcons = {
            'scooter': '🛵',
            'motorcycle': '🏍️',
            'car': '🚗',
            'suv': '🚙',
            'auto rickshaw': '🛺',
            'bus': '🚌',
            'truck': '🚛'
        };
        const vehTypeLower = data.fuel_estimation.vehicle_type.toLowerCase();
        const vehIcon = vehIcons[vehTypeLower] || '🚗';
        document.getElementById('fuel-vehicle-icon').textContent = vehIcon;

        document.getElementById('fuel-mode-badge').textContent = data.fuel_estimation.fuel_mode;

        const fuelInsightText = document.getElementById('fuel-insight-text');
        if (data.ai_summary && data.ai_summary.fuel_insight) {
            fuelInsightText.textContent = data.ai_summary.fuel_insight;
        } else {
            fuelInsightText.textContent = 'No traffic impact on fuel. Smooth flow conditions will help optimize efficiency.';
        }
    } else if (fuelCard) {
        fuelCard.classList.add('hidden');
    }

    // AI Travel Assistant
    if (data.ai_summary) {
        document.getElementById('ai-summary-text').textContent = data.ai_summary.summary || '—';
        document.getElementById('ai-travel-rec').textContent = data.ai_summary.travel_recommendation || '—';
        document.getElementById('ai-safety-rec').textContent = data.ai_summary.safety_recommendation || '—';
        
        const weatherAlert = data.ai_summary.weather_alert;
        const weatherAlertEl = document.getElementById('ai-weather-alert');
        const weatherAlertWrapper = document.getElementById('ai-weather-alert-wrapper');
        
        if (weatherAlert && weatherAlert.trim() !== '') {
            weatherAlertEl.textContent = weatherAlert;
            weatherAlertWrapper.style.display = 'flex';
        } else {
            weatherAlertWrapper.style.display = 'none';
        }
    }

    // Stagger card reveal animations
    const cards = document.querySelectorAll('.result-card, .ai-assistant-panel, .fuel-analytics-panel');
    cards.forEach((card, i) => {
        card.classList.remove('visible');
        setTimeout(() => {
            card.style.transition = `opacity 0.5s ease ${i * 0.12}s, transform 0.5s ease ${i * 0.12}s`;
            card.classList.add('visible');
        }, 50);
    });
}

// ── Update Map ───────────────────────────────────────────────────
function updateMap(data) {
    routeLayer.clearLayers();
    markersLayer.clearLayers();

    const geometry = data.route.geometry;
    if (!geometry || !geometry.coordinates) return;

    // GeoJSON coordinates are [lon, lat], Leaflet needs [lat, lon]
    const coords = geometry.coordinates.map(c => [c[1], c[0]]);

    // Draw route polyline
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const polyline = L.polyline(coords, {
        color: isLight ? '#5b21b6' : '#00d4ff',
        weight: 4,
        opacity: 0.85,
        smoothFactor: 1.5,
        dashArray: '12, 8',
        dashOffset: '0',
    });
    routeLayer.addLayer(polyline);

    // Animate dash offset
    let offset = 0;
    const animateDash = () => {
        offset -= 0.5;
        polyline.setStyle({ dashOffset: String(offset) });
        requestAnimationFrame(animateDash);
    };
    animateDash();

    // Origin marker (green)
    const originMarker = L.circleMarker([data.origin.lat, data.origin.lon], {
        radius: 10,
        fillColor: '#00e676',
        color: isLight ? '#333' : '#fff',
        weight: 2,
        fillOpacity: 0.9,
    }).bindPopup(`<strong>Origin:</strong> ${data.origin.display_name}`);
    markersLayer.addLayer(originMarker);

    // Destination marker (red/pink)
    const destMarker = L.circleMarker([data.destination.lat, data.destination.lon], {
        radius: 10,
        fillColor: '#ff2d95',
        color: isLight ? '#333' : '#fff',
        weight: 2,
        fillOpacity: 0.9,
    }).bindPopup(`<strong>Destination:</strong> ${data.destination.display_name}`);
    markersLayer.addLayer(destMarker);

    // Fit map bounds
    const bounds = L.latLngBounds(coords);
    map.fitBounds(bounds, { padding: [50, 50] });
}

// ── Update Route Insights ────────────────────────────────────────
function updateExplanation(fullData) {
    const container = document.getElementById('insights-list');
    container.innerHTML = '';

    if (!fullData) {
        explanationSection.classList.add('hidden');
        return;
    }

    const insights = [];

    // 1. Traffic insights
    const congLevel = fullData.congestion.level;
    if (congLevel === 'Free-flow') {
        insights.push({
            icon: '🟢',
            text: 'Traffic is moving smoothly. You should experience minimal delays on this route.',
            type: 'success'
        });
    } else if (congLevel === 'Moderate') {
        insights.push({
            icon: '🟡',
            text: 'Moderate traffic. Expect typical city intersections and local bottlenecks.',
            type: 'warning'
        });
    } else if (congLevel === 'Heavy') {
        insights.push({
            icon: '🟠',
            text: 'Heavy traffic congestion. Extended delays expected near busy junctions.',
            type: 'warning'
        });
    } else if (congLevel === 'Gridlock') {
        insights.push({
            icon: '🔴',
            text: 'Severe gridlock/stalled traffic. Seek alternative routes if possible, as speeds are extremely slow.',
            type: 'danger'
        });
    }

    // 2. Vehicle-specific travel insights
    const vehicle = fullData.vehicle_type;
    const delay = fullData.travel_time.delay_minutes;
    if (vehicle === 'Bike' && delay > 5) {
        insights.push({
            icon: '🏍️',
            text: 'Two-wheeler selected: Lane splitting allowed. You can bypass major car gridlocks, saving substantial delay time.',
            type: 'success'
        });
    } else if (vehicle === 'Auto') {
        insights.push({
            icon: '🛺',
            text: 'Auto-rickshaw selected: High maneuverability in narrow lanes, but top speed is restricted to 40-50 km/h.',
            type: 'success'
        });
    } else if ((vehicle === 'Bus' || vehicle === 'Truck') && congLevel !== 'Free-flow') {
        insights.push({
            icon: '🚛',
            text: 'Heavy vehicle selected: Size and acceleration restrictions will amplify delays in congested zones.',
            type: 'warning'
        });
    }

    // 3. Weather & Waterlogging warnings
    const rain = fullData.weather ? fullData.weather.rain_mm : 0;
    const visibility = fullData.weather ? fullData.weather.visibility_km : 10;
    if (rain > 0) {
        if (rain > 8) {
            insights.push({
                icon: '🌊',
                text: `Heavy rain (${rain} mm) reported. High risk of waterlogged streets and open potholes. Reduce speed!`,
                type: 'danger'
            });
        } else {
            insights.push({
                icon: '🌧️',
                text: `Wet roads/rain (${rain} mm). Braking distance will increase. Watch out for sudden two-wheeler maneuvers.`,
                type: 'warning'
            });
        }
    }
    if (visibility < 4) {
        insights.push({
            icon: '🌫️',
            text: `Low visibility (${visibility} km) due to fog/dust. Turn on low-beam headlights and maintain safe spacing.`,
            type: 'warning'
        });
    }

    // 4. Air Quality advisory
    const aqi = fullData.aqi ? fullData.aqi.aqi : -1;
    if (aqi > 100) {
        if (aqi > 200) {
            insights.push({
                icon: '😷',
                text: `Hazardous air quality (AQI: ${aqi}) at destination. Keep vehicle windows closed and set AC to recycle mode.`,
                type: 'danger'
            });
        } else {
            insights.push({
                icon: '🌬️',
                text: `Poor air quality (AQI: ${aqi}) detected. Sensitive travelers should take appropriate precautions.`,
                type: 'warning'
            });
        }
    }

    // 5. Road Safety warnings
    const safetyReasons = fullData.accident_risk.reasons || [];
    if (safetyReasons.length > 0) {
        const warningsList = safetyReasons.map(r => r.toLowerCase()).join(', ');
        insights.push({
            icon: '⚠️',
            text: `Caution: Travel risks active on this route — ${warningsList}. Drive defensively.`,
            type: 'warning'
        });
    } else if (congLevel === 'Free-flow' && rain === 0) {
        insights.push({
            icon: '🛡️',
            text: 'Excellent driving conditions. No severe weather or safety alerts on this route.',
            type: 'success'
        });
    }

    // Render insights
    if (insights.length > 0) {
        explanationSection.classList.remove('hidden');
        insights.forEach((insight) => {
            const li = document.createElement('li');
            li.className = `insight-item ${insight.type}`;
            li.innerHTML = `
                <span class="insight-icon">${insight.icon}</span>
                <span class="insight-text">${insight.text}</span>
            `;
            container.appendChild(li);
        });
    } else {
        explanationSection.classList.add('hidden');
    }
}

// ── Update Weather ───────────────────────────────────────────────
function updateWeather(weather) {
    if (!weather) {
        weatherSection.classList.add('hidden');
        return;
    }

    weatherSection.classList.remove('hidden');
    const grid = document.getElementById('weather-grid');
    grid.innerHTML = `
        <div class="weather-item">
            <span class="weather-item-icon">🌡️</span>
            <div>
                <div class="weather-item-value">${weather.temp_c}°C</div>
                <div class="weather-item-label">Temperature</div>
            </div>
        </div>
        <div class="weather-item">
            <span class="weather-item-icon">🌧️</span>
            <div>
                <div class="weather-item-value">${weather.rain_mm} mm</div>
                <div class="weather-item-label">Rainfall</div>
            </div>
        </div>
        <div class="weather-item">
            <span class="weather-item-icon">👁️</span>
            <div>
                <div class="weather-item-value">${weather.visibility_km} km</div>
                <div class="weather-item-label">Visibility</div>
            </div>
        </div>
        <div class="weather-item">
            <span class="weather-item-icon">💨</span>
            <div>
                <div class="weather-item-value">${weather.wind_speed_kmh} km/h</div>
                <div class="weather-item-label">Wind Speed</div>
            </div>
        </div>
        <div class="weather-item">
            <span class="weather-item-icon">💧</span>
            <div>
                <div class="weather-item-value">${weather.humidity}%</div>
                <div class="weather-item-label">Humidity</div>
            </div>
        </div>
    `;
}

// ── Helpers ──────────────────────────────────────────────────────
function formatTime(minutes) {
    if (minutes == null || isNaN(minutes)) return '—';
    const m = Math.round(minutes);
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    const rem = m % 60;
    return rem > 0 ? `${h} hr ${rem} min` : `${h} hr`;
}

function getSeverityClass(level) {
    const map = {
        'Free-flow': 'severity-freeflow',
        'Moderate': 'severity-moderate',
        'Heavy': 'severity-heavy',
        'Gridlock': 'severity-gridlock',
        'Low': 'severity-low',
        'Medium': 'severity-medium',
        'High': 'severity-high',
    };
    return map[level] || '';
}

function getSeverityColor(level) {
    const map = {
        'Free-flow': '#00e676',
        'Moderate': '#ffea00',
        'Heavy': '#ff9100',
        'Gridlock': '#ff1744',
        'Low': '#00e676',
        'Medium': '#ff9100',
        'High': '#ff1744',
    };
    return map[level] || '#00d4ff';
}

// ── Loading ──────────────────────────────────────────────────────
function showLoading(show) {
    if (show) {
        loadingOverlay.classList.remove('hidden');
    } else {
        loadingOverlay.classList.add('hidden');
    }
}

// ── Toast Notifications ──────────────────────────────────────────
function showToast(message) {
    errorToastMsg.textContent = message;
    errorToast.classList.remove('hidden');
    setTimeout(() => hideToast(), 6000);
}

function hideToast() {
    errorToast.classList.add('hidden');
}
