/* ═══════════════════════════════════════════════════════════════
   YatrAI — Frontend Logic
   Leaflet map, API calls, result rendering, theme toggle
   ═══════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────────
let googleMap;
let routePolyline = null;
let trafficLayer = null;
let autocompleteOrigin;
let autocompleteDestination;
let currentMarkers = [];
window._originLat = null;
window._originLng = null;
window._destLat = null;
window._destLng = null;
let trafficEnabled = false;
let _lastRouteData = null; // cache for route redraw on traffic toggle
let mapInitialized = false; // guard to prevent double-init

let selectedVehicle = 'Car';
let lastPredictionData = null;     // cache to re-predict on vehicle change
let hasPredicted = false;          // track if user has predicted at least once

// ── DOM References ───────────────────────────────────────────────
const originInput = document.getElementById('loc-start');
const destInput = document.getElementById('loc-end');
const predictBtn = document.getElementById('predict-btn');
const loadingOverlay = document.getElementById('loading-overlay');
const resultsSection = document.getElementById('screen-results');
const explanationSection = document.getElementById('explanation-section');
const weatherSection = document.getElementById('weather-section');
const errorToast = document.getElementById('error-toast');
const errorToastMsg = document.getElementById('error-toast-msg');

// ── Initialize ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTheme();

    // ── Ensure inputs are always usable regardless of Maps API state ──

    // If Google Maps already loaded synchronously, init map now
    if (typeof google !== 'undefined' && google.maps) {
        try { initMap(); } catch(e) { console.warn('Maps init error:', e); }
    }
    // else initMap() will be called by Maps API callback (see script tag)
    initVehicleSelector();
    initKeyboardShortcuts();
    initDepartureTime();
    initAutoPredictOnChanges();
    initResultsNavigation();
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

function initResultsNavigation() {
    const tabBtns = document.querySelectorAll('.tab-nav-btn');
    const panels = document.querySelectorAll('.tab-panel-content');
    const backBtn = document.getElementById('back-btn');
    const headerLogo = document.getElementById('header-logo-icon');
    const headerTagline = document.getElementById('header-tagline');
    const screenForm = document.getElementById('screen-form');
    const screenResults = document.getElementById('screen-results');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.dataset.tab;
            
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            panels.forEach(panel => {
                if (panel.id === `tab-${targetTab}`) {
                    panel.classList.remove('hidden');
                    panel.classList.add('active');
                } else {
                    panel.classList.add('hidden');
                    panel.classList.remove('active');
                }
            });

            // Refresh map size if the overview tab becomes active
            if (targetTab === 'overview' && googleMap) {
                setTimeout(() => {
                    google.maps.event.trigger(googleMap, 'resize');
                }, 50);
            }
        });
    });

    if (backBtn) {
        backBtn.addEventListener('click', () => {
            if (screenResults) screenResults.classList.add('hidden');
            if (screenForm) screenForm.classList.remove('hidden');
            backBtn.classList.add('hidden');
            if (headerLogo) headerLogo.classList.remove('hidden');
            if (headerTagline) {
                headerTagline.textContent = 'Smart traffic intelligence for Indian roads';
            }
            hasPredicted = false;
        });
    }
}

const darkMapStyle = [
    { elementType: "geometry", stylers: [{ color: "#0B0F1F" }] },
    { elementType: "labels.text.stroke", stylers: [{ color: "#0B0F1F" }] },
    { elementType: "labels.text.fill", stylers: [{ color: "#8b949e" }] },
    { featureType: "administrative.locality", elementType: "labels.text.fill", stylers: [{ color: "#58a6ff" }] },
    { featureType: "poi", elementType: "labels.text.fill", stylers: [{ color: "#8b949e" }] },
    { featureType: "poi.park", elementType: "geometry", stylers: [{ color: "#091c28" }] },
    { featureType: "poi.park", elementType: "labels.text.fill", stylers: [{ color: "#488258" }] },
    { featureType: "road", elementType: "geometry", stylers: [{ color: "#161b30" }] },
    { featureType: "road", elementType: "geometry.stroke", stylers: [{ color: "#21263d" }] },
    { featureType: "road", elementType: "labels.text.fill", stylers: [{ color: "#8b949e" }] },
    { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#1f293d" }] },
    { featureType: "road.highway", elementType: "geometry.stroke", stylers: [{ color: "#28354e" }] },
    { featureType: "road.highway", elementType: "labels.text.fill", stylers: [{ color: "#f3f4f6" }] },
    { featureType: "transit", elementType: "geometry", stylers: [{ color: "#1f293d" }] },
    { featureType: "water", elementType: "geometry", stylers: [{ color: "#070b19" }] },
    { featureType: "water", elementType: "labels.text.fill", stylers: [{ color: "#58a6ff" }] }
];

const lightMapStyle = [
    { elementType: "geometry", stylers: [{ color: "#f5f5f5" }] },
    { elementType: "labels.text.fill", stylers: [{ color: "#616161" }] },
    { elementType: "labels.text.stroke", stylers: [{ color: "#f5f5f5" }] },
    { featureType: "road", elementType: "geometry", stylers: [{ color: "#ffffff" }] },
    { featureType: "water", elementType: "geometry", stylers: [{ color: "#e9e9e9" }] }
];

function updateMapTiles() {
    if (!googleMap) return;
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    googleMap.setOptions({
        styles: isLight ? lightMapStyle : darkMapStyle
    });
}

// ── Initialize Map ───────────────────────────────────────────────
window.initMap = function initMap() {
    if (mapInitialized) return;
    mapInitialized = true;

    try {
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        const mapOptions = {
            center: { lat: 20.5937, lng: 78.9629 }, // Center of India
            zoom: 5,
            styles: isLight ? lightMapStyle : darkMapStyle,
            zoomControl: true,
            streetViewControl: false,
            mapTypeControl: false,
            fullscreenControl: false
        };

        const mapEl = document.getElementById('map');
        if (mapEl) {
            googleMap = new google.maps.Map(mapEl, mapOptions);
        }

        // Try to init Places autocomplete — but NEVER let it break the form
        try { initAutocomplete(); } catch(e) { console.warn('Places autocomplete unavailable:', e); }

    } catch(e) {
        console.warn('Google Maps failed to initialize — map features disabled:', e);
    }
};

function initAutocomplete() {
    if (!google || !google.maps || !google.maps.places) {
        console.warn('Places API not available — using plain text input');
        return;
    }

    autocompleteOrigin = new google.maps.places.Autocomplete(originInput, {
        componentRestrictions: { country: "in" },
        fields: ["formatted_address", "geometry"]
    });
    autocompleteDestination = new google.maps.places.Autocomplete(destInput, {
        componentRestrictions: { country: "in" },
        fields: ["formatted_address", "geometry"]
    });

    // Removing the autocomplete attribute override so Chrome's contact chip doesn't reappear


    // Listeners for selection
    autocompleteOrigin.addListener("place_changed", () => {
        const place = autocompleteOrigin.getPlace();
        if (place.geometry && place.geometry.location) {
            window._originLat = place.geometry.location.lat();
            window._originLng = place.geometry.location.lng();
        }
    });
    autocompleteDestination.addListener("place_changed", () => {
        const place = autocompleteDestination.getPlace();
        if (place.geometry && place.geometry.location) {
            window._destLat = place.geometry.location.lat();
            window._destLng = place.geometry.location.lng();
        }
    });

    // Reset coordinates if user types manually to force geocoding fallback
    originInput.addEventListener("input", () => {
        window._originLat = null;
        window._originLng = null;
    });
    destInput.addEventListener("input", () => {
        window._destLat = null;
        window._destLng = null;
    });
}

// ── Google Maps Geocoding & Traffic Helpers ───────────────────────
async function geocodeAddress(address) {
    const geocoder = new google.maps.Geocoder();
    return new Promise((resolve, reject) => {
        geocoder.geocode({ address: address, componentRestrictions: { country: "in" } }, (results, status) => {
            if (status === "OK" && results && results[0]) {
                const loc = results[0].geometry.location;
                resolve({ lat: loc.lat(), lng: loc.lng(), formatted: results[0].formatted_address });
            } else {
                reject(new Error(`Geocoding status: ${status}`));
            }
        });
    });
}

window.toggleTraffic = function() {
    trafficEnabled = !trafficEnabled;
    const btn = document.getElementById('traffic-toggle');
    const label = btn ? btn.querySelector('span') : null;
    
    if (trafficEnabled) {
        if (!trafficLayer) {
            trafficLayer = new google.maps.TrafficLayer();
        }
        trafficLayer.setMap(googleMap);
        if (btn) btn.classList.add('active');
        if (label) label.textContent = 'Traffic: On';
    } else {
        if (trafficLayer) {
            trafficLayer.setMap(null);
        }
        if (btn) btn.classList.remove('active');
        if (label) label.textContent = 'Traffic: Off';
    }

    // Redraw route with or without traffic model so the polyline reflects
    // the chosen mode (traffic-aware vs fastest no-traffic path)
    if (_lastRouteData) {
        drawRoute(_lastRouteData);
    }
};

function getCongestionColor(level) {
    const colors = {
        'Free-flow': '#5DCAA5',
        'Moderate': '#EF9F27',
        'Heavy': '#D4537E',
        'Gridlock': '#E24B4A'
    };
    return colors[level] || '#2563eb';
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

    let originLat = window._originLat;
    let originLng = window._originLng;
    let destLat = window._destLat;
    let destLng = window._destLng;

    try {
        if (!originLat || !originLng) {
            const geo = await geocodeAddress(origin);
            originLat = geo.lat;
            originLng = geo.lng;
            window._originLat = originLat;
            window._originLng = originLng;
        }
        if (!destLat || !destLng) {
            const geo = await geocodeAddress(destination);
            destLat = geo.lat;
            destLng = geo.lng;
            window._destLat = destLat;
            window._destLng = destLng;
        }
    } catch (geoErr) {
        console.warn("Google Geocoding failed, falling back to string query:", geoErr);
    }

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
                custom_mileage: fuelMode === 'custom' ? customMileageVal : null,
                // Pass coordinates resolved by Google Maps to skip backend Nominatim geocoding
                origin_lat: originLat || null,
                origin_lon: originLng || null,
                dest_lat: destLat || null,
                dest_lon: destLng || null,
            }),
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || `Server error: ${response.status}`);
        }

        const data = await response.json();
        lastPredictionData = data;
        hasPredicted = true;

        // Toggle screen visibility
        const screenForm = document.getElementById('screen-form');
        const backBtn = document.getElementById('back-btn');
        const headerLogo = document.getElementById('header-logo-icon');
        const headerTagline = document.getElementById('header-tagline');

        if (screenForm) screenForm.classList.add('hidden');
        if (resultsSection) resultsSection.classList.remove('hidden');
        if (backBtn) backBtn.classList.remove('hidden');
        if (headerLogo) headerLogo.classList.add('hidden');
        
        if (headerTagline) {
            headerTagline.textContent = `${data.origin.name} → ${data.destination.name} · ${data.route.distance_km} km · ${data.vehicle_type}`;
        }

        // Reset to Overview tab upon new prediction
        const tabBtns = document.querySelectorAll('.tab-nav-btn');
        const panels = document.querySelectorAll('.tab-panel-content');
        tabBtns.forEach(btn => {
            if (btn.dataset.tab === 'overview') btn.classList.add('active');
            else btn.classList.remove('active');
        });
        panels.forEach(panel => {
            if (panel.id === 'tab-overview') {
                panel.classList.remove('hidden');
                panel.classList.add('active');
            } else {
                panel.classList.add('hidden');
                panel.classList.remove('active');
            }
        });

        updateResults(data);
        updateMap(data);
        updateExplanation(data);
        updateWeather(data.weather);

        // Save prediction history to Firestore
        try {
            if (window.firebaseAuth && window.firebaseDb && window.firebaseOps && window.firebaseAuth.currentUser) {
                const uid = window.firebaseAuth.currentUser.uid;
                const { collection, addDoc, serverTimestamp } = window.firebaseOps;
                
                const historyData = {
                    timestamp: serverTimestamp(),
                    origin: data.origin.name,
                    originDisplay: data.origin.display_name,
                    destination: data.destination.name,
                    destinationDisplay: data.destination.display_name,
                    vehicle: data.vehicle_type,
                    eta: formatTime(data.travel_time.eta_minutes),
                    etaMinutes: data.travel_time.eta_minutes,
                    traffic: data.congestion.level,
                    aqi: data.aqi.aqi || 0,
                    weather: `${data.weather.temp_c}°C · ${data.weather.rain_mm} mm rain`,
                    weatherFull: data.weather,
                    fuelCost: `₹${data.fuel_estimation.fuel_cost_rupees}`,
                    co2: `${data.sustainability_analytics.co2_emission_kg} kg`,
                    accidentRisk: data.accident_risk.level,
                    distance: `${data.route.distance_km} km`,
                    predictionJson: JSON.stringify(data)
                };
                
                await addDoc(collection(window.firebaseDb, "users", uid, "history"), historyData);
                console.log("Prediction history saved to Firestore!");
            }
        } catch (dbErr) {
            console.error("Failed to save history to Firestore:", dbErr);
        }

        // Refresh Google map layout since it was loaded hidden
        if (googleMap) {
            setTimeout(() => {
                google.maps.event.trigger(googleMap, 'resize');
            }, 100);
        }

        // Scroll layout to top
        window.scrollTo({ top: 0, behavior: 'smooth' });

        // ── Async AI Insights (non-blocking, fetched after fast main result) ──
        // Fire and forget — patches AI text fields when Gemini responds
        fetchAndInjectInsights(data).catch(e => console.warn('Insights fetch failed:', e));

    } catch (err) {
        showToast(err.message || 'Failed to get prediction. Please try again.');
    } finally {
        showLoading(false);
    }
}

// ── Async AI Insights Fetcher ────────────────────────────────────────
async function fetchAndInjectInsights(data) {
    const payload = {
        origin: data.origin.name,
        destination: data.destination.name,
        vehicle_type: data.vehicle_type,
        congestion_level: data.congestion.level,
        confidence: data.congestion.confidence || 0.5,
        eta_minutes: data.travel_time.eta_minutes,
        accident_risk: data.accident_risk.level,
        aqi: data.aqi.aqi || -1,
        temp_c: data.weather.temp_c,
        rain_mm: data.weather.rain_mm,
        visibility_km: data.weather.visibility_km,
        departure_time: null,
        fuel_needed_liters: data.fuel_estimation.fuel_needed_liters,
        fuel_cost_rupees: data.fuel_estimation.fuel_cost_rupees,
        traffic_impact_percent: data.fuel_estimation.traffic_impact_percent,
        co2_emission_kg: data.sustainability_analytics.co2_emission_kg,
    };

    const res = await fetch('/predict/insights', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!res.ok) return;
    const insights = await res.json();

    // Inject AI Travel Summary (AI insights tab)
    const summaryEl = document.getElementById('ai-summary-text');
    if (summaryEl && insights.summary) summaryEl.textContent = insights.summary;

    const travelRecEl = document.getElementById('ai-travel-rec');
    if (travelRecEl && insights.travel_recommendation) travelRecEl.textContent = insights.travel_recommendation;

    const safetyRecEl = document.getElementById('ai-safety-rec');
    if (safetyRecEl && insights.safety_recommendation) safetyRecEl.textContent = insights.safety_recommendation;

    // Inject fuel insight into Cost & Eco tab
    const fuelInsightEl = document.getElementById('fuel-insight-text');
    if (fuelInsightEl && insights.fuel_insight) fuelInsightEl.textContent = insights.fuel_insight;

    // Inject sustainability insight
    const sustainInsightEl = document.getElementById('sustain-ai-insight-text');
    if (sustainInsightEl && insights.sustainability_insight) sustainInsightEl.textContent = insights.sustainability_insight;
}

// ── Update Results Cards ─────────────────────────────────────────
function updateResults(data) {
    resultsSection.classList.remove('hidden');

    // Route summary
    const summary = document.getElementById('route-summary');
    if (summary) {
        summary.innerHTML = `
            <strong>${data.origin.name}</strong> → <strong>${data.destination.name}</strong>
            &nbsp;·&nbsp; ${data.route.distance_km} km &nbsp;·&nbsp; ${data.vehicle_type}
        `;
    }

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
    congCard.className = 'compact-card border-' + congLevel.toLowerCase().replace('-', '');

    const congColor = getSeverityColor(congLevel);
    document.getElementById('congestion-icon-bg').style.background = `${congColor}22`;

    // ETA card
    const etaValue = document.getElementById('eta-value');
    const etaDetail = document.getElementById('eta-detail');
    etaValue.textContent = formatTime(data.travel_time.eta_minutes);
    etaDetail.innerHTML = `
        <div class="eta-grid">
            <div class="eta-grid-cell"><strong>Departure:</strong> ${data.travel_time.departure_time}</div>
            <div class="eta-grid-cell"><strong>Arrival:</strong> ${data.travel_time.arrival_time}</div>
            <div class="eta-grid-cell opacity-soft">Typical: ${formatTime(data.travel_time.base_minutes)}</div>
            <div class="eta-grid-cell opacity-soft">Delay: +${formatTime(data.travel_time.delay_minutes)}</div>
        </div>
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
    riskCard.className = 'compact-card border-' + riskLevel.toLowerCase();

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
        const fuelVehIconEl = document.getElementById('fuel-vehicle-icon');
        if (fuelVehIconEl) fuelVehIconEl.textContent = vehIcon;

        const fuelModeBadgeEl = document.getElementById('fuel-mode-badge');
        if (fuelModeBadgeEl) fuelModeBadgeEl.textContent = data.fuel_estimation.fuel_mode;

        const fuelInsightText = document.getElementById('fuel-insight-text');
        if (data.ai_summary && data.ai_summary.fuel_insight) {
            fuelInsightText.textContent = data.ai_summary.fuel_insight;
        } else {
            fuelInsightText.textContent = 'No traffic impact on fuel. Smooth flow conditions will help optimize efficiency.';
        }
    } else if (fuelCard) {
        fuelCard.classList.add('hidden');
    }

    // Sustainability Analytics Card
    const sustainabilityCard = document.getElementById('sustainability-card');
    if (data.sustainability_analytics && sustainabilityCard) {
        sustainabilityCard.classList.remove('hidden');

        document.getElementById('sustain-co2-val').textContent = `${data.sustainability_analytics.co2_emission_kg} kg`;
        document.getElementById('sustain-tree-val').textContent = `${data.sustainability_analytics.tree_days} Tree-Days`;
        document.getElementById('sustain-tree-interpretation-text').textContent = data.sustainability_analytics.tree_offset_interpretation || '—';

        // Traffic impact %
        const trafficImpactVal = document.getElementById('sustain-traffic-val');
        const trafficImpactCard = document.getElementById('sustain-traffic-impact-card');
        const trafficImpactPct = data.sustainability_analytics.traffic_impact_percent;
        trafficImpactVal.textContent = `+${trafficImpactPct}%`;
        if (trafficImpactCard) {
            trafficImpactCard.classList.remove('hidden');
        }
        
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

        // Weather impact %
        const weatherImpactVal = document.getElementById('sustain-weather-val');
        const weatherImpactCard = document.getElementById('sustain-weather-impact-card');
        const weatherImpactPct = data.sustainability_analytics.weather_impact_percent;
        weatherImpactVal.textContent = `+${weatherImpactPct}%`;
        
        if (weatherImpactPct === 0) {
            weatherImpactVal.textContent = '0% (None)';
            weatherImpactVal.style.color = 'var(--color-green)';
            weatherImpactCard.style.borderColor = 'var(--border-glass)';
        } else if (weatherImpactPct <= 5) {
            weatherImpactVal.style.color = 'var(--color-yellow)';
            weatherImpactCard.style.borderColor = 'rgba(255, 234, 0, 0.2)';
        } else if (weatherImpactPct <= 10) {
            weatherImpactVal.style.color = 'var(--color-orange)';
            weatherImpactCard.style.borderColor = 'rgba(255, 145, 0, 0.2)';
        } else {
            weatherImpactVal.style.color = 'var(--color-red)';
            weatherImpactCard.style.borderColor = 'rgba(255, 23, 68, 0.2)';
        }

        // Impact level badge
        const impactBadge = document.getElementById('sustainability-impact-badge');
        const impactLevel = data.sustainability_analytics.environmental_impact_level;
        impactBadge.textContent = impactLevel;
        
        // Remove existing badge classes
        impactBadge.className = 'cost-hero-badge';
        if (impactLevel === 'Low Impact') {
            impactBadge.classList.add('badge-low-impact');
        } else if (impactLevel === 'Moderate Impact') {
            impactBadge.classList.add('badge-moderate-impact');
        } else if (impactLevel === 'High Impact') {
            impactBadge.classList.add('badge-high-impact');
        } else {
            impactBadge.classList.add('badge-veryhigh-impact');
        }

        // Eco recommendation text
        document.getElementById('sustain-recommendation-text').textContent = data.sustainability_analytics.eco_recommendation || 'No negative conditions detected. Driving efficiently helps preserve resources!';

        // AI Sustainability insight text
        document.getElementById('sustain-ai-insight-text').textContent = data.sustainability_analytics.sustainability_insight || 'No dynamic AI insight available. Aim to drive outside peak congestion hours to minimize carbon footprint.';
    } else if (sustainabilityCard) {
        sustainabilityCard.classList.add('hidden');
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
    const cards = document.querySelectorAll('.compact-card, .detail-card, .cost-hero-card, .recommendations-container-card');
    cards.forEach((card, i) => {
        card.classList.remove('visible');
        setTimeout(() => {
            card.style.transition = `opacity 0.4s ease ${i * 0.08}s, transform 0.4s ease ${i * 0.08}s`;
            card.classList.add('visible');
        }, 50);
    });
}

// ── Update Map ───────────────────────────────────────────────────
function updateMap(data) {
    _lastRouteData = data; // cache for traffic toggle redraws

    // Clear custom markers
    currentMarkers.forEach(m => m.setMap(null));
    currentMarkers = [];

    const originLat = parseFloat(data.origin.lat);
    const originLng = parseFloat(data.origin.lon);
    const destLat = parseFloat(data.destination.lat);
    const destLng = parseFloat(data.destination.lon);

    window._originLat = originLat;
    window._originLng = originLng;
    window._destLat = destLat;
    window._destLng = destLng;

    // Draw Origin Marker
    const originMarker = new google.maps.Marker({
        position: { lat: originLat, lng: originLng },
        map: googleMap,
        title: `Origin: ${data.origin.display_name}`,
        icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: 8,
            fillColor: '#00e676',
            fillOpacity: 0.9,
            strokeColor: '#ffffff',
            strokeWeight: 2
        }
    });
    currentMarkers.push(originMarker);

    // Draw Destination Marker
    const destMarker = new google.maps.Marker({
        position: { lat: destLat, lng: destLng },
        map: googleMap,
        title: `Destination: ${data.destination.display_name}`,
        icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: 8,
            fillColor: '#ff2d95',
            fillOpacity: 0.9,
            strokeColor: '#ffffff',
            strokeWeight: 2
        }
    });
    currentMarkers.push(destMarker);

    drawRoute(data);
}

// ── Draw Route Polyline (optimal ML-predicted path) ────────────────
function drawRoute(data) {
    if (!googleMap) return;

    // Clear previous polyline
    if (routePolyline) {
        routePolyline.setMap(null);
        routePolyline = null;
    }

    const geometry = data.route.geometry;
    if (!geometry || !geometry.coordinates) return;

    // Convert GeoJSON [lon, lat] coordinates to Google Maps LatLng
    const pathCoords = geometry.coordinates.map(c => ({
        lat: parseFloat(c[1]),
        lng: parseFloat(c[0])
    }));

    const routeColor = getCongestionColor(data.congestion.level);

    routePolyline = new google.maps.Polyline({
        path: pathCoords,
        geodesic: true,
        strokeColor: routeColor,
        strokeOpacity: 0.9,
        strokeWeight: 6,
        map: googleMap
    });

    // Fit bounds to show the entire route
    const bounds = new google.maps.LatLngBounds();
    pathCoords.forEach(coord => bounds.extend(coord));
    googleMap.fitBounds(bounds);
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
        if (explanationSection) explanationSection.classList.remove('hidden');
        insights.forEach((insight) => {
            const div = document.createElement('div');
            div.className = `insight-item ${insight.type}`;
            div.innerHTML = `
                <span class="insight-icon">${insight.icon}</span>
                <span class="insight-text">${insight.text}</span>
            `;
            container.appendChild(div);
        });
    } else {
        if (explanationSection) explanationSection.classList.add('hidden');
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
                <div class="weather-item-value">${Number(weather.wind_speed_kmh).toFixed(2)} km/h</div>
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
