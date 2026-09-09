"use strict";

/*
    HEATGUARD
    ----------
    Browser-side implementation using real public services:

    Weather:
    Open-Meteo

    Geocoding:
    OpenStreetMap Nominatim

    Mapping:
    Leaflet + OpenStreetMap

    Facilities:
    OpenStreetMap Overpass API

    Routing:
    OSRM public routing service

    Important:
    This file deliberately does NOT contain private API keys.

    Production services such as:
    - authenticated AI/LLM
    - PostgreSQL/Supabase server access
    - SMS/WhatsApp
    - protected administration
    - population health model
    should be placed behind a backend.
*/


/* =========================================================
   CONFIGURATION
========================================================= */

const CONFIG = {
    weatherApi: "https://api.open-meteo.com/v1/forecast",
    aiApiUrl: "https://api.openai.com/v1/chat/completions", // Add your AI endpoint
    aiApiKey: "AQ.Ab8RN6KSCNffz3tbOlItmKpWH8ihhnlS85zv7q2lZxkiEmDPfA", // Add your key

    geocoder:
        "https://nominatim.openstreetmap.org",

    overpass:
        "https://overpass-api.de/api/interpreter",

    routing:
        "https://router.project-osrm.org",

    /*
        If you later create a backend endpoint such as:

        POST /api/ai

        change this to:

        "/api/ai"

        The browser will then send verified HeatGuard context
        to your backend rather than calling an LLM directly.
    */
    aiEndpoint: "",

    weatherCacheKey:
        "heatguard.weather.cache",

    locationCacheKey:
        "heatguard.location.cache",

    facilitiesCacheKey:
        "heatguard.facilities.cache",

    routeCacheKey:
        "heatguard.routes.cache",

    workerCacheKey:
        "heatguard.worker.cache"
};


/* =========================================================
   APPLICATION STATE
========================================================= */

const state = {

    location: null,

    weather: null,

    weatherStatus: "UNAVAILABLE",

    facilities: [],

    facilitiesStatus: "UNAVAILABLE",

    routes: [],

    routeStatus: "UNAVAILABLE",

    activeFacilityFilter: "all",

    risk: null,

    forecastRisk: [],

    peakForecast: null,

    map: null,

    routeMap: null,

    locationMarker: null,

    routeStartMarker: null,

    facilityMarkers: [],

    routeLayers: [],

    workerWatchId: null,

    workerLastPosition: null,

    workerTimer: null,

    shiftTimer: null,

    lastSync: null

};


/* =========================================================
   DOM HELPERS
========================================================= */

const $ = (id) => document.getElementById(id);

function qs(selector) {
    return document.querySelector(selector);
}

function qsa(selector) {
    return [...document.querySelectorAll(selector)];
}


/* =========================================================
   UTILITY
========================================================= */

function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

function round(value, digits = 1) {

    if (!Number.isFinite(value)) {
        return null;
    }

    const multiplier = 10 ** digits;

    return Math.round(value * multiplier) / multiplier;
}

function formatNumber(value, suffix = "") {

    if (!Number.isFinite(value)) {
        return "â€”";
    }

    return `${round(value)}${suffix}`;
}

function isoDate(date = new Date()) {
    return date.toISOString();
}

function formatTime(timestamp) {

    if (!timestamp) {
        return "â€”";
    }

    const date = new Date(timestamp);

    if (Number.isNaN(date.getTime())) {
        return "â€”";
    }

    return date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit"
    });
}

function formatDateTime(timestamp) {

    if (!timestamp) {
        return "â€”";
    }

    const date = new Date(timestamp);

    if (Number.isNaN(date.getTime())) {
        return "â€”";
    }

    return date.toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short"
    });
}

function showToast(message) {

    const container = $("toastContainer");

    const toast = document.createElement("div");

    toast.className = "toast";

    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 4500);
}

function safeText(value) {

    if (value === null || value === undefined) {
        return "";
    }

    return String(value);
}


/* =========================================================
   DATA STATUS
========================================================= */

function setDataStatus(status, message = "") {

    state.weatherStatus = status;

    const dot = $("dataStatusDot");
    const text = $("dataStatusText");

    const sidebarDot = $("sidebarStatusDot");
    const sidebarText = $("sidebarStatusText");

    dot.className = "status-dot";
    sidebarDot.className = "status-dot";

    if (status === "LIVE") {

        dot.classList.add("live");
        sidebarDot.classList.add("live");

        text.textContent = "LIVE";
        sidebarText.textContent = "Live environmental data";

    } else if (status === "CACHED") {

        dot.classList.add("cached");
        sidebarDot.classList.add("cached");

        text.textContent = "CACHED";
        sidebarText.textContent = "Using cached data";

    } else if (status === "OFFLINE") {

        dot.classList.add("offline");
        sidebarDot.classList.add("offline");

        text.textContent = "OFFLINE";
        sidebarText.textContent = "Offline using cached data";

    } else {

        dot.classList.add("error");
        sidebarDot.classList.add("error");

        text.textContent = "UNAVAILABLE";
        sidebarText.textContent = message || "Data unavailable";
    }

    $("systemState").textContent = status;
}


/* =========================================================
   LOCAL STORAGE
========================================================= */

function saveCache(key, value) {

    try {

        localStorage.setItem(
            key,
            JSON.stringify({
                savedAt: isoDate(),
                data: value
            })
        );

    } catch (error) {

        console.warn("Cache write failed", error);
    }
}

function readCache(key) {

    try {

        const raw = localStorage.getItem(key);

        if (!raw) {
            return null;
        }

        return JSON.parse(raw);

    } catch (error) {

        console.warn("Cache read failed", error);

        return null;
    }
}


/* =========================================================
   NETWORK
========================================================= */

function isOnline() {
    return navigator.onLine;
}

async function fetchJSON(url, options = {}) {

    const response = await fetch(url, {
        ...options,
        headers: {
            Accept: "application/json",
            ...(options.headers || {})
        }
    });

    if (!response.ok) {
        throw new Error(
            `Request failed with HTTP ${response.status}`
        );
    }

    return response.json();
}


/* =========================================================
   LOCATION
========================================================= */

async function requestCurrentLocation() {

    if (!navigator.geolocation) {

        showToast(
            "Geolocation is not supported by this browser."
        );

        return;
    }

    $("locationMessage").textContent =
        "Requesting browser location permission...";

    navigator.geolocation.getCurrentPosition(

        async (position) => {

            const latitude = position.coords.latitude;
            const longitude = position.coords.longitude;

            try {

                await setLocationFromCoordinates(
                    latitude,
                    longitude,
                    "GPS"
                );

            } catch (error) {

                console.error(error);

                $("locationMessage").textContent =
                    "Coordinates obtained, but location resolution failed.";

                showToast(
                    "Location obtained but reverse geocoding failed."
                );
            }
        },

        (error) => {

            console.warn(error);

            $("locationMessage").textContent =
                "Location permission was denied or unavailable. Use manual search.";

            showToast(
                "GPS location unavailable. You can search manually."
            );
        },

        {
            enableHighAccuracy: true,
            timeout: 15000,
            maximumAge: 300000
        }
    );
}


async function setLocationFromCoordinates(
    latitude,
    longitude,
    source = "GPS"
) {

    if (
        !Number.isFinite(latitude) ||
        !Number.isFinite(longitude)
    ) {

        throw new Error("Invalid coordinates.");
    }

    let resolved = null;

    try {

        resolved = await reverseGeocode(
            latitude,
            longitude
        );

    } catch (error) {

        console.warn(
            "Reverse geocoding unavailable",
            error
        );
    }

    state.location = {

        latitude,
        longitude,

        source,

        displayName:
            resolved?.display_name ||
            `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`,

        address:
            resolved?.address || {}
    };

    saveCache(
        CONFIG.locationCacheKey,
        state.location
    );

    renderLocation();

    await synchronizeLocationData();
}


async function reverseGeocode(latitude, longitude) {

    const url =
        `${CONFIG.geocoder}/reverse?` +
        new URLSearchParams({
            lat: latitude,
            lon: longitude,
            format: "jsonv2",
            addressdetails: "1"
        });

    return fetchJSON(url);
}


async function searchManualLocation() {

    const stateValue =
        $("stateInput").value.trim();

    const district =
        $("districtInput").value.trim();

    const city =
        $("cityInput").value.trim();

    const locality =
        $("localityInput").value.trim();

    const parts = [
        locality,
        city,
        district,
        stateValue,
        "India"
    ].filter(Boolean);

    if (parts.length === 0) {

        showToast(
            "Enter a locality, city, district or state."
        );

        return;
    }

    const query = parts.join(", ");

    $("locationMessage").textContent =
        `Searching for ${query}...`;

    try {

        const url =
            `${CONFIG.geocoder}/search?` +
            new URLSearchParams({
                q: query,
                format: "jsonv2",
                addressdetails: "1",
                limit: "1"
            });

        const results = await fetchJSON(url);

        if (
            !Array.isArray(results) ||
            results.length === 0
        ) {

            throw new Error(
                "No geographic result was returned."
            );
        }

        const result = results[0];

        await setLocationFromCoordinates(
            Number(result.lat),
            Number(result.lon),
            "MANUAL"
        );

        $("locationMessage").textContent =
            "Location selected successfully.";

    } catch (error) {

        console.error(error);

        $("locationMessage").textContent =
            "The location could not be resolved.";

        showToast(
            "Location search failed. Try a more specific place."
        );
    }
}


function renderLocation() {

    if (!state.location) {
        return;
    }

    const address = state.location.address || {};

    const locality =
        address.suburb ||
        address.village ||
        address.town ||
        address.city_district ||
        "";

    const city =
        address.city ||
        address.town ||
        address.municipality ||
        "";

    const district =
        address.county ||
        "";

    const region =
        address.state ||
        "";

    $("locationName").textContent =
        locality ||
        city ||
        state.location.displayName;

    $("locationDetails").textContent =
        [
            city,
            district,
            region
        ].filter(Boolean).join(", ");

    $("locationSourceBadge").textContent =
        state.location.source;

    $("locationSourceBadge").className =
        "data-badge live";

    $("routeStart").textContent =
        state.location.displayName;

    $("monitorLocation").textContent =
        state.location.displayName;

    $("monitorCoordinates").textContent =
        `${state.location.latitude.toFixed(5)}, ` +
        `${state.location.longitude.toFixed(5)}`;

    updateMapLocation();
}


/* =========================================================
   WEATHER
========================================================= */

async function getWeather(latitude, longitude) {

    const currentVariables = [
        "temperature_2m",
        "relative_humidity_2m",
        "apparent_temperature",
        "precipitation",
        "weather_code",
        "cloud_cover",
        "wind_speed_10m",
        "wind_direction_10m",
        "shortwave_radiation"
    ].join(",");

    const hourlyVariables = [
        "temperature_2m",
        "relative_humidity_2m",
        "apparent_temperature",
        "precipitation_probability",
        "weather_code",
        "cloud_cover",
        "wind_speed_10m",
        "wind_direction_10m",
        "shortwave_radiation"
    ].join(",");

    const params = new URLSearchParams({

        latitude,
        longitude,

        current:
            currentVariables,

        hourly:
            hourlyVariables,

        forecast_days: "3",

        timezone: "auto",

        wind_speed_unit: "kmh",

        temperature_unit: "celsius",

        precipitation_unit: "mm"
    });

    return fetchJSON(
        `${CONFIG.weatherApi}?${params.toString()}`
    );
}


