import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import './App.css'

const BLANK_TILE = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
const EMPTY_FC = { type: 'FeatureCollection', features: [] }
const METERS_PER_DEGREE_LAT = 111320

// Multi-source basemap style
const MAP_STYLE = {
  version: 8,
  sources: {
    'esri-satellite': {
      type: 'raster',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256,
      maxzoom: 19,
      attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics',
    },
    'osm': {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      maxzoom: 19,
      attribution: '&copy; OpenStreetMap contributors',
    },
    'carto-labels': {
      type: 'raster',
      tiles: ['https://a.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}@2x.png'],
      tileSize: 256,
      maxzoom: 19,
      attribution: '&copy; CartoDB',
    },
  },
  layers: [
    {
      id: 'layer-esri-satellite',
      type: 'raster',
      source: 'esri-satellite',
      layout: { visibility: 'visible' },
      paint: { 'raster-resampling': 'linear' },
    },
    {
      id: 'layer-osm',
      type: 'raster',
      source: 'osm',
      layout: { visibility: 'none' },
      paint: { 'raster-resampling': 'linear' },
    },
    {
      id: 'layer-carto-labels',
      type: 'raster',
      source: 'carto-labels',
      layout: { visibility: 'none' },
    },
  ],
}

function aoiPolygon(lat, lon, bufferM) {
  const dlat = bufferM / METERS_PER_DEGREE_LAT
  const dlon = bufferM / (METERS_PER_DEGREE_LAT * Math.max(Math.cos((lat * Math.PI) / 180), 0.1))
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Polygon',
      coordinates: [[
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
        [lon - dlon, lat - dlat],
      ]],
    },
  }
}

function verdictFor(year, built) {
  if (year.status === 'ok') {
    if (year.water_at_point === true) return { label: 'WATER DETECTED', cls: 'water', icon: '💧' }
    return {
      label: built?.at_point ? 'BUILT-UP / NO WATER' : 'NO WATER (DRY)',
      cls: built?.at_point ? 'built' : 'dry',
      icon: built?.at_point ? '🏗️' : '🏜️',
    }
  }
  if (year.status === 'point_obscured') return { label: 'CLOUD / SHADOW', cls: 'cloud', icon: '☁️' }
  if (year.status === 'no_clear_imagery') return { label: 'NO CLEAR IMAGERY', cls: 'cloud', icon: '🌫️' }
  if (year.status === 'degenerate_scene') return { label: 'INCONCLUSIVE', cls: 'cloud', icon: '❓' }
  return { label: 'READ ERROR', cls: 'err', icon: '⚠️' }
}

function ensureOverlayLayers(map) {
  if (map.getSource('historical-satellite')) return

  // Yearly Sentinel-2 / Landsat satellite imagery
  map.addSource('historical-satellite', {
    type: 'raster',
    tiles: [BLANK_TILE],
    tileSize: 256,
    maxzoom: 16, // prevents overzooming blur
    attribution: 'Copernicus Sentinel / USGS Landsat via Microsoft Planetary Computer',
  })
  map.addLayer({
    id: 'historical-satellite',
    type: 'raster',
    source: 'historical-satellite',
    layout: { visibility: 'none' },
    paint: { 'raster-opacity': 0.85, 'raster-resampling': 'linear' },
  })

  // AOI space bounding box
  map.addSource('aoi', { type: 'geojson', data: EMPTY_FC })
  map.addLayer({
    id: 'aoi-fill',
    type: 'fill',
    source: 'aoi',
    paint: { 'fill-color': '#e67e22', 'fill-opacity': 0.08 },
  })
  map.addLayer({
    id: 'aoi-line',
    type: 'line',
    source: 'aoi',
    paint: { 'line-color': '#e67e22', 'line-width': 2.5, 'line-dasharray': [3, 2] },
  })

  // Detected water mask
  map.addSource('water', { type: 'geojson', data: EMPTY_FC })
  map.addLayer({
    id: 'water-fill',
    type: 'fill',
    source: 'water',
    paint: { 'fill-color': '#0284c7', 'fill-opacity': 0.55 },
  })
  map.addLayer({
    id: 'water-line',
    type: 'line',
    source: 'water',
    paint: { 'line-color': '#0369a1', 'line-width': 2 },
  })
}

