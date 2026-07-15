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

// ── POI Chatbot State ────────────────────────────────────────
let currentRouteGeometry = null;   // GeoJSON geometry from /predict, sent to /poi-search
let poiMarkers = [];               // Google Maps markers for POI results


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
    initPOISearch();   // initialise the Explore Route tab
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

        // Store route geometry for POI search (used by the Explore Route tab)
        currentRouteGeometry = data.route.geometry || null;
        // Reset POI UI whenever a new route is planned
        resetPOIPanel();


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


/* ═══════════════════════════════════════════════════════════════
   POI Chatbot — "Explore Route" Tab Logic
   ═══════════════════════════════════════════════════════════════ */

/**
 * initPOISearch — wires up chip clicks, Enter key on input,
 * and tab-switch clear behaviour.
 */
function initPOISearch() {
    // Wire up each quick-select chip
    const chips = document.querySelectorAll('.poi-chip');
    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            const query = chip.dataset.query;
            // Highlight the tapped chip
            chips.forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            // Populate the text input with the chip label
            const input = document.getElementById('poi-query-input');
            if (input) input.value = query;
            handlePOIQuery(query);
        });
    });

    // Submit on Enter key inside the text input
    const input = document.getElementById('poi-query-input');
    if (input) {
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') handlePOIQuery();
        });
        // Clear chip active state when user types manually
        input.addEventListener('input', () => {
            document.querySelectorAll('.poi-chip').forEach(c => c.classList.remove('active'));
        });
    }
}

/**
 * handlePOIQuery — the main POI search trigger.
 * Called by chip clicks, send button, or Enter key.
 *
 * Flow:
 *  1. Validate that a route exists
 *  2. Show animated loading stages
 *  3. POST /poi-search with route geometry + query
 *  4. Render result cards + Google Maps markers
 */
window.handlePOIQuery = async function(query) {
    const input = document.getElementById('poi-query-input');
    const q = (query || (input && input.value) || '').trim();

    if (!q) {
        showToast('Please enter a search term or tap a category chip.');
        return;
    }

    // Guard: must have planned a route first
    if (!currentRouteGeometry || !window._originLat || !window._originLng) {
        showToast('Plan a route first, then search for places along it.');
        return;
    }

    // Show loading panel, hide previous results/empty state
    _setPOIUIState('loading');
    _animatePOIStages();

    try {
        const res = await fetch('/poi-search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                route_geometry: currentRouteGeometry,
                origin_lat: window._originLat,
                origin_lon: window._originLng,
                query: q,
                radius_km: 2.0,
            }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || `Server error ${res.status}`);
        }

        const data = await res.json();

        // Fix 1: Handle "prompt" response — user typed a location but no category
        if (data.prompt) {
            const msgEl = document.getElementById('poi-empty-msg');
            if (msgEl) msgEl.textContent = data.message || 'Please specify what you are looking for.';
            _setPOIUIState('empty');
            return;
        }

        if (!data.pois || data.pois.length === 0) {
            // Show empty state with server-provided message
            const msgEl = document.getElementById('poi-empty-msg');
            if (msgEl && data.message) msgEl.textContent = data.message;
            _setPOIUIState('empty');
            return;
        }


        renderPOIResults(data);
        plotPOIMarkers(data.pois);

    } catch (err) {
        _setPOIUIState('hidden');
        showToast(err.message || 'POI search failed. Please try again.');
    }
};

/** Controls which POI UI panel is visible */
function _setPOIUIState(state) {
    const loading  = document.getElementById('poi-loading');
    const results  = document.getElementById('poi-results-list');
    const empty    = document.getElementById('poi-empty-state');
    const header   = document.getElementById('poi-results-header');

    loading.style.display = 'none';
    results.style.display  = 'none';
    empty.style.display    = 'none';
    header.style.display   = 'none';

    if (state === 'loading') loading.style.display = 'flex';
    if (state === 'empty')   empty.style.display   = 'flex';
    if (state === 'results') {
        results.style.display = 'flex';
        header.style.display  = 'flex';
    }
}

/**
 * Animates the 3-stage loading text while the request is in-flight.
 * Stages illuminate in sequence to give feedback on long Overpass waits.
 */
function _animatePOIStages() {
    const s1 = document.getElementById('poi-stage-1');
    const s2 = document.getElementById('poi-stage-2');
    const s3 = document.getElementById('poi-stage-3');
    if (!s1) return;

    // Reset
    [s1, s2, s3].forEach(s => s.classList.add('dim'));
    s1.classList.remove('dim');

    // Stage 2 lights up after 1.2 s (Overpass in flight)
    const t2 = setTimeout(() => { if (s2) s2.classList.remove('dim'); }, 1200);
    // Stage 3 lights up after 3 s (embedding/FAISS)
    const t3 = setTimeout(() => { if (s3) s3.classList.remove('dim'); }, 3000);

    // Store timers so we can cancel if needed (not critical here)
    window._poiStageTimers = [t2, t3];
}