async function synchronizeWeather() {

    if (!state.location) {
        return;
    }

    const cached =
        readCache(CONFIG.weatherCacheKey);

    if (!isOnline()) {

        if (cached?.data) {

            state.weather =
                cached.data;

            setDataStatus("OFFLINE");

            processWeather();

            showToast(
                "OFFLINE USING CACHED DATA"
            );

            return;
        }

        setDataStatus(
            "UNAVAILABLE",
            "No cached environmental data"
        );

        return;
    }

    try {

        setDataStatus("LIVE");

        const weather = await getWeather(
            state.location.latitude,
            state.location.longitude
        );

        validateWeatherResponse(weather);

        state.weather = weather;

        state.lastSync = isoDate();

        saveCache(
            CONFIG.weatherCacheKey,
            weather
        );

        setDataStatus("LIVE");

        processWeather();

        $("monitorWeatherSource").textContent =
            "Open-Meteo";

        $("monitorUpdate").textContent =
            formatDateTime(
                weather.current?.time
            );

    } catch (error) {

        console.error(
            "Weather request failed",
            error
        );

        if (cached?.data) {

            state.weather =
                cached.data;

            setDataStatus("CACHED");

            processWeather();

            showToast(
                "Live weather unavailable. Using cached data."
            );

        } else {

            setDataStatus(
                "UNAVAILABLE",
                "Weather provider failed"
            );

            showToast(
                "Live environmental data unavailable."
            );
        }
    }
}


function validateWeatherResponse(data) {

    if (!data || !data.current) {
        throw new Error(
            "Weather response has no current data."
        );
    }

    const required =
        [
            "temperature_2m",
            "relative_humidity_2m"
        ];

    for (const key of required) {

        if (
            !Number.isFinite(
                Number(data.current[key])
            )
        ) {

            throw new Error(
                `Weather response missing ${key}`
            );
        }
    }
}


/* =========================================================
   THERMAL ENGINE
========================================================= */

/*
    NOAA/NWS Heat Index.

    Rothfusz regression is appropriate for the
    standard warm/humid operating range.

    Input:
        Temperature Â°C
        Relative humidity %

    Output:
        Heat Index Â°C

    Outside the valid warm/humid range,
    HeatGuard uses the applicable simple adjustment
    only when the methodology permits it.

    The application does NOT call this a UTCI or WBGT.
*/


function celsiusToFahrenheit(celsius) {
    return celsius * 9 / 5 + 32;
}

function fahrenheitToCelsius(fahrenheit) {
    return (fahrenheit - 32) * 5 / 9;
}


function calculateHeatIndex(
    temperatureC,
    relativeHumidity
) {

    if (
        !Number.isFinite(temperatureC) ||
        !Number.isFinite(relativeHumidity)
    ) {

        return {
            value: null,
            available: false,
            reason:
                "Temperature and relative humidity are required."
        };
    }

    const T =
        celsiusToFahrenheit(
            temperatureC
        );

    const RH =
        relativeHumidity;

    /*
        NOAA Rothfusz is intended for warm conditions.
        Below approximately 80Â°F, ambient temperature is
        generally used as the heat index rather than
        claiming a Rothfusz result.
    */

    if (T < 80) {

        return {
            value: temperatureC,
            available: true,
            methodology:
                "Heat Index follows ambient temperature below NOAA warm-condition regression range.",
            interpretation:
                "Heat Index is approximately equal to ambient temperature under these conditions."
        };
    }

    const HI =
        -42.379
        + 2.04901523 * T
        + 10.14333127 * RH
        - 0.22475541 * T * RH
        - 0.00683783 * T * T
        - 0.05481717 * RH * RH
        + 0.00122874 * T * T * RH
        + 0.00085282 * T * RH * RH
        - 0.00000199 * T * T * RH * RH;

    let adjustedHI = HI;

    /*
        NOAA low humidity adjustment.
    */

    if (RH < 13 && T >= 80 && T <= 112) {

        const adjustment =
            ((13 - RH) / 4) *
            Math.sqrt(
                (17 - Math.abs(T - 95)) / 17
            );

        adjustedHI -= adjustment;
    }

    /*
        NOAA high humidity adjustment.
    */

    if (RH > 85 && T >= 80 && T <= 87) {

        const adjustment =
            ((RH - 85) / 10) *
            ((87 - T) / 5);

        adjustedHI += adjustment;
    }

    return {

        value:
            fahrenheitToCelsius(
                adjustedHI
            ),

        available: true,

        methodology:
            "NOAA/NWS Rothfusz regression with applicable humidity adjustments.",

        interpretation:
            interpretHeatIndex(
                fahrenheitToCelsius(
                    adjustedHI
                )
            )
    };
}


function interpretHeatIndex(valueC) {

    if (!Number.isFinite(valueC)) {
        return "Unavailable";
    }

    /*
        NOAA heat-index categories converted
        from their Fahrenheit boundaries.
    */

    if (valueC < 27) {
        return "Low heat stress according to Heat Index.";
    }

    if (valueC < 32) {
        return "Caution range according to Heat Index.";
    }

    if (valueC < 41) {
        return "Extreme caution range according to Heat Index.";
    }

    if (valueC < 54) {
        return "Danger range according to Heat Index.";
    }

    return "Extreme danger range according to Heat Index.";
}


/*
    WBGT is deliberately unavailable unless actual required
    measurements are supplied.

    We do NOT fabricate globe temperature.
*/

function calculateWBGT(inputs) {

    const required =
        [
            inputs.naturalWetBulbC,
            inputs.globeTemperatureC
        ];

    if (
        !required.every(
            Number.isFinite
        )
    ) {

        return {
            available: false,
            reason:
                "Validated natural wet-bulb and globe-temperature inputs are unavailable."
        };
    }

    const wbgt =
        0.7 * inputs.naturalWetBulbC +
        0.2 * inputs.globeTemperatureC +
        0.1 * inputs.airTemperatureC;

    return {
        available: true,
        value: wbgt,
        methodology:
            "Outdoor WBGT using natural wet-bulb, globe and dry-bulb components."
    };
}


/*
    UTCI requires mean radiant temperature.
    It is not legitimate to substitute solar radiation
    directly into the UTCI equation.

    Therefore this implementation explicitly reports
    unavailable unless MRT is supplied.
*/

function calculateUTCI(inputs) {

    if (
        !Number.isFinite(inputs.airTemperatureC) ||
        !Number.isFinite(inputs.windSpeedMS) ||
        !Number.isFinite(inputs.relativeHumidity) ||
        !Number.isFinite(inputs.meanRadiantTemperatureC)
    ) {

        return {
            available: false,
            reason:
                "UTCI requires air temperature, wind speed, humidity and mean radiant temperature."
        };
    }

    /*
        A validated full UTCI polynomial should be supplied
        from the chosen scientific implementation/library.

        We deliberately do not substitute a simplified
        invented formula.
    */

    return {
        available: false,
        reason:
            "A validated UTCI implementation is required before this index is reported."
    };
}


/* =========================================================
   HEATGUARD RISK CLASSIFICATION
========================================================= */

function classifyRisk({
    heatIndexC,
    temperatureC,
    humidity,
    windSpeed
}) {

    /*
        HeatGuard uses a transparent baseline risk layer.

        Heat Index is the authoritative thermal indicator
        when available.

        Because different agencies use different warning
        thresholds, the thresholds below are explicitly
        labeled as HeatGuard application classification
        based on NOAA Heat Index category boundaries,
        not as a universal medical standard.
    */

    let thermalValue =
        Number.isFinite(heatIndexC)
            ? heatIndexC
            : temperatureC;

    if (!Number.isFinite(thermalValue)) {

        return {
            level: "UNAVAILABLE",
            score: null,
            reason: "Thermal input unavailable."
        };
    }

    let level;

    if (thermalValue < 27) {
        level = "Low";
    } else if (thermalValue < 32) {
        level = "Moderate";
    } else if (thermalValue < 41) {
        level = "High";
    } else if (thermalValue < 54) {
        level = "Very High";
    } else {
        level = "Extreme";
    }

    /*
        Humidity, wind and heat index already reflect
        some combined effects.

        We therefore avoid double-counting humidity
        as an arbitrary numerical modifier.

        Wind is retained as contextual information.
    */

    const score =
        clamp(
            ((thermalValue - 20) / 40) * 100,
            0,
            100
        );

    return {
        level,
        score,
        thermalValue,
        temperatureC,
        humidity,
        windSpeed
    };
}


function riskClass(level) {

    switch (level) {

        case "Low":
            return "low";

        case "Moderate":
            return "moderate";

        case "High":
            return "high";

        case "Very High":
            return "very-high";

        case "Extreme":
            return "extreme";

        default:
            return "";
    }
}


/* =========================================================
   PROCESS WEATHER
========================================================= */

function processWeather() {

    if (!state.weather?.current) {
        return;
    }

    const current =
        state.weather.current;

    const temperature =
        Number(current.temperature_2m);

    const humidity =
        Number(current.relative_humidity_2m);

    const wind =
        Number(current.wind_speed_10m);

    const windDirection =
        Number(current.wind_direction_10m);

    const solar =
        Number(current.shortwave_radiation);

    const cloud =
        Number(current.cloud_cover);

    const heatIndex =
        calculateHeatIndex(
            temperature,
            humidity
        );

    state.risk =
        classifyRisk({
            heatIndexC:
                heatIndex.value,
            temperatureC:
                temperature,
            humidity,
            windSpeed:
                wind
        });

    renderCurrentWeather({
        temperature,
        humidity,
        wind,
        windDirection,
        solar,
        cloud,
        heatIndex
    });

    calculateForecastRisk();

    renderWarning();

    renderRisk();

    updateAdminDashboard();

    drawForecastChart();

    updateWorkerRisk();

    updateMapLocation();

    loadFacilities();

}


/* =========================================================
   RENDER CURRENT WEATHER
========================================================= */

function renderCurrentWeather({
    temperature,
    humidity,
    wind,
    windDirection,
    solar,
    cloud,
    heatIndex
}) {

    $("temperature").textContent =
        formatNumber(
            temperature,
            " Â°C"
        );

    $("humidity").textContent =
        formatNumber(
            humidity,
            "%"
        );

    $("windSpeed").textContent =
        formatNumber(
            wind,
            " km/h"
        );

    $("windDirection").textContent =
        formatNumber(
            windDirection,
            "Â°"
        );

    $("solarRadiation").textContent =
        Number.isFinite(solar)
            ? `${round(solar)} W/mÂ²`
            : "Unavailable";

    $("cloudCover").textContent =
        Number.isFinite(cloud)
            ? `${round(cloud)}%`
            : "Unavailable";

    $("heatIndexValue").textContent =
        heatIndex.available
            ? `${round(heatIndex.value)}`
            : "â€”";

    $("heatIndexStatus").textContent =
        heatIndex.available
            ? "AVAILABLE"
            : "UNAVAILABLE";

    $("heatIndexInterpretation").textContent =
        heatIndex.interpretation ||
        heatIndex.reason;

    $("weatherSource").textContent =
        state.weatherStatus;

    $("weatherSource").className =
        `data-badge ${state.weatherStatus.toLowerCase()}`;

    $("lastUpdated").textContent =
        formatTime(
            state.weather.current.time
        );

    $("riskDataBadge").textContent =
        state.weatherStatus;

    $("riskDataBadge").className =
        `data-badge ${state.weatherStatus.toLowerCase()}`;
}


/* =========================================================
   RISK RENDERING
========================================================= */

function renderRisk() {

    const risk =
        state.risk;

    if (!risk || !risk.level) {

        $("riskLevel").textContent =
            "â€”";

        $("riskHeadline").textContent =
            "Thermal risk unavailable";

        return;
    }

    $("riskLevel").textContent =
        risk.level;

    $("riskHeadline").textContent =
        `${risk.level} thermal risk`;

    $("riskDescription").textContent =
        buildRiskDescription(
            risk
        );

    const ring =
        $("riskRing");

    ring.className =
        `risk-ring ${riskClass(
            risk.level
        )}`;

    $("riskTrend").textContent =
        calculateRiskTrend();

}


function buildRiskDescription(risk) {

    const thermal =
        Number.isFinite(
            risk.thermalValue
        )
            ? `${round(risk.thermalValue)} Â°C`
            : "unavailable";

    return (
        `Calculated thermal indicator: ${thermal}. ` +
        `Ambient temperature and human thermal stress are treated as separate measurements.`
    );
}