const DEFAULT_PRESETS = [
  {
    id: 'nellore',
    name: 'Nellore Urban Tank (India)',
    tag: 'Pond Built Over by Buildings',
    desc: 'Water body existed through 2023, subsequently built over with 98% building density by 2025',
    lat: 14.451,
    lon: 79.968,
    start_year: 2021,
    end_year: 2025,
    buffer_m: 300,
    zoom: 15,
  },
  {
    id: 'songdo',
    name: 'Songdo Reclaimed District (S. Korea)',
    tag: 'Coastal Basin Reclaimed',
    desc: 'Tidal water basin reclaimed and converted into high-density commercial towers',
    lat: 37.395,
    lon: 126.655,
    start_year: 2021,
    end_year: 2023,
    buffer_m: 300,
    zoom: 14.5,
  },
  {
    id: 'pallikaranai',
    name: 'Pallikaranai Marsh Encroachment (Chennai)',
    tag: 'Shrinking Wetland',
    desc: 'Historic marshland shrunk and built over by urban infrastructure & IT corridor',
    lat: 12.9365,
    lon: 80.2246,
    start_year: 2021,
    end_year: 2026,
    buffer_m: 300,
    zoom: 14,
  },
  {
    id: 'bellandur',
    name: 'Bellandur Lake Catchment (Bengaluru)',
    tag: 'Lake Encroachment',
    desc: 'Wetland catchment encroached by tech parks & residential towers',
    lat: 12.935,
    lon: 77.678,
    start_year: 2021,
    end_year: 2025,
    buffer_m: 200,
    zoom: 15,
  },
]