/**
 * renderPOIResults — populates the results list with POI cards
 * and updates the count/cache header.
 */
function renderPOIResults(data) {
    const list   = document.getElementById('poi-results-list');
    const count  = document.getElementById('poi-result-count');
    const badge  = document.getElementById('poi-cache-badge');

    list.innerHTML = '';

    // ── Broad-search notice (when no keyword matched, show a subtle tip) ──
    if (data.query_matched === false) {
        const notice = document.createElement('div');
        notice.className = 'poi-broad-notice';
        notice.innerHTML = `⚠️ No exact category matched “${_escHtml(data.query)}” — showing semantically closest results from a broad search.`;
        list.appendChild(notice);
    }

    // ── Location-specific search notice ──
    if (data.anchor_location) {
        const locNotice = document.createElement('div');
        locNotice.className = 'poi-broad-notice';
        locNotice.style.borderColor = 'rgba(20, 184, 166, 0.3)';
        locNotice.style.color = '#5eead4';
        locNotice.innerHTML = `📍 Showing results near <strong>${_escHtml(data.anchor_location)}</strong>`;
        list.appendChild(locNotice);
    }

    data.pois.forEach((poi, i) => {
        const card = document.createElement('div');
        card.className = 'poi-result-card';

        const tags = poi.tags || {};
        
        // 1. Opening hours
        let hoursHtml = '<span style="color:#9ca3af;font-size:12px;">🕑 Hours unknown</span>';
        if (tags.opening_hours) {
            const oh = tags.opening_hours.toLowerCase();
            if (oh.includes('24/7')) {
                hoursHtml = '<span style="color:#10b981;font-size:12px;font-weight:600;">🟢 Open 24/7</span>';
            } else {
                hoursHtml = `<span style="color:#3b82f6;font-size:12px;">🕐 ${_escHtml(tags.opening_hours)}</span>`;
            }
        }
        
        // 2. Phone
        const phone = tags['contact:phone'] || tags['phone'];
        const phoneHtml = phone ? `<div style="margin-top:3px;"><a href="tel:${_escHtml(phone)}" style="color:#3b82f6;text-decoration:none;font-size:13px;">📞 ${_escHtml(phone)}</a></div>` : '';
        
        // 3. Address
        const street = tags['addr:street'];
        const city = tags['addr:city'];
        let addrStr = '';
        if (street && city) addrStr = `${street}, ${city}`;
        else if (street) addrStr = street;
        else if (city) addrStr = city;
        else addrStr = poi.address || '';
        
        // 4 & 5. Distance and Detour
        const distStr = poi.distance_km ? `${poi.distance_km} km from start` : '';
        const detourKm = poi.detour_km;
        let detourStr = '';
        let detourBadge = '';
        if (detourKm !== undefined) {
            detourStr = ` · +${detourKm} km detour`;
            if (detourKm <= 0.5) detourBadge = '<span style="color:#10b981;font-weight:600;">🟢 On your way</span>';
            else if (detourKm <= 2.0) detourBadge = '<span style="color:#f59e0b;font-weight:600;">🟡 Small detour</span>';
            else detourBadge = '<span style="color:#ef4444;font-weight:600;">🔴 Detour needed</span>';
        }
        const distanceHtml = distStr ? `<span class="poi-card-distance" style="display:block;margin-bottom:4px;">📍 ${distStr}${detourStr} &nbsp; ${detourBadge}</span>` : '';
        
        // 6. Category-specific row
        let catHtml = '';
        const typeLower = (poi.type || '').toLowerCase();
        if (typeLower.includes('petrol') || typeLower.includes('fuel')) {
            const fuels = [];
            if (tags['fuel:petrol'] === 'yes') fuels.push('Petrol');
            if (tags['fuel:diesel'] === 'yes') fuels.push('Diesel');
            if (tags['fuel:cng'] === 'yes') fuels.push('CNG');
            if (fuels.length) catHtml = `<div style="font-size:12px;color:#6b7280;margin-top:3px;">⛽ Fuels: ${fuels.join(', ')}</div>`;
        } else if (typeLower.includes('hospital')) {
            const ops = tags['operator:type'];
            const emg = tags['emergency'] === 'yes' ? '🚨 Emergency' : '';
            if (ops || emg) catHtml = `<div style="font-size:12px;color:#6b7280;margin-top:3px;">🏥 ${ops ? _escHtml(ops) + ' ' : ''}${emg}</div>`;
        } else if (typeLower.includes('restaurant') || typeLower.includes('food')) {
            const cuisine = tags['cuisine'];
            const veg = tags['diet:vegetarian'] === 'yes' ? '🟢 Pure Veg' : '';
            if (cuisine || veg) catHtml = `<div style="font-size:12px;color:#6b7280;margin-top:3px;">🍽️ ${cuisine ? _escHtml(cuisine) + (veg ? ' · ' : '') : ''}${veg}</div>`;
        } else if (typeLower.includes('hotel') || typeLower.includes('resort')) {
            const stars = tags['stars'];
            if (stars) catHtml = `<div style="font-size:12px;color:#eab308;margin-top:3px;">⭐ ${stars} Star Hotel</div>`;
        } else if (typeLower.includes('atm') || typeLower.includes('bank')) {
            const op = tags['operator'] || tags['network'];
            if (op) catHtml = `<div style="font-size:12px;color:#6b7280;margin-top:3px;">🏦 ${_escHtml(op)}</div>`;
        }

        const icon     = getPOITypeIcon(poi.type);
        const badgeCls = getPOIBadgeClass(poi.type);
        const mapsUrl  = _googleMapsUrl(poi);

        card.innerHTML = `
            <span class="poi-card-rank">${icon}</span>
            <div class="poi-card-body">
                <div class="poi-card-top" style="margin-bottom: 4px;">
                    <span class="poi-card-name">${_escHtml(poi.name)}</span>
                    <span class="poi-result-badge ${badgeCls}">${_escHtml(poi.type)}</span>
                </div>
                <div style="margin-bottom: 4px;">${hoursHtml}</div>
                <div class="poi-card-meta">
                    ${distanceHtml}
                    ${addrStr ? `<span class="poi-card-address">${_escHtml(addrStr)}</span>` : ''}
                    ${phoneHtml}
                    ${catHtml}
                </div>
                <div class="poi-card-actions">
                    <button class="poi-map-btn" data-idx="${i}" title="Show on this map">
                        🗺️ Show on map
                    </button>
                    <a class="poi-gmaps-btn" href="${mapsUrl}" target="_blank" rel="noopener noreferrer"
                       title="Open in Google Maps">
                        📍 Google Maps
                    </a>
                </div>
            </div>
        `;

        // "Show on map" button — pans to marker and switches to Overview tab
        card.querySelector('.poi-map-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            if (googleMap && poiMarkers[i]) {
                googleMap.panTo({ lat: poi.lat, lng: poi.lon });
                googleMap.setZoom(16);
                const overviewBtn = document.querySelector('.tab-nav-btn[data-tab="overview"]');
                if (overviewBtn) overviewBtn.click();
                google.maps.event.trigger(poiMarkers[i], 'click');
            }
        });

        list.appendChild(card);
    });

    // Update header
    if (count) count.textContent = `${data.count} result${data.count !== 1 ? 's' : ''} for “${data.query || ''}”`;
    if (badge) badge.style.display = data.from_cache ? 'inline-flex' : 'none';

    _setPOIUIState('results');
}