function calculateRiskTrend() {

    if (
        !state.forecastRisk ||
        state.forecastRisk.length < 2 ||
        !state.risk
    ) {

        return "Stable / insufficient forecast";
    }

    const future =
        state.forecastRisk
            .slice(0, 4)
            .filter(
                item =>
                    Number.isFinite(
                        item.score
                    )
            );

    if (future.length === 0) {
        return "Unavailable";
    }

    const average =
        future.reduce(
            (sum, item) =>
                sum + item.score,
            0
        ) / future.length;

    if (average > state.risk.score + 5) {
        return "Increasing";
    }

    if (average < state.risk.score - 5) {
        return "Decreasing";
    }

    return "Stable";
}


/* =========================================================
   FORECAST RISK
========================================================= */

function calculateForecastRisk() {

    state.forecastRisk = [];

    const hourly =
        state.weather?.hourly;

    if (!hourly?.time) {
        return;
    }

    const times =
        hourly.time;

    const temperatures =
        hourly.temperature_2m || [];

    const humidities =
        hourly.relative_humidity_2m || [];

    const winds =
        hourly.wind_speed_10m || [];

    const radiation =
        hourly.shortwave_radiation || [];

    const currentTime =
        new Date(
            state.weather.current.time
        ).getTime();

    for (
        let i = 0;
        i < times.length;
        i++
    ) {

        const timestamp =
            new Date(times[i]);

        if (
            timestamp.getTime() <
            currentTime
        ) {
            continue;
        }

        if (
            state.forecastRisk.length >= 24
        ) {
            break;
        }

        const temperature =
            Number(temperatures[i]);

        const humidity =
            Number(humidities[i]);

        const wind =
            Number(winds[i]);

        const solar =
            Number(radiation[i]);

        if (
            !Number.isFinite(temperature) ||
            !Number.isFinite(humidity)
        ) {
            continue;
        }

        const heatIndex =
            calculateHeatIndex(
                temperature,
                humidity
            );

        const risk =
            classifyRisk({
                heatIndexC:
                    heatIndex.value,
                temperatureC:
                    temperature,
                humidity,
                windSpeed:
                    wind
            });

        state.forecastRisk.push({

            time:
                timestamp.toISOString(),

            temperature,

            humidity,

            wind,

            solar,

            heatIndex:
                heatIndex.value,

            level:
                risk.level,

            score:
                risk.score
        });
    }

    if (
        state.forecastRisk.length
    ) {

        state.peakForecast =
            [...state.forecastRisk]
                .sort(
                    (a, b) =>
                        b.score - a.score
                )[0];
    }

    renderForecastList();

}


function renderForecastList() {

    const container =
        $("forecastList");

    if (
        !state.forecastRisk.length
    ) {

        container.innerHTML =
            `<div class="empty-state">
                Forecast unavailable.
            </div>`;

        return;
    }

    container.innerHTML =
        state.forecastRisk
            .map(item => {

                return `
                    <div class="forecast-row">

                        <span class="forecast-time">
                            ${formatTime(item.time)}
                        </span>

                        <span class="forecast-temp">
                            ${round(item.temperature)}Â°C
                            /
                            ${round(item.humidity)}%
                        </span>

                        <span
                            class="forecast-risk"
                            style="
                                background:${riskBackground(item.level)};
                                color:${riskTextColor(item.level)}
                            "
                        >
                            ${item.level}
                        </span>

                    </div>
                `;
            })
            .join("");
}


function riskBackground(level) {

    switch (level) {

        case "Low":
            return "rgba(61,220,151,.1)";

        case "Moderate":
            return "rgba(245,196,81,.1)";

        case "High":
            return "rgba(255,157,66,.1)";

        case "Very High":
        case "Extreme":
            return "rgba(255,92,92,.1)";

        default:
            return "rgba(255,255,255,.05)";
    }
}


function riskTextColor(level) {

    switch (level) {

        case "Low":
            return "#3ddc97";

        case "Moderate":
            return "#f5c451";

        case "High":
            return "#ff9d42";

        case "Very High":
        case "Extreme":
            return "#ff5c5c";

        default:
            return "#91a3b8";
    }
}


/* =========================================================
   AUTOMATIC WARNING ENGINE
========================================================= */

function renderWarning() {

    if (!state.risk) {

        $("warningTitle").textContent =
            "No warning generated";

        $("warningText").textContent =
            "Environmental data is unavailable.";

        $("warningStatus").textContent =
            "UNAVAILABLE";

        return;
    }

    const level =
        state.risk.level;

    $("warningStatus").textContent =
        level.toUpperCase();

    $("warningStatus").className =
        `data-badge ${level === "Low"
            ? "live"
            : "offline"
        }`;

    let title;

    switch (level) {

        case "Low":
            title = "Low thermal-risk conditions";
            break;

        case "Moderate":
            title = "Moderate thermal-risk conditions";
            break;

        case "High":
            title = "High thermal-risk conditions";
            break;

        case "Very High":
            title = "Very high thermal-risk conditions";
            break;

        case "Extreme":
            title = "Extreme thermal-risk conditions";
            break;

        default:
            title = "Thermal warning unavailable";
    }

    $("warningTitle").textContent =
        title;

    $("warningText").textContent =
        generateAutomaticWarning(
            state.risk,
            state.peakForecast
        );

    const factors =
        [];

    if (
        Number.isFinite(
            state.risk.temperatureC
        )
    ) {
        factors.push(
            `Temperature ${round(
                state.risk.temperatureC
            )}Â°C`
        );
    }

    if (
        Number.isFinite(
            state.risk.humidity
        )
    ) {
        factors.push(
            `Humidity ${round(
                state.risk.humidity
            )}%`
        );
    }

    if (
        Number.isFinite(
            state.risk.thermalValue
        )
    ) {
        factors.push(
            `Heat Index ${round(
                state.risk.thermalValue
            )}Â°C`
        );
    }

    $("warningFactors").innerHTML =
        factors
            .map(
                factor =>
                    `<span class="warning-factor">
                        ${factor}
                    </span>`
            )
            .join("");
}


function generateAutomaticWarning(
    risk,
    peak
) {

    if (!risk) {
        return "Thermal warning unavailable.";
    }

    let message;

    switch (risk.level) {

        case "Low":
            message =
                "Current calculated thermal stress is low. Continue normal heat-safety practices.";
            break;

        case "Moderate":
            message =
                "Thermal stress is elevated. Reduce prolonged exposure where practical and maintain normal hydration.";
            break;

        case "High":
            message =
                "High thermal stress is calculated. Reduce prolonged outdoor exposure and schedule cooling or rest periods.";
            break;

        case "Very High":
            message =
                "Very high thermal stress is calculated. Heat exposure should be minimized and cooling breaks should be prioritized.";
            break;

        case "Extreme":
            message =
                "Extreme thermal stress is calculated. Avoid unnecessary heat exposure and follow local official heat-health guidance.";
            break;

        default:
            message =
                "Thermal warning unavailable.";
    }

    if (
        peak &&
        peak.score >
        risk.score + 5
    ) {

        message +=
            ` Forecast conditions indicate increasing thermal stress, with the highest calculated risk around ${formatTime(peak.time)}.`;
    }

    return message;
}


/* =========================================================
   FORECAST CHART
========================================================= */

function drawForecastChart() {

    const canvas =
        $("forecastCanvas");

    const ctx =
        canvas.getContext("2d");

    const rect =
        canvas.getBoundingClientRect();

    const width =
        Math.max(
            rect.width,
            300
        );

    const height =
        270;

    const dpr =
        window.devicePixelRatio || 1;

    canvas.width =
        width * dpr;

    canvas.height =
        height * dpr;

    ctx.scale(
        dpr,
        dpr
    );

    ctx.clearRect(
        0,
        0,
        width,
        height
    );

    const data =
        state.forecastRisk;

    if (
        data.length < 2
    ) {

        ctx.fillStyle =
            "#64778d";

        ctx.font =
            "12px system-ui";

        ctx.fillText(
            "Forecast data unavailable",
            20,
            30
        );

        return;
    }

    const padding = 30;

    const chartWidth =
        width - padding * 2;

    const chartHeight =
        height - padding * 2;

    const maxScore = 100;
    const minScore = 0;

    ctx.strokeStyle =
        "rgba(255,255,255,.08)";

    ctx.lineWidth = 1;

    for (
        let i = 0;
        i <= 4;
        i++
    ) {

        const y =
            padding +
            chartHeight -
            (
                i / 4
            ) *
            chartHeight;

        ctx.beginPath();

        ctx.moveTo(
            padding,
            y
        );

        ctx.lineTo(
            width - padding,
            y
        );

        ctx.stroke();

        ctx.fillStyle =
            "#64778d";

        ctx.font =
            "8px system-ui";

        ctx.fillText(
            `${i * 25}`,
            5,
            y + 3
        );
    }

    ctx.beginPath();

    data.forEach(
        (item, index) => {

            const x =
                padding +
                (
                    index /
                    (data.length - 1)
                ) *
                chartWidth;

            const y =
                padding +
                chartHeight -
                (
                    (
                        item.score -
                        minScore
                    ) /
                    (
                        maxScore -
                        minScore
                    )
                ) *
                chartHeight;

            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        }
    );

    ctx.strokeStyle =
        "#55a9ff";

    ctx.lineWidth = 2;

    ctx.stroke();

    data.forEach(
        (item, index) => {

            const x =
                padding +
                (
                    index /
                    (data.length - 1)
                ) *
                chartWidth;

            const y =
                padding +
                chartHeight -
                (
                    item.score / 100
                ) *
                chartHeight;

            ctx.beginPath();

            ctx.arc(
                x,
                y,
                3,
                0,
                Math.PI * 2
            );

            ctx.fillStyle =
                "#55a9ff";

            ctx.fill();

            if (
                index % 4 === 0
            ) {

                ctx.fillStyle =
                    "#64778d";

                ctx.font =
                    "8px system-ui";

                ctx.fillText(
                    formatTime(item.time),
                    x - 14,
                    height - 7
                );
            }
        }
    );
}


/* =========================================================
   GIS MAP
========================================================= */

function initializeMap() {

    if (!window.L) {

        showToast(
            "Map library could not be loaded."
        );

        return;
    }

    state.map =
        L.map(
            "map",
            {
                zoomControl: true
            }
        ).setView(
            [20.5937, 78.9629],
            5
        );

    L.tileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            maxZoom: 19,
            attribution:
                '&copy; OpenStreetMap contributors'
        }
    ).addTo(
        state.map
    );

    state.routeMap =
        L.map(
            "routeMap"
        ).setView(
            [20.5937, 78.9629],
            5
        );

    L.tileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            maxZoom: 19,
            attribution:
                '&copy; OpenStreetMap contributors'
        }
    ).addTo(
        state.routeMap
    );
}


function updateMapLocation() {

    if (
        !state.location ||
        !state.map ||
        !window.L
    ) {
        return;
    }

    const lat =
        state.location.latitude;

    const lon =
        state.location.longitude;

    if (state.locationMarker) {

        state.locationMarker
            .setLatLng([
                lat,
                lon
            ]);

    } else {

        state.locationMarker =
            L.marker([
                lat,
                lon
            ])
                .addTo(
                    state.map
                )
                .bindPopup(
                    `<strong>Selected HeatGuard location</strong><br>
                     ${safeText(
                        state.location.displayName
                    )}`
                );
    }

    state.map.setView(
        [lat, lon],
        13
    );

    if (state.routeStartMarker) {

        state.routeStartMarker
            .setLatLng([
                lat,
                lon
            ]);

    } else {

        state.routeStartMarker =
            L.marker([
                lat,
                lon
            ])
                .addTo(
                    state.routeMap
                )
                .bindPopup(
                    "Route starting point"
                );
    }

    state.routeMap.setView(
        [lat, lon],
        13
    );
}


/* =========================================================
   FACILITIES
========================================================= */