export default function App() {
  const mapContainer = useRef(null)
  const mapRef = useRef(null)
  const markerRef = useRef(null)
  const abortRef = useRef(null)
  const paramsRef = useRef({})
  const pickedRef = useRef(null)

  // Map state
  const [basemapMode, setBasemapMode] = useState('satellite') // 'satellite' | 'street' | 'hybrid'
  const [mapZoom, setMapZoom] = useState(15)
  const [point, setPoint] = useState({ lat: 14.451, lon: 79.968 }) // Nellore pond encroachment demo
  const [startYear, setStartYear] = useState(2021)
  const [endYear, setEndYear] = useState(2025)
  const [bufferM, setBufferM] = useState(300)

  // Analysis state
  const [loading, setLoading] = useState(false)
  const [loadingStep, setLoadingStep] = useState('')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [selectedYear, setSelectedYear] = useState(null)
  const [selectedPhotoModal, setSelectedPhotoModal] = useState(null)

  // Overlay controls
  const [showWaterMask, setShowWaterMask] = useState(true)
  const [showHistoricalOverlay, setShowHistoricalOverlay] = useState(true)
  const [historicalOpacity, setHistoricalOpacity] = useState(0.85)

  // Search & presets
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [presets, setPresets] = useState(DEFAULT_PRESETS)

  paramsRef.current = { startYear, endYear, bufferM }

  // Fetch presets on load to supplement defaults
  useEffect(() => {
    fetch('/api/presets')
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        if (data && data.length > 0) setPresets(data)
      })
      .catch(() => {})
  }, [])

  // Initialize MapLibre
  useEffect(() => {
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: [79.968, 14.451],
      zoom: 15,
      minZoom: 2,
      maxZoom: 18.5, // Stop over-zoom blur
      preserveDrawingBuffer: true,
    })

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')

    map.on('load', () => {
      ensureOverlayLayers(map)
      updateBasemap(map, 'satellite')

      // Create initial marker
      const el = document.createElement('div')
      el.className = 'custom-marker'
      el.innerHTML = '<div class="marker-pulse"></div><div class="marker-dot"></div>'
      markerRef.current = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([point.lon, point.lat])
        .addTo(map)

      // Run initial analysis
      runAnalysis(point)
    })

    map.on('zoom', () => {
      setMapZoom(map.getZoom().toFixed(1))
    })

    map.on('click', (e) => {
      const newP = { lat: +e.lngLat.lat.toFixed(5), lon: +e.lngLat.lng.toFixed(5) }
      setPoint(newP)
      if (markerRef.current) {
        markerRef.current.setLngLat([newP.lon, newP.lat])
      }
      runAnalysis(newP)
    })

    mapRef.current = map
    return () => map.remove()
  }, [])

  // Basemap switching handler
  function updateBasemap(map, mode) {
    if (!map || !map.isStyleLoaded()) return
    const isSat = mode === 'satellite' || mode === 'hybrid'
    const isOsm = mode === 'street'
    const isLabels = mode === 'hybrid'

    map.setLayoutProperty('layer-esri-satellite', 'visibility', isSat ? 'visible' : 'none')
    map.setLayoutProperty('layer-osm', 'visibility', isOsm ? 'visible' : 'none')
    map.setLayoutProperty('layer-carto-labels', 'visibility', isLabels ? 'visible' : 'none')
  }

  useEffect(() => {
    updateBasemap(mapRef.current, basemapMode)
  }, [basemapMode])

  // Marker & Run trigger when point changes
  useEffect(() => {
    if (!point) return
    const map = mapRef.current
    if (!map) return

    if (!markerRef.current) {
      const el = document.createElement('div')
      el.className = 'custom-marker'
      el.innerHTML = '<div class="marker-pulse"></div><div class="marker-dot"></div>'
      markerRef.current = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([point.lon, point.lat])
        .addTo(map)
    } else {
      markerRef.current.setLngLat([point.lon, point.lat])
    }

    runAnalysis(point)
  }, [point])

  // Update AOI and yearly overlays on map
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const apply = () => {
      ensureOverlayLayers(map)

      // 1. AOI polygon
      if (point) {
        map.getSource('aoi').setData(aoiPolygon(point.lat, point.lon, bufferM))
      } else {
        map.getSource('aoi').setData(EMPTY_FC)
      }

      // 2. Yearly historical satellite scene
      const y = result?.years.find((r) => r.year === selectedYear)
      const src = map.getSource('historical-satellite')
      const tiles = y?.imagery_tiles

      if (src && tiles && src._tileUrl !== tiles) {
        src._tileUrl = tiles
        src.setTiles([tiles])
      }

      const canShowOverlay = showHistoricalOverlay && Boolean(tiles)
      map.setLayoutProperty('historical-satellite', 'visibility', canShowOverlay ? 'visible' : 'none')
      map.setPaintProperty('historical-satellite', 'raster-opacity', historicalOpacity)

      // 3. Water polygon mask
      if (showWaterMask && y?.water_geojson) {
        map.getSource('water').setData({
          type: 'Feature',
          properties: { year: y.year },
          geometry: y.water_geojson,
        })
      } else {
        map.getSource('water').setData(EMPTY_FC)
      }
    }

    if (map.isStyleLoaded()) apply()
    else map.once('load', apply)
  }, [result, selectedYear, point, bufferM, showHistoricalOverlay, showWaterMask, historicalOpacity])

  // Geocoding search suggestions
  useEffect(() => {
    if (query.trim().length < 3 || query === pickedRef.current) {
      setSuggestions([])
      return
    }
    const t = setTimeout(async () => {
      try {
        const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=${encodeURIComponent(query)}`
        const res = await fetch(url, { headers: { Accept: 'application/json' } })
        setSuggestions(res.ok ? await res.json() : [])
      } catch {
        setSuggestions([])
      }
    }, 500)
    return () => clearTimeout(t)
  }, [query])

  async function runAnalysis(p, overrideParams = null) {
    const s = overrideParams?.startYear ?? paramsRef.current.startYear
    const e = overrideParams?.endYear ?? paramsRef.current.endYear
    const b = overrideParams?.bufferM ?? paramsRef.current.bufferM
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setLoadingStep('Searching Sentinel-2 & Landsat archives...')
    setError(null)

    try {
      const url = `/api/timeline?lat=${p.lat}&lon=${p.lon}&start_year=${s}&end_year=${e}&buffer_m=${b}`
      const res = await fetch(url, { signal: controller.signal })
      if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`)
      const data = await res.json()
      setResult(data)

      // Auto-select latest year or year with detected water
      const withWater = [...data.years].reverse().find((y) => y.water_at_point === true)
      setSelectedYear((withWater ?? data.years[data.years.length - 1]).year)
    } catch (err) {
      if (err.name !== 'AbortError') setError(err.message)
    } finally {
      if (abortRef.current === controller) setLoading(false)
    }
  }

  function pickPreset(preset) {
    setStartYear(preset.start_year)
    setEndYear(preset.end_year)
    setBufferM(preset.buffer_m)
    const newP = { lat: preset.lat, lon: preset.lon }
    setPoint(newP)
    mapRef.current?.flyTo({ center: [preset.lon, preset.lat], zoom: preset.zoom || 15 })
    if (markerRef.current) {
      markerRef.current.setLngLat([preset.lon, preset.lat])
    }
    runAnalysis(newP, { startYear: preset.start_year, endYear: preset.end_year, bufferM: preset.buffer_m })
  }

  function pickSuggestion(s) {
    const lat = parseFloat(s.lat)
    const lon = parseFloat(s.lon)
    pickedRef.current = s.display_name
    setSuggestions([])
    setQuery(s.display_name)
    mapRef.current?.flyTo({ center: [lon, lat], zoom: 15 })
    setPoint({ lat, lon })
  }

  function zoomToSpace() {
    if (!point || !mapRef.current) return
    mapRef.current.flyTo({ center: [point.lon, point.lat], zoom: 15, essential: true })
  }

  function download(filename, blob) {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = filename
    a.click()
    URL.revokeObjectURL(a.href)
  }

  function exportGeoJSON() {
    if (!result || !point) return
    const features = result.years
      .filter((y) => y.water_geojson)
      .map((y) => ({
        type: 'Feature',
        geometry: y.water_geojson,
        properties: {
          year: y.year,
          status: y.status,
          water_at_point: y.water_at_point,
          confidence_pct: y.confidence_pct,
          scene_id: y.scene_id,
          scene_date: y.date,
        },
      }))
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [point.lon, point.lat] },
      properties: {
        encroachment: result.encroachment_analysis,
        summary: result.summary,
        built_area: result.built_area,
      },
    })
    const fc = { type: 'FeatureCollection', features }
    download(`water-detection-${point.lat.toFixed(4)}_${point.lon.toFixed(4)}.geojson`, new Blob([JSON.stringify(fc, null, 2)], { type: 'application/geo+json' }))
  }

  function exportPNG() {
    mapRef.current?.getCanvas().toBlob((blob) => {
      if (blob) download(`water-map-${selectedYear}.png`, blob)
    }, 'image/png')
  }

  const years = result?.years ?? []
  const minYear = years[0]?.year
  const maxYear = years[years.length - 1]?.year
  const selectedYearData = years.find((y) => y.year === selectedYear)
  const encroachment = result?.encroachment_analysis

  // Find before (water) and after (building) photos for comparison
  const waterYearData = years.find((y) => y.water_at_point === true || (y.water_fraction_aoi || 0) >= 0.2)
  const latestDryData = [...years].reverse().find((y) => y.water_at_point === false && y.photo_url)

  return (
    <div className="app-container">
      {/* Top Navigation Bar */}
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon">🛰️</div>
          <div>
            <h1>AquaTrace <span>AI</span></h1>
            <p className="subtitle">Historical Water Body &amp; Encroachment Detection</p>
          </div>
        </div>

        {/* Quick Location Presets */}
        <div className="presets-bar">
          <span className="preset-label">Explore Cases:</span>
          {presets.map((p) => (
            <button
              key={p.id}
              className={`preset-btn ${point.lat === p.lat && point.lon === p.lon ? 'active' : ''}`}
              onClick={() => pickPreset(p)}
              title={p.desc}
            >
              {p.name.split(' (')[0]}
            </button>
          ))}
        </div>

        {/* Floating Basemap Selector */}
        <div className="basemap-group">
          <button
            className={`bm-btn ${basemapMode === 'satellite' ? 'active' : ''}`}
            onClick={() => setBasemapMode('satellite')}
            title="High-resolution crystal-clear satellite aerial imagery"
          >
            🛰️ Satellite
          </button>
          <button
            className={`bm-btn ${basemapMode === 'street' ? 'active' : ''}`}
            onClick={() => setBasemapMode('street')}
            title="Standard OpenStreetMap street map"
          >
            🗺️ Street Map
          </button>
          <button
            className={`bm-btn ${basemapMode === 'hybrid' ? 'active' : ''}`}
            onClick={() => setBasemapMode('hybrid')}
            title="Satellite imagery with street and place names"
          >
            🌐 Hybrid
          </button>
        </div>
      </header>

      {/* Main Workspace Layout */}
      <div className="main-content">
        {/* Left Sidebar Controls & Insights */}
        <aside className="sidebar">
          {/* 1. Location & Space Selection Card */}
          <section className="card-panel">
            <h2 className="panel-title">1. Choose Space &amp; Location</h2>

            <div className="search-box">
              <input
                type="text"
                placeholder="Search place, lake, marsh (e.g. Pallikaranai, Songdo)..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {suggestions.length > 0 && (
                <ul className="suggestions-dropdown">
                  {suggestions.map((s) => (
                    <li key={s.place_id} onClick={() => pickSuggestion(s)}>
                      {s.display_name}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="space-params">
              <div className="param-item">
                <label>Radius (AOI)</label>
                <div className="radius-pills">
                  {[50, 100, 150, 300, 500].map((r) => (
                    <button
                      key={r}
                      type="button"
                      className={`pill ${bufferM === r ? 'selected' : ''}`}
                      onClick={() => setBufferM(r)}
                    >
                      {r}m
                    </button>
                  ))}
                </div>
              </div>

              <div className="param-item">
                <label>Time Range</label>
                <div className="year-inputs">
                  <input
                    type="number"
                    min="2015"
                    max="2026"
                    value={startYear}
                    onChange={(e) => setStartYear(+e.target.value)}
                  />
                  <span>to</span>
                  <input
                    type="number"
                    min="2015"
                    max="2026"
                    value={endYear}
                    onChange={(e) => setEndYear(+e.target.value)}
                  />
                </div>
              </div>
            </div>

            <div className="point-status">
              <div className="coords">
                📍 <span>{point.lat.toFixed(5)}, {point.lon.toFixed(5)}</span>
              </div>
              <div className="btn-group">
                <button className="btn-sm" onClick={zoomToSpace} title="Zoom map to selected space">
                  🔍 Focus
                </button>
                <button
                  className="btn-primary"
                  disabled={loading}
                  onClick={() => runAnalysis(point)}
                >
                  {loading ? 'Analyzing...' : 'Run Analysis'}
                </button>
              </div>
            </div>
          </section>

          {/* Loading & Status Message */}
          {loading && (
            <div className="status-banner loading">
              <div className="spinner"></div>
              <div>
                <strong>Retrieving Satellite Scenes...</strong>
                <p>Querying Sentinel-2 (MNDWI + Otsu) &amp; Land Cover from Planetary Computer ({endYear - startYear + 1} years).</p>
              </div>
            </div>
          )}

          {error && (
            <div className="status-banner error">
              ⚠️ {error}
            </div>
          )}

          {/* 2. Lake Encroachment & Building-Over-Water Alert */}
          {encroachment && (
            <section className={`encroachment-card ${encroachment.status}`}>
              <div className="encroachment-header">
                <span className="badge-pill" style={{ background: encroachment.badge_color }}>
                  {encroachment.badge}
                </span>
                <span className="confidence-tag">Confidence: {encroachment.confidence}</span>
              </div>

              <h3 className="encroachment-headline">{encroachment.headline}</h3>
              <p className="encroachment-details">{encroachment.details}</p>

              {/* Before & After Photo Comparison */}
              {encroachment.encroachment_detected && waterYearData && latestDryData && (
                <div className="before-after-container">
                  <div className="ba-card before">
                    <div className="ba-tag">BEFORE ({waterYearData.year})</div>
                    <div className="ba-img-wrap">
                      {waterYearData.photo_url ? (
                        <img src={waterYearData.photo_url} alt="Historical Water" />
                      ) : (
                        <div className="img-placeholder">💧 Water Detected</div>
                      )}
                    </div>
                    <span className="ba-desc">💧 Active Water Space</span>
                  </div>

                  <div className="ba-arrow">➔</div>

                  <div className="ba-card after">
                    <div className="ba-tag">AFTER ({latestDryData.year})</div>
                    <div className="ba-img-wrap">
                      {latestDryData.photo_url ? (
                        <img src={latestDryData.photo_url} alt="Modern Encroachment" />
                      ) : (
                        <div className="img-placeholder">🏗️ Urban Structures</div>
                      )}
                    </div>
                    <span className="ba-desc">🏗️ Built-up Construction</span>
                  </div>
                </div>
              )}

              {/* Land Cover & Water Occurrence Metrics */}
              <div className="metrics-row">
                {result?.built_area && (
                  <div className="metric-box">
                    <span className="label">Built-Up Density ({result.built_area.year})</span>
                    <span className="value">{Math.round(result.built_area.built_fraction * 100)}%</span>
                  </div>
                )}
                {result?.historical_water_occurrence && (
                  <div className="metric-box">
                    <span className="label">JRC Historical Water</span>
                    <span className="value">{result.historical_water_occurrence.occurrence_pct}%</span>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* 3. Year-Wise Photos Filmstrip / Gallery */}
          {years.length > 0 && (
            <section className="card-panel">
              <div className="panel-header-row">
                <h2 className="panel-title">Year-Wise Satellite Photos</h2>
                <span className="panel-subtitle">Click photo to view on map</span>
              </div>

              <div className="photo-gallery">
                {years.map((y) => {
                  const v = verdictFor(y, result?.built_area)
                  const isSelected = y.year === selectedYear
                  return (
                    <div
                      key={y.year}
                      className={`photo-card ${v.cls} ${isSelected ? 'active' : ''}`}
                      onClick={() => setSelectedYear(y.year)}
                    >
                      <div className="photo-header">
                        <span className="photo-year">{y.year}</span>
                        <span className={`photo-badge ${v.cls}`}>{v.icon} {v.label}</span>
                      </div>

                      <div className="photo-img-container">
                        {y.photo_url ? (
                          <img
                            src={y.photo_url}
                            alt={`Satellite photo ${y.year}`}
                            loading="lazy"
                            onError={(e) => {
                              e.target.style.display = 'none'
                              e.target.nextSibling.style.display = 'flex'
                            }}
                          />
                        ) : null}
                        <div
                          className="photo-placeholder"
                          style={{ display: y.photo_url ? 'none' : 'flex' }}
                        >
                          {v.icon}
                        </div>

                        {isSelected && <div className="selected-glow">Active View</div>}
                      </div>

                      <div className="photo-footer">
                        <span>{String(y.date || '').slice(0, 10) || 'Composite'}</span>
                        {y.cloud_cover_pct != null && (
                          <span>Cloud {y.cloud_cover_pct.toFixed(0)}%</span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Extended Search Probes (Landsat) */}
              {result?.extended_search && (
                <div className="extended-section">
                  <div className="extended-title">
                    <span>📜 Extended Historical Search (Landsat 30m)</span>
                  </div>
                  <p className="extended-finding">{result.extended_search.finding}</p>

                  <div className="photo-gallery extended-gallery">
                    {result.extended_search.years.map((y) => {
                      const v = verdictFor(y, result?.built_area)
                      return (
                        <div key={`ext-${y.year}`} className={`photo-card ${v.cls}`}>
                          <div className="photo-header">
                            <span className="photo-year">{y.year}</span>
                            <span className={`photo-badge ${v.cls}`}>{v.icon} {v.label}</span>
                          </div>
                          <div className="photo-img-container">
                            {y.photo_url ? (
                              <img src={y.photo_url} alt={`Landsat ${y.year}`} loading="lazy" />
                            ) : (
                              <div className="photo-placeholder">{v.icon}</div>
                            )}
                          </div>
                          <div className="photo-footer">
                            <span>Landsat {y.scene_id ? String(y.scene_id).slice(-10) : ''}</span>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </section>
          )}

          {/* Export Actions */}
          {result && (
            <div className="export-actions">
              <button className="btn-secondary" onClick={exportGeoJSON}>
                📥 Export GeoJSON
              </button>
              <button className="btn-secondary" onClick={exportPNG}>
                📸 Snapshot Map
              </button>
            </div>
          )}

          <footer className="sidebar-footer">
            <p>
              Data: Sentinel-2 L2A (10m) &amp; Landsat (30m) via Microsoft Planetary Computer.
              Land cover: Esri/Impact Observatory 10m Annual Land Cover.
            </p>
          </footer>
        </aside>

        {/* Right Map Canvas & Interactive Overlays */}
        <div className="map-wrapper">
          <div ref={mapContainer} className="map-canvas" />

          {/* Floating Map Overlays Controller */}
          <div className="floating-map-controls">
            <div className="control-card">
              <div className="control-header">
                <strong>Layer Overlays</strong>
                <span className="zoom-indicator">Zoom: {mapZoom}x</span>
              </div>

              <div className="control-toggles">
                <label className="toggle-item">
                  <input
                    type="checkbox"
                    checked={showWaterMask}
                    onChange={(e) => setShowWaterMask(e.target.checked)}
                  />
                  <span className="color-swatch water"></span>
                  Water Detection Mask
                </label>

                <label className="toggle-item">
                  <input
                    type="checkbox"
                    checked={showHistoricalOverlay}
                    onChange={(e) => setShowHistoricalOverlay(e.target.checked)}
                  />
                  <span className="color-swatch satellite"></span>
                  Year {selectedYear} Satellite Scene
                </label>
              </div>

              {showHistoricalOverlay && (
                <div className="opacity-slider-box">
                  <div className="slider-label">
                    <span>Scene Blend Opacity</span>
                    <span>{Math.round(historicalOpacity * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={historicalOpacity}
                    onChange={(e) => setHistoricalOpacity(+e.target.value)}
                  />
                </div>
              )}
            </div>

            {/* Quick Timeline Slider Floating over Map */}
            {years.length > 0 && (
              <div className="timeline-float">
                <div className="timeline-labels">
                  <span>{minYear}</span>
                  <strong>Showing Year: {selectedYear}</strong>
                  <span>{maxYear}</span>
                </div>
                <input
                  type="range"
                  min={minYear}
                  max={maxYear}
                  step="1"
                  value={selectedYear ?? maxYear}
                  onChange={(e) => setSelectedYear(+e.target.value)}
                />
              </div>
            )}
          </div>

          {/* Anti-Blur Zoom Helper Indicator */}
          {parseFloat(mapZoom) > 16 && (
            <div className="zoom-alert-toast">
              🔍 Showing ultra-crisp high-resolution aerial basemap. Satellite detection data is ~10m resolution.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
