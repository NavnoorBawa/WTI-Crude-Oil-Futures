#!/usr/bin/env python3
"""
WTI crude volatility dashboard API: the local development server, and the renderer freeze.py uses
================================================================================================
/data serves the dashboard payload, docs/DATA_CONTRACT.md data.json schema_version 3:

  contract + price fields   backend/oil.py get_current_wti_contract (Yahoo CL=F)
  unified_data.actual       backend/oil.py get_historical_data (CL=F daily and hourly bars, then the quote)
  vol_forecast              backend/vol_forecast.py forecast_bundle (the validated HAR-IV forecaster)
  supply_shock_playbook     backend/supply_shock_playbook.py (EIA spot history; cached, key optional)
  retracted_direction_model a fixed statement, not a model output

freeze.py renders it once per refresh into the static site's data.json and adds frozen_at.

The 1-week price-direction ensemble (oil.py PremiumWTIPredictor) is RETRACTED: purged of its
look-ahead leak it is a coin flip out of sample. Nothing here trains or runs it, and no news or other
optional keyed provider is called; backend/backtest_walk_forward.py keeps the post-mortem reproducible.
"""

import time
import threading
import os
from datetime import datetime, timezone
import logging
from flask import Flask, jsonify, make_response, request
from flask_cors import CORS

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "https://navnoorbawa.github.io,http://127.0.0.1:3000,http://localhost:3000",
    ).split(",")
    if origin.strip()
]
CORS(
    app,
    origins=ALLOWED_ORIGINS,
    methods=["GET", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)

SERVICE_NAME = 'WTI Crude Volatility Dashboard API'
SCHEMA_VERSION = 3
RETRACTED_DIRECTION_MODEL = {
    'status': 'RETRACTED',
    'reason': 'look-ahead leak; purged, the signal is a coin flip',
    'evidence': 'research.json postmortem',
}

# Startup synchronization: /data is served only once the contract quote has been fetched.
_startup_ready = threading.Event()
_startup_lock = threading.Lock()
_startup_thread = None
_startup_started = False
_startup_attempts = 0
_startup_next_retry_at = 0.0
_rate_limit_lock = threading.Lock()
_data_request_times = {}

# Import oil.py functions - CRITICAL DEPENDENCY
try:
    from .oil import get_current_wti_contract, get_historical_data
    OIL_IMPORTS_AVAILABLE = True
except Exception as e:
    logger.critical("CRITICAL: Cannot import oil.py functions (%s)", type(e).__name__)
    OIL_IMPORTS_AVAILABLE = False

system_state = {'initialized': False}


def _utc_now_iso():
    """Timezone-aware UTC timestamp. A naive local time is parsed as the BROWSER's local time by
    JavaScript's Date, so every timestamp the dashboard reads must carry its offset."""
    return datetime.now(timezone.utc).isoformat()


def _bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s; using %s", name, default)
        value = default
    return max(minimum, min(maximum, value))


API_STARTUP_RETRY_SECONDS = _bounded_env_int('API_STARTUP_RETRY_SECONDS', 5, 2, 60)
STARTUP_RETRY_COOLDOWN_SECONDS = _bounded_env_int(
    'STARTUP_RETRY_COOLDOWN_SECONDS', 20, 5, 600
)
DATA_RATE_LIMIT_PER_MINUTE = _bounded_env_int('DATA_RATE_LIMIT_PER_MINUTE', 30, 5, 600)


def json_response(payload, status_code=200, retry_after=None):
    """Standard JSON response with no-store cache semantics for live market data."""
    response = make_response(jsonify(payload), status_code)
    response.headers['Cache-Control'] = 'no-store, no-cache, max-age=0, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    if retry_after is not None:
        response.headers['Retry-After'] = str(int(retry_after))
    return response


@app.after_request
def _set_security_headers(response):
    """Apply browser hardening to every API response, including errors."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    return response


def startup_payload(message, retry_after_seconds=None):
    retry_seconds = int(retry_after_seconds or API_STARTUP_RETRY_SECONDS)
    payload = {
        'service': SERVICE_NAME,
        'status': 'INITIALIZING',
        'ready': False,
        'startup_ready': False,
        'error': 'SYSTEM_INITIALIZING',
        'message': message,
        'retry_after_seconds': retry_seconds,
        'server_time': _utc_now_iso()
    }
    if _startup_attempts:
        payload['startup_attempts'] = _startup_attempts
    if _startup_next_retry_at and not _startup_ready.is_set():
        payload['next_retry_at'] = datetime.fromtimestamp(_startup_next_retry_at, timezone.utc).isoformat()
    return payload


def startup_retry_seconds():
    if _startup_next_retry_at and not _startup_ready.is_set():
        return max(1, int(round(_startup_next_retry_at - time.time())))
    return API_STARTUP_RETRY_SECONDS


# Both cached blocks are rebuilt at most every 6 hours, one build at a time. A failed build is retried
# only after FAILURE_BACKOFF_SECONDS (no retry storm on Yahoo or EIA), and the last good build is
# served meanwhile.
VOL_FORECAST_TTL_SECONDS = 21600
PLAYBOOK_TTL_SECONDS = 21600
FAILURE_BACKOFF_SECONDS = 600
_VOL_CACHE: dict = {"data": None, "built_at": 0.0, "failed_at": 0.0}
_PLAYBOOK_CACHE: dict = {"data": None, "built_at": 0.0, "failed_at": 0.0}
_vol_lock = threading.Lock()
_playbook_lock = threading.Lock()


def _cached_build(cache, lock, ttl, build, label):
    now = time.time()
    with lock:
        if cache["data"] is not None and now - cache["built_at"] < ttl:
            return cache["data"]
        if now - cache["failed_at"] < FAILURE_BACKOFF_SECONDS:
            return cache["data"]
        try:
            result = build()
        except Exception as exc:
            logger.warning("%s unavailable (%s)", label, type(exc).__name__)
            result = None
        if result is None:
            cache["failed_at"] = now
        else:
            cache.update(data=result, built_at=now)
        return cache["data"]


def _build_vol_forecast():
    from .vol_forecast import forecast_bundle
    return forecast_bundle()


def _build_supply_shock_playbook():
    # Cache-first on the committed EIA series (data/eia_wti_spot_daily.json); EIA_API_KEY, when set,
    # only extends it. Analogues are ranked by recency: no news feed is read to rank them by driver.
    from .supply_shock_playbook import get_playbook_for_api
    return get_playbook_for_api()


def _load_vol_forecast():
    """The validated HAR-IV block (backend/vol_forecast.py forecast_bundle), or None while unavailable."""
    return _cached_build(_VOL_CACHE, _vol_lock, VOL_FORECAST_TTL_SECONDS, _build_vol_forecast,
                         "Volatility forecast")


def _load_supply_shock_playbook():
    """The EIA supply-shock event study, or None while unavailable."""
    return _cached_build(_PLAYBOOK_CACHE, _playbook_lock, PLAYBOOK_TTL_SECONDS, _build_supply_shock_playbook,
                         "Supply-shock playbook")


def _vol_forecast_ready():
    return _VOL_CACHE["data"] is not None


def _vol_retry_seconds():
    """When the volatility forecast is next attempted (it is rebuilt on a /data request)."""
    remaining = _VOL_CACHE["failed_at"] + FAILURE_BACKOFF_SECONDS - time.time()
    return max(API_STARTUP_RETRY_SECONDS, int(round(remaining)))


def _volume_display(volume):
    if volume >= 1000000:
        return f"{volume / 1000000:.1f}M"
    if volume >= 1000:
        return f"{volume / 1000:.1f}K"
    return f"{volume:.0f}" if volume > 0 else "N/A"


def dashboard_payload(contract_info):
    """The data.json schema_version 3 payload for a contract quote (docs/DATA_CONTRACT.md)."""
    current_price = float(contract_info['current_price'])
    volume = contract_info.get('volume') or 0
    # oil.py publishes an unknown daily change as null, never as a fabricated 0.00%: withheld on
    # purpose around a roll or before the previous settlement is out (weekday evenings).
    price_change = contract_info.get('price_change')
    price_change_percent = contract_info.get('price_change_percent')
    price_change_quality = contract_info.get('price_change_quality') or 'unavailable'
    if price_change is None and not price_change_quality.startswith('unavailable'):
        price_change_quality = 'unavailable'
    return {
        'schema_version': SCHEMA_VERSION,
        'last_update': _utc_now_iso(),
        # Yahoo's NYMEX quotes are exchange-delayed (~10 min), so the best state is LIVE, never
        # "REAL-TIME"; freeze.py overrides this to SNAPSHOT for the static site.
        'feed_status': 'LIVE',
        'contract': {
            'symbol': contract_info['symbol'],
            'description': contract_info['description'],
            'expiry_date': contract_info.get('expiry_date'),
            'last_trade_date': contract_info.get('contract_last_trade_date') or contract_info.get('expiry_date'),
            'days_to_expiry': contract_info.get('days_to_expiry'),
            'market_time': contract_info.get('market_time'),
            # How the label was established ('calendar', 'quote_match' near a roll, or
            # 'contract_symbol' when quoted from the contract itself).
            'contract_basis': contract_info.get('contract_basis'),
            'quote_symbol': contract_info.get('yfinance_symbol'),
            'history_symbol': contract_info.get('history_symbol'),
            'market_session_date': contract_info.get('market_session_date'),
            'quote_session_date': contract_info.get('quote_session_date'),
        },
        'current_price': round(current_price, 2),
        'price_change': round(price_change, 3) if price_change is not None else None,
        'price_change_percent': round(price_change_percent, 2) if price_change_percent is not None else None,
        'price_change_quality': price_change_quality,
        'volume': volume,
        'volume_display': _volume_display(volume),
        # The chart ends at the quote itself (kind 'live'), stamped with its exchange time.
        'unified_data': get_historical_data(
            live_quote=(contract_info.get('market_time'), current_price, volume)
        ),
        'vol_forecast': _load_vol_forecast(),
        'supply_shock_playbook': _load_supply_shock_playbook(),
        'retracted_direction_model': dict(RETRACTED_DIRECTION_MODEL),
    }


def initialize_oil_system():
    """Fetch the contract quote and warm the volatility forecast. Trains and runs no model.

    Raises when there is no quote, so a one-shot freeze fails visibly and the previous good snapshot
    stays live. A volatility-forecast failure is only logged here: /data retries it after the backoff,
    and freeze.py refuses to publish without it.
    """
    if not OIL_IMPORTS_AVAILABLE:
        raise RuntimeError("CRITICAL: oil.py imports not available - cannot start server")
    try:
        contract_info = get_current_wti_contract()
    except Exception as exc:
        system_state['initialized'] = False
        logger.error("Initialization failed: no contract quote (%s)", type(exc).__name__)
        raise RuntimeError(f"Cannot initialize: no contract quote ({type(exc).__name__})") from exc
    logger.info("Active contract: %s @ $%.2f", contract_info['symbol'], contract_info['current_price'])
    system_state['initialized'] = True
    if _load_vol_forecast() is None:
        logger.warning("Volatility forecast unavailable at startup; retried on /data after the backoff")
    return True


@app.route('/')
def root():
    """Root endpoint - server status"""
    ensure_startup_started()

    if not OIL_IMPORTS_AVAILABLE:
        return json_response({
            'service': SERVICE_NAME,
            'status': 'CRITICAL_ERROR',
            'error': 'oil.py imports not available',
            'message': 'Server cannot function without oil.py',
            'ready': False,
            'server_time': _utc_now_iso()
        }, 503)

    if not _startup_ready.is_set():
        return json_response(
            startup_payload(
                'Background startup in progress. API data will be available shortly.',
                startup_retry_seconds()
            ),
            503,
            retry_after=startup_retry_seconds(),
        )

    ready = _vol_forecast_ready()
    return json_response({
        'service': SERVICE_NAME,
        'status': 'ACTIVE' if ready else 'DEGRADED',
        'version': '5.0.0',
        'schema_version': SCHEMA_VERSION,
        'startup_ready': True,
        'vol_forecast_ready': ready,
        'ready': ready,
        'endpoints': {
            '/': 'Server status',
            '/data': 'Dashboard payload (docs/DATA_CONTRACT.md data.json, schema_version 3)',
            '/health': 'Readiness check (the volatility forecast is available)',
            '/live': 'Process liveness check',
        },
        'server_time': _utc_now_iso()
    }, 200 if ready else 503, retry_after=None if ready else _vol_retry_seconds())


@app.route('/data')
def get_data():
    """The dashboard payload (docs/DATA_CONTRACT.md data.json, schema_version 3)."""
    ensure_startup_started()

    # Served only after startup: the first request must not race the initial quote fetch.
    if not _startup_ready.is_set():
        return json_response(
            startup_payload('Server starting up. Please wait a few seconds...', startup_retry_seconds()),
            503,
            retry_after=startup_retry_seconds()
        )

    if not OIL_IMPORTS_AVAILABLE:
        return json_response({
            'error': 'CRITICAL_ERROR',
            'message': 'oil.py imports not available - cannot serve data',
            'server_time': _utc_now_iso()
        }, 503)

    try:
        contract_info = get_current_wti_contract()
        if not contract_info or not contract_info.get('current_price'):
            return json_response(
                startup_payload('Waiting for contract data to become available.', startup_retry_seconds()),
                503,
                retry_after=startup_retry_seconds()
            )
        return json_response(dashboard_payload(contract_info))

    except Exception as exc:
        logger.error("Data endpoint failed (%s)", type(exc).__name__)
        return json_response({
            'error': 'DATA_UNAVAILABLE',
            'message': 'Live market data is temporarily unavailable.',
            'server_time': _utc_now_iso()
        }, 503, retry_after=API_STARTUP_RETRY_SECONDS)


@app.route('/live')
def live():
    """Process-only liveness probe; never calls an upstream provider."""
    return json_response({
        'status': 'ALIVE',
        'timestamp': _utc_now_iso(),
    })


@app.route('/health')
def health():
    """Readiness: startup done and the volatility forecast available. Reads cached state only."""
    ensure_startup_started()
    try:
        if not OIL_IMPORTS_AVAILABLE:
            return json_response({
                'status': 'CRITICAL',
                'ready': False,
                'message': 'oil.py imports not available',
                'timestamp': _utc_now_iso()
            }, 503)

        if not _startup_ready.is_set():
            return json_response({
                'status': 'INITIALIZING',
                'ready': False,
                'startup_ready': False,
                'vol_forecast_ready': False,
                'message': 'Background startup in progress',
                'retry_after_seconds': startup_retry_seconds(),
                'startup_attempts': _startup_attempts,
                'timestamp': _utc_now_iso()
            }, 503, retry_after=startup_retry_seconds())

        ready = _vol_forecast_ready()
        built_at = _VOL_CACHE["built_at"]
        return json_response({
            'status': 'HEALTHY' if ready else 'DEGRADED',
            'ready': ready,
            'startup_ready': True,
            'vol_forecast_ready': ready,
            'vol_forecast_built_at': (datetime.fromtimestamp(built_at, timezone.utc).isoformat()
                                      if ready and built_at else None),
            'timestamp': _utc_now_iso()
        }, 200 if ready else 503, retry_after=None if ready else _vol_retry_seconds())

    except Exception as exc:
        logger.error("Health endpoint failed (%s)", type(exc).__name__)
        return json_response({
            'status': 'UNHEALTHY',
            'ready': False,
            'error': 'HEALTH_CHECK_FAILED',
            'timestamp': _utc_now_iso()
        }, 503)


def startup_initialization():
    """Initialize in the background, then signal readiness; a failure is retried after a cooldown."""
    global _startup_started, _startup_thread, _startup_next_retry_at
    try:
        logger.info("Starting initialization...")
        initialize_oil_system()
        _startup_next_retry_at = 0.0
        _startup_ready.set()
        logger.info("Startup complete - ready to serve requests")

    except Exception as e:
        logger.critical("Initialization FAILED (%s)", type(e).__name__)
        system_state['initialized'] = False
        _startup_next_retry_at = time.time() + STARTUP_RETRY_COOLDOWN_SECONDS
        with _startup_lock:
            _startup_started = False
            _startup_thread = None


def ensure_startup_started():
    """Start background initialization only once and avoid side effects on import."""
    global _startup_thread, _startup_started, _startup_attempts
    if _startup_ready.is_set():
        return
    with _startup_lock:
        if _startup_ready.is_set():
            return
        if _startup_thread and _startup_thread.is_alive():
            return
        if _startup_started:
            return
        if time.time() < _startup_next_retry_at:
            return
        _startup_attempts += 1
        _startup_thread = threading.Thread(target=startup_initialization, daemon=True)
        _startup_thread.start()
        _startup_started = True


@app.before_request
def _ensure_startup_for_requests():
    """Guarantee startup thread is running when the app is imported by a WSGI server.

    Except for /live: initialization calls Yahoo, and a liveness probe must never start it (nor
    restart it after each failed attempt's cooldown).
    """
    if request.path == '/live':
        return None
    ensure_startup_started()

    if request.path != '/data':
        return None
    now = time.time()
    client = request.remote_addr or 'unknown'
    with _rate_limit_lock:
        if len(_data_request_times) > 1024:
            for stale_client, stamps in list(_data_request_times.items()):
                if not stamps or now - stamps[-1] >= 60:
                    _data_request_times.pop(stale_client, None)
        recent = [stamp for stamp in _data_request_times.get(client, []) if now - stamp < 60]
        if len(recent) >= DATA_RATE_LIMIT_PER_MINUTE:
            _data_request_times[client] = recent
            return json_response({
                'error': 'RATE_LIMITED',
                'message': 'Too many data requests; retry shortly.',
            }, 429, retry_after=60)
        recent.append(now)
        _data_request_times[client] = recent
    return None


def run_server(host='127.0.0.1', port=9000, debug=False):
    """Run the Flask development server (python -m backend.server)."""
    ensure_startup_started()
    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    run_server()