/**
 * plotPOIMarkers — adds a distinct Google Maps marker for each POI.
 * InfoWindow now includes an "Open in Google Maps" link.
 */
function plotPOIMarkers(pois) {
    if (!googleMap) return;
    clearPOIMarkers();

    pois.forEach((poi, i) => {
        const icon = getPOITypeIcon(poi.type);
        const mapsUrl = _googleMapsUrl(poi);

        const marker = new google.maps.Marker({
            position: { lat: poi.lat, lng: poi.lon },
            map: googleMap,
            title: poi.name,
            label: { text: icon, fontSize: '18px' },
            icon: {
                url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAABjE+ibYAAAAASUVORK5CYII=',
                scaledSize: new google.maps.Size(1, 1),
                anchor: new google.maps.Point(0, 0),
            },
            zIndex: 200 + i,
        });

        // ── InfoWindow with Google Maps deep-link ──
        const tags = poi.tags || {};
        let hoursHtml = '';
        if (tags.opening_hours) {
            const oh = tags.opening_hours.toLowerCase();
            if (oh.includes('24/7')) {
                hoursHtml = '<div style="color:#10b981;font-weight:600;margin-bottom:2px;">🟢 Open 24/7</div>';
            } else {
                hoursHtml = `<div style="color:#3b82f6;margin-bottom:2px;">🕐 ${_escHtml(tags.opening_hours)}</div>`;
            }
        }
        
        let detourStr = '';
        if (poi.detour_km !== undefined) detourStr = ` · +${poi.detour_km}km detour`;
        
        const distLine = poi.distance_km
            ? `<div style="color:#10b981;font-weight:600;margin-top:2px;">📍 ${poi.distance_km} km from start${detourStr}</div>`
            : '';
            
        // Use full address if available
        let addrStr = '';
        if (tags['addr:street'] && tags['addr:city']) addrStr = `${tags['addr:street']}, ${tags['addr:city']}`;
        else addrStr = tags['addr:street'] || tags['addr:city'] || poi.address || '';
        
        const addrLine = addrStr
            ? `<div style="color:#6b7280;margin-top:2px;">${_escHtml(addrStr)}</div>`
            : '';

        const infoContent = `
            <div style="font-family:'Outfit',sans-serif;font-size:13px;min-width:180px;max-width:240px;padding:2px 0;">
                <div style="font-size:15px;font-weight:700;color:#111;margin-bottom:3px;">${_escHtml(poi.name)}</div>
                <div style="color:#7c3aed;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;">${_escHtml(poi.type)}</div>
                ${hoursHtml}
                ${distLine}
                ${addrLine}
                <div style="margin-top:10px;">
                    <a href="${mapsUrl}"
                       target="_blank"
                       rel="noopener noreferrer"
                       style="display:inline-flex;align-items:center;gap:5px;
                              background:#1a73e8;color:#fff;font-weight:700;
                              font-size:12px;padding:6px 12px;border-radius:6px;
                              text-decoration:none;font-family:'Outfit',sans-serif;">
                        📍 Open in Google Maps
                    </a>
                </div>
            </div>`;

        const infoWin = new google.maps.InfoWindow({ content: infoContent, maxWidth: 260 });

        marker.addListener('click', () => {
            if (window._openPOIInfoWin) window._openPOIInfoWin.close();
            infoWin.open({ anchor: marker, map: googleMap });
            window._openPOIInfoWin = infoWin;
        });

        poiMarkers.push(marker);
    });

    if (poiMarkers.length > 0) {
        const bounds = new google.maps.LatLngBounds();
        poiMarkers.forEach(m => bounds.extend(m.getPosition()));
        googleMap.fitBounds(bounds, { padding: 60 });
    }
}