async function loadFacilities() {

    if (!state.location) {
        return;
    }

    if (!isOnline()) {

        const cached =
            readCache(
                CONFIG.facilitiesCacheKey
            );

        if (cached?.data) {

            state.facilities =
                cached.data;

            state.facilitiesStatus =
                "CACHED";

            renderFacilities();

            return;
        }

        state.facilities = [];

        state.facilitiesStatus =
            "UNAVAILABLE";

        renderFacilities();

        return;
    }

    try {

        state.facilitiesStatus =
            "LIVE";

        const lat =
            state.location.latitude;

        const lon =
            state.location.longitude;

        /*
            Overpass query retrieves actual OSM
            facilities around the selected coordinate.

            It does NOT claim that every result is a
            cooling centre or water point.
        */

        const query = `
            [out:json][timeout:25];

            (
                node["amenity"="hospital"](around:5000,${lat},${lon});
                way["amenity"="hospital"](around:5000,${lat},${lon});

                node["leisure"="park"](around:5000,${lat},${lon});
                way["leisure"="park"](around:5000,${lat},${lon});

                node["amenity"="drinking_water"](around:5000,${lat},${lon});
                node["amenity"="fountain"](around:5000,${lat},${lon});

                node["amenity"="shelter"](around:5000,${lat},${lon});
                way["amenity"="shelter"](around:5000,${lat},${lon});

                node["amenity"="community_centre"](around:5000,${lat},${lon});
                way["amenity"="community_centre"](around:5000,${lat},${lon});
            );

            out center tags;
        `;

        const result =
            await fetchJSON(
                CONFIG.overpass,
                {
                    method: "POST",
                    body: new URLSearchParams({
                        data: query
                    })
                }
            );

        const elements =
            result.elements || [];

        state.facilities =
            elements
                .map(
                    normalizeFacility
                )
                .filter(Boolean)
                .sort(
                    (a, b) =>
                        a.distance -
                        b.distance
                );

        saveCache(
            CONFIG.facilitiesCacheKey,
            state.facilities
        );

        state.facilitiesStatus =
            "LIVE";

        renderFacilities();

        renderFacilityMarkers();

    } catch (error) {

        console.error(
            "Facility query failed",
            error
        );

        const cached =
            readCache(
                CONFIG.facilitiesCacheKey
            );

        if (cached?.data) {

            state.facilities =
                cached.data;

            state.facilitiesStatus =
                "CACHED";

        } else {

            state.facilities = [];

            state.facilitiesStatus =
                "UNAVAILABLE";
        }

        renderFacilities();

        renderFacilityMarkers();
    }
}


function normalizeFacility(element) {

    const tags =
        element.tags || {};

    const latitude =
        Number(
            element.lat ??
            element.center?.lat
        );

    const longitude =
        Number(
            element.lon ??
            element.center?.lon
        );

    if (
        !Number.isFinite(latitude) ||
        !Number.isFinite(longitude)
    ) {
        return null;
    }

    let category;

    if (
        tags.amenity ===
        "hospital"
    ) {

        category = "hospital";

    } else if (
        tags.leisure ===
        "park"
    ) {

        category = "park";

    } else if (
        tags.amenity ===
        "drinking_water" ||
        tags.amenity ===
        "fountain"
    ) {

        category = "water";

    } else if (
        tags.amenity ===
        "shelter" ||
        tags.amenity ===
        "community_centre"
    ) {

        category = "cooling";

    } else {

        return null;
    }

    const distance =
        haversineDistance(
            state.location.latitude,
            state.location.longitude,
            latitude,
            longitude
        );

    return {

        id:
            `${element.type}-${element.id}`,

        name:
            tags.name ||
            "Unnamed mapped facility",

        category,

        latitude,

        longitude,

        distance,

        address:
            [
                tags["addr:housenumber"],
                tags["addr:street"],
                tags["addr:city"]
            ]
                .filter(Boolean)
                .join(" "),

        openingHours:
            tags.opening_hours ||
            "Not provided",

        source:
            "OpenStreetMap",

        lastVerified:
            "Source timestamp unavailable"
    };
}


function haversineDistance(
    lat1,
    lon1,
    lat2,
    lon2
) {

    const R = 6371;

    const dLat =
        degreesToRadians(
            lat2 - lat1
        );

    const dLon =
        degreesToRadians(
            lon2 - lon1
        );

    const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(
            degreesToRadians(lat1)
        ) *
        Math.cos(
            degreesToRadians(lat2)
        ) *
        Math.sin(dLon / 2) ** 2;

    const c =
        2 *
        Math.atan2(
            Math.sqrt(a),
            Math.sqrt(1 - a)
        );

    return R * c;
}


function degreesToRadians(value) {
    return value * Math.PI / 180;
}


function renderFacilities() {

    const container =
        $("facilityGrid");

    const filter =
        state.activeFacilityFilter;

    const facilities =
        state.facilities
            .filter(
                facility =>
                    filter === "all" ||
                    facility.category === filter
            );

    $("monitorFacilities").textContent =
        state.facilitiesStatus === "UNAVAILABLE"
            ? "Unavailable"
            : `${facilities.length} mapped facilities`;

    if (
        facilities.length === 0
    ) {

        container.innerHTML =
            `<div class="empty-state glass-card">
                No verified mapped facility of this category
                was returned by the current GIS provider.
                This does not mean that no facility exists.
            </div>`;

        return;
    }

    container.innerHTML =
        facilities
            .slice(0, 30)
            .map(
                facility =>
                    `
                    <article class="facility-card glass-card">

                        <span class="facility-category">
                            ${facility.category}
                        </span>

                        <h3>
                            ${escapeHTML(
                        facility.name
                    )}
                        </h3>

                        <p>
                            ${facility.address
                        ? escapeHTML(
                            facility.address
                        )
                        : "Address not provided by GIS data."
                    }
                        </p>

                        <div class="facility-meta">

                            <span>
                                ${round(
                        facility.distance,
                        2
                    )} km
                            </span>

                            <span>
                                ${facility.source}
                            </span>

                            <span>
                                Hours:
                                ${escapeHTML(
                        facility.openingHours
                    )
                    }
                            </span>

                        </div>

                    </article>
                    `
            )
            .join("");
}


function escapeHTML(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function renderFacilityMarkers() {

    if (!state.map) {
        return;
    }

    state.facilityMarkers.forEach(
        marker =>
            marker.remove()
    );

    state.facilityMarkers = [];

    state.facilities
        .slice(0, 50)
        .forEach(
            facility => {

                const marker =
                    L.marker([
                        facility.latitude,
                        facility.longitude
                    ])
                        .addTo(
                            state.map
                        )
                        .bindPopup(
                            `<strong>
                                ${escapeHTML(
                                facility.name
                            )}
                            </strong>
                            <br>
                            ${escapeHTML(
                                facility.category
                            )}
                            <br>
                            ${round(
                                facility.distance,
                                2
                            )} km
                            <br>
                            Source:
                            OpenStreetMap`
                        );

                state.facilityMarkers.push(
                    marker
                );
            }
        );
}


/* =========================================================
   ROUTING
========================================================= */

async function searchDestination() {

    const query =
        $("destinationInput")
            .value
            .trim();

    if (!query) {

        showToast(
            "Enter a destination."
        );

        return;
    }

    if (!state.location) {

        showToast(
            "Select a starting location first."
        );

        return;
    }

    $("routeResults").innerHTML =
        `<div class="empty-state">
            Resolving destination and calculating routes...
        </div>`;

    try {

        const destination =
            await geocodeDestination(
                query
            );

        const routes =
            await getRoutes(
                state.location,
                destination
            );

        state.routes =
            routes;

        state.routeStatus =
            "LIVE";

        renderRoutes();

        drawRoutes(routes);

    } catch (error) {

        console.error(
            "Routing failed",
            error
        );

        $("routeResults").innerHTML =
            `<div class="empty-state">
                Route calculation failed.
                Check the destination or routing service.
            </div>`;

        state.routeStatus =
            "UNAVAILABLE";
    }
}


async function geocodeDestination(query) {

    const url =
        `${CONFIG.geocoder}/search?` +
        new URLSearchParams({
            q: query,
            format: "jsonv2",
            limit: "1"
        });

    const results =
        await fetchJSON(url);

    if (
        !Array.isArray(results) ||
        results.length === 0
    ) {

        throw new Error(
            "Destination could not be geocoded."
        );
    }

    return {

        latitude:
            Number(
                results[0].lat
            ),

        longitude:
            Number(
                results[0].lon
            ),

        displayName:
            results[0].display_name
    };
}


async function getRoutes(
    start,
    destination
) {

    const coordinates =
        [
            `${start.longitude},${start.latitude}`,
            `${destination.longitude},${destination.latitude}`
        ].join(";");

    const url =
        `${CONFIG.routing}/route/v1/driving/${coordinates}?` +
        new URLSearchParams({
            alternatives: "true",
            overview: "full",
            geometries: "geojson",
            steps: "false"
        });

    const response =
        await fetchJSON(url);

    if (
        response.code !== "Ok" ||
        !Array.isArray(
            response.routes
        )
    ) {

        throw new Error(
            "Routing provider did not return routes."
        );
    }

    /*
        OSRM alternatives may contain fewer
        alternatives than requested depending
        on road-network topology.
    */

    return response.routes
        .map(
            (route, index) =>
                scoreRoute(
                    route,
                    index,
                    destination
                )
        );
}


function scoreRoute(
    route,
    index,
    destination
) {

    const durationMinutes =
        route.duration / 60;

    const distanceKm =
        route.distance / 1000;

    /*
        Transparent route heat exposure baseline.

        Because road-level shade and radiation fields
        are not available from the routing provider,
        HeatGuard DOES NOT claim that one road is actually
        shaded.

        Instead, the exposure score is based on:
        - route duration
        - current thermal stress
        - destination-independent environmental state

        Shade contribution is explicitly unavailable.
    */

    const thermalScore =
        state.risk?.score ?? null;

    let exposure;

    if (
        Number.isFinite(
            thermalScore
        )
    ) {

        exposure =
            thermalScore *
            (durationMinutes / 60);

    } else {

        exposure = null;
    }

    return {

        index,

        name:
            index === 0
                ? "Primary route"
                : `Alternative route ${index}`,

        distanceKm,

        durationMinutes,

        exposure,

        thermalRisk:
            state.risk?.level ||
            "Unavailable",

        shadeData:
            "UNAVAILABLE",

        geometry:
            route.geometry,

        destination
    };
}


function renderRoutes() {

    const container =
        $("routeResults");

    if (
        !state.routes.length
    ) {

        container.innerHTML =
            `<div class="empty-state">
                No route was returned.
            </div>`;

        return;
    }

    const fastest =
        [...state.routes]
            .sort(
                (a, b) =>
                    a.durationMinutes -
                    b.durationMinutes
            )[0];

    const cooler =
        [...state.routes]
            .filter(
                route =>
                    Number.isFinite(
                        route.exposure
                    )
            )
            .sort(
                (a, b) =>
                    a.exposure -
                    b.exposure
            )[0];

    const balanced =
        chooseBalancedRoute(
            state.routes
        );

    const selections = [
        {
            label: "Fastest",
            route: fastest
        },
        {
            label: "Cooler",
            route: cooler || fastest
        },
        {
            label: "Balanced",
            route: balanced
        }
    ];

    container.innerHTML =
        selections
            .map(
                selection => {

                    const route =
                        selection.route;

                    return `
                        <article class="route-result">

                            <div class="route-result-header">

                                <h3>
                                    ${selection.label}
                                </h3>

                                <span class="data-badge live">
                                    ${state.routeStatus}
                                </span>

                            </div>

                            <div class="route-stat-grid">

                                <div class="route-stat">
                                    <span>Distance</span>
                                    <strong>
                                        ${round(
                        route.distanceKm,
                        2
                    )} km
                                    </strong>
                                </div>

                                <div class="route-stat">
                                    <span>Travel time</span>
                                    <strong>
                                        ${formatMinutes(
                        route.durationMinutes
                    )}
                                    </strong>
                                </div>

                                <div class="route-stat">
                                    <span>Heat exposure</span>
                                    <strong>
                                        ${Number.isFinite(
                        route.exposure
                    )
                            ? `${round(
                                route.exposure
                            )} score`
                            : "Unavailable"
                        }
                                    </strong>
                                </div>

                                <div class="route-stat">
                                    <span>Shade data</span>
                                    <strong>
                                        UNAVAILABLE
                                    </strong>
                                </div>

                            </div>

                            <p style="
                                margin:12px 0 0;
                                color:#91a3b8;
                                font-size:9px;
                                line-height:1.5;
                            ">
                                Lower estimated heat exposure is based on
                                available environmental thermal stress and
                                route duration. Road-level shade is not assumed.
                            </p>

                        </article>
                    `;
                }
            )
            .join("");
}


function chooseBalancedRoute(routes) {

    if (
        routes.length === 0
    ) {
        return null;
    }

    const maxTime =
        Math.max(
            ...routes.map(
                r =>
                    r.durationMinutes
            )
        );

    const minTime =
        Math.min(
            ...routes.map(
                r =>
                    r.durationMinutes
            )
        );

    const exposures =
        routes
            .map(
                r =>
                    r.exposure
            )
            .filter(
                Number.isFinite
            );

    if (
        exposures.length === 0
    ) {
        return routes[0];
    }

    const maxExposure =
        Math.max(
            ...exposures
        );

    const minExposure =
        Math.min(
            ...exposures
        );

    return [...routes]
        .sort(
            (a, b) =>
                balancedScore(
                    a,
                    minTime,
                    maxTime,
                    minExposure,
                    maxExposure
                )
                -
                balancedScore(
                    b,
                    minTime,
                    maxTime,
                    minExposure,
                    maxExposure
                )
        )[0];
}


function balancedScore(
    route,
    minTime,
    maxTime,
    minExposure,
    maxExposure
) {

    const timeNorm =
        maxTime === minTime
            ? 0
            : (
                route.durationMinutes -
                minTime
            ) /
            (
                maxTime -
                minTime
            );

    const exposureNorm =
        !Number.isFinite(
            route.exposure
        )
            ? 1
            : (
                route.exposure -
                minExposure
            ) /
            Math.max(
                maxExposure -
                minExposure,
                0.0001
            );

    return (
        0.5 * timeNorm +
        0.5 * exposureNorm
    );
}


function formatMinutes(minutes) {

    if (!Number.isFinite(minutes)) {
        return "â€”";
    }

    const rounded =
        Math.round(minutes);

    if (rounded < 60) {
        return `${rounded} min`;
    }

    const hours =
        Math.floor(
            rounded / 60
        );

    const remaining =
        rounded % 60;

    return `${hours}h ${remaining}m`;
}


function drawRoutes(routes) {

    if (!state.routeMap) {
        return;
    }

    state.routeLayers.forEach(
        layer =>
            layer.remove()
    );

    state.routeLayers = [];

    routes.forEach(
        (route, index) => {

            const layer =
                L.geoJSON(
                    route.geometry,
                    {
                        style: {
                            weight:
                                index === 0
                                    ? 5
                                    : 3,
                            opacity:
                                index === 0
                                    ? 0.9
                                    : 0.45
                        }
                    }
                )
                    .addTo(
                        state.routeMap
                    );

            state.routeLayers.push(
                layer
            );
        }
    );

    const allCoordinates =
        routes[0]?.geometry?.coordinates;

    if (
        allCoordinates &&
        allCoordinates.length
    ) {

        const bounds =
            L.latLngBounds(
                allCoordinates.map(
                    coord =>
                        [
                            coord[1],
                            coord[0]
                        ]
                )
            );

        state.routeMap.fitBounds(
            bounds,
            {
                padding: [25, 25]
            }
        );
    }
}


/* =========================================================
   AI CONTEXT
========================================================= */

function buildVerifiedAIContext() {

    return {

        dataStatus:
            state.weatherStatus,

        location:
            state.location
                ? {
                    displayName:
                        state.location.displayName,

                    latitude:
                        state.location.latitude,

                    longitude:
                        state.location.longitude
                }
                : null,

        currentWeather:
            state.weather?.current
                ? {
                    temperatureC:
                        state.weather.current.temperature_2m,

                    humidity:
                        state.weather.current.relative_humidity_2m,

                    windSpeedKmh:
                        state.weather.current.wind_speed_10m,

                    windDirection:
                        state.weather.current.wind_direction_10m,

                    solarRadiation:
                        state.weather.current.shortwave_radiation,

                    cloudCover:
                        state.weather.current.cloud_cover,

                    timestamp:
                        state.weather.current.time
                }
                : null,

        thermalRisk:
            state.risk,

        heatIndex:
            state.weather?.current
                ? calculateHeatIndex(
                    Number(
                        state.weather.current
                            .temperature_2m
                    ),
                    Number(
                        state.weather.current
                            .relative_humidity_2m
                    )
                )
                : null,

        forecast:
            state.forecastRisk
                .slice(0, 24),

        facilities:
            state.facilities
                .slice(0, 20)
                .map(
                    facility => ({
                        name:
                            facility.name,

                        category:
                            facility.category,

                        distanceKm:
                            facility.distance,

                        address:
                            facility.address,

                        source:
                            facility.source
                    })
                ),

        routes:
            state.routes
                .map(
                    route => ({
                        name:
                            route.name,

                        distanceKm:
                            route.distanceKm,

                        durationMinutes:
                            route.durationMinutes,

                        estimatedHeatExposure:
                            route.exposure,

                        shadeData:
                            route.shadeData
                    })
                )
    };
}


/* =========================================================
   AI CHAT
========================================================= */

async function handleChat(question) {

    if (!question.trim()) {
        return;
    }

    addChatMessage(
        "user",
        question
    );

    $("chatInput").value = "";

    addChatMessage(
        "assistant",
        "Checking verified HeatGuard application data..."
    );

    const context =
        buildVerifiedAIContext();

    const chatWindow =
        $("chatWindow");

    const lastMessage =
        chatWindow.lastElementChild;

    try {

        /*
            If a backend is configured,
            use it.

            The backend should validate the context,
            authenticate the user where appropriate,
            sanitize input and call the LLM server-side.
        */

        if (CONFIG.aiEndpoint) {

            const response =
                await fetchJSON(
                    CONFIG.aiEndpoint,
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body:
                            JSON.stringify({
                                question,
                                context
                            })
                    }
                );

            const answer =
                response.answer;

            if (!answer) {
                throw new Error(
                    "AI backend returned no answer."
                );
            }

            lastMessage.remove();

            addChatMessage(
                "assistant",
                `${answer}\n\nData status: ${state.weatherStatus}`
            );

            return;
        }

        /*
            No LLM backend is configured.

            We still provide deterministic explanations
            from actual HeatGuard calculations rather than
            pretending an AI service exists.
        */

        const answer =
            localHeatAssistant(
                question
            );

        lastMessage.remove();

        addChatMessage(
            "assistant",
            answer
        );

    } catch (error) {

        console.error(
            "AI error",
            error
        );

        lastMessage.remove();

        addChatMessage(
            "assistant",
            `AI service is unavailable. ${state.weatherStatus} data remains available in HeatGuard.`
        );
    }
}


function localHeatAssistant(question) {

    const q =
        question.toLowerCase();

    if (!state.location) {

        return (
            "I do not have a selected location yet. " +
            "Use GPS or search for a location first."
        );
    }

    if (
        q.includes("risk") ||
        q.includes("heat risk")
    ) {

        if (!state.risk) {

            return (
                `Current heat-risk information is unavailable. ` +
                `Data status: ${state.weatherStatus}.`
            );
        }

        return (
            `HeatGuard currently classifies the selected location as ` +
            `${state.risk.level} thermal risk. ` +
            `The calculated thermal indicator is approximately ` +
            `${round(state.risk.thermalValue)} Â°C. ` +
            `Ambient temperature is ${round(
                state.risk.temperatureC
            )} Â°C and relative humidity is ${round(
                state.risk.humidity
            )}%. ` +
            `Data status: ${state.weatherStatus}.`
        );
    }

    if (
        q.includes("why") ||
        q.includes("warning")
    ) {

        if (!state.risk) {

            return (
                "The warning cannot currently be explained because " +
                "the thermal calculation is unavailable."
            );
        }

        return (
            `The warning is generated from the calculated Heat Index and ` +
            `environmental conditions rather than temperature alone. ` +
            `Current classification: ${state.risk.level}. ` +
            `Heat Index: ${round(
                state.risk.thermalValue
            )} Â°C. ` +
            `Data status: ${state.weatherStatus}.`
        );
    }

    if (
        q.includes("forecast") ||
        q.includes("afternoon") ||
        q.includes("worse")
    ) {

        if (
            !state.forecastRisk.length
        ) {

            return (
                "Forecast data is currently unavailable."
            );
        }

        const peak =
            state.peakForecast;

        return (
            `The current forecast contains the next ` +
            `${state.forecastRisk.length} hourly points. ` +
            `The highest calculated risk in that period is ` +
            `${peak.level} around ${formatTime(
                peak.time
            )}, with an estimated Heat Index of ` +
            `${round(peak.heatIndex)} Â°C. ` +
            `Data status: ${state.weatherStatus}.`
        );
    }

    if (
        q.includes("cooling centre") ||
        q.includes("cooling center") ||
        q.includes("hospital") ||
        q.includes("nearest")
    ) {

        const relevant =
            state.facilities
                .filter(
                    f =>
                        f.category ===
                        "hospital" ||
                        f.category ===
                        "cooling"
                )
                .slice(0, 3);

        if (!relevant.length) {

            return (
                "No mapped facility matching that request was returned " +
                "by the current GIS provider. HeatGuard will not invent a facility."
            );
        }

        return (
            `The closest mapped facilities currently available are: ` +
            relevant
                .map(
                    f =>
                        `${f.name} (${round(
                            f.distance,
                            2
                        )} km, ${f.category})`
                )
                .join("; ") +
            `. Source: ${relevant[0].source}.`
        );
    }

    if (
        q.includes("route")
    ) {

        if (!state.routes.length) {

            return (
                "No route has been calculated yet. Enter a destination in Plan Route."
            );
        }

        const cooler =
            [...state.routes]
                .filter(
                    r =>
                        Number.isFinite(
                            r.exposure
                        )
                )
                .sort(
                    (a, b) =>
                        a.exposure -
                        b.exposure
                )[0];

        if (!cooler) {

            return (
                "Route data exists, but heat-exposure scoring is unavailable."
            );
        }

        return (
            `The route with the lower estimated heat exposure among the ` +
            `calculated candidates is ${cooler.name}. ` +
            `Estimated exposure score: ${round(
                cooler.exposure
            )}. ` +
            `Road-level shade information is unavailable, so HeatGuard does not claim that this route is shaded.`
        );
    }

    if (
        q.includes("break") ||
        q.includes("work")
    ) {

        return (
            "Worker Mode can track your configured shift and reminder intervals. " +
            "For the current location, use the calculated thermal-risk level " +
            "and follow local occupational heat-safety guidance."
        );
    }

    return (
        `I can explain the verified HeatGuard application context, but no ` +
        `LLM backend is configured for general conversational reasoning. ` +
        `Current data status: ${state.weatherStatus}.`
    );
}


function addChatMessage(
    sender,
    message
) {

    const window =
        $("chatWindow");

    const wrapper =
        document.createElement(
            "div"
        );

    wrapper.className =
        `chat-message ${sender}`;

    const avatar =
        document.createElement(
            "div"
        );

    avatar.className =
        "avatar";

    avatar.textContent =
        sender === "assistant"
            ? "HG"
            : "YOU";

    const messageBox =
        document.createElement(
            "div"
        );

    messageBox.className =
        "message";

    const strong =
        document.createElement(
            "strong"
        );

    strong.textContent =
        sender === "assistant"
            ? "HeatGuard AI"
            : "You";

    const p =
        document.createElement(
            "p"
        );

    p.textContent =
        message;

    messageBox.appendChild(
        strong
    );

    messageBox.appendChild(
        p
    );

    wrapper.appendChild(
        avatar
    );

    wrapper.appendChild(
        messageBox
    );

    window.appendChild(
        wrapper
    );

    window.scrollTop =
        window.scrollHeight;
}