/**
 * _googleMapsUrl — builds a Google Maps search URL for a POI.
 * Uses the place name + type as the search query, centred on its coordinates.
 * This reliably opens the place listing with reviews, hours, etc.
 *
 * URL format: https://www.google.com/maps/search/?api=1&query=NAME+TYPE&center=LAT,LON
 */
function _googleMapsUrl(poi) {
    const searchTerm = encodeURIComponent(`${poi.name} ${poi.type}`);
    return `https://www.google.com/maps/search/?api=1&query=${searchTerm}&center=${poi.lat},${poi.lon}`;
}

/** Removes all POI markers from the map. */
function clearPOIMarkers() {
    if (window._openPOIInfoWin) {
        window._openPOIInfoWin.close();
        window._openPOIInfoWin = null;
    }
    poiMarkers.forEach(m => m.setMap(null));
    poiMarkers = [];
}

/** Resets the POI panel to its initial state (called after a new route is planned). */
function resetPOIPanel() {
    clearPOIMarkers();
    _setPOIUIState('hidden');
    const input = document.getElementById('poi-query-input');
    if (input) input.value = '';
    document.querySelectorAll('.poi-chip').forEach(c => c.classList.remove('active'));
}

/**
 * getPOITypeIcon — maps POI type labels to emoji icons.
 * Used for both the result card rank column and the map marker label.
 */
function getPOITypeIcon(type) {
    const t = (type || '').toLowerCase();
    // Fuel / EV
    if (t.includes('petrol') || t.includes('fuel') || t.includes('ev') || t.includes('charg')) return '⛽';
    // Food
    if (t.includes('restaurant') || t.includes('dhaba') || t.includes('food court')) return '🍽️';
    if (t.includes('fast food'))  return '🍔';
    if (t.includes('café') || t.includes('cafe') || t.includes('coffee')) return '☕';
    if (t.includes('bakery'))     return '🍞';
    if (t.includes('sweet'))      return '🍬';
    // Accommodation
    if (t.includes('hotel') || t.includes('motel') || t.includes('resort')) return '🏨';
    if (t.includes('guest'))      return '🛏️';
    if (t.includes('hostel'))     return '🏕️';
    // Medical
    if (t.includes('hospital'))   return '🏥';
    if (t.includes('clinic'))     return '🩺';
    if (t.includes('pharmacy') || t.includes('chemist')) return '💊';
    if (t.includes('dentist'))    return '🪷';
    if (t.includes('veterinary')) return '🐾';
    // Finance
    if (t.includes('atm'))        return '🏧';
    if (t.includes('bank'))       return '🏦';
    if (t.includes('exchange'))   return '💱';
    // Education
    if (t.includes('school'))     return '🏫';
    if (t.includes('college') || t.includes('university')) return '🎓';
    if (t.includes('library'))    return '📚';
    if (t.includes('kindergarten') || t.includes('nursery') || t.includes('playschool')) return '🧸';
    if (t.includes('language') || t.includes('training') || t.includes('coaching')) return '📝';
    // Sports / Recreation
    if (t.includes('gym') || t.includes('fitness')) return '🏋️';
    if (t.includes('stadium'))    return '🏟️';
    if (t.includes('swimming') || t.includes('pool')) return '🏊';
    if (t.includes('sports') || t.includes('pitch')) return '⚽';
    if (t.includes('playground')) return '🛷';
    // Shopping
    if (t.includes('supermarket') || t.includes('grocery') || t.includes('market')) return '🛒';
    if (t.includes('mall'))       return '🏬';
    if (t.includes('cloth'))      return '👕';
    if (t.includes('electronic') || t.includes('mobile')) return '📱';
    // Emergency
    if (t.includes('police'))     return '🚓';
    if (t.includes('fire'))       return '🚒';
    // Government / Post
    if (t.includes('post'))       return '📮';
    if (t.includes('government')) return '🏛️';
    // Transport
    if (t.includes('bus'))        return '🚌';
    if (t.includes('railway') || t.includes('train') || t.includes('metro')) return '🚆';
    if (t.includes('airport'))    return '✈️';
    if (t.includes('taxi'))       return '🚕';
    // Facilities
    if (t.includes('parking'))    return '🅿️';
    if (t.includes('restroom') || t.includes('toilet')) return '🚻';
    // Religious
    if (t.includes('temple') || t.includes('mosque') || t.includes('church') || t.includes('worship')) return '⛪';
    // Tourist / Nature
    if (t.includes('museum'))     return '🏛️';
    if (t.includes('monument'))   return '🗿';
    if (t.includes('zoo'))        return '🦁';
    if (t.includes('amusement') || t.includes('arcade')) return '🎡';
    if (t.includes('viewpoint') || t.includes('attraction')) return '🏞️';
    if (t.includes('beach'))      return '🏖️';
    if (t.includes('garden'))     return '🌺';
    if (t.includes('park') || t.includes('nature')) return '🌳';
    if (t.includes('rest area'))  return '🛑';
    return '📍';
}

/**
 * getPOIBadgeClass — returns the CSS badge colour class for a given type.
 */
function getPOIBadgeClass(type) {
    const t = (type || '').toLowerCase();
    if (t.includes('petrol') || t.includes('fuel') || t.includes('charg')) return 'poi-badge-fuel';
    if (t.includes('restaurant') || t.includes('food') || t.includes('dhaba') || t.includes('fast food') || t.includes('bakery') || t.includes('sweet')) return 'poi-badge-restaurant';
    if (t.includes('café') || t.includes('cafe')) return 'poi-badge-cafe';
    if (t.includes('hotel') || t.includes('motel') || t.includes('guest') || t.includes('hostel') || t.includes('resort')) return 'poi-badge-hotel';
    if (t.includes('hospital') || t.includes('clinic') || t.includes('dentist') || t.includes('veterinary')) return 'poi-badge-hospital';
    if (t.includes('pharmacy')) return 'poi-badge-pharmacy';
    if (t.includes('atm') || t.includes('bank') || t.includes('exchange')) return 'poi-badge-atm';
    if (t.includes('school') || t.includes('college') || t.includes('university') || t.includes('library') || t.includes('training') || t.includes('kindergarten')) return 'poi-badge-education';
    if (t.includes('gym') || t.includes('fitness') || t.includes('stadium') || t.includes('swimming') || t.includes('sports') || t.includes('playground')) return 'poi-badge-sports';
    if (t.includes('police') || t.includes('fire') || t.includes('emergency')) return 'poi-badge-emergency';
    if (t.includes('bus') || t.includes('railway') || t.includes('train') || t.includes('airport') || t.includes('taxi') || t.includes('metro')) return 'poi-badge-transport';
    return 'poi-badge-default';
}

/** Simple HTML escape to prevent XSS in dynamically inserted POI data. */
function _escHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