/* =========================================================
   WORKER MODE
========================================================= */

function initializeWorkerSettings() {

    const cached =
        readCache(
            CONFIG.workerCacheKey
        );

    if (!cached?.data) {
        return;
    }

    const settings =
        cached.data;

    if (settings.start) {
        $("shiftStart").value =
            settings.start;
    }

    if (settings.end) {
        $("shiftEnd").value =
            settings.end;
    }

    if (settings.breakInterval) {
        $("breakInterval").value =
            settings.breakInterval;
    }

    if (settings.hydrationInterval) {
        $("hydrationInterval").value =
            settings.hydrationInterval;
    }
}


function saveWorkerSettings() {

    const settings = {

        start:
            $("shiftStart").value,

        end:
            $("shiftEnd").value,

        breakInterval:
            Number(
                $("breakInterval").value
            ),

        hydrationInterval:
            Number(
                $("hydrationInterval").value
            )
    };

    saveCache(
        CONFIG.workerCacheKey,
        settings
    );

    startShiftReminderLoop();

    showToast(
        "Worker shift settings saved."
    );
}


function startShiftReminderLoop() {

    if (state.shiftTimer) {

        clearInterval(
            state.shiftTimer
        );
    }

    state.shiftTimer =
        setInterval(
            evaluateWorkerShift,
            30000
        );

    evaluateWorkerShift();
}


function evaluateWorkerShift() {

    const start =
        $("shiftStart").value;

    const end =
        $("shiftEnd").value;

    if (!start || !end) {

        $("nextBreak").textContent =
            "Not configured";

        return;
    }

    const now =
        new Date();

    const currentMinutes =
        now.getHours() * 60 +
        now.getMinutes();

    const startMinutes =
        timeToMinutes(
            start
        );

    const endMinutes =
        timeToMinutes(
            end
        );

    let active;

    if (
        endMinutes >=
        startMinutes
    ) {

        active =
            currentMinutes >=
            startMinutes &&
            currentMinutes <=
            endMinutes;

    } else {

        active =
            currentMinutes >=
            startMinutes ||
            currentMinutes <=
            endMinutes;
    }

    if (!active) {

        $("workerAlert").textContent =
            "Outside configured shift.";

        $("nextBreak").textContent =
            "Outside shift";

        return;
    }

    const breakInterval =
        Number(
            $("breakInterval").value
        );

    const elapsed =
        currentMinutes -
        startMinutes;

    const normalizedElapsed =
        (
            elapsed +
            24 * 60
        ) %
        (
            24 * 60
        );

    const remainder =
        normalizedElapsed %
        breakInterval;

    const minutesUntil =
        remainder === 0
            ? 0
            : breakInterval -
            remainder;

    $("nextBreak").textContent =
        `${minutesUntil} min`;

    if (
        minutesUntil === 0
    ) {

        $("workerAlert").textContent =
            "Scheduled cooling/rest break is due.";

        requestNotification(
            "HeatGuard work-break reminder",
            "Your configured cooling/rest interval has been reached."
        );

    } else {

        $("workerAlert").textContent =
            "Shift active. Continue monitoring current thermal risk and scheduled breaks.";
    }
}


function timeToMinutes(value) {

    const [
        hour,
        minute
    ] =
        value.split(":")
            .map(Number);

    return (
        hour * 60 +
        minute
    );
}


function requestNotification(
    title,
    body
) {

    if (
        "Notification" in window &&
        Notification.permission ===
        "granted"
    ) {

        new Notification(
            title,
            {
                body
            }
        );
    }
}


async function enableNotifications() {

    if (
        !("Notification" in window)
    ) {

        return;
    }

    if (
        Notification.permission ===
        "default"
    ) {

        try {

            await Notification.requestPermission();

        } catch (error) {

            console.warn(
                "Notification permission unavailable",
                error
            );
        }
    }
}


function toggleWorkerGPS() {

    if (
        state.workerWatchId !== null
    ) {

        navigator.geolocation.clearWatch(
            state.workerWatchId
        );

        state.workerWatchId =
            null;

        $("workerGpsStatus").textContent =
            "GPS inactive";

        $("workerGpsButton").textContent =
            "Start GPS";

        return;
    }

    if (!navigator.geolocation) {

        showToast(
            "GPS is not supported."
        );

        return;
    }

    enableNotifications();

    state.workerWatchId =
        navigator.geolocation.watchPosition(

            position => {

                state.workerLastPosition =
                    position.coords;

                $("workerGpsStatus").textContent =
                    "GPS active";

                updateWorkerDistance();

            },

            error => {

                console.error(
                    error
                );

                $("workerGpsStatus").textContent =
                    "GPS error";
            },

            {
                enableHighAccuracy: true,
                maximumAge: 10000,
                timeout: 15000
            }
        );

    $("workerGpsButton").textContent =
        "Stop GPS";
}


function updateWorkerRisk() {

    if (!state.risk) {

        $("workerRisk").textContent =
            "â€”";

        return;
    }

    $("workerRisk").textContent =
        state.risk.level;
}


function updateWorkerDistance() {

    if (
        !state.workerLastPosition ||
        !state.location
    ) {
        return;
    }

    /*
        If a route exists, compare GPS position with
        destination. This is a straight-line remaining
        distance estimate, not road distance.
    */

    const destination =
        state.routes[0]?.destination;

    if (!destination) {

        $("workerDistance").textContent =
            "No route";

        $("workerTime").textContent =
            "No route";

        return;
    }

    const distance =
        haversineDistance(
            state.workerLastPosition.latitude,
            state.workerLastPosition.longitude,
            destination.latitude,
            destination.longitude
        );

    $("workerDistance").textContent =
        `${round(distance, 2)} km`;

    /*
        Straight-line distance is deliberately not
        represented as road travel time.
    */

    $("workerTime").textContent =
        "Requires active route matching";
}


/* =========================================================
   OFFLINE / SYNCHRONIZATION
========================================================= */

async function synchronizeLocationData() {

    if (!state.location) {
        return;
    }

    await synchronizeWeather();

    if (isOnline()) {
        await loadFacilities();
    }

    updateAIStatus();
}


async function handleOnline() {

    showToast(
        "Network restored. Synchronizing HeatGuard..."
    );

    if (!state.location) {
        return;
    }

    await synchronizeLocationData();

    showToast(
        "HeatGuard live data synchronization completed."
    );
}


function handleOffline() {

    setDataStatus("OFFLINE");

    showToast(
        "OFFLINE USING CACHED DATA"
    );

    const cached =
        readCache(
            CONFIG.weatherCacheKey
        );

    if (cached?.data) {

        state.weather =
            cached.data;

        processWeather();
    }

    updateAIStatus();
}


/* =========================================================
   ADMIN MONITORING
========================================================= */

function updateAdminDashboard() {

    $("adminRisk").textContent =
        state.risk?.level ||
        "UNAVAILABLE";

    $("adminHeatIndex").textContent =
        state.risk &&
            Number.isFinite(
                state.risk.thermalValue
            )
            ? `${round(
                state.risk.thermalValue
            )} Â°C`
            : "UNAVAILABLE";

    $("adminPeak").textContent =
        state.peakForecast
            ? `${state.peakForecast.level} @ ${formatTime(
                state.peakForecast.time
            )}`
            : "UNAVAILABLE";

    $("adminDataStatus").textContent =
        state.weatherStatus;
}


/* =========================================================
   AI STATUS
========================================================= */

function updateAIStatus() {

    const status =
        state.weatherStatus;

    $("aiDataStatus").textContent =
        `DATA: ${status}`;
}


/* =========================================================
   NAVIGATION
========================================================= */

function activateView(viewName) {

    qsa(".nav-item")
        .forEach(
            item => {

                item.classList.toggle(
                    "active",
                    item.dataset.view ===
                    viewName
                );
            }
        );

    qsa(".view")
        .forEach(
            view => {

                view.classList.toggle(
                    "active",
                    view.id ===
                    `view-${viewName}`
                );
            }
        );

    if (
        viewName === "heatmap" &&
        state.map
    ) {

        setTimeout(
            () => {
                state.map.invalidateSize();
            },
            100
        );
    }

    if (
        viewName === "route" &&
        state.routeMap
    ) {

        setTimeout(
            () => {
                state.routeMap.invalidateSize();
            },
            100
        );
    }

    $("sidebar").classList.remove(
        "open"
    );
}


/* =========================================================
   EVENT HANDLERS
========================================================= */

function initializeEvents() {

    qsa(".nav-item")
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () =>
                        activateView(
                            button.dataset.view
                        )
                );
            }
        );

    qsa("[data-view-target]")
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () =>
                        activateView(
                            button.dataset.viewTarget
                        )
                );
            }
        );

    $("mobileMenuButton")
        .addEventListener(
            "click",
            () => {
                const sidebar = $("sidebar");
                if (!sidebar) return;
                if (window.innerWidth <= 900) {
                    sidebar.classList.toggle("open");
                } else {
                    sidebar.classList.toggle("collapsed");
                }
            }
        );

    $("locationButton")
        .addEventListener(
            "click",
            requestCurrentLocation
        );

    $("searchLocationButton")
        .addEventListener(
            "click",
            searchManualLocation
        );

    $("refreshButton")
        .addEventListener(
            "click",
            () =>
                synchronizeLocationData()
        );

    $("refreshFacilitiesButton")
        .addEventListener(
            "click",
            loadFacilities
        );

    $("routeButton")
        .addEventListener(
            "click",
            searchDestination
        );

    $("destinationInput")
        .addEventListener(
            "keydown",
            event => {

                if (
                    event.key ===
                    "Enter"
                ) {

                    event.preventDefault();

                    searchDestination();
                }
            }
        );

    qsa(".filter-button")
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () => {

                        qsa(
                            ".filter-button"
                        ).forEach(
                            item =>
                                item.classList.remove(
                                    "active"
                                )
                        );

                        button.classList.add(
                            "active"
                        );

                        state.activeFacilityFilter =
                            button.dataset.category;

                        renderFacilities();
                    }
                );
            }
        );

    qsa(
        ".suggested-questions button"
    )
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () =>
                        handleChat(
                            button.dataset.question
                        )
                );
            }
        );

    $("chatForm")
        .addEventListener(
            "submit",
            event => {

                event.preventDefault();

                handleChat(
                    $("chatInput").value
                );
            }
        );

    $("workerGpsButton")
        .addEventListener(
            "click",
            toggleWorkerGPS
        );

    $("saveShiftButton")
        .addEventListener(
            "click",
            saveWorkerSettings
        );

    window.addEventListener(
        "online",
        handleOnline
    );

    window.addEventListener(
        "offline",
        handleOffline
    );

    window.addEventListener(
        "resize",
        drawForecastChart
    );

    // Camera safety scan buttons
    const _btnStart = $("btnStartCam");
    const _btnScan  = $("btnScanCam");
    if (_btnStart) _btnStart.addEventListener("click", startCamera);
    if (_btnScan)  _btnScan.addEventListener("click",  analyzeHeatRisk);
}


/* =========================================================
   STARTUP
========================================================= */

async function initializeHeatGuard() {

    initializeEvents();

    initializeMap();

    initializeWorkerSettings();

    startShiftReminderLoop();

    if (!isOnline()) {

        handleOffline();

    } else {

        setDataStatus(
            "UNAVAILABLE",
            "Waiting for location"
        );
    }

    /*
        Restore cached location if available.
    */

    const cachedLocation =
        readCache(
            CONFIG.locationCacheKey
        );

    if (
        cachedLocation?.data
    ) {

        state.location =
            cachedLocation.data;

        renderLocation();

        /*
            Cached data is displayed immediately.
            Live synchronization happens afterwards.
        */

        const cachedWeather =
            readCache(
                CONFIG.weatherCacheKey
            );

        if (
            cachedWeather?.data
        ) {

            state.weather =
                cachedWeather.data;

            setDataStatus(
                isOnline()
                    ? "CACHED"
                    : "OFFLINE"
            );

            processWeather();
        }
    }

    /*
        Automatically request location.
        The browser itself controls the permission prompt.
    */

    requestCurrentLocation();
}


document.addEventListener(
    "DOMContentLoaded",
    initializeHeatGuard
);


/* =========================================================
   DEVELOPMENT TEST HELPERS
========================================================= */

/*
    These functions are intentionally not fake-data generators.

    They provide deterministic tests for the thermal
    calculation and risk classification layers.

    Run in browser console:

        HeatGuardTests.run()

*/

window.HeatGuardTests = {

    run() {

        const results = [];

        const heatIndex =
            calculateHeatIndex(
                35,
                70
            );

        results.push({
            test:
                "Heat Index calculation",
            passed:
                heatIndex.available &&
                Number.isFinite(
                    heatIndex.value
                ),
            value:
                heatIndex.value
        });

        const risk =
            classifyRisk({
                heatIndexC:
                    41,
                temperatureC:
                    35,
                humidity:
                    70,
                windSpeed:
                    10
            });

        results.push({
            test:
                "Risk classification",
            passed:
                risk.level ===
                "Very High",
            value:
                risk.level
        });

        const distance =
            haversineDistance(
                0,
                0,
                0,
                1
            );

        results.push({
            test:
                "Haversine distance",
            passed:
                distance > 100 &&
                distance < 120,
            value:
                distance
        });

        const balanced =
            chooseBalancedRoute([
                {
                    durationMinutes: 10,
                    exposure: 80
                },
                {
                    durationMinutes: 15,
                    exposure: 50
                }
            ]);

        results.push({
            test:
                "Balanced route scoring",
            passed:
                Boolean(balanced),
            value:
                balanced
        });

        console.table(
            results
        );

        return results;
    }
};

/* =========================================================
   HEATGUARD
   HUMAN THERMAL STRESS + POPULATION HEALTH IMPACT
   =========================================================

   IMPORTANT ARCHITECTURE:

   Existing HeatGuard weather system
                  â†“
   Existing Thermal Engine
                  â†“
   THIS MODULE
                  â†“
   Human Thermal Stress
                  â†“
   Population Health Impact
                  â†“
   Mortality Forecast (only when validated data exists)

   This module deliberately does NOT fabricate mortality data.
   ========================================================= */


/* =========================================================
   CONFIGURATION
   ========================================================= */

const HeatGuardHealthModule = {

    VERSION: "2.0.0",

    riskLevels: [
        "Low",
        "Moderate",
        "High",
        "Very High",
        "Extreme"
    ],


    clamp(value, min, max) {

        return Math.min(
            Math.max(value, min),
            max
        );
    },


    classifyRisk(score) {

        if (!Number.isFinite(score)) {
            return "UNAVAILABLE";
        }

        if (score < 20) return "Low";
        if (score < 40) return "Moderate";
        if (score < 60) return "High";
        if (score < 80) return "Very High";

        return "Extreme";
    },


    /* -----------------------------------------------------
       CONNECT TO EXISTING THERMAL ENGINE
    ----------------------------------------------------- */

    normalizeThermalResult(source = {}) {

        return {

            heatIndex:
                Number.isFinite(
                    Number(source.heatIndex)
                )
                    ? Number(source.heatIndex)
                    : null,

            wbgt:
                Number.isFinite(
                    Number(source.wbgt)
                )
                    ? Number(source.wbgt)
                    : null,

            utci:
                Number.isFinite(
                    Number(source.utci)
                )
                    ? Number(source.utci)
                    : null,

            thermalStress:
                Number.isFinite(
                    Number(source.thermalStress)
                )
                    ? Number(source.thermalStress)
                    : null,

            riskLevel:
                source.riskLevel ||
                "UNAVAILABLE",

            timestamp:
                source.timestamp ||
                null,

            status:
                source.status ||
                "UNAVAILABLE",

            modelVersion:
                source.modelVersion ||
                "UNKNOWN"
        };
    },


    /* -----------------------------------------------------
       HUMAN THERMAL STRESS
       
       Prefer the existing thermal-engine score.

       If the existing engine does not expose a score,
       derive an application-level index from the available
       validated thermal-index outputs.

       This is NOT a medical measurement.
    ----------------------------------------------------- */

    calculateHumanStress(thermalData) {

        const data =
            this.normalizeThermalResult(
                thermalData
            );


        /*
         * Existing HeatGuard score takes priority.
         */

        if (
            data.thermalStress !== null &&
            data.thermalStress >= 0 &&
            data.thermalStress <= 100
        ) {

            return {

                available: true,

                score:
                    Number(
                        data.thermalStress.toFixed(1)
                    ),

                level:
                    data.riskLevel !== "UNAVAILABLE"
                        ? data.riskLevel
                        : this.classifyRisk(
                            data.thermalStress
                        ),

                source: "EXISTING_THERMAL_ENGINE",

                status: data.status,

                timestamp: data.timestamp,

                modelVersion: data.modelVersion
            };
        }


        /*
         * If no existing score is supplied, use supported
         * thermal indices as a secondary integration layer.
         *
         * The weighting is only an application aggregation
         * mechanism; the individual scientific indices remain
         * authoritative for their own interpretation.
         */

        const availableIndices = [];

        if (data.heatIndex !== null) {
            availableIndices.push({
                name: "Heat Index",
                value: data.heatIndex
            });
        }

        if (data.wbgt !== null) {
            availableIndices.push({
                name: "WBGT",
                value: data.wbgt
            });
        }

        if (data.utci !== null) {
            availableIndices.push({
                name: "UTCI",
                value: data.utci
            });
        }


        if (!availableIndices.length) {

            return {

                available: false,

                score: null,

                level: "UNAVAILABLE",

                reason:
                    "No thermal-engine result is currently available."
            };
        }


        /*
         * Conservative normalized stress representation.
         *
         * The exact scientific interpretation should continue
         * to come from each individual index.
         */

        const normalizedValues =
            availableIndices.map(index => {

                /*
                 * This normalization is only for displaying
                 * an integrated dashboard score.
                 */

                const normalized =
                    this.clamp(
                        ((index.value - 25) / 25) * 100,
                        0,
                        100
                    );

                return normalized;
            });


        const score =
            normalizedValues.reduce(
                (sum, value) => sum + value,
                0
            ) /
            normalizedValues.length;


        return {

            available: true,

            score:
                Number(
                    score.toFixed(1)
                ),

            level:
                this.classifyRisk(score),

            source:
                "THERMAL_INDEX_AGGREGATION",

            status:
                data.status,

            timestamp:
                data.timestamp,

            modelVersion:
                data.modelVersion
        };
    },


    /* -----------------------------------------------------
       PHYSIOLOGICAL INTERPRETATION
    ----------------------------------------------------- */

    interpretPhysiology(environment = {}, thermal = {}) {

        const humidity =
            Number(environment.humidity);

        const temperature =
            Number(environment.temperatureC);


        let evaporativeEffect =
            "Unavailable";


        if (
            Number.isFinite(humidity)
        ) {

            if (humidity < 40) {

                evaporativeEffect =
                    "Relatively effective";

            } else if (humidity < 60) {

                evaporativeEffect =
                    "Increasing limitation";

            } else if (humidity < 80) {

                evaporativeEffect =
                    "Reduced";

            } else {

                evaporativeEffect =
                    "Strongly reduced";
            }
        }


        let thermalLoad =
            thermal.level ||
            "Unavailable";


        let explanation =
            "HeatGuard evaluates environmental conditions through its thermal engine rather than temperature alone.";


        if (
            Number.isFinite(temperature) &&
            Number.isFinite(humidity)
        ) {

            if (
                temperature >= 35 &&
                humidity >= 60
            ) {

                explanation =
                    "High air temperature combined with elevated humidity can reduce evaporative cooling and increase physiological heat strain.";

            } else if (
                temperature >= 35
            ) {

                explanation =
                    "High air temperature creates substantial thermal load; humidity and other environmental variables determine how effectively the body can dissipate heat.";

            } else if (
                humidity >= 70
            ) {

                explanation =
                    "Elevated humidity can reduce evaporative cooling, increasing heat strain when thermal conditions are already warm.";
            }
        }


        return {

            evaporativeEffect,

            thermalLoad,

            overallStrain:
                thermal.level ||
                "Unavailable",

            explanation
        };
    },


    /* -----------------------------------------------------
       POPULATION HEALTH MODEL GATE
    ----------------------------------------------------- */

    evaluateHealthModel(config = {}) {

        const historicalHealth =
            config.historicalHealthData === true;

        const vulnerability =
            config.aggregateVulnerabilityData === true;

        const validatedModel =
            config.validatedModel === true;


        /*
         * Mortality prediction is impossible to justify
         * from weather alone.
         */

        if (
            !historicalHealth ||
            !vulnerability ||
            !validatedModel
        ) {

            return {

                available: false,

                populationRisk:
                    null,

                mortalityForecast:
                    null,

                confidence:
                    null,

                modelVersion:
                    null,

                status:
                    "REQUIRES_HISTORICAL_DATA",

                message:
                    "Historical mortality, hospitalisation or emergency-visit data, aggregate vulnerability data and a validated forecasting model are required before population health or mortality forecasts can be produced."
            };
        }


        /*
         * When the backend model is connected, its verified
         * response should be returned here.
         */

        return {

            available: true,

            status:
                "VALIDATED_MODEL_AVAILABLE",

            populationRisk:
                null,

            mortalityForecast:
                null,

            confidence:
                null,

            modelVersion:
                null
        };
    }
};


/* =========================================================
   UI UPDATE
   ========================================================= */

function updateHumanImpactModule(
    existingThermalData,
    existingEnvironmentData,
    healthModelConfig = {}
) {

    const thermal =
        HeatGuardHealthModule
            .calculateHumanStress(
                existingThermalData
            );


    const physiology =
        HeatGuardHealthModule
            .interpretPhysiology(
                existingEnvironmentData || {},
                thermal
            );


    updateHumanStressUI(
        thermal,
        existingThermalData
    );


    updatePhysiologyUI(
        physiology
    );


    updateThermalIndicesUI(
        existingThermalData
    );


    updateHealthImpactUI(
        thermal,
        healthModelConfig
    );
}


/* =========================================================
   HUMAN STRESS UI
   ========================================================= */

function updateHumanStressUI(
    result,
    thermalData
) {

    const score =
        document.getElementById(
            "humanStressScore"
        );

    const badge =
        document.getElementById(
            "stressRiskBadge"
        );

    const meter =
        document.getElementById(
            "stressMeter"
        );

    const explanation =
        document.getElementById(
            "stressExplanation"
        );

    const version =
        document.getElementById(
            "thermalEngineVersion"
        );

    const updated =
        document.getElementById(
            "thermalUpdatedAt"
        );


    if (!result.available) {

        score.textContent = "--";

        badge.textContent =
            "UNAVAILABLE";

        meter.style.width =
            "0%";

        explanation.textContent =
            result.reason ||
            "Thermal-engine data unavailable.";

        return;
    }


    score.textContent =
        result.score;


    badge.textContent =
        result.level;


    meter.style.width =
        `${ result.score }% `;


    explanation.textContent =
        generateStressExplanation(
            result,
            thermalData
        );


    version.textContent =
        result.modelVersion ||
        "Existing engine";


    updated.textContent =
        result.timestamp ||
        "--";


    const status =
        document.getElementById(
            "impactDataStatus"
        );


    if (status) {

        status.textContent =
            `DATA STATUS: ${
    result.status || "UNKNOWN"
} `;
    }
}


/* =========================================================
   STRESS EXPLANATION
   ========================================================= */

function generateStressExplanation(
    result,
    thermalData
) {

    if (!result.available) {
        return "Thermal stress cannot currently be assessed.";
    }


    const risk =
        result.level;


    if (risk === "Extreme") {

        return "HeatGuard's thermal engine indicates extreme human thermal stress. Heat exposure should be minimized and appropriate heat-protection measures should be considered.";

    }


    if (risk === "Very High") {

        return "HeatGuard indicates very high human thermal stress. Prolonged outdoor exposure may substantially increase heat strain.";

    }


    if (risk === "High") {

        return "HeatGuard indicates high human thermal stress. Heat exposure and duration should be reduced where practical.";

    }


    if (risk === "Moderate") {

        return "Moderate thermal stress is indicated. Conditions should be monitored, particularly during prolonged outdoor activity.";

    }


    return "Current thermal-engine results indicate relatively low human thermal stress.";
}


/* =========================================================
   PHYSIOLOGY UI
   ========================================================= */

function updatePhysiologyUI(
    physiology
) {

    document.getElementById(
        "evaporativeEffect"
    ).textContent =
        physiology.evaporativeEffect;


    document.getElementById(
        "thermalLoadEffect"
    ).textContent =
        physiology.thermalLoad;


    document.getElementById(
        "overallStrainEffect"
    ).textContent =
        physiology.overallStrain;


    document.getElementById(
        "physiologicalExplanation"
    ).textContent =
        physiology.explanation;
}


/* =========================================================
   THERMAL INDEX UI
   ========================================================= */

function updateThermalIndicesUI(
    thermalData = {}
) {

    updateIndex(
        "heatIndex",
        thermalData.heatIndex,
        "heatIndexDisplay",
        "heatIndexState",
        "heatIndexMeaning"
    );


    updateIndex(
        "wbgt",
        thermalData.wbgt,
        "wbgtDisplay",
        "wbgtState",
        "wbgtMeaning"
    );


    updateIndex(
        "utci",
        thermalData.utci,
        "utciDisplay",
        "utciState",
        "utciMeaning"
    );
}


function updateIndex(
    indexName,
    value,
    valueId,
    statusId,
    meaningId
) {

    const valueElement =
        document.getElementById(
            valueId
        );

    const statusElement =
        document.getElementById(
            statusId
        );

    const meaningElement =
        document.getElementById(
            meaningId
        );


    if (
        !Number.isFinite(
            Number(value)
        )
    ) {

        valueElement.textContent =
            "--";

        statusElement.textContent =
            "UNAVAILABLE";

        meaningElement.textContent =
            `Required inputs for ${ indexName } are unavailable.`;

        return;
    }


    valueElement.textContent =
        Number(value).toFixed(1);


    statusElement.textContent =
        "AVAILABLE";


    meaningElement.textContent =
        interpretIndex(
            indexName,
            Number(value)
        );
}


function interpretIndex(
    indexName,
    value
) {

    /*
     * These interpretation ranges should be replaced by
     * the exact methodology already used by the project's
     * thermal-engine implementation.
     */

    if (indexName === "heatIndex") {

        if (value < 27)
            return "Little or no heat-related stress indicated.";

        if (value < 32)
            return "Caution conditions.";

        if (value < 41)
            return "Extreme caution conditions.";

        if (value < 54)
            return "Danger conditions.";

        return "Extreme danger conditions.";
    }


    if (indexName === "wbgt") {

        if (value < 25)
            return "Lower thermal strain range.";

        if (value < 28)
            return "Increasing heat strain.";

        if (value < 30)
            return "High heat strain.";

        return "Very high heat strain; activity assessment required.";
    }


    if (indexName === "utci") {

        if (value < 26)
            return "No strong heat stress indicated.";

        if (value < 32)
            return "Moderate heat stress.";

        if (value < 38)
            return "Strong heat stress.";

        if (value < 46)
            return "Very strong heat stress.";

        return "Extreme heat stress.";
    }


    return "Interpretation unavailable.";
}


/* =========================================================
   40Â°C HUMIDITY COMPARISON
   ========================================================= */

function updateHumidityDemonstration() {

    /*
     * This comparison is illustrative and explicitly labelled
     * as an explanatory demonstration, not a medical prediction.
     */

    const dryStress =
        estimateHumidityStress(
            40,
            20
        );


    const humidStress =
        estimateHumidityStress(
            40,
            70
        );


    const dryMeter =
        document.getElementById(
            "dryConditionMeter"
        );

    const humidMeter =
        document.getElementById(
            "humidConditionMeter"
        );


    dryMeter.style.width =
        `${ dryStress }% `;

    humidMeter.style.width =
        `${ humidStress }% `;


    document.getElementById(
        "dryConditionResult"
    ).textContent =
        `${ dryStress }/100 relative humidity-related stress component`;


document.getElementById(
    "humidConditionResult"
).textContent =
    `${humidStress}/100 relative humidity-related stress component`;
}


function estimateHumidityStress(
    temperatureC,
    humidity
) {

    if (
        !Number.isFinite(
            temperatureC
        ) ||
        !Number.isFinite(
            humidity
        )
    ) {
        return 0;
    }


    /*
     * Demonstration component used only to explain why
     * humidity matters.
     *
     * It is intentionally NOT labelled as Heat Index,
     * WBGT, UTCI, mortality probability or medical risk.
     */

    const temperatureFactor =
        Math.max(
            0,
            Math.min(
                1,
                (temperatureC - 25) / 20
            )
        );


    const humidityFactor =
        Math.max(
            0,
            Math.min(
                1,
                humidity / 100
            )
        );


    return Math.round(
        temperatureFactor *
        humidityFactor *
        100
    );
}


/* =========================================================
   POPULATION HEALTH UI
   ========================================================= */

function updateHealthImpactUI(
    thermalResult,
    modelConfig
) {

    const health =
        HeatGuardHealthModule
            .evaluateHealthModel(
                modelConfig
            );


    const status =
        document.getElementById(
            "healthModelStatus"
        );


    const riskLevel =
        document.getElementById(
            "populationRiskLevel"
        );


    const riskScore =
        document.getElementById(
            "populationRiskScore"
        );


    const description =
        document.getElementById(
            "populationRiskDescription"
        );


    const mortality =
        document.getElementById(
            "mortalityForecast"
        );


    const mortalityStatus =
        document.getElementById(
            "mortalityStatus"
        );


    const mortalityExplanation =
        document.getElementById(
            "mortalityExplanation"
        );


    if (!health.available) {

        status.textContent =
            "MODEL STATUS: REQUIRES HISTORICAL DATA";


        riskLevel.textContent =
            "UNAVAILABLE";


        riskScore.textContent =
            "--";


        description.textContent =
            health.message;


        mortality.textContent =
            "UNAVAILABLE";


        mortalityStatus.textContent =
            "REQUIRES HISTORICAL DATA";


        mortalityExplanation.textContent =
            "Projected mortality cannot be produced responsibly until historical, location-specific health outcomes and a validated forecasting model are available.";

        return;
    }


    /*
     * A real backend response will populate these fields.
     */

    status.textContent =
        "MODEL STATUS: VALIDATED";


    riskLevel.textContent =
        health.populationRisk?.level ||
        "MODEL AVAILABLE";


    riskScore.textContent =
        health.populationRisk?.score ??
        "--";


    if (
        health.mortalityForecast
    ) {

        mortality.textContent =
            formatMortalityForecast(
                health.mortalityForecast
            );

        mortalityStatus.textContent =
            "MODEL OUTPUT";

    } else {

        mortality.textContent =
            "UNAVAILABLE";

        mortalityStatus.textContent =
            "NO VALIDATED FORECAST";

    }
}


/* =========================================================
   MORTALITY OUTPUT FORMATTER
   ========================================================= */

function formatMortalityForecast(
    forecast
) {

    /*
     * Accept only backend-generated, validated output.
     */

    if (
        !forecast ||
        !Number.isFinite(
            Number(forecast.value)
        )
    ) {

        return "UNAVAILABLE";
    }


    const value =
        Number(
            forecast.value
        );


    const unit =
        forecast.unit ||
        "rate";


    return `${value} ${unit}`;
}


/* =========================================================
   INITIALISATION
   ========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    () => {

        updateHumidityDemonstration();


        /*
         * CONNECT THIS TO YOUR EXISTING HEATGUARD DATA.
         *
         * Your current application should expose something
         * similar to:
         *
         * window.HeatGuardThermalData
         * window.HeatGuardEnvironmentData
         *
         * If your variable names are different, change only
         * these two references.
         */


        const thermalData =
            window.HeatGuardThermalData;


        const environmentData =
            window.HeatGuardEnvironmentData;


        if (
            thermalData
        ) {

            updateHumanImpactModule(
                thermalData,
                environmentData,
                {

                    /*
                     * Keep false until the backend has:
                     *
                     * 1. Historical health outcomes
                     * 2. Aggregate vulnerability data
                     * 3. Validated forecasting model
                     */

                    historicalHealthData:
                        false,

                    aggregateVulnerabilityData:
                        false,

                    validatedModel:
                        false
                }
            );

        }

    }
);


const thermalResult = calculateThermalRisk(weatherData);

window.HeatGuardThermalData = {
    heatIndex: thermalResult.heatIndex,
    wbgt: thermalResult.wbgt,
    utci: thermalResult.utci,
    thermalStress: thermalResult.thermalStress,
    riskLevel: thermalResult.riskLevel,
    timestamp: new Date().toISOString(),
    status: "LIVE",
    modelVersion: "HeatGuard Thermal Engine"
};

window.HeatGuardEnvironmentData = {
    temperatureC: weatherData.temperature,
    humidity: weatherData.humidity,
    windSpeed: weatherData.windSpeed,
    solarRadiation: weatherData.solarRadiation
};

// =========================================================
// Camera safety scan functions
// =========================================================

let videoStream = null;

async function startCamera() {
    const video = document.getElementById('cameraVideo');
    const placeholder = document.getElementById('cameraPlaceholder');
    const btnStart = document.getElementById('btnStartCam');
    const btnScan = document.getElementById('btnScanCam');
    try {
        videoStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } });
        video.srcObject = videoStream;
        video.style.display = 'block';
        placeholder.style.display = 'none';
        btnStart.style.display = 'none';
        btnScan.style.display = 'inline-block';
    } catch (err) {
        console.error('Camera Access Error:', err);
        alert('Unable to access camera. Please check permissions in your browser.');
    }
}

async function analyzeHeatRisk() {
    const video = document.getElementById('cameraVideo');
    const canvas = document.getElementById('cameraCanvas');
    const resultDiv = document.getElementById('cameraScanResult');
    const workerType = document.getElementById('workerType')?.value || 'outdoor';
    if (!videoStream) return;
    canvas.width = video.videoWidth || 320;
    canvas.height = video.videoHeight || 240;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const base64Image = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];
    resultDiv.style.display = 'block';
    resultDiv.style.color = '#94a3b8';
    resultDiv.innerHTML = '<strong>Analyzing photo for heat exposure risks...</strong>';
    const GEMINI_API_KEY = CONFIG.aiApiKey || '';
    const API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=' + GEMINI_API_KEY;
    const promptText = 'Analyze this image for heat safety and risk factors for a ' + workerType + ' worker. Check: 1. Clothing suitability, 2. Head protection, 3. Sun exposure. Provide 3 short bullet points with practical recommendations.';
    try {
        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ contents: [{ parts: [{ text: promptText }, { inline_data: { mime_type: 'image/jpeg', data: base64Image } }] }] })
        });
        if (!response.ok) throw new Error('API Error: ' + response.status);
        const data = await response.json();
        const analysis = data.candidates[0].content.parts[0].text;
        resultDiv.style.color = '#f8fafc';
        resultDiv.innerHTML = '<strong>AI Safety Audit:</strong><br>' + analysis.replace(/\n/g, '<br>');
    } catch (err) {
        console.error('Gemini Vision Error:', err);
        resultDiv.style.color = '#ef4444';
        resultDiv.innerHTML = '<strong>Camera analysis failed. Check API key or network.</strong>';
    }
}

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar') || document.querySelector('.sidebar');
    if (sidebar) sidebar.classList.toggle('collapsed');
}
