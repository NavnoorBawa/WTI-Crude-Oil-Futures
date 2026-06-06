"""
PREMIUM WTI Oil Price Prediction Engine - REAL DATA ONLY
========================================================
Advanced ML-based WTI crude oil price prediction system using premium data sources.
NO RANDOM DATA - REAL MULTI-SOURCE PREDICTIONS ONLY.
Fallbacks and weak horizons are labeled explicitly so the API can distinguish them from qualified forecasts.
"""

import pandas as pd
import numpy as np
import yfinance as yf
import requests
import json
import warnings
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from io import StringIO
from typing import Dict, Optional, Tuple, List, Union
import time
import os
import hashlib
import copy
import tempfile
from pathlib import Path
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ML imports
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.preprocessing import RobustScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.feature_selection import SelectKBest, f_regression, mutual_info_regression
from xgboost import XGBRegressor
from lightgbm import LGBMRegressor

warnings.filterwarnings('ignore')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# yfinance emits HTTP 404 ERROR logs while probing future-dated WTI contracts (e.g. CLN26,
# CLQ26) that are not yet listed during contract discovery. These are expected and handled
# gracefully downstream, so quiet yfinance's own logger to keep operational output clean.
logging.getLogger('yfinance').setLevel(logging.CRITICAL)


def _build_yf_session():
    """Browser-impersonation session for yfinance.

    Yahoo Finance aggressively rate-limits datacenter IPs (Render, AWS, etc.) when the default
    python-requests User-Agent is used, returning HTTP 429 "Too Many Requests". A curl_cffi
    session that impersonates a real Chrome browser dramatically reduces these failures, which
    is the single most common cause of "No valid WTI contracts found" on cloud hosts. Falls
    back to no custom session if curl_cffi is unavailable.
    """
    try:
        from curl_cffi import requests as _cffi_requests
        return _cffi_requests.Session(impersonate="chrome")
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning(f"curl_cffi session unavailable, using default yfinance transport: {exc}")
        return None


_YF_SESSION = _build_yf_session()


def _yf_ticker(symbol):
    """Return a yf.Ticker bound to the browser-impersonation session when available."""
    if _YF_SESSION is not None:
        try:
            return yf.Ticker(symbol, session=_YF_SESSION)
        except TypeError:
            # Older/newer yfinance variants that do not accept a session kwarg.
            pass
    return yf.Ticker(symbol)


def _is_rate_limit_error(err) -> bool:
    msg = str(err).lower()
    return 'too many requests' in msg or 'rate limit' in msg or '429' in msg


def _yf_history_with_retry(symbol, *, period, interval, timeout, max_attempts=3, base_delay=2.0):
    """Fetch yfinance history, retrying ONLY on transient Yahoo rate-limit errors with backoff.

    Empty results are returned as-is (a not-yet-listed future contract legitimately has no data),
    so callers keep their existing empty-handling. Non-rate-limit errors propagate immediately.
    """
    for attempt in range(max_attempts):
        try:
            return _yf_ticker(symbol).history(period=period, interval=interval, timeout=timeout)
        except Exception as err:
            if not _is_rate_limit_error(err) or attempt == max_attempts - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))  # 2s, then 4s

# Backward-compatible API key fallback: use env vars first, then legacy embedded keys.
ALLOW_LEGACY_EMBEDDED_KEYS = os.getenv('ALLOW_LEGACY_EMBEDDED_KEYS', 'false').lower() == 'true'


def _resolve_api_key(env_name: str, legacy_value: str) -> str:
    env_value = os.getenv(env_name, '').strip()
    if env_value:
        return env_value
    return legacy_value if ALLOW_LEGACY_EMBEDDED_KEYS else ''

# Premium API Configuration - Load from environment variables (FIX #1)
@dataclass
class PremiumAPIConfig:
    # Keys are read ONLY from environment variables (locally: .env; in CI: GitHub Actions
    # Secrets). No credentials are committed to source — required so the repo can be public.
    USDA_NASS_KEY: str = field(default_factory=lambda: _resolve_api_key('USDA_NASS_KEY', ''))
    NOAA_CDO_KEY: str = field(default_factory=lambda: _resolve_api_key('NOAA_CDO_KEY', ''))
    ALPHA_VANTAGE_KEY: str = field(default_factory=lambda: _resolve_api_key('ALPHA_VANTAGE_KEY', ''))
    NEWSAPI_KEY: str = field(default_factory=lambda: _resolve_api_key('NEWSAPI_KEY', ''))
    FINNHUB_KEY: str = field(default_factory=lambda: _resolve_api_key('FINNHUB_KEY', ''))
    EIA_API_KEY: str = field(default_factory=lambda: _resolve_api_key('EIA_API_KEY', ''))
    EIA_BASE_URL: str = "https://api.eia.gov/v2"
    FRED_BASE_URL: str = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    def get_missing_required_keys(self) -> List[str]:
        """Return missing premium API keys required for strict mode."""
        required_keys = [
            ('USDA_NASS_KEY', self.USDA_NASS_KEY),
            ('NOAA_CDO_KEY', self.NOAA_CDO_KEY),
            ('ALPHA_VANTAGE_KEY', self.ALPHA_VANTAGE_KEY),
            ('NEWSAPI_KEY', self.NEWSAPI_KEY),
            ('FINNHUB_KEY', self.FINNHUB_KEY),
            ('EIA_API_KEY', self.EIA_API_KEY),
        ]
        return [name for name, value in required_keys if not value]
    
    def __post_init__(self):
        """Validate all required keys are present"""
        missing = self.get_missing_required_keys()
        if missing:
            logger.warning(f"⚠️  Missing API keys: {', '.join(missing)}")
            logger.warning("Set environment variables: export KEY=value")

# Month codes for futures contracts
MONTH_CODES = {
    1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
    7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'
}

# API calibration constants (FIX: Remove magic numbers)
FRED_TYPICAL_DAILY_CHANGE = 0.001
FRED_TYPICAL_VOLATILITY = 0.005

# ── Geopolitical Event Database ───────────────────────────────────────────────
# Historical Iran/Middle East events with verified WTI price impact data.
# Used by the scenario engine to find analogues for current geopolitical signals.
# Fields: peak_pct = max intraday/intraweek spike; settled_pct = price ~2wks later.
IRAN_GEOPOLITICAL_EVENTS = [
    {
        'id': 'abqaiq_2019',
        'date': '2019-09-14',
        'event': 'Abqaiq & Khurais Saudi oil facility attack (Iran/Houthi)',
        'type': 'supply_attack',
        'drivers': ['conflict', 'iran'],
        'wti_before': 54.85,
        'wti_peak': 62.90,
        'peak_pct': 14.6,
        'settled_pct': 5.7,
        'duration_days': 7,
        'supply_disruption_mbpd': 5.7,
        'strait_risk': False,
        'notes': 'Largest single-day supply shock in history; Saudi restored output in weeks',
    },
    {
        'id': 'soleimani_2020',
        'date': '2020-01-03',
        'event': 'US airstrike kills IRGC General Soleimani',
        'type': 'military_escalation',
        'drivers': ['iran', 'conflict'],
        'wti_before': 63.05,
        'wti_peak': 65.47,
        'peak_pct': 3.8,
        'settled_pct': -6.4,
        'duration_days': 14,
        'supply_disruption_mbpd': 0.0,
        'strait_risk': True,
        'notes': 'Geopolitical premium fully reversed in 2 weeks; actual supply unaffected',
    },
    {
        'id': 'jcpoa_exit_2018',
        'date': '2018-05-08',
        'event': 'US exits JCPOA Iran nuclear deal; sanctions reimposed',
        'type': 'sanctions',
        'drivers': ['sanctions', 'iran'],
        'wti_before': 70.73,
        'wti_peak': 76.41,
        'peak_pct': 8.0,
        'settled_pct': -8.1,
        'duration_days': 90,
        'supply_disruption_mbpd': 1.5,
        'strait_risk': False,
        'notes': 'Iranian exports fell ~1.5 mbpd by Nov 2018; WTI peaked Oct then crashed on macro',
    },
    {
        'id': 'eu_iran_embargo_2012',
        'date': '2012-07-01',
        'event': 'EU oil embargo on Iran takes effect',
        'type': 'sanctions',
        'drivers': ['sanctions', 'iran'],
        'wti_before': 84.00,
        'wti_peak': 106.00,
        'peak_pct': 26.2,
        'settled_pct': 4.8,
        'duration_days': 120,
        'supply_disruption_mbpd': 1.0,
        'strait_risk': False,
        'notes': 'Iranian exports dropped from 2.2 to 1.2 mbpd; sustained WTI elevation in 2012',
    },
    {
        'id': 'tanker_attacks_2019',
        'date': '2019-06-13',
        'event': 'Iran tanker attacks in Gulf of Oman / Strait of Hormuz standoff',
        'type': 'strait_tension',
        'drivers': ['iran', 'conflict'],
        'wti_before': 52.51,
        'wti_peak': 54.66,
        'peak_pct': 4.1,
        'settled_pct': -4.9,
        'duration_days': 21,
        'supply_disruption_mbpd': 0.0,
        'strait_risk': True,
        'notes': 'Limpet mine attacks; US-Iran naval standoff; no actual supply cut; premium faded',
    },
    {
        'id': 'iran_israel_strike_2024',
        'date': '2024-04-13',
        'event': 'Iran direct missile/drone strike on Israel (first ever direct attack)',
        'type': 'military_escalation',
        'drivers': ['iran', 'conflict'],
        'wti_before': 85.66,
        'wti_peak': 87.67,
        'peak_pct': 2.3,
        'settled_pct': -4.3,
        'duration_days': 10,
        'supply_disruption_mbpd': 0.0,
        'strait_risk': False,
        'notes': 'Market rapidly de-escalated after limited Israeli retaliation; WTI fell post-event',
    },
    {
        'id': 'houthi_red_sea_2023',
        'date': '2023-12-18',
        'event': 'Houthi Red Sea shipping attacks escalate (Iran-backed)',
        'type': 'strait_tension',
        'drivers': ['conflict', 'iran'],
        'wti_before': 71.00,
        'wti_peak': 79.00,
        'peak_pct': 11.3,
        'settled_pct': 5.6,
        'duration_days': 45,
        'supply_disruption_mbpd': 0.0,
        'strait_risk': True,
        'notes': 'Shipping rerouted via Cape of Good Hope; oil supply unaffected but freight +300%',
    },
    {
        'id': 'gulf_war_1990',
        'date': '1990-08-02',
        'event': 'Iraq invades Kuwait — Gulf War I (largest modern supply shock)',
        'type': 'supply_attack',
        'drivers': ['conflict', 'opec'],
        'wti_before': 21.24,
        'wti_peak': 41.15,
        'peak_pct': 93.7,
        'settled_pct': 17.7,
        'duration_days': 180,
        'supply_disruption_mbpd': 4.3,
        'strait_risk': False,
        'notes': 'Kuwait+Iraq 4.3 mbpd removed; Saudi spare capacity absorbed shock; war ended premium',
    },
    {
        'id': 'iran_nuclear_2012_sanctions',
        'date': '2012-01-23',
        'event': 'US/EU tighten Iran nuclear sanctions (central bank + oil)',
        'type': 'sanctions',
        'drivers': ['sanctions', 'iran'],
        'wti_before': 99.00,
        'wti_peak': 109.77,
        'peak_pct': 10.9,
        'settled_pct': 6.5,
        'duration_days': 60,
        'supply_disruption_mbpd': 0.5,
        'strait_risk': True,
        'notes': 'Iran threatened Strait closure; elevated WTI for months; no actual blockade',
    },
    {
        'id': 'libya_civil_war_2011',
        'date': '2011-02-17',
        'event': 'Libya civil war disrupts 1.6 mbpd output (Arab Spring)',
        'type': 'supply_attack',
        'drivers': ['conflict'],
        'wti_before': 88.00,
        'wti_peak': 113.00,
        'peak_pct': 28.4,
        'settled_pct': 9.1,
        'duration_days': 60,
        'supply_disruption_mbpd': 1.6,
        'strait_risk': False,
        'notes': 'WTI peaked Apr 2011 as Libyan output collapsed; sustained premium for months',
    },
    {
        'id': 'russia_ukraine_2022',
        'date': '2022-02-24',
        'event': 'Russia full-scale invasion of Ukraine; Western sanctions on Russian oil',
        'type': 'supply_attack',
        'drivers': ['conflict', 'sanctions'],
        'wti_before': 92.00,
        'wti_peak': 130.00,
        'peak_pct': 41.3,
        'settled_pct': 8.7,
        'duration_days': 14,
        'supply_disruption_mbpd': 3.0,
        'strait_risk': False,
        'notes': 'Fastest major supply shock in decades; peak held only 2 days; settled ~$100 for months',
    },
    {
        'id': 'opec_cut_2016',
        'date': '2016-11-30',
        'event': 'OPEC agrees first production cut in 8 years (1.2 mbpd)',
        'type': 'supply_cut',
        'drivers': ['opec'],
        'wti_before': 44.00,
        'wti_peak': 55.00,
        'peak_pct': 25.0,
        'settled_pct': 13.6,
        'duration_days': 30,
        'supply_disruption_mbpd': 1.2,
        'strait_risk': False,
        'notes': 'OPEC+ deal after 2-year price war; market bought the announcement; premium sustained',
    },
    {
        'id': 'us_iraq_war_2003',
        'date': '2003-01-01',
        'event': 'US Iraq War buildup and invasion — buy rumor, sell news',
        'type': 'military_escalation',
        'drivers': ['conflict'],
        'wti_before': 32.00,
        'wti_peak': 40.00,
        'peak_pct': 25.0,
        'settled_pct': -12.5,
        'duration_days': 75,
        'supply_disruption_mbpd': 0.0,
        'strait_risk': False,
        'notes': 'Classic buy-the-rumor sell-the-news; WTI collapsed after Baghdad fell; no supply impact',
    },
]

# Strait of Hormuz disruption scenarios: ~21 mbpd passes through daily.
# Price impact estimates derived from IEA supply elasticity and historical analogues.
HORMUZ_SCENARIOS = [
    {
        'name': 'MINOR DISRUPTION',
        'description': 'Brief incident, shipping delays, no sustained closure (<3 days)',
        'supply_loss_mbpd': 1.0,
        'price_impact_low': 3.0,
        'price_impact_mid': 8.0,
        'price_impact_high': 15.0,
        'duration_weeks': 1,
        'historical_ref': 'tanker_attacks_2019',
    },
    {
        'name': 'PARTIAL CLOSURE',
        'description': 'Military action forces reduced throughput, rerouting required (1-4 weeks)',
        'supply_loss_mbpd': 5.0,
        'price_impact_low': 15.0,
        'price_impact_mid': 28.0,
        'price_impact_high': 45.0,
        'duration_weeks': 4,
        'historical_ref': 'abqaiq_2019',
    },
    {
        'name': 'EXTENDED BLOCKADE',
        'description': 'Sustained military conflict blocks major throughput (1-3 months)',
        'supply_loss_mbpd': 12.0,
        'price_impact_low': 40.0,
        'price_impact_mid': 65.0,
        'price_impact_high': 100.0,
        'duration_weeks': 12,
        'historical_ref': 'gulf_war_1990',
    },
    {
        'name': 'FULL CLOSURE',
        'description': 'Complete blockade of ~21 mbpd (~20% of global supply)',
        'supply_loss_mbpd': 21.0,
        'price_impact_low': 60.0,
        'price_impact_mid': 90.0,
        'price_impact_high': 150.0,
        'duration_weeks': 8,
        'historical_ref': 'gulf_war_1990',
    },
]

# Contract discovery cache avoids repeated CL=F metadata fetches in a single run.
CONTRACT_CACHE_TTL_SECONDS = max(30, int(os.getenv('CONTRACT_CACHE_TTL_SECONDS', '90')))
_contract_cache = {
    'fetched_at': 0.0,
    'data': None,
}

def calculate_wti_expiry_date(year, month):
    """Calculate WTI futures expiry date: third business day prior to 25th of the month before delivery month"""
    expiry_month = month - 1
    expiry_year = year
    if expiry_month <= 0:
        expiry_month = 12
        expiry_year -= 1
    
    twenty_fifth = datetime(expiry_year, expiry_month, 25).date()
    
    business_days_back = 0
    current_date = twenty_fifth
    
    while business_days_back < 3:
        current_date -= timedelta(days=1)
        if current_date.weekday() < 5:
            business_days_back += 1
    
    return current_date

def get_current_wti_contract(force_refresh: bool = False):
    """Get current active WTI contract with auto-switching logic"""
    now_ts = time.time()
    cached = _contract_cache.get('data')
    fetched_at = _contract_cache.get('fetched_at', 0.0)
    if (not force_refresh) and cached and (now_ts - fetched_at) <= CONTRACT_CACHE_TTL_SECONDS:
        return copy.deepcopy(cached)

    def _cache_and_return(payload: Dict) -> Dict:
        _contract_cache['fetched_at'] = now_ts
        _contract_cache['data'] = copy.deepcopy(payload)
        return payload

    now = datetime.now()
    current_month = now.month
    current_year = now.year
    
    # Always try CL=F first as it's the most reliable continuous contract
    try:
        logger.info("🔍 Fetching WTI data from CL=F (continuous contract)")
        validation_data = _yf_history_with_retry("CL=F", period="5d", interval="1d", timeout=10)

        if not validation_data.empty and len(validation_data) >= 1:
            current_price = float(validation_data['Close'].iloc[-1])
            volume = int(validation_data['Volume'].iloc[-1]) if not pd.isna(validation_data['Volume'].iloc[-1]) else 0

            # For continuous contract, map to the first delivery month that is not near expiry.
            # This avoids returning expired/invalid symbols when month boundaries roll over.
            selected_year = None
            selected_month = None
            selected_expiry = None
            selected_days = None
            for i in range(1, 7):
                total_months = current_month - 1 + i
                candidate_year = current_year + total_months // 12
                candidate_month = (total_months % 12) + 1
                candidate_expiry = calculate_wti_expiry_date(candidate_year, candidate_month)
                candidate_days = (candidate_expiry - now.date()).days
                if candidate_days >= 7:
                    selected_year = candidate_year
                    selected_month = candidate_month
                    selected_expiry = candidate_expiry
                    selected_days = candidate_days
                    break

            if selected_year is None:
                # Last-resort fallback keeps symbol generation deterministic.
                total_months = current_month + 5
                selected_year = current_year + total_months // 12
                selected_month = (total_months % 12) + 1
                selected_expiry = calculate_wti_expiry_date(selected_year, selected_month)
                selected_days = (selected_expiry - now.date()).days

            contract_symbol = f"CL{MONTH_CODES[selected_month]}{str(selected_year)[-2:]}"
            expiry_date = selected_expiry
            days_to_expiry = selected_days

            logger.info(f"✅ Found WTI data: {contract_symbol} @ ${current_price:.2f}")

            return _cache_and_return({
                'symbol': contract_symbol,
                'yfinance_symbol': 'CL=F',
                'history_symbol': contract_symbol,
                'current_price': current_price,
                'volume': volume,
                'expiry_date': expiry_date.isoformat(),
                'days_to_expiry': days_to_expiry,
                'description': f'WTI CRUDE OIL FUTURES {contract_symbol}',
                'security_name': f'{contract_symbol} WTI CRUDE',
                'data_source': 'yfinance_continuous',
                'timestamp': datetime.now().isoformat()
            })
            
    except Exception as e:
        logger.error(f"❌ Failed to get CL=F data: {e}")
    
    # If CL=F fails, try specific contract symbols
    contracts_to_try = []
    contract_failures = []
    skipped_near_expiry = []
    
    # Generate next 6 months of contract symbols
    # BUG24 FIX: Proper modulo arithmetic for month/year wraparound
    for i in range(6):
        total_months = current_month - 1 + i
        target_year = current_year + total_months // 12
        target_month = (total_months % 12) + 1
        
        month_code = MONTH_CODES[target_month]
        year_code = str(target_year)[-2:]
        contract_symbol = f"CL{month_code}{year_code}"
        contracts_to_try.append((contract_symbol, target_year, target_month))
    
    # Try each contract. Space probes slightly so a burst of requests does not trip Yahoo's
    # rate limiter, and retry each probe on transient 429s via the shared helper.
    for probe_index, (contract_symbol, year, month) in enumerate(contracts_to_try):
        try:
            if probe_index > 0:
                time.sleep(0.4)
            logger.info(f"🔍 Trying specific WTI contract: {contract_symbol}")
            validation_data = _yf_history_with_retry(contract_symbol, period="3d", interval="1d", timeout=8)
            
            if not validation_data.empty and len(validation_data) >= 1:
                current_price = float(validation_data['Close'].iloc[-1])
                volume = int(validation_data['Volume'].iloc[-1]) if not pd.isna(validation_data['Volume'].iloc[-1]) else 0
                expiry_date = calculate_wti_expiry_date(year, month)
                days_to_expiry = (expiry_date - now.date()).days
                
                # Skip if contract expires in less than 7 days
                if days_to_expiry < 7:
                    logger.info(f"⚠️  Skipping {contract_symbol}: expires in {days_to_expiry} days")
                    skipped_near_expiry.append(f"{contract_symbol}({days_to_expiry}d)")
                    continue
                
                logger.info(f"✅ Found valid WTI contract: {contract_symbol} @ ${current_price:.2f}")
                
                return _cache_and_return({
                    'symbol': contract_symbol,
                    'yfinance_symbol': contract_symbol,
                    'history_symbol': contract_symbol,
                    'current_price': current_price,
                    'volume': volume,
                    'expiry_date': expiry_date.isoformat(),
                    'days_to_expiry': days_to_expiry,
                    'description': f'WTI CRUDE OIL FUTURES {contract_symbol}',
                    'security_name': f'{contract_symbol} WTI CRUDE',
                    'data_source': 'yfinance_specific',
                    'timestamp': datetime.now().isoformat()
                })
                
        except Exception as e:
            logger.warning(f"Contract {contract_symbol} failed: {e}")
            contract_failures.append(f"{contract_symbol}: {e}")
            continue
    
    # If all contracts fail, this is a critical error
    details = []
    if skipped_near_expiry:
        details.append("near_expiry=" + ", ".join(skipped_near_expiry))
    if contract_failures:
        details.append("fetch_failures=" + "; ".join(contract_failures[:3]))
    detail_msg = (" Details: " + " | ".join(details)) if details else ""
    raise Exception("CRITICAL: No valid WTI contracts found. Cannot operate without real data." + detail_msg)

class PremiumWTIPredictor:
    """Premium WTI Oil Price Prediction Engine - REAL DATA ONLY"""
    
    def __init__(self):
        """Initialize the premium prediction engine"""
        self.config = PremiumAPIConfig()
        # Free-API mode by default: run with available real sources unless strict mode is explicitly enabled.
        self.strict_premium_api_required = os.getenv('STRICT_PREMIUM_API_REQUIRED', 'false').lower() == 'true'
        # Floor of 0 (not 1) so a public auto-refresh deploy can produce a snapshot even when
        # every keyed external API is unavailable — the core price/ML/scenario engine still runs.
        self.min_required_external_sources = max(0, int(os.getenv('MIN_REQUIRED_EXTERNAL_SOURCES', '1')))
        self.external_fetch_workers = max(2, int(os.getenv('EXTERNAL_FETCH_WORKERS', '4')))
        self.model_n_estimators = max(20, int(os.getenv('MODEL_N_ESTIMATORS', '60')))
        self.model_cpu_workers = max(1, int(os.getenv('MODEL_CPU_WORKERS', '1')))
        self.interval_quantile = min(0.95, max(0.60, float(os.getenv('INTERVAL_CALIBRATION_QUANTILE', '0.80'))))
        self.target_interval_coverage = min(0.95, max(0.55, float(os.getenv('TARGET_INTERVAL_COVERAGE', '0.80'))))
        self.interval_coverage_gain = min(0.60, max(0.0, float(os.getenv('INTERVAL_COVERAGE_GAIN', '0.25'))))
        self.confidence_floor = max(5.0, min(50.0, float(os.getenv('CONFIDENCE_FLOOR_PERCENT', '10'))))
        self.min_live_quality_samples = max(4, int(os.getenv('MIN_LIVE_QUALITY_SAMPLES', '10')))
        self.min_live_direction_accuracy = float(os.getenv('MIN_LIVE_DIRECTION_ACCURACY_PERCENT', '50'))
        self.min_backtest_direction_accuracy = float(os.getenv('MIN_BACKTEST_DIRECTION_ACCURACY_PERCENT', '45'))
        self.min_backtest_samples = max(10, int(os.getenv('MIN_BACKTEST_SAMPLES', '30')))
        self.min_quality_confidence = float(os.getenv('MIN_QUALITY_CONFIDENCE_PERCENT', '15'))
        self.max_quality_drift_score = float(os.getenv('MAX_QUALITY_DRIFT_SCORE', '3.0'))
        self.actual_quote_heartbeat_seconds = max(60, int(os.getenv('ACTUAL_QUOTE_HEARTBEAT_SECONDS', '300')))
        self.market_timezone = ZoneInfo(os.getenv('MARKET_TIMEZONE', 'America/Chicago'))
        self.storage_timezone = datetime.now().astimezone().tzinfo or timezone.utc
        self.time_series_cv_splits = max(2, int(os.getenv('TIME_SERIES_CV_SPLITS', '2')))
        self.max_hourly_training_samples = max(240, int(os.getenv('MAX_HOURLY_TRAINING_SAMPLES', '720')))
        self.max_selected_features = max(12, int(os.getenv('MAX_SELECTED_FEATURES', '24')))
        self.external_data_ttl_seconds = max(30, int(os.getenv('EXTERNAL_DATA_TTL_SECONDS', '180')))
        self.market_data_ttl_seconds = max(10, int(os.getenv('MARKET_DATA_TTL_SECONDS', '60')))
        self.daily_training_period = os.getenv('DAILY_TRAINING_PERIOD', '18mo')
        self.hourly_training_period = os.getenv('HOURLY_TRAINING_PERIOD', '90d')
        self.market_context_period = os.getenv('MARKET_CONTEXT_PERIOD', '3y')
        base_daily_lookback = max(30, int(os.getenv('DAILY_FEATURE_LOOKBACK_BARS', '63')))
        self.daily_feature_lookback_bars_1d = max(30, int(os.getenv('DAILY_FEATURE_LOOKBACK_BARS_1D', str(base_daily_lookback))))
        self.daily_feature_lookback_bars_1w = max(self.daily_feature_lookback_bars_1d, int(os.getenv('DAILY_FEATURE_LOOKBACK_BARS_1W', str(base_daily_lookback))))
        self.daily_feature_lookback_bars = max(base_daily_lookback, self.daily_feature_lookback_bars_1w)
        self.hourly_feature_lookback_bars = max(24, int(os.getenv('HOURLY_FEATURE_LOOKBACK_BARS', '96')))
        self.daily_target_mode = os.getenv('DAILY_TARGET_MODE', 'return').strip().lower() or 'return'
        if self.daily_target_mode not in {'price', 'return', 'excess_return'}:
            self.daily_target_mode = 'return'
        # External API snapshots are point-in-time values; avoid injecting them into historical rows by default.
        self.use_external_features_in_training = os.getenv('USE_EXTERNAL_FEATURES_IN_TRAINING', 'false').lower() == 'true'
        # Default OFF: walk-forward backtest showed the FRED/EIA macro family does not improve the
        # 1W signal (Sharpe 2.07 without macro vs 1.90 with it) AND is the only revision-prone data
        # source. Deploying without it makes the live model identical to the leakage-proof backtested
        # configuration. Re-enable via env var only after auditing point-in-time (ALFRED) vintages.
        self.use_historical_external_features_in_training = os.getenv('USE_HISTORICAL_EXTERNAL_FEATURES_IN_TRAINING', 'false').lower() == 'true'
        self.eia_release_lag_days = max(3, int(os.getenv('EIA_RELEASE_LAG_DAYS', '5')))
        self.fred_daily_release_lag_days = max(1, int(os.getenv('FRED_DAILY_RELEASE_LAG_DAYS', '1')))
        self.fred_monthly_release_lag_days = max(5, int(os.getenv('FRED_MONTHLY_RELEASE_LAG_DAYS', '15')))
        self.contract_refresh_ttl_seconds = max(30, int(os.getenv('CONTRACT_REFRESH_TTL_SECONDS', '120')))
        self.model_cache = {}
        self.latest_diagnostics = {}
        self._external_data_mem_cache = {'fetched_at': 0.0, 'data': None}
        self._market_data_mem_cache = {}
        self._historical_external_mem_cache = {}
        self._last_contract_refresh_ts = 0.0
        self._market_source_info = {
            'daily_history': None,
            'hourly_history': None,
        }
        
        # Get current contract info
        self.contract_info = get_current_wti_contract()
        self.contract_symbol = self.contract_info['symbol']
        self.yfinance_symbol = self.contract_info['yfinance_symbol']
        self.history_symbol = self.contract_info.get('history_symbol', self.yfinance_symbol)
        
        # Setup data storage paths
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        
        # Core data files
        self.predictions_file = self.data_dir / f"{self.contract_symbol}_predictions.json"
        self.actual_prices_file = self.data_dir / f"{self.contract_symbol}_actual_prices.json"
        self.accuracy_file = self.data_dir / f"{self.contract_symbol}_accuracy_metrics.json"
        self.external_data_cache = self.data_dir / f"{self.contract_symbol}_external_data.json"
        
        # Horizon-specific files for detailed storage
        self.predictions_1h_file = self.data_dir / f"{self.contract_symbol}_predictions_1h.json"
        self.predictions_1d_file = self.data_dir / f"{self.contract_symbol}_predictions_1d.json"
        self.predictions_1w_file = self.data_dir / f"{self.contract_symbol}_predictions_1w.json"
        
        # Load existing data
        self.stored_predictions = self._load_stored_predictions()
        self.stored_actual_prices = self._load_stored_actual_prices()
        self.accuracy_metrics = self._load_accuracy_metrics()
        
        # Load horizon-specific predictions
        self.predictions_1h = self._load_horizon_predictions('1h')
        self.predictions_1d = self._load_horizon_predictions('1d')
        self.predictions_1w = self._load_horizon_predictions('1w')

        if not self.use_external_features_in_training:
            logger.info("Training external features disabled (USE_EXTERNAL_FEATURES_IN_TRAINING=false)")
        
        logger.info(f"Premium WTI Predictor initialized for contract: {self.contract_symbol}")

    def _refresh_contract_storage_paths(self):
        """Update contract-bound storage paths when active contract rolls over."""
        self.predictions_file = self.data_dir / f"{self.contract_symbol}_predictions.json"
        self.actual_prices_file = self.data_dir / f"{self.contract_symbol}_actual_prices.json"
        self.accuracy_file = self.data_dir / f"{self.contract_symbol}_accuracy_metrics.json"
        self.external_data_cache = self.data_dir / f"{self.contract_symbol}_external_data.json"
        self.predictions_1h_file = self.data_dir / f"{self.contract_symbol}_predictions_1h.json"
        self.predictions_1d_file = self.data_dir / f"{self.contract_symbol}_predictions_1d.json"
        self.predictions_1w_file = self.data_dir / f"{self.contract_symbol}_predictions_1w.json"

    def _refresh_contract_if_needed(self):
        """Refresh current contract and reload storage if contract symbol changed."""
        now_ts = time.time()
        if self._last_contract_refresh_ts and (now_ts - self._last_contract_refresh_ts) < self.contract_refresh_ttl_seconds:
            return

        latest = get_current_wti_contract()
        self._last_contract_refresh_ts = now_ts
        latest_symbol = latest.get('symbol', self.contract_symbol)
        latest_yf_symbol = latest.get('yfinance_symbol', self.yfinance_symbol)
        latest_history_symbol = latest.get('history_symbol', latest_yf_symbol)

        if latest_symbol != self.contract_symbol:
            logger.info(f"Contract rollover detected: {self.contract_symbol} -> {latest_symbol}")
            self.contract_info = latest
            self.contract_symbol = latest_symbol
            self.yfinance_symbol = latest_yf_symbol
            self.history_symbol = latest_history_symbol
            self._refresh_contract_storage_paths()

            self.stored_predictions = self._load_stored_predictions()
            self.stored_actual_prices = self._load_stored_actual_prices()
            self.accuracy_metrics = self._load_accuracy_metrics()
            self.predictions_1h = self._load_horizon_predictions('1h')
            self.predictions_1d = self._load_horizon_predictions('1d')
            self.predictions_1w = self._load_horizon_predictions('1w')
            self._market_data_mem_cache = {}
            self._external_data_mem_cache = {'fetched_at': 0.0, 'data': None}
        else:
            self.contract_info = latest
            self.yfinance_symbol = latest_yf_symbol
            self.history_symbol = latest_history_symbol

    def _missing_key_source_payload(self, source_name: str, key_name: str) -> Dict:
        """Return a standardized payload when an optional free API key is missing."""
        return {
            'data_quality': 0,
            'source': f'{source_name}_missing_key',
            'skipped': True,
            'missing_key': key_name,
            'timestamp': datetime.now().isoformat()
        }

    def _validate_required_api_keys(self):
        """Validate premium API keys with optional strict mode enforcement."""
        missing_keys = self.config.get_missing_required_keys()
        if missing_keys and self.strict_premium_api_required:
            raise Exception(
                "CRITICAL: Missing required premium API keys: "
                + ", ".join(missing_keys)
                + ". Set env vars or disable strict mode with STRICT_PREMIUM_API_REQUIRED=false."
            )
        if missing_keys:
            logger.warning(
                "⚠️ Continuing with missing premium API keys because "
                "STRICT_PREMIUM_API_REQUIRED=false"
            )

    def _validate_external_data_sources(self, external_data: Dict[str, Dict]):
        """Validate external sources with strict and free-API modes."""
        available_sources = []
        failed_sources = []
        for source_name, source_data in external_data.items():
            if not isinstance(source_data, dict):
                failed_sources.append(source_name)
                continue
            if source_data.get('error'):
                failed_sources.append(source_name)
                continue
            if source_data.get('data_quality', 0) <= 0:
                failed_sources.append(source_name)
                continue
            available_sources.append(source_name)

        # Strict mode requires all configured premium sources to be available.
        if failed_sources and self.strict_premium_api_required:
            raise Exception(
                "CRITICAL: Required premium external data sources unavailable: "
                + ", ".join(sorted(set(failed_sources)))
                + ". No fallback mode is enabled."
            )

        # Free-API mode: require a minimum number of real external sources.
        if len(available_sources) < self.min_required_external_sources:
            raise Exception(
                "CRITICAL: Insufficient real external data sources. "
                + f"Available={len(available_sources)}, Required={self.min_required_external_sources}."
            )

        if failed_sources:
            logger.warning(
                "⚠️ Continuing with degraded external data because "
                "STRICT_PREMIUM_API_REQUIRED=false. Failed sources: "
                + ", ".join(sorted(set(failed_sources)))
            )
        logger.info(
            f"External data sources available: {len(available_sources)}/{len(external_data)} "
            f"(minimum required: {self.min_required_external_sources})"
        )
    
    def _load_stored_predictions(self):
        """Load stored predictions"""
        if self.predictions_file.exists():
            try:
                with open(self.predictions_file, 'r') as f:
                    return self._normalize_time_index_store(json.load(f))
            except Exception as e:
                logger.warning(f"Could not load predictions: {e}")
        return {}
    
    def _load_stored_actual_prices(self):
        """Load stored actual prices"""
        if self.actual_prices_file.exists():
            try:
                with open(self.actual_prices_file, 'r') as f:
                    loaded = self._normalize_time_index_store(json.load(f))
                    cleaned, changed = self._dedupe_actual_price_store(loaded)
                    if changed:
                        self._atomic_write_json(self.actual_prices_file, cleaned)
                    return cleaned
            except Exception as e:
                logger.warning(f"Could not load actual prices: {e}")
        return {}
    
    def _load_accuracy_metrics(self):
        """Load accuracy metrics"""
        if self.accuracy_file.exists():
            try:
                with open(self.accuracy_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load accuracy metrics: {e}")
        return {}
    
    def _load_horizon_predictions(self, horizon):
        """Load horizon-specific predictions"""
        file_path = getattr(self, f'predictions_{horizon}_file')
        if file_path.exists():
            try:
                with open(file_path, 'r') as f:
                    return self._normalize_time_index_store(json.load(f))
            except Exception as e:
                logger.warning(f"Could not load {horizon} predictions: {e}")
        return {}

    def _normalize_time_index_store(self, payload):
        """Normalize persisted records to a timestamp-keyed dict format."""
        if isinstance(payload, dict):
            # Forward compatibility for wrapped payloads.
            if isinstance(payload.get('records'), dict):
                return payload['records']
            return payload
        return {}

    def _sorted_time_items(self, payload):
        """Return timestamp-keyed payload items in chronological order."""
        return sorted(
            (payload or {}).items(),
            key=lambda kv: self._sort_timestamp_key(kv[0]),
        )

    def _current_timestamp_iso(self):
        """Return a canonical UTC timestamp for persisted records."""
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    def _sort_timestamp_key(self, timestamp_value):
        """Return a numeric key safe for sorting naive and aware timestamps together."""
        parsed = self._safe_parse_iso(timestamp_value)
        return parsed.timestamp() if parsed is not None else float('-inf')

    def _record_market_source(self, series_kind, symbol, rows, from_cache=False):
        """Persist the last market data provenance used for model/history fetches."""
        self._market_source_info[series_kind] = {
            'symbol': symbol,
            'rows': int(rows),
            'from_cache': bool(from_cache),
            'recorded_at': self._current_timestamp_iso(),
        }

    def _to_market_time(self, timestamp_value=None):
        """Convert a timestamp to America/Chicago for CME session checks."""
        try:
            if timestamp_value is None:
                return datetime.now(timezone.utc).astimezone(self.market_timezone)

            ts = pd.Timestamp(timestamp_value)
            if ts.tzinfo is None:
                ts = ts.tz_localize('UTC')
            else:
                ts = ts.tz_convert('UTC')
            return ts.tz_convert(self.market_timezone).to_pydatetime()
        except Exception:
            return datetime.now(timezone.utc).astimezone(self.market_timezone)

    def _is_cme_cl_session_open(self, timestamp_value=None):
        """WTI futures session check: Sun 17:00 CT to Fri 16:00 CT with daily 16:00-17:00 break."""
        market_time = self._to_market_time(timestamp_value)
        weekday = market_time.weekday()
        minute_of_day = market_time.hour * 60 + market_time.minute

        if weekday == 5:
            return False
        if weekday == 6:
            return minute_of_day >= 17 * 60
        if weekday == 4:
            return minute_of_day < 16 * 60
        return not (16 * 60 <= minute_of_day < 17 * 60)

    def _prices_match(self, left_price, left_volume, right_price, right_volume):
        """Compare two stored quotes conservatively to avoid duplicate actual points."""
        left_numeric = pd.to_numeric(left_price, errors='coerce')
        right_numeric = pd.to_numeric(right_price, errors='coerce')
        if pd.isna(left_numeric) or pd.isna(right_numeric):
            return False

        left_volume_numeric = pd.to_numeric(left_volume, errors='coerce')
        right_volume_numeric = pd.to_numeric(right_volume, errors='coerce')
        left_volume_value = int(left_volume_numeric) if not pd.isna(left_volume_numeric) else 0
        right_volume_value = int(right_volume_numeric) if not pd.isna(right_volume_numeric) else 0

        return abs(float(left_numeric) - float(right_numeric)) < 1e-9 and left_volume_value == right_volume_value

    def _dedupe_actual_price_store(self, payload):
        """Collapse redundant stored quote heartbeats while preserving the latest closed-session print."""
        changed = False
        cleaned = {}
        last_kept_timestamp = None
        last_kept_data = None

        for timestamp, raw_data in self._sorted_time_items(payload):
            if not isinstance(raw_data, dict):
                changed = True
                continue

            price_value = pd.to_numeric(raw_data.get('price'), errors='coerce')
            if pd.isna(price_value) or float(price_value) <= 0:
                changed = True
                continue

            volume_numeric = pd.to_numeric(raw_data.get('volume'), errors='coerce')
            normalized_row = {
                'timestamp': str(raw_data.get('timestamp') or timestamp),
                'price': float(price_value),
                'volume': int(volume_numeric) if not pd.isna(volume_numeric) and float(volume_numeric) > 0 else 0,
            }

            current_time = self._safe_parse_iso(timestamp)
            if last_kept_timestamp and last_kept_data:
                last_time = self._safe_parse_iso(last_kept_timestamp)
                gap_seconds = None
                if current_time is not None and last_time is not None:
                    gap_seconds = (current_time - last_time).total_seconds()

                if self._prices_match(
                    last_kept_data.get('price'),
                    last_kept_data.get('volume'),
                    normalized_row.get('price'),
                    normalized_row.get('volume'),
                ):
                    if not self._is_cme_cl_session_open(timestamp):
                        cleaned.pop(last_kept_timestamp, None)
                        cleaned[timestamp] = normalized_row
                        last_kept_timestamp = timestamp
                        last_kept_data = normalized_row
                        changed = True
                        continue

                    if gap_seconds is not None and gap_seconds < self.actual_quote_heartbeat_seconds:
                        changed = True
                        continue

            cleaned[timestamp] = normalized_row
            last_kept_timestamp = timestamp
            last_kept_data = normalized_row

        return cleaned, changed

    def _hybrid_feature_scores(self, X, y):
        """Blend linear and nonlinear relevance so feature selection is less brittle than F-test only."""
        X_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        feature_count = X_arr.shape[1] if X_arr.ndim == 2 else 0
        if feature_count <= 0:
            return np.array([]), np.array([])

        score_blocks = []
        try:
            f_scores, _ = f_regression(X_arr, y_arr)
            score_blocks.append(np.nan_to_num(f_scores, nan=0.0, posinf=0.0, neginf=0.0))
        except Exception:
            score_blocks.append(np.zeros(feature_count, dtype=float))

        try:
            mi_scores = mutual_info_regression(X_arr, y_arr, random_state=42)
            score_blocks.append(np.nan_to_num(mi_scores, nan=0.0, posinf=0.0, neginf=0.0))
        except Exception:
            score_blocks.append(np.zeros(feature_count, dtype=float))

        normalized_blocks = []
        for block in score_blocks:
            block = np.clip(np.asarray(block, dtype=float), 0.0, None)
            max_value = float(np.max(block)) if block.size else 0.0
            normalized_blocks.append(block / max_value if max_value > 0 else block)

        blended_scores = np.mean(normalized_blocks, axis=0) if normalized_blocks else np.zeros(feature_count, dtype=float)
        return blended_scores, np.ones(feature_count, dtype=float)

    def _compose_model_weight_score(self, regression_score, direction_accuracy):
        """Blend regression fit with directional skill because the product is judged on both."""
        regression_component = float(np.clip(regression_score, 0.0, 1.0))
        direction_component = float(np.clip((float(direction_accuracy or 50.0) / 100.0), 0.0, 1.0))
        direction_weight = float(np.clip(getattr(self, 'model_direction_weight', 0.6), 0.0, 1.0))
        regression_weight = 1.0 - direction_weight
        return float(np.clip(
            regression_weight * regression_component + direction_weight * direction_component,
            0.0,
            1.0,
        ))

    def _stabilize_ensemble_prediction(self, reference_price, ensemble_prediction, model_predictions, model_scores, model_direction_scores):
        """
        Reduce oversized moves when models disagree and lean slightly toward the best directional model
        when the raw ensemble sign conflicts with stronger directional evidence.
        """
        reference_price = float(reference_price)
        if not np.isfinite(reference_price) or reference_price <= 0:
            return float(ensemble_prediction), {'direction_consensus': 1.0, 'shrink_factor': 1.0, 'leader_model': None}

        prediction_map = {
            name: float(pred)
            for name, pred in (model_predictions or {}).items()
            if pred is not None and np.isfinite(pred)
        }
        if not prediction_map:
            return float(ensemble_prediction), {'direction_consensus': 1.0, 'shrink_factor': 1.0, 'leader_model': None}

        weighted_direction = 0.0
        weighted_accuracy = 0.0
        total_weight = 0.0
        for model_name, pred in prediction_map.items():
            weight = max(0.3, min(1.0, float((model_scores or {}).get(model_name, 0.5) or 0.5)))
            delta = float(pred) - reference_price
            direction = 0.0 if abs(delta) < 1e-9 else float(np.sign(delta))
            weighted_direction += direction * weight
            weighted_accuracy += float((model_direction_scores or {}).get(model_name, 50.0) or 50.0) * weight
            total_weight += weight

        direction_consensus = abs(weighted_direction) / total_weight if total_weight > 0 else 1.0
        avg_direction_accuracy = weighted_accuracy / total_weight if total_weight > 0 else 50.0
        direction_leader = max(
            prediction_map,
            key=lambda name: float((model_direction_scores or {}).get(name, 50.0) or 50.0)
        )

        adjusted_prediction = float(ensemble_prediction)
        leader_prediction = prediction_map.get(direction_leader, adjusted_prediction)
        ensemble_sign = float(np.sign(adjusted_prediction - reference_price))
        leader_sign = float(np.sign(leader_prediction - reference_price))
        leader_accuracy = float((model_direction_scores or {}).get(direction_leader, 50.0) or 50.0)
        if ensemble_sign != 0.0 and leader_sign != 0.0 and ensemble_sign != leader_sign and leader_accuracy >= 52.0:
            leader_blend = 0.2 + 0.35 * float(np.clip((leader_accuracy - 50.0) / 25.0, 0.0, 1.0))
            adjusted_prediction = ((1.0 - leader_blend) * adjusted_prediction) + (leader_blend * leader_prediction)

        delta = adjusted_prediction - reference_price
        shrink_floor = float(np.clip(getattr(self, 'model_consensus_shrink_floor', 0.35), 0.1, 0.9))
        consensus_shrink = shrink_floor + ((1.0 - shrink_floor) * direction_consensus)
        accuracy_shrink = 0.45 + (0.55 * float(np.clip(avg_direction_accuracy / 100.0, 0.0, 1.0)))
        shrink_factor = float(np.clip(consensus_shrink * accuracy_shrink, shrink_floor, 1.0))
        adjusted_prediction = reference_price + (delta * shrink_factor)

        return float(adjusted_prediction), {
            'direction_consensus': float(direction_consensus),
            'average_direction_accuracy': float(avg_direction_accuracy),
            'shrink_factor': float(shrink_factor),
            'leader_model': direction_leader,
        }

    def _compute_drift_challenger(self, price_series, reference_price, horizon):
        """Simple challenger forecast based on recent average step change."""
        horizon_steps = 1 if horizon == '1d' else 5 if horizon == '1w' else 1
        series = pd.Series(price_series, dtype=float).dropna()
        if len(series) < 2:
            return float(reference_price)

        avg_step_change = float(series.diff().dropna().tail(20).mean())
        if not np.isfinite(avg_step_change):
            avg_step_change = 0.0
        return float(reference_price + (avg_step_change * horizon_steps))

    def _blend_with_drift_challenger(self, reference_price, candidate_prediction, drift_prediction, backtest_metrics, drift_score=0.0, direction_consensus=1.0, horizon=None):
        """Lean toward a simple drift baseline when the model's directional evidence is weak."""
        direction_accuracy = float((backtest_metrics or {}).get('direction_accuracy', 50.0) or 50.0)
        blend_weight = 0.0
        if direction_accuracy < 50.0:
            blend_weight += 0.12 + (0.23 * float(np.clip((50.0 - direction_accuracy) / 15.0, 0.0, 1.0)))
        if float(direction_consensus or 1.0) < 0.55:
            blend_weight += 0.12
        if float(drift_score or 0.0) > 2.5:
            blend_weight += 0.08
        blend_weight = float(np.clip(blend_weight, 0.0, 0.45))
        blended_prediction = ((1.0 - blend_weight) * float(candidate_prediction)) + (blend_weight * float(drift_prediction))
        return float(blended_prediction), blend_weight

    def _atomic_write_json(self, file_path: Path, payload):
        """Persist JSON atomically to avoid partial writes on crashes/restarts."""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                delete=False,
                dir=str(file_path.parent),
                prefix=f"{file_path.name}.",
                suffix='.tmp'
            ) as tmp_file:
                json.dump(payload, tmp_file, indent=2, sort_keys=True)
                temp_path = Path(tmp_file.name)
            os.replace(temp_path, file_path)
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    
    def _save_predictions(self):
        """Save predictions to file"""
        try:
            self._atomic_write_json(self.predictions_file, self.stored_predictions)
        except Exception as e:
            logger.error(f"Could not save predictions: {e}")
    
    def _save_actual_prices(self):
        """Save actual prices to file"""
        try:
            cleaned, _ = self._dedupe_actual_price_store(self.stored_actual_prices)
            self.stored_actual_prices = cleaned
            self._atomic_write_json(self.actual_prices_file, self.stored_actual_prices)
        except Exception as e:
            logger.error(f"Could not save actual prices: {e}")
    
    def _save_accuracy_metrics(self):
        """Save accuracy metrics to file"""
        try:
            self._atomic_write_json(self.accuracy_file, self.accuracy_metrics)
        except Exception as e:
            logger.error(f"Could not save accuracy metrics: {e}")
    
    def _save_horizon_predictions(self, horizon, data):
        """Save horizon-specific predictions"""
        file_path = getattr(self, f'predictions_{horizon}_file')
        try:
            self._atomic_write_json(file_path, data)
        except Exception as e:
            logger.error(f"Could not save {horizon} predictions: {e}")

    def _safe_parse_iso(self, timestamp_str):
        """Parse ISO timestamp safely and tolerate trailing Z."""
        if not timestamp_str:
            return None
        try:
            parsed = pd.Timestamp(str(timestamp_str))
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(self.storage_timezone)
            else:
                parsed = parsed.tz_convert('UTC')
            return parsed.tz_convert('UTC').to_pydatetime()
        except Exception:
            try:
                fallback = datetime.fromisoformat(str(timestamp_str).replace('Z', '+00:00'))
                if fallback.tzinfo is None:
                    fallback = fallback.replace(tzinfo=self.storage_timezone)
                return fallback.astimezone(timezone.utc)
            except Exception:
                return None

    def _get_prediction_reference_price(self, fallback_price):
        """Use the live contract quote as the forecast baseline when available."""
        live_price = pd.to_numeric((self.contract_info or {}).get('current_price'), errors='coerce')
        if not pd.isna(live_price) and float(live_price) > 0:
            return float(live_price)

        fallback_numeric = pd.to_numeric(fallback_price, errors='coerce')
        if not pd.isna(fallback_numeric) and float(fallback_numeric) > 0:
            return float(fallback_numeric)

        return 0.0

    def _get_horizon_delta_and_window(self, horizon):
        """Return target delta and matching window for realized accuracy joins."""
        time_deltas = {'1h': timedelta(hours=1), '1d': timedelta(days=1), '1w': timedelta(weeks=1)}
        search_windows = {
            # Use forward-only joins plus slightly wider windows so exchange breaks/weekends
            # do not suppress otherwise matured forecasts.
            '1h': timedelta(hours=6),
            '1d': timedelta(days=3),
            '1w': timedelta(days=10),
        }
        return time_deltas[horizon], search_windows.get(horizon, timedelta(days=30))

    def _find_closest_actual_price(self, target_time, search_window):
        """Find the first realized price at/after target timestamp within a forward window."""
        closest_actual = None
        min_time_diff = timedelta.max

        for actual_timestamp, actual_data in self.stored_actual_prices.items():
            actual_time = self._safe_parse_iso(actual_timestamp)
            if actual_time is None:
                continue

            # Use forward-only matching so unmatured forecasts are never evaluated early.
            time_diff = actual_time - target_time
            if time_diff < timedelta(0):
                continue
            if time_diff > search_window:
                continue

            if time_diff < min_time_diff:
                min_time_diff = time_diff
                closest_actual = float(actual_data.get('price', 0.0))

        return closest_actual

    def _get_recent_realized_abs_errors(self, horizon, limit=80):
        """Collect recent absolute forecast errors for interval calibration."""
        horizon_data = getattr(self, f'predictions_{horizon}', {})
        if not horizon_data:
            return []

        delta, search_window = self._get_horizon_delta_and_window(horizon)
        sorted_preds = sorted(
            horizon_data.items(),
            key=lambda kv: self._sort_timestamp_key(kv[0]),
            reverse=True,
        )

        errors = []
        for pred_timestamp, pred_data in sorted_preds:
            pred_time = self._safe_parse_iso(pred_timestamp)
            if pred_time is None:
                continue

            actual_price = self._find_closest_actual_price(pred_time + delta, search_window)
            if actual_price is None:
                continue

            predicted_price = float(pred_data.get('prediction', 0.0))
            errors.append(abs(predicted_price - actual_price))
            if len(errors) >= limit:
                break

        return errors

    def _compute_feature_drift_score(self, transformed_features):
        """Estimate feature drift magnitude from transformed feature values."""
        try:
            arr = np.asarray(transformed_features, dtype=float).reshape(-1)
            arr = np.clip(arr, -8.0, 8.0)
            return float(np.mean(np.abs(arr)))
        except Exception:
            return 0.0

    def _calibrated_interval_margin(self, horizon, pred_std, current_price, backtest_metrics, drift_score):
        """Calibrate interval width using model dispersion plus realized-error history."""
        floor_margin = max(0.05, current_price * 0.0015)
        model_margin = 1.64 * max(0.0, float(pred_std))

        candidates = [floor_margin, model_margin]
        adaptive_quantile = float(self.interval_quantile)
        coverage_ratio = None

        horizon_accuracy = self.accuracy_metrics.get(horizon, {}) if isinstance(self.accuracy_metrics, dict) else {}
        if isinstance(horizon_accuracy, dict):
            coverage_pct = float(horizon_accuracy.get('interval_coverage', 0.0) or 0.0)
            interval_total = int(horizon_accuracy.get('interval_total', 0) or 0)
            if coverage_pct > 0 and interval_total > 0:
                coverage_ratio = coverage_pct / 100.0
                adaptive_shift = (self.target_interval_coverage - coverage_ratio) * 0.20
                adaptive_quantile = float(np.clip(adaptive_quantile + adaptive_shift, 0.65, 0.98))

        if isinstance(backtest_metrics, dict):
            backtest_mae = float(backtest_metrics.get('mae', 0.0) or 0.0)
            backtest_rmse = float(backtest_metrics.get('rmse', 0.0) or 0.0)
            if backtest_mae > 0:
                candidates.append(backtest_mae * 1.1)
            if backtest_rmse > 0:
                candidates.append(backtest_rmse * 0.9)

        realized_errors = self._get_recent_realized_abs_errors(horizon, limit=80)
        if len(realized_errors) >= 8:
            candidates.append(float(np.quantile(realized_errors, adaptive_quantile)))

        margin = max(candidates)
        drift_multiplier = 1.0 + min(0.35, max(0.0, drift_score - 1.5) * 0.10)
        margin *= drift_multiplier

        if coverage_ratio is not None:
            gap = self.target_interval_coverage - coverage_ratio
            coverage_multiplier = 1.0 + float(np.clip(gap * self.interval_coverage_gain, -0.18, 0.35))
            margin *= coverage_multiplier

        return float(max(floor_margin, margin))

    def _compose_horizon_confidence(self, base_score, current_price, interval_obj, drift_score, backtest_metrics):
        """Build confidence from validation score, uncertainty width, drift, and realized backtest direction."""
        base_pct = float(np.clip(base_score * 100.0, self.confidence_floor, 95.0))
        direction_accuracy = 50.0
        if isinstance(backtest_metrics, dict):
            direction_accuracy = float(backtest_metrics.get('direction_accuracy', 50.0) or 50.0)

        direction_adjustment = (direction_accuracy - 50.0) * 0.55
        interval_width = float(interval_obj.get('upper', current_price) - interval_obj.get('lower', current_price))
        interval_ratio = interval_width / max(1e-9, float(current_price))
        uncertainty_penalty = min(45.0, max(0.0, interval_ratio) * 230.0)
        drift_penalty = min(25.0, max(0.0, drift_score - 1.2) * 10.0)

        confidence = base_pct + direction_adjustment - uncertainty_penalty - drift_penalty
        return float(np.clip(confidence, self.confidence_floor, 95.0))

    def _assess_horizon_quality(self, horizon, confidence_pct, drift_score, backtest_metrics):
        """Classify each horizon so the API can distinguish real-but-weak forecasts from qualified ones."""
        live_metrics = self.accuracy_metrics.get(horizon, {}) if isinstance(self.accuracy_metrics, dict) else {}
        live_samples = int(live_metrics.get('total_predictions', 0) or 0) if isinstance(live_metrics, dict) else 0
        live_direction_accuracy = float(live_metrics.get('direction_accuracy', 0.0) or 0.0) if isinstance(live_metrics, dict) else 0.0

        backtest_samples = int(backtest_metrics.get('samples', 0) or 0) if isinstance(backtest_metrics, dict) else 0
        backtest_direction_accuracy = float(backtest_metrics.get('direction_accuracy', 0.0) or 0.0) if isinstance(backtest_metrics, dict) else 0.0

        evaluation_source = 'none'
        observed_accuracy = None
        observed_samples = 0
        min_accuracy_threshold = self.min_backtest_direction_accuracy

        if live_samples >= self.min_live_quality_samples:
            evaluation_source = 'live'
            observed_accuracy = live_direction_accuracy
            observed_samples = live_samples
            min_accuracy_threshold = self.min_live_direction_accuracy
        elif backtest_samples >= self.min_backtest_samples:
            evaluation_source = 'backtest'
            observed_accuracy = backtest_direction_accuracy
            observed_samples = backtest_samples
            min_accuracy_threshold = self.min_backtest_direction_accuracy
        elif live_samples > 0:
            evaluation_source = 'live_sparse'
            observed_accuracy = live_direction_accuracy
            observed_samples = live_samples
            min_accuracy_threshold = self.min_live_direction_accuracy
        elif backtest_samples > 0:
            evaluation_source = 'backtest_sparse'
            observed_accuracy = backtest_direction_accuracy
            observed_samples = backtest_samples
            min_accuracy_threshold = self.min_backtest_direction_accuracy

        reasons = []
        if observed_accuracy is None:
            reasons.append('no_evaluation_evidence')
        elif observed_accuracy < min_accuracy_threshold:
            reasons.append('low_direction_accuracy')

        if evaluation_source.endswith('_sparse'):
            reasons.append('limited_samples')
        if float(confidence_pct or 0.0) < self.min_quality_confidence:
            reasons.append('low_confidence')
        if float(drift_score or 0.0) > self.max_quality_drift_score:
            reasons.append('high_feature_drift')

        if not reasons:
            status = 'qualified'
        elif all(reason in {'limited_samples', 'no_evaluation_evidence'} for reason in reasons):
            status = 'watch'
        else:
            status = 'unqualified'

        return {
            'status': status,
            'qualified': status == 'qualified',
            'evaluation_source': evaluation_source,
            'observed_direction_accuracy': observed_accuracy,
            'observed_samples': observed_samples,
            'min_required_accuracy': float(min_accuracy_threshold),
            'live_direction_accuracy': live_direction_accuracy,
            'live_samples': live_samples,
            'backtest_direction_accuracy': backtest_direction_accuracy,
            'backtest_samples': backtest_samples,
            'confidence': float(confidence_pct or 0.0),
            'drift_score': float(drift_score or 0.0),
            'reasons': reasons,
        }

    def _encode_target_value(self, reference_price, target_price, target_mode='price', baseline_return=0.0):
        """Map absolute future price into the model target space."""
        if target_mode in {'return', 'excess_return'}:
            ref = pd.to_numeric(reference_price, errors='coerce')
            target = pd.to_numeric(target_price, errors='coerce')
            if pd.isna(ref) or pd.isna(target) or float(ref) <= 0:
                return 0.0
            target_return = float((float(target) / float(ref)) - 1.0)
            if target_mode == 'excess_return':
                baseline = pd.to_numeric(baseline_return, errors='coerce')
                baseline_value = float(baseline) if not pd.isna(baseline) and np.isfinite(float(baseline)) else 0.0
                return float(target_return - baseline_value)
            return target_return
        target = pd.to_numeric(target_price, errors='coerce')
        return float(target) if not pd.isna(target) else 0.0

    def _decode_target_value(self, reference_price, target_value, target_mode='price', baseline_return=0.0):
        """Convert model outputs back into price space for evaluation and display."""
        predicted = pd.to_numeric(target_value, errors='coerce')
        if pd.isna(predicted):
            return 0.0
        if target_mode in {'return', 'excess_return'}:
            ref = pd.to_numeric(reference_price, errors='coerce')
            if pd.isna(ref) or float(ref) <= 0:
                return 0.0
            total_return = float(predicted)
            if target_mode == 'excess_return':
                baseline = pd.to_numeric(baseline_return, errors='coerce')
                baseline_value = float(baseline) if not pd.isna(baseline) and np.isfinite(float(baseline)) else 0.0
                total_return += baseline_value
            return float(float(ref) * (1.0 + total_return))
        return float(predicted)

    def _get_daily_feature_lookback(self, horizon):
        """Use a shorter context for 1D and a broader one for 1W."""
        if horizon == '1d':
            return max(30, int(getattr(self, 'daily_feature_lookback_bars_1d', self.daily_feature_lookback_bars)))
        if horizon == '1w':
            return max(30, int(getattr(self, 'daily_feature_lookback_bars_1w', self.daily_feature_lookback_bars)))
        return max(30, int(getattr(self, 'daily_feature_lookback_bars', 63)))

    def _build_training_signature(self, features_df, target_column, target_mode='price'):
        """Create a compact fingerprint for cache-safe model reuse."""
        tail_values = []
        if target_column in features_df.columns:
            tail_values = (
                features_df[target_column]
                .tail(25)
                .fillna(0)
                .round(6)
                .astype(float)
                .tolist()
            )
        payload = {
            'target': target_column,
            'target_mode': str(target_mode),
            'shape': features_df.shape,
            'columns': features_df.columns.tolist(),
            'last_index': str(features_df.index[-1]) if len(features_df.index) > 0 else 'none',
            'tail_target': tail_values,
        }
        payload_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha1(payload_str.encode('utf-8')).hexdigest()

    def _drop_horizon_noise_features(self, features_df, target_column, horizon):
        """Prune feature families that are structurally mismatched to a horizon."""
        if features_df is None or features_df.empty:
            return features_df

        feature_columns = [col for col in features_df.columns if col != target_column]
        drop_columns = set()

        if horizon == '1w':
            drop_columns.update({
                'open_gap_pct',
                'intraday_return',
                'candle_body_to_range',
                'close_location_value',
                'upper_wick_pct',
                'lower_wick_pct',
                'signed_volume_pressure',
                'gap_reversal_pressure',
                'range_to_atr14',
            })
        elif horizon == '1d':
            drop_columns.update({
                'hist_eia_stocks_level',
                'hist_eia_stocks_zscore_12w',
                'hist_eia_stock_draw_4w',
                'hist_fred_curve_slope',
                'hist_fred_fedfunds_level',
                'hist_fred_indpro_yoy',
                'hist_fred_umcsent_level',
                'hist_fred_umcsent_change_3m',
                'hist_macro_growth_vs_rates',
            })

        selected_drop_columns = [col for col in feature_columns if col in drop_columns]
        if not selected_drop_columns:
            return features_df
        return features_df.drop(columns=selected_drop_columns, errors='ignore')

    def _compute_backtest_metrics(self, y_true, y_pred, baseline):
        """Compute leakage-safe fold metrics for objective monitoring."""
        y_true_arr = np.asarray(y_true, dtype=float)
        y_pred_arr = np.asarray(y_pred, dtype=float)
        baseline_arr = np.asarray(baseline, dtype=float)

        if len(y_true_arr) == 0:
            return {
                'samples': 0,
                'mae': 0.0,
                'rmse': 0.0,
                'mape': 0.0,
                'direction_accuracy': 0.0,
            }

        abs_errors = np.abs(y_true_arr - y_pred_arr)
        safe_denominator = np.maximum(np.abs(y_true_arr), 1e-6)
        pred_direction = np.sign(y_pred_arr - baseline_arr)
        actual_direction = np.sign(y_true_arr - baseline_arr)

        return {
            'samples': int(len(y_true_arr)),
            'mae': float(np.mean(abs_errors)),
            'rmse': float(np.sqrt(np.mean((y_true_arr - y_pred_arr) ** 2))),
            'mape': float(np.mean(abs_errors / safe_denominator) * 100),
            'direction_accuracy': float(np.mean(pred_direction == actual_direction) * 100),
        }

    def _train_or_reuse_model_package(self, features_df, target_column, horizon, target_mode='price'):
        """Train a horizon package once and reuse it while source data fingerprint is unchanged."""
        features_df = self._drop_horizon_noise_features(features_df, target_column, horizon)
        signature = self._build_training_signature(features_df, target_column, target_mode)
        cached = self.model_cache.get(horizon)

        if cached and cached.get('signature') == signature:
            return cached['package'], True

        models, scores, scaler, selector, selected_features, all_feature_names, diagnostics = self.train_prediction_models(
            features_df,
            target_column,
            target_mode=target_mode,
        )
        package = {
            'models': models,
            'scores': scores,
            'scaler': scaler,
            'selector': selector,
            'selected_features': selected_features,
            'all_feature_names': all_feature_names,
            'diagnostics': diagnostics,
            'target_mode': target_mode,
        }
        self.model_cache[horizon] = {
            'signature': signature,
            'package': package,
            'updated_at': datetime.now().isoformat(),
        }
        return package, False

    def _apply_feature_defaults(self, feature_frame, feature_names):
        """Fill missing inference features with the same defaults used across the system."""
        for feature in feature_names:
            if feature in feature_frame.columns:
                continue
            feature_frame[feature] = self._default_feature_value(feature)
        return feature_frame

    def _default_feature_value(self, feature_name):
        """Centralize feature defaults so training, inference, and backtests stay aligned."""
        feature_name = str(feature_name or '')
        explicit_defaults = {
            'fred_dollar_strength': 100.0,
            'fred_economic_stability': 70.0,
            'news_market_buzz': 50.0,
            'news_bullish_ratio': 0.5,
            'hist_eia_stocks_level': 0.0,
            'hist_eia_stocks_change_1w': 0.0,
            'hist_eia_stocks_zscore_12w': 0.0,
            'hist_eia_stock_change_zscore_12w': 0.0,
            'hist_eia_stock_draw_4w': 0.0,
            'hist_fred_indpro_mom': 0.0,
            'hist_fred_indpro_yoy': 0.0,
            'hist_fred_curve_slope': 0.0,
            'hist_fred_curve_change_20d': 0.0,
            'hist_fred_fedfunds_level': 0.0,
            'hist_fred_fedfunds_change_3m': 0.0,
            'hist_fred_umcsent_level': 0.0,
            'hist_fred_umcsent_change_3m': 0.0,
            'hist_macro_growth_vs_rates': 0.0,
        }
        if feature_name in explicit_defaults:
            return float(explicit_defaults[feature_name])
        if 'dollar_strength' in feature_name:
            return 100.0
        if 'bullish_ratio' in feature_name:
            return 0.5
        if 'trend' in feature_name or 'momentum' in feature_name or 'divergence' in feature_name:
            return 0.0
        return 0.0

    def _safe_series(self, values, index=None, fill_value=0.0):
        """Convert raw values into a finite float series for feature engineering."""
        series = pd.Series(pd.to_numeric(values, errors='coerce'), index=index, dtype=float)
        if series.empty:
            return series
        series = series.replace([np.inf, -np.inf], np.nan)
        series = series.ffill().bfill()
        if series.isna().all():
            return series.fillna(float(fill_value))
        return series.fillna(float(fill_value))

    def _safe_pct_change_value(self, series, periods):
        """Latest percentage change over N bars."""
        clean = self._safe_series(series)
        if clean.empty or len(clean) <= periods:
            return 0.0
        base_value = float(clean.iloc[-periods - 1])
        latest_value = float(clean.iloc[-1])
        if not np.isfinite(base_value) or abs(base_value) < 1e-9 or not np.isfinite(latest_value):
            return 0.0
        return float((latest_value / base_value) - 1.0)

    def _safe_diff_value(self, series, periods=1):
        """Latest absolute difference over N bars."""
        clean = self._safe_series(series)
        if clean.empty or len(clean) <= periods:
            return 0.0
        return float(clean.iloc[-1] - clean.iloc[-periods - 1])

    def _safe_ratio(self, numerator, denominator, default=0.0):
        """Finite ratio helper used across normalized features."""
        num = pd.to_numeric(numerator, errors='coerce')
        den = pd.to_numeric(denominator, errors='coerce')
        if pd.isna(num) or pd.isna(den) or abs(float(den)) < 1e-9:
            return float(default)
        value = float(num) / float(den)
        if not np.isfinite(value):
            return float(default)
        return float(value)

    def _latest_rolling_zscore(self, series, window):
        """Latest z-score of a series against its own rolling history."""
        clean = self._safe_series(series)
        if clean.empty:
            return 0.0
        tail = clean.tail(max(3, int(window)))
        std = float(tail.std())
        if not np.isfinite(std) or std < 1e-9:
            return 0.0
        return float((tail.iloc[-1] - tail.mean()) / std)

    def _latest_trend_slope(self, series, window):
        """Normalized log-price slope over the trailing window."""
        clean = self._safe_series(series)
        tail = clean[clean > 0].tail(max(3, int(window)))
        if len(tail) < 3:
            return 0.0
        log_values = np.log(np.clip(tail.to_numpy(dtype=float), 1e-9, None))
        x_axis = np.arange(len(log_values), dtype=float)
        slope = np.polyfit(x_axis, log_values, 1)[0]
        return float(slope)

    def _latest_directional_hit_rate(self, series, window):
        """Share of positive returns in the trailing window."""
        clean = self._safe_series(series)
        returns = clean.pct_change().replace([np.inf, -np.inf], np.nan).dropna().tail(max(2, int(window)))
        if returns.empty:
            return 0.5
        return float((returns > 0).mean())

    def _latest_volatility(self, series, window):
        """Latest realized log-return volatility over N bars."""
        clean = self._safe_series(series)
        log_returns = np.log(clean.clip(lower=1e-9)).diff().replace([np.inf, -np.inf], np.nan).dropna()
        tail = log_returns.tail(max(2, int(window)))
        if tail.empty:
            return 0.0
        return float(tail.std())

    def _latest_atr_percent(self, highs, lows, closes, window):
        """Average true range normalized by the latest close."""
        high_series = self._safe_series(highs)
        low_series = self._safe_series(lows)
        close_series = self._safe_series(closes)
        if high_series.empty or low_series.empty or close_series.empty:
            return 0.0

        true_range = pd.concat(
            [
                high_series - low_series,
                (high_series - close_series.shift(1)).abs(),
                (low_series - close_series.shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = float(true_range.tail(max(2, int(window))).mean())
        latest_close = float(close_series.iloc[-1])
        if not np.isfinite(atr) or latest_close <= 0:
            return 0.0
        return float(atr / latest_close)

    def _latest_drawdown(self, series, window):
        """Distance from trailing peak, expressed as a negative percentage when below the peak."""
        clean = self._safe_series(series)
        if clean.empty:
            return 0.0
        tail = clean.tail(max(2, int(window)))
        rolling_peak = float(tail.max())
        if rolling_peak <= 0:
            return 0.0
        return float((tail.iloc[-1] / rolling_peak) - 1.0)

    def _latest_distance_from_low(self, series, window):
        """Distance from trailing low, useful for rebound vs breakdown context."""
        clean = self._safe_series(series)
        if clean.empty:
            return 0.0
        tail = clean.tail(max(2, int(window)))
        rolling_low = float(tail.min())
        if rolling_low <= 0:
            return 0.0
        return float((tail.iloc[-1] / rolling_low) - 1.0)

    def _latest_correlation(self, left_series, right_series, window):
        """Rolling correlation helper for cross-asset context."""
        left = self._safe_series(left_series)
        right = self._safe_series(right_series)
        if left.empty or right.empty:
            return 0.0
        aligned = pd.concat([left.pct_change(), right.pct_change()], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        tail = aligned.tail(max(3, int(window)))
        if len(tail) < 3:
            return 0.0
        corr = float(tail.iloc[:, 0].corr(tail.iloc[:, 1]))
        return corr if np.isfinite(corr) else 0.0

    def _latest_skewness(self, series, window):
        """Rolling skewness for return asymmetry signals."""
        clean = self._safe_series(series)
        returns = clean.pct_change().replace([np.inf, -np.inf], np.nan).dropna().tail(max(3, int(window)))
        if len(returns) < 3:
            return 0.0
        skew_value = float(returns.skew())
        return skew_value if np.isfinite(skew_value) else 0.0

    def _latest_close_location_value(self, opens, highs, lows, closes):
        """Bar close-location value: +1 near the high, -1 near the low."""
        open_series = self._safe_series(opens)
        high_series = self._safe_series(highs)
        low_series = self._safe_series(lows)
        close_series = self._safe_series(closes)
        if open_series.empty or high_series.empty or low_series.empty or close_series.empty:
            return 0.0
        latest_high = float(high_series.iloc[-1])
        latest_low = float(low_series.iloc[-1])
        latest_close = float(close_series.iloc[-1])
        bar_range = latest_high - latest_low
        if not np.isfinite(bar_range) or bar_range <= 1e-9:
            return 0.0
        return float(((latest_close - latest_low) - (latest_high - latest_close)) / bar_range)

    def _latest_obv_slope(self, closes, volumes, window):
        """Normalized OBV trend over the trailing window."""
        close_series = self._safe_series(closes)
        volume_series = self._safe_series(volumes)
        if close_series.empty or volume_series.empty:
            return 0.0
        direction = np.sign(close_series.diff().fillna(0.0))
        obv = (direction * volume_series).cumsum()
        obv = obv.replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)
        return self._latest_trend_slope(obv, window)

    def _compute_target_baseline_return(self, close_series, horizon):
        """Estimate horizon drift so the model learns residual return instead of raw drift."""
        clean = self._safe_series(close_series)
        if clean.empty or len(clean) < 2:
            return 0.0

        horizon_steps = 1 if horizon == '1d' else 5 if horizon == '1w' else 1
        returns = clean.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        if returns.empty:
            return 0.0

        short_tail = returns.tail(max(3, horizon_steps * 3))
        medium_tail = returns.tail(max(5, horizon_steps * 8))
        short_mean = float(short_tail.mean()) if not short_tail.empty else 0.0
        medium_mean = float(medium_tail.mean()) if not medium_tail.empty else short_mean
        recent_horizon_return = self._safe_pct_change_value(clean, horizon_steps)
        recent_per_bar_return = recent_horizon_return / max(1, horizon_steps)

        baseline_per_bar = (
            (0.50 * short_mean)
            + (0.30 * medium_mean)
            + (0.20 * recent_per_bar_return)
        )
        baseline_return = baseline_per_bar * horizon_steps
        if not np.isfinite(baseline_return):
            return 0.0
        return float(np.clip(baseline_return, -0.25, 0.25))
    
    def get_current_price(self):
        """Get real-time WTI price from yfinance"""
        try:
            self._refresh_contract_if_needed()
            return {
                'price': self.contract_info['current_price'],
                'volume': self.contract_info['volume'],
                'symbol': self.contract_info['symbol'],
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get current price: {e}")
            raise Exception(f"Cannot get real price data: {e}")

    def _get_market_symbol_candidates(self):
        """Prefer the active contract for history, then fall back to the continuous quote."""
        candidates = []
        for symbol in [self.history_symbol, self.yfinance_symbol]:
            if symbol and symbol not in candidates:
                candidates.append(symbol)
        return candidates
    
    def get_wti_historical_data(self, period=None, interval="1d"):
        """Get historical WTI data from yfinance"""
        try:
            period = period or self.daily_training_period
            now_ts = time.time()
            fetch_errors = []

            for symbol in self._get_market_symbol_candidates():
                cache_key = f"historical:{symbol}:{period}:{interval}"
                cached = self._market_data_mem_cache.get(cache_key)
                if cached and (now_ts - cached['fetched_at']) <= self.market_data_ttl_seconds:
                    cached_df = cached.get('data')
                    if cached_df is not None and not cached_df.empty:
                        self._record_market_source('daily_history', symbol, len(cached_df), from_cache=True)
                        logger.info(f"Using cached WTI historical data for {symbol} ({len(cached_df)} rows)")
                        return cached_df.copy()

                try:
                    ticker = _yf_ticker(symbol)
                    historical_data = ticker.history(period=period, interval=interval, timeout=15)
                    if historical_data.empty:
                        raise Exception(f"No historical data available for {symbol}")

                    self._market_data_mem_cache[cache_key] = {
                        'fetched_at': now_ts,
                        'data': historical_data.copy()
                    }
                    self._record_market_source('daily_history', symbol, len(historical_data), from_cache=False)
                    logger.info(f"Loaded {len(historical_data)} WTI data points from {symbol}")
                    return historical_data
                except Exception as symbol_error:
                    fetch_errors.append(f"{symbol}: {symbol_error}")

            raise Exception("; ".join(fetch_errors) if fetch_errors else "No WTI history sources available")
            
        except Exception as e:
            logger.error(f"Failed to get historical data: {e}")
            raise Exception(f"Cannot get historical data: {e}")

    def get_wti_hourly_data(self, period=None):
        """Get WTI hourly data from yfinance (last 730 days max)"""
        try:
            hourly_period = period or self.hourly_training_period
            now_ts = time.time()

            for symbol in self._get_market_symbol_candidates():
                cache_key = f"hourly:{symbol}:{hourly_period}:1h"
                cached = self._market_data_mem_cache.get(cache_key)
                if cached and (now_ts - cached['fetched_at']) <= self.market_data_ttl_seconds:
                    cached_df = cached.get('data')
                    if cached_df is not None and not cached_df.empty:
                        self._record_market_source('hourly_history', symbol, len(cached_df), from_cache=True)
                        logger.info(f"Using cached WTI hourly data for {symbol} ({len(cached_df)} rows)")
                        return cached_df.copy()

                try:
                    # yfinance allows 1h data for up to 730 days.
                    ticker = _yf_ticker(symbol)
                    hourly_data = ticker.history(period=hourly_period, interval="1h", timeout=15)
                    if hourly_data.empty:
                        logger.warning(f"No hourly data available for {symbol}")
                        continue

                    self._market_data_mem_cache[cache_key] = {
                        'fetched_at': now_ts,
                        'data': hourly_data.copy()
                    }
                    self._record_market_source('hourly_history', symbol, len(hourly_data), from_cache=False)
                    logger.info(f"Loaded {len(hourly_data)} WTI hourly data points from {symbol}")
                    return hourly_data
                except Exception as symbol_error:
                    logger.warning(f"Failed to get hourly data from {symbol}: {symbol_error}")

            logger.warning("No hourly WTI data available from active or continuous symbols")
            return None
            
        except Exception as e:
            logger.warning(f"Failed to get hourly data: {e}")
            return None

    def _date_feature_key(self, timestamp_value):
        """Normalize timestamps to date-only keys for cross-asset joins."""
        ts = pd.Timestamp(timestamp_value)
        try:
            if ts.tzinfo is not None:
                ts = ts.tz_convert(None)
        except Exception:
            pass
        try:
            ts = ts.tz_localize(None)
        except Exception:
            pass
        return ts.normalize()

    def _historical_external_feature_defaults(self):
        """Defaults for historically aligned exogenous features."""
        defaults = {
            'hist_eia_stocks_level': 0.0,
            'hist_eia_stocks_change_1w': 0.0,
            'hist_eia_stocks_zscore_12w': 0.0,
            'hist_eia_stock_change_zscore_12w': 0.0,
            'hist_eia_stock_draw_4w': 0.0,
            'hist_fred_indpro_mom': 0.0,
            'hist_fred_indpro_yoy': 0.0,
            'hist_fred_curve_slope': 0.0,
            'hist_fred_curve_change_20d': 0.0,
            'hist_fred_fedfunds_level': 0.0,
            'hist_fred_fedfunds_change_3m': 0.0,
            'hist_fred_umcsent_level': 0.0,
            'hist_fred_umcsent_change_3m': 0.0,
            'hist_macro_growth_vs_rates': 0.0,
        }
        return {name: float(value) for name, value in defaults.items()}

    def _empty_historical_external_feature_map(self, index_values):
        """Return a zero-filled exogenous map keyed to the provided timestamps."""
        defaults = self._historical_external_feature_defaults()
        return {
            self._date_feature_key(ts): defaults.copy()
            for ts in pd.Index(index_values)
        }

    def _fetch_eia_weekly_stocks_series(self):
        """Fetch the historical weekly U.S. crude stocks series from EIA."""
        cache_key = 'eia:weekly_crude_stocks'
        cached = self._historical_external_mem_cache.get(cache_key)
        if cached is not None:
            return cached.copy() if hasattr(cached, 'copy') else cached

        if not self.config.EIA_API_KEY:
            logger.info("Historical EIA feature fetch skipped: EIA_API_KEY unavailable")
            self._historical_external_mem_cache[cache_key] = None
            return None

        try:
            url = f"{self.config.EIA_BASE_URL}/petroleum/stoc/wstk/data/"
            params = {
                'frequency': 'weekly',
                'data[0]': 'value',
                'facets[product][]': 'EPC0',
                'facets[duoarea][]': 'NUS',
                'facets[process][]': 'SAE',
                'sort[0][column]': 'period',
                'sort[0][direction]': 'asc',
                'offset': 0,
                'length': 5000,
                'api_key': self.config.EIA_API_KEY,
            }
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            rows = response.json().get('response', {}).get('data', [])
            if not rows:
                self._historical_external_mem_cache[cache_key] = None
                return None

            periods = []
            values = []
            for row in rows:
                period = row.get('period')
                value = row.get('value')
                if not period or value in (None, '', '.'):
                    continue
                try:
                    periods.append(pd.Timestamp(period))
                    values.append(float(value))
                except Exception:
                    continue

            if not periods:
                self._historical_external_mem_cache[cache_key] = None
                return None

            series = pd.Series(values, index=pd.Index(periods, name='period'), dtype=float)
            series = series[~series.index.duplicated(keep='last')].sort_index()
            self._historical_external_mem_cache[cache_key] = series
            return series.copy()
        except Exception as e:
            logger.warning(f"Historical EIA series fetch failed: {e}")
            self._historical_external_mem_cache[cache_key] = None
            return None

    def _fetch_fred_csv_series(self, series_id, start_date=None, end_date=None):
        """Fetch a historical FRED series via the public CSV graph endpoint."""
        start_date = str(start_date or '1990-01-01')
        end_date = str(end_date or datetime.now().strftime('%Y-%m-%d'))
        cache_key = f'fred:{series_id}:{start_date}:{end_date}'
        cached = self._historical_external_mem_cache.get(cache_key)
        if cached is not None:
            return cached.copy() if hasattr(cached, 'copy') else cached

        try:
            url = f"{self.config.FRED_BASE_URL}?id={series_id}&cosd={start_date}&coed={end_date}&fq=Daily&fam=avg&fgst=lin&line_index=1&transformation=lin&vintage_date={end_date}&revision_date={end_date}&nd=1970-01-01&ost=-99999&oet=99999&mma=0&fml=a&fmt=csv"
            # FRED's graph endpoint is occasionally slow; retry with backoff so a single slow
            # response doesn't silently disable an entire macro feature family for the whole run.
            response = None
            last_error = None
            for attempt in range(3):
                try:
                    response = requests.get(url, timeout=(10, 45))
                    response.raise_for_status()
                    break
                except requests.exceptions.RequestException as req_err:
                    last_error = req_err
                    response = None
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
            if response is None:
                raise last_error if last_error is not None else RuntimeError('FRED fetch failed')
            frame = pd.read_csv(StringIO(response.text))
            if frame.empty or len(frame.columns) < 2:
                self._historical_external_mem_cache[cache_key] = None
                return None

            date_col = frame.columns[0]
            value_col = frame.columns[1]
            frame[date_col] = pd.to_datetime(frame[date_col], errors='coerce')
            frame[value_col] = pd.to_numeric(frame[value_col], errors='coerce')
            frame = frame.dropna(subset=[date_col, value_col])
            if frame.empty:
                self._historical_external_mem_cache[cache_key] = None
                return None

            series = pd.Series(frame[value_col].astype(float).values, index=pd.Index(frame[date_col], name='date'))
            series = series[~series.index.duplicated(keep='last')].sort_index()
            self._historical_external_mem_cache[cache_key] = series
            return series.copy()
        except Exception as e:
            logger.warning(f"Historical FRED series fetch failed for {series_id}: {e}")
            self._historical_external_mem_cache[cache_key] = None
            return None

    def _align_released_series_to_index(self, index_values, series, lag_days):
        """Align a lower-frequency series to market dates using conservative publication lags."""
        target_index = pd.Index(index_values)
        target_keys = pd.Index([self._date_feature_key(ts) for ts in target_index])
        if series is None or len(series) == 0:
            return pd.Series(np.nan, index=target_keys, dtype=float)

        normalized_index = pd.Index([self._date_feature_key(ts) for ts in series.index])
        normalized = pd.Series(pd.to_numeric(series.values, errors='coerce'), index=normalized_index, dtype=float)
        normalized = normalized[~normalized.index.duplicated(keep='last')].sort_index().dropna()
        if normalized.empty:
            return pd.Series(np.nan, index=target_keys, dtype=float)

        # pd.merge_asof requires both keys to share the SAME datetime resolution. Under pandas 2.x,
        # FRED CSV dates and the yfinance index can parse at different units (e.g. [us] vs [s]),
        # which raises "incompatible merge keys" and silently drops every FRED/EIA feature to its
        # zero default. Coerce both sides to nanosecond resolution before the join to prevent this.
        available_date = pd.to_datetime(
            normalized.index + pd.to_timedelta(int(lag_days), unit='D')
        ).astype('datetime64[ns]')
        left_date_key = pd.to_datetime(pd.Index(target_keys)).astype('datetime64[ns]')

        available = pd.DataFrame(
            {'available_date': available_date, 'value': normalized.values}
        ).sort_values('available_date')
        left = pd.DataFrame({'date_key': left_date_key, '_order': np.arange(len(target_keys))})
        merged = pd.merge_asof(
            left.sort_values('date_key'),
            available,
            left_on='date_key',
            right_on='available_date',
            direction='backward',
        ).sort_values('_order')
        return pd.Series(pd.to_numeric(merged['value'], errors='coerce').values, index=target_keys, dtype=float)

    def build_historical_external_feature_map(self, wti_data):
        """Build a leak-safe historical exogenous feature map for model training and inference."""
        if wti_data is None or len(wti_data) < 10:
            return {}

        default_map = self._empty_historical_external_feature_map(wti_data.index)
        if not getattr(self, 'use_historical_external_features_in_training', True):
            return default_map

        start_key = self._date_feature_key(wti_data.index[0]).strftime('%Y-%m-%d')
        end_key = self._date_feature_key(wti_data.index[-1]).strftime('%Y-%m-%d')
        cache_key = f'historical_external:{start_key}:{end_key}:{len(wti_data)}'
        cached = self._historical_external_mem_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

        feature_frame = pd.DataFrame(index=pd.Index([self._date_feature_key(ts) for ts in wti_data.index]))
        defaults = self._historical_external_feature_defaults()
        for feature_name, default_value in defaults.items():
            feature_frame[feature_name] = float(default_value)

        try:
            eia_stocks = self._fetch_eia_weekly_stocks_series()
            if eia_stocks is not None and len(eia_stocks) >= 2:
                eia_level = eia_stocks.astype(float)
                eia_change_1w = eia_level.diff(1)
                eia_level_std = eia_level.rolling(12).std().replace(0, np.nan)
                eia_change_std = eia_change_1w.rolling(12).std().replace(0, np.nan)
                eia_level_z = (eia_level - eia_level.rolling(12).mean()) / eia_level_std
                eia_change_z = (eia_change_1w - eia_change_1w.rolling(12).mean()) / eia_change_std
                eia_draw_4w = -(eia_level.diff(4))

                feature_frame['hist_eia_stocks_level'] = self._align_released_series_to_index(
                    wti_data.index, eia_level, self.eia_release_lag_days
                ).values
                feature_frame['hist_eia_stocks_change_1w'] = self._align_released_series_to_index(
                    wti_data.index, eia_change_1w, self.eia_release_lag_days
                ).values
                feature_frame['hist_eia_stocks_zscore_12w'] = self._align_released_series_to_index(
                    wti_data.index, eia_level_z, self.eia_release_lag_days
                ).values
                feature_frame['hist_eia_stock_change_zscore_12w'] = self._align_released_series_to_index(
                    wti_data.index, eia_change_z, self.eia_release_lag_days
                ).values
                feature_frame['hist_eia_stock_draw_4w'] = self._align_released_series_to_index(
                    wti_data.index, eia_draw_4w, self.eia_release_lag_days
                ).values
        except Exception as e:
            logger.warning(f"Historical EIA feature assembly failed: {e}")

        fred_start = (pd.Timestamp(wti_data.index[0]) - pd.Timedelta(days=600)).strftime('%Y-%m-%d')
        fred_end = (pd.Timestamp(wti_data.index[-1]) + pd.Timedelta(days=7)).strftime('%Y-%m-%d')
        fred_specs = {
            'INDPRO': ('monthly', {
                'hist_fred_indpro_mom': lambda s: s.pct_change(1),
                'hist_fred_indpro_yoy': lambda s: s.pct_change(12),
            }),
            'FEDFUNDS': ('monthly', {
                'hist_fred_fedfunds_level': lambda s: s,
                'hist_fred_fedfunds_change_3m': lambda s: s.diff(3),
            }),
            'UMCSENT': ('monthly', {
                'hist_fred_umcsent_level': lambda s: s,
                'hist_fred_umcsent_change_3m': lambda s: s.diff(3),
            }),
            'T10Y2Y': ('daily', {
                'hist_fred_curve_slope': lambda s: s,
                'hist_fred_curve_change_20d': lambda s: s.diff(20),
            }),
        }

        for series_id, (frequency, transforms) in fred_specs.items():
            try:
                series = self._fetch_fred_csv_series(series_id, start_date=fred_start, end_date=fred_end)
                if series is None or len(series) < 2:
                    continue
                lag_days = self.fred_monthly_release_lag_days if frequency == 'monthly' else self.fred_daily_release_lag_days
                for feature_name, transform in transforms.items():
                    transformed = transform(series.astype(float))
                    feature_frame[feature_name] = self._align_released_series_to_index(
                        wti_data.index, transformed, lag_days
                    ).values
            except Exception as e:
                logger.warning(f"Historical FRED feature assembly failed for {series_id}: {e}")

        feature_frame['hist_macro_growth_vs_rates'] = (
            feature_frame['hist_fred_indpro_yoy'].fillna(0.0)
            - feature_frame['hist_fred_fedfunds_level'].fillna(0.0) * 0.01
            + feature_frame['hist_fred_curve_slope'].fillna(0.0) * 0.1
        )

        for feature_name, default_value in defaults.items():
            feature_frame[feature_name] = (
                pd.to_numeric(feature_frame[feature_name], errors='coerce')
                .replace([np.inf, -np.inf], np.nan)
                .ffill()
                .fillna(float(default_value))
                .astype(float)
            )

        feature_map = {
            row_key: {name: float(row[name]) for name in defaults.keys()}
            for row_key, row in feature_frame[list(defaults.keys())].iterrows()
        }
        self._historical_external_mem_cache[cache_key] = copy.deepcopy(feature_map)
        return feature_map

    def _get_next_wti_contract_symbol(self):
        """Infer the next-month WTI contract symbol from the active contract code."""
        try:
            current = str(self.contract_symbol)
            if len(current) < 5 or not current.startswith('CL'):
                return None

            month_lookup = {code: month for month, code in MONTH_CODES.items()}
            month_code = current[2]
            year_suffix = int(current[3:5])
            month_value = month_lookup.get(month_code)
            if month_value is None:
                return None

            year_value = 2000 + year_suffix
            next_month = month_value + 1
            next_year = year_value
            if next_month > 12:
                next_month = 1
                next_year += 1

            return f"CL{MONTH_CODES[next_month]}{str(next_year)[-2:]}"
        except Exception:
            return None

    def _fetch_market_series(self, symbol, period='2y', interval='1d'):
        """Fetch and cache close-price series for contextual cross-asset features."""
        cache_key = f"series:{symbol}:{period}:{interval}"
        now_ts = time.time()
        cached = self._market_data_mem_cache.get(cache_key)
        if cached and (now_ts - cached.get('fetched_at', 0.0)) <= self.market_data_ttl_seconds:
            cached_series = cached.get('data')
            if cached_series is not None and len(cached_series) > 0:
                return cached_series.copy()

        try:
            ticker = _yf_ticker(symbol)
            data = ticker.history(period=period, interval=interval, timeout=12)
            if data is None or data.empty or 'Close' not in data.columns:
                return None

            close_series = pd.to_numeric(data['Close'], errors='coerce')
            close_series = close_series[~close_series.index.duplicated(keep='last')].sort_index()

            self._market_data_mem_cache[cache_key] = {
                'fetched_at': now_ts,
                'data': close_series.copy(),
            }
            return close_series
        except Exception as e:
            logger.warning(f"Market context fetch failed for {symbol}: {e}")
            return None

    def build_market_context_feature_map(self, wti_data):
        """Build date-keyed cross-asset and term-structure features aligned to WTI history."""
        if wti_data is None or len(wti_data) < 10 or 'Close' not in wti_data.columns:
            return {}

        date_index = pd.Index([self._date_feature_key(ts) for ts in wti_data.index])
        feature_frame = pd.DataFrame(index=date_index)
        feature_frame['wti_close'] = pd.to_numeric(wti_data['Close'], errors='coerce').values
        feature_frame['wti_close'] = pd.to_numeric(feature_frame['wti_close'], errors='coerce').ffill().bfill()

        context_symbols = {
            'brent_close': 'BZ=F',
            'dxy_close': 'DX-Y.NYB',
            'vix_close': '^VIX',
            'ovx_close': '^OVX',
            'tnx_close': '^TNX',
            'xle_close': 'XLE',
            'xop_close': 'XOP',
            'spy_close': 'SPY',
        }

        for col_name, symbol in context_symbols.items():
            series = self._fetch_market_series(symbol, period=self.market_context_period, interval='1d')
            if series is None or len(series) == 0:
                feature_frame[col_name] = np.nan
                continue

            normalized_index = pd.Index([self._date_feature_key(ts) for ts in series.index])
            normalized_series = pd.Series(series.values, index=normalized_index)
            normalized_series = normalized_series[~normalized_series.index.duplicated(keep='last')].sort_index()
            feature_frame[col_name] = normalized_series.reindex(feature_frame.index).ffill()

        next_contract = self._get_next_wti_contract_symbol()
        if next_contract:
            next_series = self._fetch_market_series(next_contract, period=self.market_context_period, interval='1d')
            if next_series is not None and len(next_series) > 0:
                next_index = pd.Index([self._date_feature_key(ts) for ts in next_series.index])
                next_series_norm = pd.Series(next_series.values, index=next_index)
                next_series_norm = next_series_norm[~next_series_norm.index.duplicated(keep='last')].sort_index()
                feature_frame['next_contract_close'] = next_series_norm.reindex(feature_frame.index).ffill()
            else:
                feature_frame['next_contract_close'] = np.nan
        else:
            feature_frame['next_contract_close'] = np.nan

        for column_name in [
            'brent_close', 'dxy_close', 'vix_close', 'ovx_close', 'tnx_close',
            'xle_close', 'xop_close', 'spy_close', 'next_contract_close'
        ]:
            feature_frame[column_name] = pd.to_numeric(feature_frame[column_name], errors='coerce').ffill()

        feature_frame['wti_return_1d'] = feature_frame['wti_close'].pct_change(1)
        feature_frame['wti_return_5d'] = feature_frame['wti_close'].pct_change(5)
        feature_frame['wti_return_20d'] = feature_frame['wti_close'].pct_change(20)
        feature_frame['wti_return_60d'] = feature_frame['wti_close'].pct_change(60)
        feature_frame['brent_return_1d'] = feature_frame['brent_close'].pct_change(1)
        feature_frame['brent_return_5d'] = feature_frame['brent_close'].pct_change(5)
        feature_frame['brent_return_20d'] = feature_frame['brent_close'].pct_change(20)
        feature_frame['xle_return_1d'] = feature_frame['xle_close'].pct_change(1)
        feature_frame['xop_return_1d'] = feature_frame['xop_close'].pct_change(1)
        feature_frame['spy_return_1d'] = feature_frame['spy_close'].pct_change(1)
        feature_frame['brent_wti_spread'] = feature_frame['brent_close'] - feature_frame['wti_close']
        feature_frame['brent_wti_ratio'] = feature_frame['brent_close'] / feature_frame['wti_close'].replace(0, np.nan)
        feature_frame['wti_vs_brent_gap_5d'] = feature_frame['wti_return_5d'] - feature_frame['brent_return_5d']
        feature_frame['brent_wti_spread_change_5d'] = feature_frame['brent_wti_spread'].diff(5)
        feature_frame['brent_wti_spread_zscore_20d'] = (
            feature_frame['brent_wti_spread'] - feature_frame['brent_wti_spread'].rolling(20).mean()
        ) / feature_frame['brent_wti_spread'].rolling(20).std().replace(0, np.nan)
        feature_frame['dxy_return_1d'] = feature_frame['dxy_close'].pct_change(1)
        feature_frame['dxy_return_5d'] = feature_frame['dxy_close'].pct_change(5)
        feature_frame['dxy_return_20d'] = feature_frame['dxy_close'].pct_change(20)
        feature_frame['dxy_level'] = feature_frame['dxy_close']
        feature_frame['dxy_level_zscore_20d'] = (
            feature_frame['dxy_close'] - feature_frame['dxy_close'].rolling(20).mean()
        ) / feature_frame['dxy_close'].rolling(20).std().replace(0, np.nan)
        feature_frame['vix_level'] = feature_frame['vix_close']
        feature_frame['vix_return_1d'] = feature_frame['vix_close'].pct_change(1)
        feature_frame['vix_return_5d'] = feature_frame['vix_close'].pct_change(5)
        feature_frame['vix_zscore_20d'] = (
            feature_frame['vix_close'] - feature_frame['vix_close'].rolling(20).mean()
        ) / feature_frame['vix_close'].rolling(20).std().replace(0, np.nan)
        feature_frame['ovx_level'] = feature_frame['ovx_close']
        feature_frame['ovx_return_1d'] = feature_frame['ovx_close'].pct_change(1)
        feature_frame['ovx_return_5d'] = feature_frame['ovx_close'].pct_change(5)
        feature_frame['ovx_zscore_20d'] = (
            feature_frame['ovx_close'] - feature_frame['ovx_close'].rolling(20).mean()
        ) / feature_frame['ovx_close'].rolling(20).std().replace(0, np.nan)
        feature_frame['ovx_vix_spread'] = feature_frame['ovx_close'] - feature_frame['vix_close']
        feature_frame['ovx_vix_ratio'] = feature_frame['ovx_close'] / feature_frame['vix_close'].replace(0, np.nan)
        ovx_vix_ratio_std = feature_frame['ovx_vix_ratio'].rolling(20).std().replace(0, np.nan)
        feature_frame['ovx_vix_ratio_zscore_20d'] = (
            feature_frame['ovx_vix_ratio'] - feature_frame['ovx_vix_ratio'].rolling(20).mean()
        ) / ovx_vix_ratio_std
        feature_frame['tnx_level'] = feature_frame['tnx_close']
        feature_frame['tnx_change_1d'] = feature_frame['tnx_close'].diff(1)
        feature_frame['tnx_change_5d'] = feature_frame['tnx_close'].diff(5)
        feature_frame['tnx_level_zscore_20d'] = (
            feature_frame['tnx_close'] - feature_frame['tnx_close'].rolling(20).mean()
        ) / feature_frame['tnx_close'].rolling(20).std().replace(0, np.nan)
        feature_frame['xle_return_5d'] = feature_frame['xle_close'].pct_change(5)
        feature_frame['xle_return_20d'] = feature_frame['xle_close'].pct_change(20)
        feature_frame['xop_return_5d'] = feature_frame['xop_close'].pct_change(5)
        feature_frame['xop_return_20d'] = feature_frame['xop_close'].pct_change(20)
        feature_frame['spy_return_5d'] = feature_frame['spy_close'].pct_change(5)
        feature_frame['spy_return_20d'] = feature_frame['spy_close'].pct_change(20)
        feature_frame['wti_xle_corr_20d'] = feature_frame['wti_return_1d'].rolling(20).corr(feature_frame['xle_return_1d'])
        feature_frame['wti_xop_corr_20d'] = feature_frame['wti_return_1d'].rolling(20).corr(feature_frame['xop_close'].pct_change(1))
        feature_frame['wti_spy_corr_20d'] = feature_frame['wti_return_1d'].rolling(20).corr(feature_frame['spy_close'].pct_change(1))
        feature_frame['wti_dxy_corr_20d'] = feature_frame['wti_return_1d'].rolling(20).corr(feature_frame['dxy_close'].pct_change(1))
        feature_frame['wti_brent_corr_20d'] = feature_frame['wti_return_1d'].rolling(20).corr(feature_frame['brent_close'].pct_change(1))
        feature_frame['wti_ovx_corr_20d'] = feature_frame['wti_return_1d'].rolling(20).corr(feature_frame['ovx_close'].pct_change(1))
        feature_frame['term_spread_front_next'] = feature_frame['wti_close'] - feature_frame['next_contract_close']
        feature_frame['term_spread_pct'] = feature_frame['term_spread_front_next'] / feature_frame['wti_close'].replace(0, np.nan)
        feature_frame['is_contango'] = (feature_frame['term_spread_front_next'] < 0).astype(float)
        feature_frame['term_spread_change_1d'] = feature_frame['term_spread_front_next'].diff(1)
        feature_frame['term_spread_change_5d'] = feature_frame['term_spread_front_next'].diff(5)
        spread_std = feature_frame['term_spread_front_next'].rolling(20).std().replace(0, np.nan)
        feature_frame['term_spread_zscore_20d'] = (
            feature_frame['term_spread_front_next'] - feature_frame['term_spread_front_next'].rolling(20).mean()
        ) / spread_std
        feature_frame['roll_yield_5d_ann'] = (
            (feature_frame['wti_close'] / feature_frame['next_contract_close']) - 1.0
        ) * (252.0 / 5.0)
        feature_frame['energy_equity_relative_5d'] = feature_frame['xle_return_5d'] - feature_frame['spy_return_5d']
        feature_frame['energy_equity_relative_20d'] = feature_frame['xle_return_20d'] - feature_frame['spy_return_20d']
        feature_frame['exploration_relative_5d'] = feature_frame['xop_return_5d'] - feature_frame['spy_return_5d']
        feature_frame['exploration_relative_20d'] = feature_frame['xop_return_20d'] - feature_frame['spy_return_20d']
        feature_frame['wti_vs_brent_gap_20d'] = feature_frame['wti_return_20d'] - feature_frame['brent_return_20d']
        feature_frame['wti_vs_xle_gap_20d'] = feature_frame['wti_return_20d'] - feature_frame['xle_return_20d']
        feature_frame['wti_vs_spy_gap_20d'] = feature_frame['wti_return_20d'] - feature_frame['spy_return_20d']
        feature_frame['risk_off_pressure_1d'] = (
            feature_frame['dxy_return_1d'].fillna(0.0)
            + feature_frame['vix_return_1d'].fillna(0.0)
            + feature_frame['ovx_return_1d'].fillna(0.0)
            - feature_frame['xle_return_1d'].fillna(0.0)
            - feature_frame['spy_return_1d'].fillna(0.0)
        )
        feature_frame['risk_off_pressure_5d'] = (
            feature_frame['dxy_return_5d'].fillna(0.0)
            + feature_frame['vix_return_5d'].fillna(0.0)
            + feature_frame['ovx_return_5d'].fillna(0.0)
            - feature_frame['xle_return_5d'].fillna(0.0)
            - feature_frame['spy_return_5d'].fillna(0.0)
        )
        feature_frame['macro_stress_score'] = (
            feature_frame['dxy_level_zscore_20d'].fillna(0.0)
            + feature_frame['vix_zscore_20d'].fillna(0.0)
            + feature_frame['ovx_zscore_20d'].fillna(0.0)
            + feature_frame['tnx_level_zscore_20d'].fillna(0.0)
            - feature_frame['energy_equity_relative_20d'].fillna(0.0)
        )

        feature_defaults = {
            'wti_return_5d': 0.0,
            'wti_return_20d': 0.0,
            'wti_return_60d': 0.0,
            'brent_return_1d': 0.0,
            'brent_return_5d': 0.0,
            'brent_return_20d': 0.0,
            'xop_return_1d': 0.0,
            'spy_return_1d': 0.0,
            'brent_wti_spread': 0.0,
            'brent_wti_ratio': 1.0,
            'wti_vs_brent_gap_5d': 0.0,
            'brent_wti_spread_change_5d': 0.0,
            'brent_wti_spread_zscore_20d': 0.0,
            'dxy_return_1d': 0.0,
            'dxy_return_5d': 0.0,
            'dxy_return_20d': 0.0,
            'dxy_level': 100.0,
            'dxy_level_zscore_20d': 0.0,
            'vix_level': 20.0,
            'vix_return_1d': 0.0,
            'vix_return_5d': 0.0,
            'vix_zscore_20d': 0.0,
            'ovx_level': 35.0,
            'ovx_return_1d': 0.0,
            'ovx_return_5d': 0.0,
            'ovx_zscore_20d': 0.0,
            'ovx_vix_spread': 0.0,
            'ovx_vix_ratio': 1.0,
            'ovx_vix_ratio_zscore_20d': 0.0,
            'tnx_level': 4.0,
            'tnx_change_1d': 0.0,
            'tnx_change_5d': 0.0,
            'tnx_level_zscore_20d': 0.0,
            'xle_return_5d': 0.0,
            'xle_return_20d': 0.0,
            'xop_return_5d': 0.0,
            'xop_return_20d': 0.0,
            'spy_return_5d': 0.0,
            'spy_return_20d': 0.0,
            'wti_xle_corr_20d': 0.0,
            'wti_xop_corr_20d': 0.0,
            'wti_spy_corr_20d': 0.0,
            'wti_dxy_corr_20d': 0.0,
            'wti_brent_corr_20d': 0.0,
            'wti_ovx_corr_20d': 0.0,
            'term_spread_front_next': 0.0,
            'term_spread_pct': 0.0,
            'term_spread_change_1d': 0.0,
            'term_spread_change_5d': 0.0,
            'term_spread_zscore_20d': 0.0,
            'roll_yield_5d_ann': 0.0,
            'is_contango': 0.0,
            'energy_equity_relative_5d': 0.0,
            'energy_equity_relative_20d': 0.0,
            'exploration_relative_5d': 0.0,
            'exploration_relative_20d': 0.0,
            'wti_vs_brent_gap_20d': 0.0,
            'wti_vs_xle_gap_20d': 0.0,
            'wti_vs_spy_gap_20d': 0.0,
            'risk_off_pressure_1d': 0.0,
            'risk_off_pressure_5d': 0.0,
            'macro_stress_score': 0.0,
        }

        feature_columns = list(feature_defaults.keys())
        for col_name in feature_columns:
            cleaned = pd.to_numeric(feature_frame[col_name], errors='coerce').replace([np.inf, -np.inf], np.nan)
            feature_frame[col_name] = cleaned.ffill().fillna(feature_defaults[col_name]).astype(float)

        feature_map = {}
        for row_key, row in feature_frame[feature_columns].iterrows():
            feature_map[row_key] = {name: float(row[name]) for name in feature_columns}

        return feature_map
    
    def get_external_data_sources(self):
        """Get all external data sources for premium predictions"""
        now_ts = time.time()
        cached_external = self._external_data_mem_cache.get('data')
        cached_at = self._external_data_mem_cache.get('fetched_at', 0.0)
        if cached_external and (now_ts - cached_at) <= self.external_data_ttl_seconds:
            logger.info("Using cached external data sources")
            return copy.deepcopy(cached_external)

        source_fetchers = {
            'eia': self.get_eia_oil_data,
            'fred': self.get_fred_economic_data,
            'alpha_vantage': self.get_alpha_vantage_data,
            'finnhub': self.get_finnhub_market_data,
            'news': self.get_news_sentiment,
            'usda': self.get_usda_agricultural_data,
            'noaa': self.get_noaa_weather_data,
            'geopolitical': self.get_geopolitical_risk,
        }

        external_data = {}
        with ThreadPoolExecutor(max_workers=self.external_fetch_workers) as executor:
            future_map = {
                executor.submit(fetcher): name for name, fetcher in source_fetchers.items()
            }
            for future in as_completed(future_map):
                source_name = future_map[future]
                try:
                    external_data[source_name] = future.result()
                except Exception as e:
                    logger.warning(f"External source {source_name} failed: {e}")
                    external_data[source_name] = {
                        'data_quality': 0,
                        'source': f'{source_name}_exception',
                        'error': str(e),
                        'timestamp': datetime.now().isoformat()
                    }
        
        # Cache external data
        try:
            self._atomic_write_json(self.external_data_cache, external_data)
        except Exception as e:
            logger.warning(f"Could not cache external data: {e}")

        self._external_data_mem_cache = {
            'fetched_at': now_ts,
            'data': external_data
        }
        
        return external_data
    
    def get_eia_oil_data(self):
        """Fetch EIA oil supply/demand data"""
        logger.info("Fetching EIA oil supply/demand data...")
        
        # Check if EIA API key is configured (FIX #1: was hardcoded before)
        if not self.config.EIA_API_KEY:
            logger.warning("⚠️ EIA_API_KEY not configured - skipping EIA source")
            return self._missing_key_source_payload('eia', 'EIA_API_KEY')
        
        # EIA API is sometimes slow; add retry logic
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                # EIA Crude Oil Weekly Stocks (weekly frequency supported on stoc/wstk)
                url = f"{self.config.EIA_BASE_URL}/petroleum/stoc/wstk/data/"
                params = {
                    'frequency': 'weekly',
                    'data[0]': 'value',
                    'facets[product][]': 'EPC0',
                    'facets[duoarea][]': 'NUS',
                    'facets[process][]': 'SAE',  # Ending Stocks
                    'sort[0][column]': 'period',
                    'sort[0][direction]': 'desc',
                    'offset': 0,
                    'length': 10,  # Reduced from 20 to speed up query (we only need 5)
                    'api_key': self.config.EIA_API_KEY  # FIX #1: Use config, not hardcoded
                }
                
                # Increased timeout to 15s to handle sluggish EIA v2 API responses
                response = requests.get(url, params=params, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    supply_data = data.get('response', {}).get('data', [])
                    
                    if supply_data:
                        latest_supply = float(supply_data[0]['value'])
                        # Reverse to oldest-first for correct trend slope (API returns newest-first)
                        trend_values = [float(item['value']) for item in supply_data[:5] if item.get('value')][::-1]
                        logger.info(f"✅ EIA: Latest crude oil stocks {latest_supply:,.0f} thousand barrels")
                        return {
                            'data_quality': 100,
                            'supply_level': latest_supply,
                            'supply_trend': self._calculate_trend(trend_values) if len(trend_values) >= 2 else 0,
                            'source': 'EIA_API',
                            'timestamp': datetime.now().isoformat()
                        }
                
                # If we get here but it's not the last attempt, wait and retry
                if attempt < max_retries:
                    logger.warning(f"EIA API attempt {attempt+1} failed (status {response.status_code}), retrying...")
                    time.sleep(2 ** attempt)  # Exponential backoff (1s, 2s)
                    continue
                
                # NO FALLBACK - Return error state after all retries
                logger.warning("⚠️ EIA API unavailable")
                return {
                    'error': 'EIA API unavailable',
                    'source': 'EIA_failed',
                    'data_quality': 0,
                    'timestamp': datetime.now().isoformat()
                }
                
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    logger.warning(f"EIA API attempt {attempt+1} timed out, retrying in {2 ** attempt}s...")
                    time.sleep(2 ** attempt)
                else:
                    logger.warning("EIA data fetch failed: API timed out after all retries.")
                    return {
                        'data_quality': 0,
                        'supply_level': 0,
                        'supply_trend': 0,
                        'source': 'error',
                        'timestamp': datetime.now().isoformat()
                    }
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"EIA API attempt {attempt+1} error: {e}, retrying in {2 ** attempt}s...")
                    time.sleep(2 ** attempt)
                else:
                    logger.warning(f"EIA data fetch failed after retries: {e}")
                    return {
                        'data_quality': 0,
                        'supply_level': 0,
                        'supply_trend': 0,
                        'source': 'error',
                        'timestamp': datetime.now().isoformat()
                    }
    
    def get_fred_economic_data(self):
        """Fetch FRED economic indicators"""
        logger.info("Fetching FRED economic data...")
        try:
            # Dollar Index (DXY is inversely correlated with oil)
            url = f"{self.config.FRED_BASE_URL}?id=DEXUSEU&cosd=2024-01-01&coed={datetime.now().strftime('%Y-%m-%d')}&fmt=csv"
            
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                # Parse CSV data
                lines = response.text.strip().split('\n')
                if len(lines) > 1:
                    recent_data = []
                    for line in lines[-10:]:  # Last 10 entries
                        parts = line.split(',')
                        if len(parts) >= 2 and parts[1] != '.' and parts[1] != 'VALUE':
                            try:
                                recent_data.append(float(parts[1]))
                            except ValueError:
                                continue
                    
                    if recent_data:
                        dollar_strength = recent_data[-1]
                        dollar_trend = self._calculate_trend(recent_data)
                        
                        # FIX #8: Normalize trend using calibration constants instead of magic number
                        # Old: 100 - abs(dollar_trend * 2000)
                        # New: Use actual USD volatility calibration
                        normalized_volatility = abs(dollar_trend) / FRED_TYPICAL_VOLATILITY
                        economic_stability = min(100, max(0, 100 * (1 - normalized_volatility)))
                        
                        logger.info(f"✅ FRED: USD economic data loaded (stability: {economic_stability:.0f})")
                        return {
                            'data_quality': 100,
                            'dollar_strength': dollar_strength,
                            'dollar_trend': dollar_trend,
                            'economic_stability': economic_stability,
                            'source': 'FRED_API',
                            'timestamp': datetime.now().isoformat()
                        }
            
            # NO FALLBACK - Return error state
            logger.warning("⚠️ FRED API unavailable")
            return {
                'error': 'FRED API unavailable',
                'source': 'FRED_failed',
                'data_quality': 0,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"FRED data fetch failed: {e}")
            return {
                'data_quality': 0,
                'dollar_strength': 0,
                'dollar_trend': 0,
                'economic_stability': 0,
                'source': 'error',
                'timestamp': datetime.now().isoformat()
            }
    
    def get_alpha_vantage_data(self):
        """Fetch Alpha Vantage commodity data"""
        logger.info("Fetching Alpha Vantage commodity data...")
        if not self.config.ALPHA_VANTAGE_KEY:
            logger.warning("⚠️ ALPHA_VANTAGE_KEY not configured - skipping Alpha Vantage source")
            return self._missing_key_source_payload('alpha_vantage', 'ALPHA_VANTAGE_KEY')
        try:
            # Get WTI crude oil data from Alpha Vantage
            url = "https://www.alphavantage.co/query"
            params = {
                'function': 'WTI',
                'interval': 'daily',
                'apikey': self.config.ALPHA_VANTAGE_KEY
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                
                if 'data' in data:
                    oil_data = data['data'][:30]  # Last 30 days
                    # Filter out entries with '.' (holidays) or missing values
                    prices = []
                    for entry in oil_data:
                        val = entry.get('value', '')
                        if val and val != '.':
                            try:
                                prices.append(float(val))
                            except ValueError:
                                continue
                    
                    if prices:
                        current_volatility = np.std(prices)
                        # Reverse to oldest-first for correct trend direction (API returns newest-first)
                        price_momentum = self._calculate_trend(prices[::-1])
                        
                        logger.info(f"✅ Alpha Vantage: {len(prices)} WTI price points")
                        return {
                            'data_quality': 100,
                            'volatility': current_volatility,
                            'trend_strength': abs(price_momentum) * 10,
                            'momentum_score': price_momentum,
                            'source': 'AlphaVantage_API',
                            'timestamp': datetime.now().isoformat()
                        }
            
            # NO FALLBACK - Return error state
            logger.warning("⚠️ Alpha Vantage API unavailable")
            return {
                'error': 'Alpha Vantage API unavailable',
                'source': 'AlphaVantage_failed',
                'data_quality': 0,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"Alpha Vantage data fetch failed: {e}")
            return {
                'data_quality': 0,
                'volatility': 0,
                'trend_strength': 0,
                'momentum_score': 0,
                'source': 'error',
                'timestamp': datetime.now().isoformat()
            }
    
    def get_finnhub_market_data(self):
        """Fetch Finnhub market sentiment data"""
        logger.info("Fetching Finnhub oil sector data...")
        if not self.config.FINNHUB_KEY:
            logger.warning("⚠️ FINNHUB_KEY not configured - skipping Finnhub source")
            return self._missing_key_source_payload('finnhub', 'FINNHUB_KEY')
        try:
            # Oil sector stocks for sentiment analysis
            oil_stocks = ['XOM', 'CVX', 'COP', 'EOG', 'SLB']
            sector_data = []
            
            for symbol in oil_stocks:
                try:
                    url = "https://finnhub.io/api/v1/quote"
                    params = {
                        'symbol': symbol,
                        'token': self.config.FINNHUB_KEY
                    }
                    
                    response = requests.get(url, params=params, timeout=5)
                    if response.status_code == 200:
                        quote = response.json()
                        if 'c' in quote and quote['c'] > 0:  # Current price
                            change_percent = quote.get('dp', 0)  # Daily percent change
                            sector_data.append(change_percent)
                            
                except Exception as e:
                    logger.debug(f"Failed to get {symbol}: {e}")
                    continue
            
            if sector_data:
                avg_sector_performance = np.mean(sector_data)
                sector_strength = min(100, max(0, 50 + avg_sector_performance * 2))
                
                logger.info(f"✅ Finnhub: {len(sector_data)} oil sector stocks")
                return {
                    'data_quality': 100,
                    'sector_strength': sector_strength,
                    'sector_momentum': avg_sector_performance,
                    'market_sentiment': 'bullish' if avg_sector_performance > 0 else 'bearish',
                    'source': 'Finnhub_API',
                    'timestamp': datetime.now().isoformat()
                }
            
            # NO FALLBACK - Return error state
            logger.warning("⚠️ Finnhub API unavailable")
            return {
                'error': 'Finnhub API unavailable',
                'source': 'Finnhub_failed',
                'data_quality': 0,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"Finnhub data fetch failed: {e}")
            return {
                'data_quality': 0,
                'sector_strength': 0,
                'sector_momentum': 0,
                'market_sentiment': 'unknown',
                'source': 'error',
                'timestamp': datetime.now().isoformat()
            }
    
    def get_news_sentiment(self):
        """Fetch news sentiment from NewsAPI - ENHANCED with momentum and recency weighting"""
        logger.info("Fetching NewsAPI oil sentiment...")
        if not self.config.NEWSAPI_KEY:
            logger.warning("⚠️ NEWSAPI_KEY not configured - skipping NewsAPI source")
            return self._missing_key_source_payload('news', 'NEWSAPI_KEY')
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': 'oil prices OR crude oil OR WTI OR petroleum OR OPEC',
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 30,  # Increased for better analysis
                'apiKey': self.config.NEWSAPI_KEY
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                articles = data.get('articles', [])
                
                # Enhanced sentiment analysis with finance-specific keywords
                positive_words = [
                    'rise', 'gain', 'up', 'higher', 'surge', 'boost', 'strong', 'increase',
                    'rally', 'bullish', 'jump', 'soar', 'climb', 'recover', 'spike', 'breakout',
                    'demand', 'supply cut', 'shortage', 'opec cut', 'production cut'
                ]
                negative_words = [
                    'fall', 'drop', 'down', 'lower', 'decline', 'weak', 'decrease', 'plunge',
                    'bearish', 'crash', 'slump', 'tumble', 'sink', 'collapse', 'slide',
                    'oversupply', 'glut', 'recession', 'demand drop', 'production increase'
                ]
                uncertainty_words = [
                    'uncertain', 'uncertainty', 'risk', 'volatile', 'volatility', 'war',
                    'sanction', 'tariff', 'disruption', 'tension', 'conflict', 'shock'
                ]
                forward_words = [
                    'outlook', 'forecast', 'expected', 'expects', 'guidance', 'next week',
                    'next month', 'ahead', 'future', 'projection', 'scenario', 'target'
                ]
                intensity_words = [
                    'sharply', 'significantly', 'strongly', 'materially', 'dramatically',
                    'severely', 'massively', 'rapidly', 'heavily', 'aggressively'
                ]
                
                sentiment_scores = []
                recency_weights = []
                bullish_count = 0
                bearish_count = 0
                uncertainty_scores = []
                forward_scores = []
                intensity_scores = []
                
                for i, article in enumerate(articles[:20]):
                    title = article.get('title', '').lower()
                    description = article.get('description', '').lower() if article.get('description') else ''
                    text = f"{title} {description}"
                    
                    # Calculate sentiment score
                    score = 0
                    for word in positive_words:
                        if word in text:
                            score += 1
                    for word in negative_words:
                        if word in text:
                            score -= 1
                    
                    # Track bullish/bearish articles
                    if score > 0:
                        bullish_count += 1
                    elif score < 0:
                        bearish_count += 1
                    
                    sentiment_scores.append(score)
                    uncertainty_scores.append(sum(1 for word in uncertainty_words if word in text))
                    forward_scores.append(sum(1 for word in forward_words if word in text))
                    intensity_scores.append(sum(1 for word in intensity_words if word in text))
                    # Recency weighting: recent articles (first 5) get 2x weight
                    recency_weights.append(2.0 if i < 5 else 1.0)
                
                if sentiment_scores:
                    # Weighted average with recency
                    weighted_sentiment = np.average(sentiment_scores, weights=recency_weights)
                    uncertainty_score = float(np.average(uncertainty_scores, weights=recency_weights))
                    forwardness_score = float(np.average(forward_scores, weights=recency_weights))
                    intensity_score = float(np.average(intensity_scores, weights=recency_weights))
                    
                    # Calculate sentiment momentum (newest articles vs older articles)
                    # articles list is sorted newest-first, so [:half] = most recent
                    half = len(sentiment_scores) // 2
                    newest_avg = np.mean(sentiment_scores[:half]) if half > 0 else 0
                    older_avg = np.mean(sentiment_scores[half:]) if half > 0 else 0
                    # Positive momentum = recent news MORE bullish than older news
                    sentiment_momentum = newest_avg - older_avg
                    
                    # Bullish ratio
                    total_directional = bullish_count + bearish_count
                    bullish_ratio = bullish_count / total_directional if total_directional > 0 else 0.5
                    
                    market_buzz = min(100, max(0, 50 + weighted_sentiment * 5))
                    
                    logger.info(f"✅ NewsAPI: {len(sentiment_scores)} sentiment signals (momentum: {sentiment_momentum:+.2f})")
                    return {
                        'data_quality': 100,
                        'market_buzz': market_buzz,
                        'sentiment_score': weighted_sentiment,
                        'sentiment_momentum': sentiment_momentum,  # NEW: sentiment direction
                        'bullish_ratio': bullish_ratio,  # NEW: ratio of bullish articles
                        'uncertainty_score': uncertainty_score,
                        'forwardness_score': forwardness_score,
                        'intensity_score': intensity_score,
                        'news_volume': len(articles),
                        'source': 'NewsAPI',
                        'timestamp': datetime.now().isoformat()
                    }
            
            # NO FALLBACK - Return error state
            logger.warning("⚠️ NewsAPI unavailable")
            return {
                'error': 'NewsAPI unavailable',
                'source': 'NewsAPI_failed',
                'data_quality': 0,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"NewsAPI fetch failed: {e}")
            return {
                'data_quality': 0,
                'market_buzz': 50,       # Neutral, not bearish (50 = no signal)
                'sentiment_score': 0,    # 0 is raw neutral score
                'sentiment_momentum': 0,
                'bullish_ratio': 0.5,    # Neutral ratio
                'uncertainty_score': 0,
                'forwardness_score': 0,
                'intensity_score': 0,
                'news_volume': 0,
                'source': 'error',
                'timestamp': datetime.now().isoformat()
            }
    
    def get_geopolitical_risk(self):
        """Fetch geopolitical risk signals for WTI: Iran, Strait of Hormuz, OPEC, supply shocks.

        Uses recency-weighted scoring so background noise (week-old Iran oil articles that are
        always present) registers as ELEVATED, while a genuine breaking crisis (articles published
        in the last 6 hours) drives HIGH or CRITICAL. This separates signal from noise without
        needing a historical baseline or extra API calls.
        """
        logger.info("Fetching geopolitical risk signals...")
        if not self.config.NEWSAPI_KEY:
            return self._missing_key_source_payload('geopolitical', 'NEWSAPI_KEY')

        # 30-minute cache: NewsAPI free tier = 100 req/day; frontend polls every 15s.
        # Without this cache the free tier exhausts in < 25 minutes of use.
        now_utc = datetime.now(timezone.utc)
        cache = getattr(self, '_geo_risk_cache', None)
        if cache and (now_utc - cache['ts']).total_seconds() < 1800:
            logger.info("✅ Geopolitical risk: serving from 30-min cache")
            return cache['data']

        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': (
                    'Iran oil OR Strait Hormuz OR OPEC production cut OR '
                    'oil sanctions OR Middle East conflict OR oil supply disruption OR '
                    'Iraq oil OR Houthi tanker OR Saudi Arabia oil supply'
                ),
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 30,
                'apiKey': self.config.NEWSAPI_KEY,
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return {
                    'data_quality': 0,
                    'geo_risk_score': 0,
                    'regime': 'UNKNOWN',
                    'dominant_driver': 'unknown',
                    'iran_articles': 0,
                    'opec_articles': 0,
                    'conflict_articles': 0,
                    'sanctions_articles': 0,
                    'risk_breakdown': {},
                    'top_headlines': [],
                    'total_articles_scanned': 0,
                    'recent_24h_articles': 0,
                    'novelty_spike': False,
                    'source': 'newsapi_geopolitical_failed',
                    'timestamp': datetime.now().isoformat(),
                }

            articles = response.json().get('articles', [])

            risk_keywords = {
                'iran': ['iran', 'hormuz', 'tehran', 'iranian', 'persian gulf', 'irgc'],
                'opec': ['opec', 'opec+', 'saudi', 'riyadh', 'aramco', 'production cut', 'quota'],
                'conflict': ['conflict', 'attack', 'strike', 'houthi', 'tanker', 'blockade', 'militia', 'war'],
                'sanctions': ['sanction', 'embargo', 'restriction', 'tariff', 'export ban'],
            }

            def _recency_weight(published_at_str: str) -> float:
                """Breaking news < 6h counts 20x more than week-old background articles."""
                try:
                    pub = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
                    age_h = (now_utc - pub).total_seconds() / 3600
                    if age_h < 6:   return 2.0   # breaking
                    if age_h < 24:  return 1.0   # today
                    if age_h < 72:  return 0.5   # this week
                    if age_h < 168: return 0.25  # last 7 days
                    return 0.1                   # stale
                except Exception:
                    return 0.25

            risk_counts = {k: 0 for k in risk_keywords}     # unweighted, for display
            risk_weighted = {k: 0.0 for k in risk_keywords}  # recency-weighted, for scoring
            top_headlines = []
            recent_24h = 0
            novelty_spike = False

            for article in articles:
                title = article.get('title', '').lower()
                desc = (article.get('description', '') or '').lower()
                text = f"{title} {desc}"
                pub_at = article.get('publishedAt', '')
                w = _recency_weight(pub_at)

                matched_categories = []
                for category, keywords in risk_keywords.items():
                    if any(kw in text for kw in keywords):
                        risk_counts[category] += 1
                        risk_weighted[category] += w
                        matched_categories.append(category)

                if matched_categories:
                    if w >= 1.0:
                        recent_24h += 1
                    if w >= 2.0:
                        novelty_spike = True

                if len(top_headlines) < 5 and matched_categories:
                    raw_title = article.get('title', '')
                    if raw_title:
                        top_headlines.append({
                            'headline': raw_title,
                            'published_at': pub_at,
                            'source': article.get('source', {}).get('name', ''),
                            'category': matched_categories[0],
                            'is_breaking': w >= 2.0,
                        })

            # Recency-weighted score: background noise (all articles old) = ~25 (ELEVATED).
            # Active crisis with articles in last 6h easily reaches 65+ (CRITICAL).
            # Normalizer 10.0 = approximate max weighted score at sustained crisis level.
            category_weights = {'iran': 0.40, 'opec': 0.30, 'conflict': 0.20, 'sanctions': 0.10}
            raw_score = sum(
                min(1.0, risk_weighted.get(k, 0) / 10.0) * w
                for k, w in category_weights.items()
            )
            geo_risk_score = round(raw_score * 100, 1)

            if geo_risk_score >= 65:
                regime = 'CRITICAL'
            elif geo_risk_score >= 40:
                regime = 'HIGH'
            elif geo_risk_score >= 15:
                regime = 'ELEVATED'
            else:
                regime = 'LOW'

            dominant_driver = max(risk_counts, key=risk_counts.get) if any(risk_counts.values()) else 'none'

            logger.info(
                f"✅ Geopolitical risk: score={geo_risk_score}, regime={regime}, "
                f"driver={dominant_driver}, recent_24h={recent_24h}, spike={novelty_spike}"
            )
            result = {
                'data_quality': min(100, len(articles) * 3),
                'geo_risk_score': geo_risk_score,
                'regime': regime,
                'dominant_driver': dominant_driver,
                'iran_articles': risk_counts['iran'],
                'opec_articles': risk_counts['opec'],
                'conflict_articles': risk_counts['conflict'],
                'sanctions_articles': risk_counts['sanctions'],
                'risk_breakdown': risk_counts,
                'top_headlines': top_headlines,
                'total_articles_scanned': len(articles),
                'recent_24h_articles': recent_24h,
                'novelty_spike': novelty_spike,
                'source': 'newsapi_geopolitical',
                'timestamp': datetime.now().isoformat(),
            }
            self._geo_risk_cache = {'data': result, 'ts': now_utc}
            return result

        except Exception as e:
            logger.warning(f"Geopolitical risk fetch failed: {e}")
            return {
                'data_quality': 0,
                'geo_risk_score': 0,
                'regime': 'UNKNOWN',
                'dominant_driver': 'unknown',
                'iran_articles': 0,
                'opec_articles': 0,
                'conflict_articles': 0,
                'sanctions_articles': 0,
                'risk_breakdown': {},
                'top_headlines': [],
                'total_articles_scanned': 0,
                'recent_24h_articles': 0,
                'novelty_spike': False,
                'error': str(e),
                'source': 'geopolitical_error',
                'timestamp': datetime.now().isoformat(),
            }

    def get_geopolitical_scenario_analysis(self, geo_data: dict, current_price: float) -> dict:
        """
        Match current geopolitical signals against historical Iran/Middle East events.
        Returns top analogues, implied price ranges, and Strait of Hormuz scenarios.
        Designed for hedge fund use: acknowledges ML model limitations in tail-risk regimes.
        """
        regime = str(geo_data.get('regime', 'LOW'))
        dominant_driver = str(geo_data.get('dominant_driver', 'none'))
        risk_breakdown = geo_data.get('risk_breakdown', {})
        geo_score = float(geo_data.get('geo_risk_score', 0) or 0)
        iran_count = int(risk_breakdown.get('iran', 0))
        strait_risk = iran_count >= 2 or dominant_driver == 'iran'

        def _event_similarity(event: dict) -> float:
            score = 0.0
            # Driver overlap
            event_drivers = set(event.get('drivers', []))
            if dominant_driver in event_drivers:
                score += 0.35
            for cat, count in risk_breakdown.items():
                if cat in event_drivers and count > 0:
                    score += 0.10

            # Regime match
            event_severity = event.get('peak_pct', 0)
            if regime == 'CRITICAL' and event_severity >= 10:
                score += 0.30
            elif regime == 'HIGH' and 3 <= event_severity < 20:
                score += 0.25
            elif regime == 'ELEVATED' and 1 <= event_severity < 10:
                score += 0.20
            elif regime == 'LOW' and event_severity < 5:
                score += 0.15

            # Strait risk match
            if strait_risk and event.get('strait_risk', False):
                score += 0.15

            # Supply disruption relevance
            if event.get('supply_disruption_mbpd', 0) > 0 and geo_score >= 40:
                score += 0.10

            return round(score, 3)

        # Rank events
        scored = sorted(
            [{'similarity': _event_similarity(e), **e} for e in IRAN_GEOPOLITICAL_EVENTS],
            key=lambda x: x['similarity'],
            reverse=True,
        )
        top_analogues = scored[:3]

        # Implied price range from top 2 analogues (weighted by similarity)
        implied_low = current_price
        implied_high = current_price
        implied_mid = current_price
        if top_analogues and top_analogues[0]['similarity'] > 0:
            weights = [a['similarity'] for a in top_analogues[:2]]
            total_w = sum(weights) or 1.0
            weighted_peak = sum(a['peak_pct'] * w for a, w in zip(top_analogues[:2], weights)) / total_w
            weighted_settled = sum(a['settled_pct'] * w for a, w in zip(top_analogues[:2], weights)) / total_w
            implied_high = round(current_price * (1 + weighted_peak / 100), 2)
            implied_mid = round(current_price * (1 + weighted_settled / 100), 2)
            implied_low = round(current_price * (1 - abs(weighted_settled) * 0.5 / 100), 2)

        # Illustrative escalation odds for the 4 Hormuz scenarios, conditioned on geo_score
        # + strait_risk. These are SUBJECTIVE priors (a closure has never actually occurred,
        # so there is no empirical base rate to fit) chosen to reflect the consensus that
        # minor incidents are common and full closure is a rare tail. They are decision-support
        # weights for scenario P&L, NOT a calibrated statistical forecast.
        def _scenario_probs(score: float, has_strait: bool) -> list:
            """Returns [p_minor, p_partial, p_extended, p_full], sums to 1.0 (illustrative priors)."""
            if score >= 85:
                p = [0.10, 0.26, 0.37, 0.27]
            elif score >= 65:
                p = [0.20, 0.33, 0.30, 0.17]
            elif score >= 40:
                p = [0.38, 0.37, 0.18, 0.07]
            elif score >= 15:
                p = [0.60, 0.28, 0.09, 0.03]
            else:
                p = [0.80, 0.15, 0.04, 0.01]
            if has_strait:
                # Strait risk shifts 10pp from MINOR to PARTIAL/EXTENDED
                shift = min(p[0], 0.10)
                p[0] -= shift
                p[1] += shift * 0.55
                p[2] += shift * 0.45
            total = sum(p)
            return [round(x / total, 4) for x in p]

        all_probs = _scenario_probs(geo_score, strait_risk)

        # Probability-weighted expected price impact: E[ΔP] = Σ(prob_i × impact_mid_i)
        # This is the number a risk manager uses for P&L scenario weighting.
        ev_impact_usd = round(
            sum(all_probs[i] * HORMUZ_SCENARIOS[i]['price_impact_mid']
                for i in range(len(HORMUZ_SCENARIOS))), 2
        )

        # Select which scenarios to surface based on regime
        if regime == 'CRITICAL':
            active_scenarios = HORMUZ_SCENARIOS[1:]
        elif regime == 'HIGH':
            active_scenarios = HORMUZ_SCENARIOS[:3]
        elif regime == 'ELEVATED':
            active_scenarios = HORMUZ_SCENARIOS[:2]
        else:
            active_scenarios = HORMUZ_SCENARIOS[:1]

        # Attach dollar prices and probability to each displayed scenario
        scenario_name_to_idx = {s['name']: i for i, s in enumerate(HORMUZ_SCENARIOS)}
        scenarios_with_price = []
        for s in active_scenarios:
            s_idx = scenario_name_to_idx.get(s['name'], 0)
            scenarios_with_price.append({
                **s,
                'price_target_low':  round(current_price + s['price_impact_low'], 2),
                'price_target_mid':  round(current_price + s['price_impact_mid'], 2),
                'price_target_high': round(current_price + s['price_impact_high'], 2),
                'probability': all_probs[s_idx],
            })

        # Edge = analogue-implied base-case price minus current market price.
        # Positive = geopolitical risk premium not yet priced in (upside).
        # Negative = market over-pricing vs historical precedent (mean-reversion risk).
        edge_usd = round(implied_mid - current_price, 2)
        edge_pct = round(edge_usd / current_price * 100, 2) if current_price > 0 else 0.0

        # Expected resolution timeline from similarity-weighted analogues
        valid = [a for a in top_analogues[:3] if a['similarity'] > 0]
        if valid:
            total_sim = sum(a['similarity'] for a in valid)
            expected_resolution_days = round(
                sum(a['duration_days'] * a['similarity'] for a in valid) / total_sim
            )
        else:
            expected_resolution_days = 0

        # Signal strength: geo score + analogue confidence + strait risk premium
        top_sim_score = top_analogues[0]['similarity'] if top_analogues else 0.0
        signal_strength = min(100, round(
            geo_score * 0.50 + top_sim_score * 50 + (15 if strait_risk else 0)
        ))

        # Trade signal
        if regime == 'CRITICAL' and dominant_driver in ('iran', 'conflict'):
            signal = 'LONG_BIAS'
            signal_basis = (
                f'Analogues imply +{abs(edge_usd):.1f}/bbl unpriced upside '
                f'(base: ${implied_mid:.2f}, current: ${current_price:.2f}). '
                f'CRITICAL Iran/conflict regime; EV +${ev_impact_usd:.1f}/bbl probability-weighted.'
            )
        elif regime == 'HIGH' and strait_risk:
            signal = 'LONG_BIAS'
            signal_basis = (
                f'Strait risk active. Analogue base case ${implied_mid:.2f} '
                f'vs current ${current_price:.2f} ({edge_pct:+.1f}%). '
                f'EV +${ev_impact_usd:.1f}/bbl probability-weighted Hormuz impact.'
            )
        elif regime in ('HIGH', 'ELEVATED') and dominant_driver == 'opec':
            signal = 'LONG_BIAS'
            signal_basis = (
                f'OPEC supply-cut regime. Historical OPEC cuts average +13–25% over 30 days. '
                f'EV +${ev_impact_usd:.1f}/bbl.'
            )
        elif regime == 'LOW' or dominant_driver == 'none':
            signal = 'NEUTRAL'
            signal_basis = 'No dominant geopolitical driver. ML ensemble is the primary signal in this regime.'
        else:
            signal = 'WATCH'
            signal_basis = (
                f'Elevated risk ({regime}) but no confirmed supply disruption yet. '
                f'Edge vs analogues: {edge_pct:+.1f}%. Monitor for escalation.'
            )

        ml_caveat = None
        if regime in ('HIGH', 'CRITICAL'):
            ml_caveat = (
                'WARNING: ML ensemble trained on normal-market data. '
                'In HIGH/CRITICAL geopolitical regimes, model may significantly underestimate '
                'upside tail risk. Historical analogue signal should take precedence.'
            )

        return {
            'top_analogues': [
                {
                    'id': a['id'],
                    'date': a['date'],
                    'event': a['event'],
                    'type': a['type'],
                    'peak_pct': a['peak_pct'],
                    'settled_pct': a['settled_pct'],
                    'duration_days': a['duration_days'],
                    'supply_disruption_mbpd': a['supply_disruption_mbpd'],
                    'strait_risk': a['strait_risk'],
                    'similarity': a['similarity'],
                    'notes': a['notes'],
                }
                for a in top_analogues
            ],
            'implied_price_range': {
                'low':   implied_low,
                'mid':   implied_mid,
                'high':  implied_high,
                'basis': f'Similarity-weighted avg of top {min(2, len(top_analogues))} historical analogues',
            },
            'hormuz_scenarios': scenarios_with_price,
            'geo_signal': {
                'signal':                  signal,
                'basis':                   signal_basis,
                'regime':                  regime,
                'strait_risk':             strait_risk,
                'signal_strength':         signal_strength,
                'edge_usd':                edge_usd,
                'edge_pct':                edge_pct,
                'ev_impact_usd':           ev_impact_usd,
                'expected_resolution_days': expected_resolution_days,
            },
            'ml_caveat': ml_caveat,
            'current_price': current_price,
            'timestamp': datetime.now().isoformat(),
        }

    def get_usda_agricultural_data(self):
        """Fetch USDA agricultural data"""
        logger.info("Fetching USDA agricultural data...")
        if not self.config.USDA_NASS_KEY:
            logger.warning("⚠️ USDA_NASS_KEY not configured - skipping USDA source")
            return self._missing_key_source_payload('usda', 'USDA_NASS_KEY')
        try:
            # USDA NASS API for corn prices (affects ethanol demand)
            url = "https://quickstats.nass.usda.gov/api/api_GET/"
            params = {
                'key': self.config.USDA_NASS_KEY,
                'commodity_desc': 'CORN',
                'statisticcat_desc': 'PRICE RECEIVED',
                'agg_level_desc': 'NATIONAL',
                'year': datetime.now().year,
                'format': 'JSON'
            }
            
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and data['data']:
                    # Filter out non-numeric values before converting
                    corn_prices = []
                    for item in data['data'][:5]:
                        val = item.get('Value', '')
                        if val and val.replace('.', '').replace(',', '').isdigit():
                            try:
                                corn_prices.append(float(val.replace(',', '')))
                            except ValueError:
                                continue
                    
                    if corn_prices:
                        avg_corn_price = np.mean(corn_prices)
                        # Corn price ~$3-8/bushel; normalize to 0-100 scale
                        # $4 = 40, $5 = 50, $6 = 60, $8 = 80 (reasonable range)
                        agricultural_impact = min(100, max(0, avg_corn_price * 10))
                        biofuel_demand = agricultural_impact  # Higher corn price -> higher biofuel cost
                        
                        logger.info("✅ USDA: Agricultural data loaded")
                        return {
                            'data_quality': 100,
                            'agricultural_impact': agricultural_impact,
                            'corn_price_level': avg_corn_price,
                            'biofuel_demand': min(100, biofuel_demand),
                            'source': 'USDA_API',
                            'timestamp': datetime.now().isoformat()
                        }
            
            # NO FALLBACK - Return error state
            logger.warning("⚠️ USDA API unavailable")
            return {
                'error': 'USDA API unavailable',
                'source': 'USDA_failed',
                'data_quality': 0,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"USDA API error: {e}")
            return {
                'data_quality': 0,
                'agricultural_impact': 0,
                'corn_price_level': 0,
                'biofuel_demand': 0,
                'source': 'error',
                'timestamp': datetime.now().isoformat()
            }
    
    def get_noaa_weather_data(self):
        """Fetch NOAA weather data"""
        logger.info("Fetching NOAA weather data...")
        if not self.config.NOAA_CDO_KEY:
            logger.warning("⚠️ NOAA_CDO_KEY not configured - skipping NOAA source")
            return self._missing_key_source_payload('noaa', 'NOAA_CDO_KEY')
        try:
            # NOAA Climate Data Online API for weather patterns affecting oil demand
            url = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"
            headers = {'token': self.config.NOAA_CDO_KEY}
            
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            
            params = {
                'datasetid': 'GHCND',
                'datatypeid': 'TAVG',
                'locationid': 'FIPS:US',
                'startdate': start_date,
                'enddate': end_date,
                'limit': 30
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'results' in data:
                    temp_data = [item['value'] / 10 for item in data['results'] if 'value' in item]  # Convert to Celsius
                    
                    if temp_data:
                        avg_temp = np.mean(temp_data)
                        temp_anomaly = abs(avg_temp - 20)  # Deviation from 20°C baseline
                        weather_impact = min(100, temp_anomaly * 2)
                        
                        logger.info("✅ Weather data loaded")
                        return {
                            'data_quality': 100,
                            'weather_impact': weather_impact,
                            'temperature_anomaly': temp_anomaly,
                            'seasonal_demand': min(100, max(0, 50 + (avg_temp - 20) * 2)),
                            'source': 'NOAA_API',
                            'timestamp': datetime.now().isoformat()
                        }
            
            # NO FALLBACK - Return error state
            logger.warning("⚠️ NOAA API unavailable")
            return {
                'error': 'NOAA API unavailable',
                'source': 'NOAA_failed',
                'data_quality': 0,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"NOAA data fetch failed: {e}")
            return {
                'data_quality': 0,
                'weather_impact': 0,
                'temperature_anomaly': 0,
                'seasonal_demand': 0,
                'source': 'error',
                'timestamp': datetime.now().isoformat()
            }
    
    def _calculate_trend(self, values):
        """Calculate trend from a series of values"""
        if len(values) < 2:
            return 0
        
        x = np.arange(len(values))
        y = np.array(values)
        
        # Simple linear regression
        slope = np.polyfit(x, y, 1)[0]
        return slope
    
    def engineer_premium_features(self, wti_data, external_data):
        """Engineer premium features for ML models"""
        logger.info("Engineering comprehensive features...")
        
        features = {}
        
        # Technical indicators from WTI data
        closes = wti_data['Close'].values
        volumes = wti_data['Volume'].values
        
        # Price-based features
        # BUG11 FIX: Guard against zero divisor in price change calculations
        features['current_price'] = closes[-1]
        features['price_change_1d'] = (closes[-1] - closes[-2]) / closes[-2] if len(closes) > 1 and closes[-2] != 0 else 0
        features['price_change_5d'] = (closes[-1] - closes[-6]) / closes[-6] if len(closes) > 5 and closes[-6] != 0 else 0
        features['price_change_20d'] = (closes[-1] - closes[-21]) / closes[-21] if len(closes) > 20 and closes[-21] != 0 else 0
        
        # Volatility features
        returns = np.diff(np.log(closes))
        features['volatility_5d'] = np.std(returns[-5:]) if len(returns) >= 5 else 0
        features['volatility_20d'] = np.std(returns[-20:]) if len(returns) >= 20 else 0
        
        # Volume features
        # BUG19 FIX: Check for NaN/inf values in volume data
        volume_current = volumes[-1] if len(volumes) > 0 and not np.isnan(volumes[-1]) else 0
        features['volume_current'] = volume_current if not np.isinf(volume_current) else 0
        volume_avg = np.nanmean(volumes[-20:]) if len(volumes) >= 20 else 0
        features['volume_avg_20d'] = volume_avg if not np.isnan(volume_avg) and not np.isinf(volume_avg) else 0
        features['volume_ratio'] = features['volume_current'] / max(features['volume_avg_20d'], 1)
        
        # Moving averages
        features['ma_5'] = np.mean(closes[-5:]) if len(closes) >= 5 else closes[-1]
        features['ma_20'] = np.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]
        features['ma_50'] = np.mean(closes[-50:]) if len(closes) >= 50 else closes[-1]
        
        # Technical ratios
        # BUG12 FIX: Guard against zero moving averages (can occur with all-zero data)
        features['price_to_ma20'] = closes[-1] / features['ma_20'] if features['ma_20'] > 0 else 1.0
        features['ma5_to_ma20'] = features['ma_5'] / features['ma_20'] if features['ma_20'] > 0 else 1.0
        
        # External data features - ensure consistent feature set
        # Define expected features from each source to maintain consistency
        expected_external_features = {
            'eia': ['data_quality', 'supply_level', 'supply_trend'],
            'fred': ['data_quality', 'dollar_strength', 'dollar_trend', 'economic_stability'],
            'alpha_vantage': ['data_quality', 'volatility', 'trend_strength', 'momentum_score'],
            'finnhub': ['data_quality', 'sector_strength', 'sector_momentum'],
            'news': ['data_quality', 'market_buzz', 'sentiment_score', 'sentiment_momentum', 'bullish_ratio', 'uncertainty_score', 'forwardness_score', 'intensity_score', 'news_volume'],
            'usda': ['data_quality', 'agricultural_impact', 'corn_price_level', 'biofuel_demand'],
            'noaa': ['data_quality', 'weather_impact', 'temperature_anomaly', 'seasonal_demand'],
            'geopolitical': ['data_quality', 'geo_risk_score', 'iran_articles', 'opec_articles', 'conflict_articles'],
        }
        
        for source, expected_features in expected_external_features.items():
            data = external_data.get(source, {})
            
            # If source has error or is missing, use neutral default values
            if 'error' in data or not data:
                logger.debug(f"Using defaults for {source} - API unavailable")
                # Use neutral baseline values to maintain feature consistency
                features[f'{source}_data_quality'] = 0  # Indicates missing data
                for feature in expected_features[1:]:  # Skip data_quality as already set
                    if 'trend' in feature or 'momentum' in feature:
                        features[f'{source}_{feature}'] = 0  # Neutral trend
                    elif 'strength' in feature or 'level' in feature:
                        features[f'{source}_{feature}'] = 50  # Mid-range baseline
                    else:
                        features[f'{source}_{feature}'] = 50  # Safe default
            else:
                # Use actual data if available
                data_quality = data.get('data_quality', 100)
                features[f'{source}_data_quality'] = data_quality
                
                for feature in expected_features[1:]:  # Skip data_quality as already handled
                    if feature in data and isinstance(data[feature], (int, float)):
                        features[f'{source}_{feature}'] = data[feature]
                    else:
                        # Use appropriate default for missing features
                        if 'trend' in feature or 'momentum' in feature:
                            features[f'{source}_{feature}'] = 0
                        else:
                            features[f'{source}_{feature}'] = 50
        
        # Time-based features from the BAR DATE (last row of wti_data), not datetime.now()
        # This ensures historical training rows learn correct seasonal patterns
        bar_date = wti_data.index[-1]
        features['month'] = bar_date.month
        features['quarter'] = (bar_date.month - 1) // 3 + 1
        features['day_of_year'] = bar_date.timetuple().tm_yday
        features['is_winter'] = 1 if bar_date.month in [12, 1, 2] else 0
        features['is_summer'] = 1 if bar_date.month in [6, 7, 8] else 0
        
        logger.info(f"Created {len(features)} premium features")
        return features
    
    def train_prediction_models(self, features_df, target_column, target_mode='price'):
        """Train ensemble of ML models for oil prediction"""
        logger.info("Training oil-optimized ML models...")
        training_start = time.perf_counter()
        
        # Drop ALL target columns to prevent data leakage and feature mismatch
        target_columns = ['target_1h', 'target_1d', 'target_1w']
        columns_to_drop = [col for col in target_columns if col in features_df.columns]
        X = features_df.drop(columns=columns_to_drop)
        y = features_df[target_column]

        if X.shape[1] == 0:
            raise ValueError("No input features available for model training")
        if len(y) < 5:
            raise ValueError(f"Insufficient samples for training: {len(y)}")

        target_mode = str(target_mode or 'price').lower()
        if target_mode not in {'price', 'return', 'excess_return'}:
            target_mode = 'price'
        reference_prices = None
        baseline_feature_name = None
        baseline_returns = None
        if target_mode in {'return', 'excess_return'} and 'current_price' in X.columns:
            reference_prices = X['current_price'].to_numpy(dtype=float)
        if target_mode == 'excess_return':
            horizon_suffix = str(target_column).split('_', 1)[-1]
            candidate_feature = f'baseline_return_{horizon_suffix}'
            if candidate_feature in X.columns:
                baseline_feature_name = candidate_feature
                baseline_returns = X[candidate_feature].to_numpy(dtype=float)
        
        # Store ALL feature names for prediction phase
        all_feature_names = X.columns.tolist()
        
        # Feature selection
        # Allow a broader but still bounded feature set so richer cross-asset/regime signals can survive selection.
        n_features = len(X.columns)
        max_selected_features = min(self.max_selected_features, n_features)
        if n_features <= 1:
            k_value = 1
        elif n_features <= 3:
            k_value = n_features - 1
        else:
            k_value = min(max_selected_features, max(6, int(round(n_features * 0.65))))
            if k_value >= n_features:
                k_value = n_features - 1

        # Fit global preprocessing once for final model training and production inference.
        selector = SelectKBest(score_func=self._hybrid_feature_scores, k=k_value)
        X_selected = selector.fit_transform(X, y)
        selected_features = X.columns[selector.get_support()].tolist()

        logger.info(f"Selected {len(selected_features)} best features for oil prediction")

        scaler = RobustScaler()
        X_scaled = scaler.fit_transform(X_selected)

        X_values = X.to_numpy(dtype=float)
        y_values = y.to_numpy(dtype=float)

        n_estimators = self.model_n_estimators
        cpu_workers = self.model_cpu_workers
        
        # Train multiple models - UPGRADED ENSEMBLE
        # XGBoost replaces Gradient Boosting (better performance)
        # LightGBM replaces Lasso (faster, handles mixed features)
        models = {
            'random_forest': RandomForestRegressor(n_estimators=n_estimators, random_state=42, max_depth=10, n_jobs=cpu_workers),
            'extra_trees': ExtraTreesRegressor(n_estimators=n_estimators, random_state=42, max_depth=8, n_jobs=cpu_workers),
            'elastic_net': ElasticNet(alpha=0.1, random_state=42),
            'ridge': Ridge(alpha=1.0, random_state=42),
            'xgboost': XGBRegressor(
                n_estimators=n_estimators,
                max_depth=6,
                learning_rate=0.05,
                random_state=42,
                verbosity=0,
                n_jobs=cpu_workers,
                tree_method='hist',
                subsample=0.9,
                colsample_bytree=0.9,
            ),
            'lightgbm': LGBMRegressor(
                n_estimators=n_estimators,
                max_depth=6,
                learning_rate=0.05,
                random_state=42,
                verbosity=-1,
                n_jobs=cpu_workers,
                subsample=0.9,
                colsample_bytree=0.9,
            ),
        }
        
        trained_models = {}
        model_scores = {}
        
        # Time series split for validation
        # Use a small temporal gap to reduce look-ahead leakage between train and validation windows.
        gap_size = max(0, min(3, len(X_values) // 100))
        cv_splits = []
        max_valid_splits = 0
        min_required_samples = max(10, self.time_series_cv_splits * 2 + gap_size + 1)
        if len(X_values) >= min_required_samples:
            max_valid_splits = max(2, min(self.time_series_cv_splits, len(X_values) - gap_size - 2))
            tscv = TimeSeriesSplit(n_splits=max_valid_splits, gap=gap_size)
            cv_splits = list(tscv.split(X_values))
        fold_store = [
            {
                'predictions': {},
                'y_true': None,
                'baseline': None,
            }
            for _ in cv_splits
        ]
        
        for name, model in models.items():
            try:
                if cv_splits:
                    scores = []
                    for fold_idx, (train_idx, val_idx) in enumerate(cv_splits):
                        X_train_raw = X_values[train_idx]
                        X_val_raw = X_values[val_idx]
                        y_train = y_values[train_idx]
                        y_val = y_values[val_idx]

                        # Fit preprocessing only on the fold train set to avoid leakage.
                        fold_selector = SelectKBest(score_func=self._hybrid_feature_scores, k=k_value)
                        X_train_selected = fold_selector.fit_transform(X_train_raw, y_train)
                        X_val_selected = fold_selector.transform(X_val_raw)

                        fold_scaler = RobustScaler()
                        X_train = fold_scaler.fit_transform(X_train_selected)
                        X_val = fold_scaler.transform(X_val_selected)

                        model.fit(X_train, y_train)
                        y_pred = model.predict(X_val)
                        # Guard against zero-variance validation fold (BUG3)
                        y_var = np.var(y_val)
                        if y_var > 0:
                            score = 1 - mean_squared_error(y_val, y_pred) / y_var
                        else:
                            score = 0.0  # No signal in this fold
                        scores.append(max(0, score))  # Ensure non-negative

                        if target_mode in {'return', 'excess_return'}:
                            if reference_prices is not None:
                                fold_reference = np.maximum(reference_prices[val_idx], 1e-6)
                            else:
                                fold_reference = np.ones(len(y_val), dtype=float)
                            if target_mode == 'excess_return' and baseline_returns is not None:
                                fold_baseline = np.asarray(baseline_returns[val_idx], dtype=float)
                            else:
                                fold_baseline = np.zeros(len(y_val), dtype=float)
                            actual_prices = fold_reference * (1.0 + fold_baseline + np.asarray(y_val, dtype=float))
                            predicted_prices = fold_reference * (1.0 + fold_baseline + np.asarray(y_pred, dtype=float))
                            baseline = fold_reference
                        else:
                            actual_prices = np.asarray(y_val, dtype=float)
                            predicted_prices = np.asarray(y_pred, dtype=float)
                            if len(y_train) > 0:
                                baseline = np.concatenate(([float(y_train[-1])], y_val[:-1].astype(float)))
                            else:
                                baseline = np.zeros(len(y_val), dtype=float)

                        fold_store[fold_idx]['predictions'][name] = np.asarray(predicted_prices, dtype=float)
                        fold_store[fold_idx]['y_true'] = np.asarray(actual_prices, dtype=float)
                        fold_store[fold_idx]['baseline'] = baseline

                    avg_score = float(np.mean(scores))
                    model.fit(X_scaled, y)  # Final training on all data
                else:
                    model.fit(X_scaled, y)
                    y_pred_full = model.predict(X_scaled)
                    y_var = np.var(y_values)
                    if y_var > 0:
                        avg_score = max(0.0, min(1.0, 1 - mean_squared_error(y_values, y_pred_full) / y_var))
                    else:
                        avg_score = 0.5
                
                trained_models[name] = model
                model_scores[name] = avg_score
                
            except Exception as e:
                logger.warning(f"Model {name} training failed: {e}")

        model_validation_scores = dict(model_scores)
        model_direction_scores = {}
        model_backtest_metrics = {}
        if fold_store:
            for model_name in trained_models:
                collected_preds = []
                collected_true = []
                collected_baseline = []
                for fold in fold_store:
                    fold_preds = fold['predictions'].get(model_name)
                    fold_true = fold.get('y_true')
                    fold_baseline = fold.get('baseline')
                    if fold_preds is None or fold_true is None or fold_baseline is None:
                        continue
                    collected_preds.extend(np.asarray(fold_preds, dtype=float).tolist())
                    collected_true.extend(np.asarray(fold_true, dtype=float).tolist())
                    collected_baseline.extend(np.asarray(fold_baseline, dtype=float).tolist())
                if not collected_preds:
                    continue
                backtest_metrics = self._compute_backtest_metrics(
                    np.asarray(collected_true, dtype=float),
                    np.asarray(collected_preds, dtype=float),
                    np.asarray(collected_baseline, dtype=float),
                )
                model_backtest_metrics[model_name] = backtest_metrics
                model_direction_scores[model_name] = float(backtest_metrics.get('direction_accuracy', 50.0) or 50.0)

        for model_name in trained_models:
            model_scores[model_name] = self._compose_model_weight_score(
                model_validation_scores.get(model_name, 0.5),
                model_direction_scores.get(model_name, 50.0),
            )

        latest_fold_metrics = {
            'samples': 0,
            'mae': 0.0,
            'rmse': 0.0,
            'mape': 0.0,
            'direction_accuracy': 0.0,
        }
        if fold_store:
            latest_fold = fold_store[-1]
            if latest_fold['predictions'] and latest_fold['y_true'] is not None:
                model_preds = []
                model_weights = []
                for model_name, pred_values in latest_fold['predictions'].items():
                    model_preds.append(pred_values)
                    model_weights.append(max(0.3, min(1.0, model_scores.get(model_name, 0.5))))

                ensemble_pred = np.average(np.asarray(model_preds), axis=0, weights=np.asarray(model_weights))
                latest_fold_metrics = self._compute_backtest_metrics(
                    latest_fold['y_true'],
                    ensemble_pred,
                    latest_fold['baseline']
                )
        
        logger.info(f"Trained {len(trained_models)} oil-optimized models")
        diagnostics = {
            'training_time_seconds': float(time.perf_counter() - training_start),
            'n_estimators': int(n_estimators),
            'cv_splits': int(max_valid_splits),
            'gap_size': int(gap_size),
            'cv_mode': 'leakage_safe_fold_preprocessing' if cv_splits else 'in_sample_fallback',
            'rows': int(len(features_df)),
            'feature_count': int(len(all_feature_names)),
            'target_mode': target_mode,
            'baseline_feature_name': baseline_feature_name,
            'model_validation_scores': model_validation_scores,
            'model_direction_scores': model_direction_scores,
            'model_backtest_metrics': model_backtest_metrics,
            'model_weight_scores': model_scores,
            'latest_fold_backtest': latest_fold_metrics,
        }
        
        # Return all_feature_names for proper transform during prediction
        return trained_models, model_scores, scaler, selector, selected_features, all_feature_names, diagnostics
    
    def get_multi_horizon_predictions_simple(self):
        """Generate predictions using only reliable yfinance data - no external dependencies"""
        try:
            logger.info("Starting SIMPLE multi-horizon prediction engine (yfinance only)...")
            
            # Get WTI historical data only
            logger.info("Fetching WTI historical data...")
            contract_info = get_current_wti_contract()
            # Get WTI data directly using yfinance
            ticker = _yf_ticker("CL=F")
            wti_data = ticker.history(period="6mo", interval="1d")
            
            if wti_data is None or len(wti_data) < 30:
                raise Exception("Insufficient historical WTI data")
            
            logger.info(f"Loaded {len(wti_data)} WTI data points")
            
            # Simple feature engineering (technical indicators only)
            def create_simple_features(price_data):
                closes = price_data['Close'].values
                
                features = {
                    'current_price': closes[-1],
                    'price_change': closes[-1] - closes[-2] if len(closes) > 1 else 0,
                    'price_change_pct': ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) > 1 and closes[-2] != 0 else 0,
                    'ma_5': closes[-5:].mean() if len(closes) >= 5 else closes[-1],
                    'ma_10': closes[-10:].mean() if len(closes) >= 10 else closes[-1],
                    'ma_20': closes[-20:].mean() if len(closes) >= 20 else closes[-1],
                    'volatility': closes[-10:].std() if len(closes) >= 10 else 1.0,
                    'rsi': self.calculate_rsi(closes) if len(closes) >= 14 else 50,
                    'price_position': ((closes[-1] - closes[-20:].min()) / (closes[-20:].max() - closes[-20:].min())) if len(closes) >= 20 and (closes[-20:].max() - closes[-20:].min()) > 0 else 0.5
                }
                return features
            
            # Create current features
            current_features = create_simple_features(wti_data)
            
            # Simple prediction logic based on technical analysis
            current_price = current_features['current_price']
            volatility = current_features['volatility']
            trend = current_features['price_change_pct']
            rsi = current_features['rsi']
            
            # Generate predictions using technical analysis rules
            predictions = {}
            
            # 1H prediction: small variation based on current trend
            pred_1h = current_price * (1 + trend * 0.1 / 100)  # 10% of current trend
            
            # 1D prediction: moderate variation based on RSI and trend
            if rsi > 70:  # Overbought
                pred_1d = current_price * (1 - volatility * 0.01)
            elif rsi < 30:  # Oversold
                pred_1d = current_price * (1 + volatility * 0.01)
            else:  # Neutral
                pred_1d = current_price * (1 + trend * 0.5 / 100)
            
            # 1W prediction: larger variation based on moving average convergence
            ma_signal = (current_features['ma_5'] - current_features['ma_20']) / current_features['ma_20']
            pred_1w = current_price * (1 + ma_signal + trend * 0.3 / 100)
            
            # Ensure positive prices
            predictions = {
                '1h': max(1.0, pred_1h),
                '1d': max(1.0, pred_1d),
                '1w': max(1.0, pred_1w)
            }
            
            # Skip storage for simple system - just return predictions
            
            return {
                'prediction_1h': predictions['1h'],
                'prediction_1d': predictions['1d'],
                'prediction_1w': predictions['1w'],
                'is_real_prediction': True,
                'processing_time': 0.5,
                'feature_count': len(current_features),
                'model_type': 'simple_technical_analysis',
                'contract': contract_info['symbol'],
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Simple prediction engine failed: {e}")
            raise Exception(f"Cannot generate simple predictions: {e}")
    
    def calculate_rsi(self, prices, period=14):
        """Calculate RSI using Wilder's Exponential Smoothing (industry standard)"""
        if len(prices) < period + 1:
            return 50  # Neutral
        
        # Use all available history for proper Wilder smoothing warm-up
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        
        # Wilder's initial average = simple mean of first `period` values
        avg_gain = gains[:period].mean()
        avg_loss = losses[:period].mean()
        
        # Apply Wilder's exponential smoothing for remaining bars
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def create_feature_template(self, external_data):
        """Create a standardized feature template with all possible external features"""
        template = {}
        
        # Define all possible external features with safe defaults
        external_feature_defaults = {
            'eia_data_quality': 0,
            'eia_supply_level': 50,
            'eia_supply_trend': 0,
            'fred_data_quality': 0,
            'fred_dollar_strength': 100,
            'fred_dollar_trend': 0,
            'fred_economic_stability': 70,
            'alpha_vantage_data_quality': 0,
            'alpha_vantage_volatility': 2.5,
            'alpha_vantage_trend_strength': 50,
            'alpha_vantage_momentum_score': 0,
            'finnhub_data_quality': 0,
            'finnhub_sector_strength': 50,
            'finnhub_sector_momentum': 0,
            'news_data_quality': 0,
            'news_market_buzz': 50,
            'news_sentiment_score': 0,
            'news_sentiment_momentum': 0,
            'news_bullish_ratio': 0.5,
            'news_uncertainty_score': 0,
            'news_forwardness_score': 0,
            'news_intensity_score': 0,
            'news_news_volume': 10,
            'usda_data_quality': 0,
            'usda_agricultural_impact': 50,
            'usda_corn_price_level': 5.0,
            'usda_biofuel_demand': 50,
            'noaa_data_quality': 0,
            'noaa_weather_impact': 30,
            'noaa_temperature_anomaly': 5,
            'noaa_seasonal_demand': 60,
            'geopolitical_data_quality': 0,
            'geopolitical_geo_risk_score': 0,
            'geopolitical_iran_articles': 0,
            'geopolitical_opec_articles': 0,
            'geopolitical_conflict_articles': 0,
        }
        
        # Start with defaults
        template.update(external_feature_defaults)
        
        # Override with actual data where available
        for source, data in external_data.items():
            if 'error' not in data:
                # Handle naming patterns
                if source.lower() == 'alpha_vantage':
                    prefixes = ['alpha_vantage']  # Consistent source key
                else:
                    prefixes = [source.lower()]
                
                for prefix in prefixes:
                    for key, value in data.items():
                        if key not in ['source', 'timestamp', 'error', 'quality'] and isinstance(value, (int, float)):
                            feature_name = f'{prefix}_{key}'
                            template[feature_name] = value
        
        return template
    
    def engineer_technical_features(self, wti_data):
        """Engineer technical features from WTI price data only"""
        open_values = wti_data['Open'] if 'Open' in wti_data.columns else wti_data['Close']
        open_series = self._safe_series(open_values, index=wti_data.index)
        close_series = self._safe_series(wti_data['Close'], index=wti_data.index)
        high_series = self._safe_series(wti_data['High'], index=wti_data.index)
        low_series = self._safe_series(wti_data['Low'], index=wti_data.index)
        volume_series = self._safe_series(wti_data['Volume'], index=wti_data.index)
        closes = close_series.to_numpy(dtype=float)

        # Calculate RSI once and reuse (avoid repeated recomputation).
        rsi_value = self.calculate_rsi(closes) if len(closes) >= 14 else 50

        # Use the date of the last bar in the window (not datetime.now())
        # so historical training rows get correct seasonal features.
        bar_date = wti_data.index[-1]
        latest_close = float(close_series.iloc[-1]) if not close_series.empty else 0.0
        latest_open = float(open_series.iloc[-1]) if not open_series.empty else latest_close
        latest_volume = float(volume_series.iloc[-1]) if not volume_series.empty else 0.0
        dollar_volume = latest_close * latest_volume if latest_close > 0 and latest_volume > 0 else 0.0

        ma_5 = float(close_series.tail(5).mean()) if len(close_series) >= 5 else latest_close
        ma_10 = float(close_series.tail(10).mean()) if len(close_series) >= 10 else latest_close
        ma_20 = float(close_series.tail(20).mean()) if len(close_series) >= 20 else latest_close
        ma_60 = float(close_series.tail(60).mean()) if len(close_series) >= 60 else ma_20
        trailing_20 = close_series.tail(20)
        trailing_60 = close_series.tail(60)
        price_range_20 = float(trailing_20.max() - trailing_20.min()) if len(trailing_20) >= 2 else 0.0
        price_range_60 = float(trailing_60.max() - trailing_60.min()) if len(trailing_60) >= 2 else 0.0
        log_returns = np.log(close_series.clip(lower=1e-9)).diff().replace([np.inf, -np.inf], np.nan).dropna()
        returns = close_series.pct_change().replace([np.inf, -np.inf], np.nan)
        true_range = pd.concat(
            [
                high_series - low_series,
                (high_series - close_series.shift(1)).abs(),
                (low_series - close_series.shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)

        positive_returns = returns.where(returns > 0)
        negative_returns = (-returns.where(returns < 0))
        volume_tail_20 = volume_series.tail(20)
        dollar_volume_series = close_series * volume_series
        latest_true_range = float(true_range.iloc[-1]) if not true_range.empty else 0.0
        range_pct = self._safe_ratio(latest_true_range, latest_close, default=0.0)
        prev_close = float(close_series.iloc[-2]) if len(close_series) > 1 else latest_close
        open_gap_pct = self._safe_ratio(latest_open - prev_close, prev_close, default=0.0)
        intraday_return = self._safe_ratio(latest_close - latest_open, latest_open, default=0.0)
        candle_body_to_range = self._safe_ratio(abs(latest_close - latest_open), latest_true_range, default=0.0)
        close_location_value = self._latest_close_location_value(open_series, high_series, low_series, close_series)
        upper_wick_pct = self._safe_ratio(float(high_series.iloc[-1]) - max(latest_open, latest_close), latest_close, default=0.0)
        lower_wick_pct = self._safe_ratio(min(latest_open, latest_close) - float(low_series.iloc[-1]), latest_close, default=0.0)
        obv_slope_10 = self._latest_obv_slope(close_series, volume_series, 10)
        return_skew_20 = self._latest_skewness(close_series, 20)
        volatility_5 = self._latest_volatility(close_series, 5)
        volatility_20 = self._latest_volatility(close_series, 20)
        volatility_60 = self._latest_volatility(close_series, 60)
        atr_pct_14 = self._latest_atr_percent(high_series, low_series, close_series, 14)

        features = {
            'current_price': latest_close,
            'price_change': float(close_series.iloc[-1] - close_series.iloc[-2]) if len(close_series) > 1 else 0.0,
            'price_change_pct': self._safe_pct_change_value(close_series, 1) * 100.0,
            'volume': latest_volume,
            'dollar_volume': float(dollar_volume),
            'open_gap_pct': open_gap_pct,
            'intraday_return': intraday_return,
            'range_pct': range_pct,
            'candle_body_to_range': candle_body_to_range,
            'close_location_value': close_location_value,
            'upper_wick_pct': upper_wick_pct,
            'lower_wick_pct': lower_wick_pct,

            # Moving averages and relative trend structure.
            'ma_5': ma_5,
            'ma_10': ma_10,
            'ma_20': ma_20,
            'ma_60': ma_60,
            'price_to_ma20': self._safe_ratio(latest_close, ma_20, default=1.0),
            'price_to_ma60': self._safe_ratio(latest_close, ma_60, default=1.0),
            'ma5_to_ma20': self._safe_ratio(ma_5, ma_20, default=1.0),
            'ma20_to_ma60': self._safe_ratio(ma_20, ma_60, default=1.0),

            # Multi-horizon momentum.
            'return_3': self._safe_pct_change_value(close_series, 3),
            'return_5': self._safe_pct_change_value(close_series, 5),
            'return_10': self._safe_pct_change_value(close_series, 10),
            'return_20': self._safe_pct_change_value(close_series, 20),
            'return_60': self._safe_pct_change_value(close_series, 60),
            'momentum_accel_5_20': self._safe_pct_change_value(close_series, 5) - self._safe_pct_change_value(close_series, 20),
            'trend_slope_5': self._latest_trend_slope(close_series, 5),
            'trend_slope_20': self._latest_trend_slope(close_series, 20),
            'trend_slope_60': self._latest_trend_slope(close_series, 60),
            'up_day_ratio_5': self._latest_directional_hit_rate(close_series, 5),
            'up_day_ratio_20': self._latest_directional_hit_rate(close_series, 20),
            'momentum_vol_ratio_5': self._safe_ratio(self._safe_pct_change_value(close_series, 5), volatility_20 * np.sqrt(5.0), default=0.0),
            'momentum_vol_ratio_20': self._safe_ratio(self._safe_pct_change_value(close_series, 20), max(volatility_60, 1e-9) * np.sqrt(20.0), default=0.0),

            # Regime and volatility structure.
            'volatility': self._latest_volatility(close_series, 10),
            'volatility_20': volatility_20,
            'volatility_60': volatility_60,
            'volatility_ratio_5_20': self._safe_ratio(volatility_5, volatility_20, default=1.0),
            'volatility_ratio_20_60': self._safe_ratio(volatility_20, volatility_60, default=1.0),
            'atr_pct_5': self._latest_atr_percent(high_series, low_series, close_series, 5),
            'atr_pct_14': atr_pct_14,
            'atr_pct_20': self._latest_atr_percent(high_series, low_series, close_series, 20),
            'range_to_atr14': self._safe_ratio(range_pct, atr_pct_14, default=1.0),
            'jump_abs_return_5': float(log_returns.abs().tail(5).max()) if not log_returns.empty else 0.0,
            'jump_abs_return_20': float(log_returns.abs().tail(20).max()) if not log_returns.empty else 0.0,
            'upside_vol_20': float(positive_returns.tail(20).std()) if positive_returns.tail(20).notna().any() else 0.0,
            'downside_vol_20': float(negative_returns.tail(20).std()) if negative_returns.tail(20).notna().any() else 0.0,
            'downside_upside_vol_ratio': self._safe_ratio(
                float(negative_returns.tail(20).std()) if negative_returns.tail(20).notna().any() else 0.0,
                float(positive_returns.tail(20).std()) if positive_returns.tail(20).notna().any() else 0.0,
                default=1.0,
            ),
            'return_skew_20': return_skew_20,

            # Price location and breakout context.
            'price_position': ((latest_close - float(trailing_20.min())) / price_range_20) if len(trailing_20) >= 2 and price_range_20 > 1e-9 else 0.5,
            'price_position_60': ((latest_close - float(trailing_60.min())) / price_range_60) if len(trailing_60) >= 2 and price_range_60 > 1e-9 else 0.5,
            'drawdown_20': self._latest_drawdown(close_series, 20),
            'drawdown_60': self._latest_drawdown(close_series, 60),
            'distance_from_low_20': self._latest_distance_from_low(close_series, 20),
            'distance_from_low_60': self._latest_distance_from_low(close_series, 60),
            'price_zscore_10': self._latest_rolling_zscore(close_series, 10),
            'price_zscore_20': self._latest_rolling_zscore(close_series, 20),
            'price_zscore_60': self._latest_rolling_zscore(close_series, 60),
            'high_low_ratio': self._safe_ratio(float(high_series.iloc[-1]), float(low_series.iloc[-1]), default=1.0),

            # Technical indicators.
            'rsi': rsi_value,
            'rsi_oversold': 1 if rsi_value < 30 else 0,
            'rsi_overbought': 1 if rsi_value > 70 else 0,
            'rsi_slope_5': float(rsi_value - (self.calculate_rsi(closes[:-5]) if len(closes) > 19 else rsi_value)),

            # Liquidity and participation.
            'volume_ratio': self._safe_ratio(latest_volume, float(volume_tail_20.mean()) if not volume_tail_20.empty else latest_volume, default=1.0),
            'volume_trend': self._safe_ratio(float(volume_series.tail(5).mean()), float(volume_tail_20.mean()) if not volume_tail_20.empty else latest_volume, default=1.0),
            'volume_zscore_20': self._latest_rolling_zscore(volume_series.replace(0.0, np.nan).fillna(0.0), 20),
            'dollar_volume_zscore_20': self._latest_rolling_zscore(dollar_volume_series, 20),
            'obv_slope_10': obv_slope_10,
            'signed_volume_pressure': intraday_return * self._safe_ratio(latest_volume, float(volume_tail_20.mean()) if not volume_tail_20.empty else latest_volume, default=1.0),
            'gap_reversal_pressure': open_gap_pct - intraday_return,

            # Time-based features from the BAR DATE (not now) for correct seasonality learning.
            'month': bar_date.month,
            'quarter': (bar_date.month - 1) // 3 + 1,
            'day_of_week': bar_date.weekday(),
            'is_quarter_end': 1 if bar_date.month in [3, 6, 9, 12] else 0,
        }

        # Bollinger Bands (20-bar, 2 std dev).
        if len(trailing_20) >= 5:
            bb_middle = float(trailing_20.mean())
            bb_std = float(trailing_20.std())
            bb_upper = bb_middle + (2.0 * bb_std)
            bb_lower = bb_middle - (2.0 * bb_std)
            band_width = bb_upper - bb_lower
            features['bb_position'] = ((latest_close - bb_lower) / band_width) if band_width > 1e-9 else 0.5
            features['bb_width'] = (band_width / bb_middle) if bb_middle > 0 else 0.0
        else:
            features['bb_position'] = 0.5
            features['bb_width'] = 0.0

        # MACD (12, 26, 9) - compute EMA series once and reuse.
        if len(close_series) >= 26:
            ema_12_series = close_series.ewm(span=12, adjust=False).mean()
            ema_26_series = close_series.ewm(span=26, adjust=False).mean()
            macd_series = ema_12_series - ema_26_series
            macd_line = float(macd_series.iloc[-1])
            signal_line = float(macd_series.ewm(span=9, adjust=False).mean().iloc[-1])
            features['macd'] = macd_line
            features['macd_signal'] = signal_line
            features['macd_histogram'] = macd_line - signal_line
            features['macd_crossover'] = 1 if macd_line > signal_line else -1
        else:
            features['macd'] = 0.0
            features['macd_signal'] = 0.0
            features['macd_histogram'] = 0.0
            features['macd_crossover'] = 0

        # RSI divergence uses direction, not raw magnitude, to stay stable across timeframes.
        if len(closes) >= 20:
            price_trend = 1 if closes[-1] > closes[-5] else -1
            rsi_prev = self.calculate_rsi(closes[:-5]) if len(closes) > 19 else rsi_value
            rsi_trend = 1 if rsi_value > rsi_prev else -1
            features['rsi_divergence'] = 1 if price_trend != rsi_trend else -1
        else:
            features['rsi_divergence'] = 0

        for key, value in list(features.items()):
            numeric_value = pd.to_numeric(value, errors='coerce')
            features[key] = float(numeric_value) if not pd.isna(numeric_value) and np.isfinite(float(numeric_value)) else 0.0

        return features

    def detect_market_regime(self, data_window):
        """
        Detect current market regime (Volatility State)
        Returns: 'LOW_VOLATILITY', 'HIGH_VOLATILITY', or 'NORMAL'
        """
        try:
            if len(data_window) < 20:
                return 'NORMAL'
                
            closes = data_window['Close'].values
            highs = data_window['High'].values
            lows = data_window['Low'].values
            
            # Calculate ATR (Average True Range)
            tr_list = []
            for i in range(1, len(closes)):
                hl = highs[i] - lows[i]
                hc = abs(highs[i] - closes[i-1])
                lc = abs(lows[i] - closes[i-1])
                tr_list.append(max(hl, hc, lc))
            
            atr = np.mean(tr_list[-14:]) if len(tr_list) >= 14 else np.mean(tr_list)
            
            # ATR as percentage of price
            atr_pct = (atr / closes[-1]) * 100
            
            # Calculate Bollinger Band Width
            bb_std = pd.Series(closes).rolling(window=20).std().iloc[-1]
            bb_mid = pd.Series(closes).rolling(window=20).mean().iloc[-1]
            bb_width = (4 * bb_std) / bb_mid * 100
            
            logger.info(f"Market Regime Metrics: ATR={atr_pct:.2f}%, BB_Width={bb_width:.2f}%")
            
            # Define Regimes
            # High Volatility: Price moving > 1.5% avg daily range OR Bands are very wide
            if atr_pct > 1.5 or bb_width > 5.0:
                return 'HIGH_VOLATILITY'
            # Low Volatility: Price moving < 0.8% avg daily range (Tight consolidation)
            elif atr_pct < 0.8 and bb_width < 2.0:
                return 'LOW_VOLATILITY'
            else:
                return 'NORMAL'
                
        except Exception as e:
            logger.warning(f"Failed to detect market regime: {e}")
            return 'NORMAL'

    def get_multi_horizon_predictions(self):
        """Generate multi-horizon predictions using real ML models - DUAL PIPELINE"""
        logger.info("Starting Premium WTI multi-horizon prediction engine...")
        logger.info("🔬 Dual-Pipeline: Hourly Data (1H) + Daily Data (1D/1W)")
        start_time = time.time()
        self._refresh_contract_if_needed()
        timings = {}
        cache_stats = {'hits': 0, 'misses': 0}

        # Enforce strict premium API readiness before prediction
        self._validate_required_api_keys()
        
        # Fetch all 7 external data sources (EIA, FRED, Alpha Vantage, Finnhub, NewsAPI, USDA, NOAA)
        external_start = time.perf_counter()
        external_data = self.get_external_data_sources()
        self._validate_external_data_sources(external_data)
        logger.info(f"Loaded {len(external_data)} external data sources")
        timings['external_data_fetch_seconds'] = float(time.perf_counter() - external_start)
        
        # Create standardized external feature set from real API data
        external_features_dict = self.create_feature_template(external_data)
        
        try:
            # === PIPELINE A: HOURLY DATA (For 1H Prediction) ===
            logger.info("--- PIPELINE A: HOURLY DATA PROCESSING ---")
            hourly_pipeline_start = time.perf_counter()
            hourly_data = self.get_wti_hourly_data()
            hourly_lookback = max(24, int(self.hourly_feature_lookback_bars))
            
            # If no hourly data, fallback to daily approximation will be handled in Pipeline B
            hourly_model_package = None
            if hourly_data is not None and len(hourly_data) > 30:
                logger.info("Engineering hourly features...")
                hourly_features = []
                hourly_targets = []
                
                # Create hourly training set
                for i in range(hourly_lookback - 1, len(hourly_data) - 2):
                    try:
                        window_data = hourly_data.iloc[i - hourly_lookback + 1:i + 1]
                        point_features = self.engineer_technical_features(window_data)
                        if self.use_external_features_in_training:
                            # Optional: merge external snapshots into hourly rows (off by default to avoid leakage/noise).
                            point_features.update(external_features_dict)
                        
                        # Target: Close price of the NEXT hour
                        # Ensure we don't go out of bounds
                        target_idx = min(i+1, len(hourly_data)-1)
                        target_price = hourly_data['Close'].iloc[target_idx]
                        
                        hourly_features.append(point_features)
                        hourly_targets.append(target_price)
                    except Exception as e:
                        logger.debug(f"Skipping hourly sample index={i} due to feature error: {e}")
                        continue
                
                if len(hourly_features) > 10:
                    features_df_1h = pd.DataFrame(hourly_features)
                    features_df_1h['target_1h'] = hourly_targets

                    if len(features_df_1h) > self.max_hourly_training_samples:
                        features_df_1h = features_df_1h.tail(self.max_hourly_training_samples).reset_index(drop=True)
                    
                    logger.info(f"Training specialized 1H models on {len(features_df_1h)} intraday samples...")
                    hourly_model_package, was_cached = self._train_or_reuse_model_package(
                        features_df_1h,
                        'target_1h',
                        '1h',
                        target_mode='price',
                    )
                    if was_cached:
                        cache_stats['hits'] += 1
                    else:
                        cache_stats['misses'] += 1
                    
                    logger.info("✅ Trained specialized 1H models on real intraday data")
                else:
                    logger.warning("Insufficient hourly training samples, skipping 1H pipeline")
            else:
                logger.warning("No hourly data available, skipping 1H pipeline")
            timings['hourly_pipeline_seconds'] = float(time.perf_counter() - hourly_pipeline_start)

            # === PIPELINE B: DAILY DATA (For 1D/1W Prediction) ===
            logger.info("--- PIPELINE B: DAILY DATA PROCESSING ---")
            daily_pipeline_start = time.perf_counter()
            logger.info("Fetching WTI historical data...")
            wti_data = self.get_wti_historical_data(period=self.daily_training_period, interval="1d")
            daily_horizons = ['1d', '1w']
            daily_lookbacks = {horizon: self._get_daily_feature_lookback(horizon) for horizon in daily_horizons}
            max_daily_lookback = max(daily_lookbacks.values())
            
            # Use ONLY technical features for consistent ML training
            logger.info("Engineering daily features...")
            market_context_map = self.build_market_context_feature_map(wti_data)
            historical_external_map = self.build_historical_external_feature_map(wti_data)
            historical_external_defaults = self._historical_external_feature_defaults()
            
            # Process each historical point with consistent feature engineering
            daily_features_by_horizon = {horizon: [] for horizon in daily_horizons}
            daily_targets = {'1d': [], '1w': []}
            
            for i in range(max_daily_lookback - 1, len(wti_data) - 5):  # Leave room for targets
                try:
                    current_close = float(wti_data['Close'].iloc[i])
                    target_prices = {
                        '1d': float(wti_data['Close'].iloc[i + 1]),
                        '1w': float(wti_data['Close'].iloc[i + 5]),
                    }
                    for horizon in daily_horizons:
                        lookback = daily_lookbacks[horizon]
                        if i < lookback - 1:
                            continue
                        window_data = wti_data.iloc[i - lookback + 1:i + 1]
                        point_features = self.engineer_technical_features(window_data)
                        row_key = self._date_feature_key(window_data.index[-1])
                        point_features.update(market_context_map.get(row_key, {}))
                        point_features.update(historical_external_map.get(row_key, historical_external_defaults))
                        baseline_return = self._compute_target_baseline_return(window_data['Close'], horizon)
                        point_features[f'baseline_return_{horizon}'] = baseline_return
                        if self.use_external_features_in_training:
                            # Optional: merge external snapshots into daily rows (off by default to avoid leakage/noise).
                            point_features.update(external_features_dict)
                        daily_features_by_horizon[horizon].append(point_features)
                        daily_targets[horizon].append(
                            self._encode_target_value(
                                current_close,
                                target_prices[horizon],
                                self.daily_target_mode,
                                baseline_return=baseline_return,
                            )
                        )
                    
                except Exception as exc:
                    logger.debug(f"Skipping daily prediction row due to: {exc}")
                    continue

            features_df_daily_by_horizon = {}
            for horizon in daily_horizons:
                horizon_df = pd.DataFrame(daily_features_by_horizon[horizon])
                horizon_df[f'target_{horizon}'] = daily_targets[horizon]
                features_df_daily_by_horizon[horizon] = horizon_df
                logger.info(
                    f"Created {len(horizon_df.columns)} features for {horizon} model "
                    f"using lookback={daily_lookbacks[horizon]} bars and rows={len(horizon_df)}"
                )
            
            # === PREDICTION GENERATION ===
            predictions = {}
            prediction_intervals = {}
            all_scores = {}
            horizon_backtests = {}
            horizon_confidence = {}
            horizon_drift_scores = {}
            horizon_fallbacks = {'1h': False, '1d': False, '1w': False}
            horizon_model_counts = {'1h': 0, '1d': 0, '1w': 0}
            reference_price = self._get_prediction_reference_price(wti_data['Close'].iloc[-1])
            total_model_count = 0

            # Calculate current daily features per horizon.
            current_features_by_horizon = {}
            for horizon in daily_horizons:
                lookback = daily_lookbacks[horizon]
                current_window = wti_data.iloc[-lookback:]
                current_features_dict = self.engineer_technical_features(current_window)
                current_key = self._date_feature_key(current_window.index[-1])
                current_features_dict.update(market_context_map.get(current_key, {}))
                current_features_dict.update(historical_external_map.get(current_key, historical_external_defaults))
                current_features_dict[f'baseline_return_{horizon}'] = self._compute_target_baseline_return(current_window['Close'], horizon)
                if self.use_external_features_in_training:
                    # Keep train/inference feature schema aligned when external training features are enabled.
                    current_features_dict.update(external_features_dict)
                current_features_by_horizon[horizon] = current_features_dict
            
            # DETECT MARKET REGIME
            market_regime = self.detect_market_regime(wti_data.iloc[-max_daily_lookback:])
            logger.info(f"📊 Current Market Regime: {market_regime}")
            
            horizon_models = {}
            if hourly_model_package:
                horizon_models['1h'] = hourly_model_package
                horizon_backtests['1h'] = hourly_model_package.get('diagnostics', {}).get('latest_fold_backtest', {})
            
            for horizon in daily_horizons:
                try:
                    target_col = f'target_{horizon}'
                    features_df_daily = features_df_daily_by_horizon[horizon]
                    model_package, was_cached = self._train_or_reuse_model_package(
                        features_df_daily,
                        target_col,
                        horizon,
                        target_mode=self.daily_target_mode,
                    )
                    if was_cached:
                        cache_stats['hits'] += 1
                    else:
                        cache_stats['misses'] += 1

                    models = model_package['models']
                    scores = model_package['scores']
                    scaler = model_package['scaler']
                    selector = model_package['selector']
                    all_feature_names = model_package['all_feature_names']
                    horizon_backtests[horizon] = model_package.get('diagnostics', {}).get('latest_fold_backtest', {})
                    
                    if models:
                        horizon_models[horizon] = model_package
                    
                    # Prepare input
                    current_features_dict = current_features_by_horizon[horizon]
                    current_features = pd.DataFrame([current_features_dict])
                    current_features = self._apply_feature_defaults(current_features, all_feature_names)
                    
                    # Transform and Predict
                    current_features_selected = selector.transform(current_features[all_feature_names])
                    current_features_scaled = scaler.transform(current_features_selected)
                    drift_score = self._compute_feature_drift_score(current_features_scaled)
                    horizon_drift_scores[horizon] = drift_score
                    target_mode = model_package.get('target_mode', 'price')
                    target_baseline_return = current_features_dict.get(f'baseline_return_{horizon}', 0.0)
                    
                    h_preds = []
                    h_weights = []
                    model_pred_map = {}
                    direction_scores = model_package.get('diagnostics', {}).get('model_direction_scores', {})
                    
                    for name, model in models.items():
                        raw_pred = model.predict(current_features_scaled)[0]
                        # BUG8 FIX: skip NaN predictions (can occur with NaN input features)
                        if np.isnan(raw_pred):
                            logger.warning(f"Model {name} returned NaN prediction — skipping")
                            continue
                        pred = self._decode_target_value(
                            reference_price,
                            raw_pred,
                            target_mode,
                            baseline_return=target_baseline_return,
                        )
                        
                        # BASE WEIGHT (Validation Score)
                        weight = max(0.3, min(1.0, scores[name]))
                        
                        # DYNAMIC REGIME WEIGHTING
                        # High Volatility -> Trust Trend Followers (Trees)
                        if market_regime == 'HIGH_VOLATILITY':
                            if any(x in name.lower() for x in ['xgboost', 'lightgbm', 'gradient']):
                                weight *= 1.5
                                logger.debug(f"🚀 Boosting {name} weight for High Volatility")
                        
                        # Low Volatility -> Trust Mean Reversion (Forests/Ensembles)
                        elif market_regime == 'LOW_VOLATILITY':
                            if any(x in name.lower() for x in ['random_forest', 'extra_trees', 'bagging']):
                                weight *= 1.5
                                logger.debug(f"🛡️ Boosting {name} weight for Low Volatility")
                        
                        h_preds.append(pred)
                        h_weights.append(weight)
                        model_pred_map[name] = float(pred)
                        horizon_model_counts[horizon] += 1
                        total_model_count += 1

                    if not h_preds:
                        logger.warning(f"No valid model outputs for {horizon}; using current price fallback")
                        horizon_fallbacks[horizon] = True
                        predictions[horizon] = reference_price
                        all_scores[horizon] = 0.0
                        prediction_intervals[horizon] = {
                            'lower': float(reference_price),
                            'upper': float(reference_price),
                            'std': 0.0,
                        }
                        continue

                    final_pred = np.average(h_preds, weights=h_weights)
                    final_pred, stabilization_meta = self._stabilize_ensemble_prediction(
                        reference_price,
                        final_pred,
                        model_pred_map,
                        scores,
                        direction_scores,
                    )
                    backtest_metrics = horizon_backtests.get(horizon, {})
                    lookback = daily_lookbacks[horizon]
                    drift_window = wti_data.iloc[-lookback:]
                    drift_challenger = self._compute_drift_challenger(drift_window['Close'], reference_price, horizon)
                    final_pred, challenger_blend = self._blend_with_drift_challenger(
                        reference_price,
                        final_pred,
                        drift_challenger,
                        backtest_metrics,
                        drift_score,
                        stabilization_meta.get('direction_consensus', 1.0),
                        horizon=horizon,
                    )
                    predictions[horizon] = final_pred
                    all_scores[horizon] = np.mean(list(scores.values()))

                    # Uncertainty from model dispersion, calibrated by historical realized errors.
                    if len(h_preds) >= 2:
                        pred_std = float(np.std(h_preds))
                    else:
                        pred_std = 0.0
                    ci_margin = self._calibrated_interval_margin(
                        horizon,
                        pred_std,
                        reference_price,
                        backtest_metrics,
                        drift_score,
                    )
                    prediction_intervals[horizon] = {
                        'lower': float(final_pred - ci_margin),
                        'upper': float(final_pred + ci_margin),
                        'std': float(pred_std),
                        'calibrated_margin': float(ci_margin),
                        'direction_consensus': stabilization_meta.get('direction_consensus'),
                        'stabilization_shrink': stabilization_meta.get('shrink_factor'),
                        'drift_challenger': float(drift_challenger),
                        'drift_challenger_blend': float(challenger_blend),
                    }
                    horizon_confidence[horizon] = self._compose_horizon_confidence(
                        all_scores[horizon],
                        reference_price,
                        prediction_intervals[horizon],
                        drift_score,
                        backtest_metrics,
                    )
                    logger.info(f"✅ {horizon} Prediction: ${final_pred:.2f} (Regime: {market_regime})")
                    
                except Exception as e:
                    logger.error(f"Failed to predict {horizon}: {e}")
                    horizon_fallbacks[horizon] = True
                    predictions[horizon] = reference_price  # Fallback
                    all_scores[horizon] = 0.0
                    horizon_drift_scores[horizon] = 0.0
                    prediction_intervals[horizon] = {
                        'lower': float(reference_price),
                        'upper': float(reference_price),
                        'std': 0.0,
                        'calibrated_margin': 0.0,
                    }
                    horizon_confidence[horizon] = self.confidence_floor
            
            # 2. 1H Prediction (Using Pipeline A if successful, else Pipeline B Fallback)
            if hourly_model_package:
                try:
                    # Generate features from latest HOURLY data
                    current_hourly_window = hourly_data.iloc[-hourly_lookback:]
                    current_hourly_features_dict = self.engineer_technical_features(current_hourly_window)
                    if self.use_external_features_in_training:
                        # Keep train/inference feature schema aligned when external training features are enabled.
                        current_hourly_features_dict.update(external_features_dict)
                    current_hourly_features = pd.DataFrame([current_hourly_features_dict])
                    
                    all_hourly_feats = hourly_model_package['all_feature_names']
                    current_hourly_features = self._apply_feature_defaults(current_hourly_features, all_hourly_feats)
                    
                    # Transform
                    h_features_selected = hourly_model_package['selector'].transform(current_hourly_features[all_hourly_feats])
                    h_features_scaled = hourly_model_package['scaler'].transform(h_features_selected)
                    h1_drift_score = self._compute_feature_drift_score(h_features_scaled)
                    horizon_drift_scores['1h'] = h1_drift_score
                    
                    # Predict
                    h1_preds = []
                    h1_weights = []
                    model_pred_map = {}
                    direction_scores = hourly_model_package.get('diagnostics', {}).get('model_direction_scores', {})
                    for name, model in hourly_model_package['models'].items():
                        pred = model.predict(h_features_scaled)[0]
                        if np.isnan(pred):
                            logger.warning(f"Model {name} returned NaN prediction — skipping")
                            continue
                        h1_preds.append(pred)
                        h1_weights.append(max(0.3, min(1.0, hourly_model_package['scores'][name])))
                        model_pred_map[name] = float(pred)
                        horizon_model_counts['1h'] += 1
                        total_model_count += 1
                    
                    # FIX #3-4: Guard against empty pred list before averaging
                    if len(h1_preds) > 0 and len(h1_weights) > 0:
                        predictions['1h'] = np.average(h1_preds, weights=h1_weights)
                        predictions['1h'], stabilization_meta = self._stabilize_ensemble_prediction(
                            reference_price,
                            predictions['1h'],
                            model_pred_map,
                            hourly_model_package['scores'],
                            direction_scores,
                        )
                        all_scores['1h'] = np.mean(list(hourly_model_package['scores'].values()))
                        if len(h1_preds) >= 2:
                            h1_std = float(np.std(h1_preds))
                        else:
                            h1_std = 0.0
                        h1_backtest = horizon_backtests.get('1h', {})
                        h1_margin = self._calibrated_interval_margin(
                            '1h',
                            h1_std,
                            reference_price,
                            h1_backtest,
                            h1_drift_score,
                        )
                        prediction_intervals['1h'] = {
                            'lower': float(predictions['1h'] - h1_margin),
                            'upper': float(predictions['1h'] + h1_margin),
                            'std': h1_std,
                            'calibrated_margin': float(h1_margin),
                            'direction_consensus': stabilization_meta.get('direction_consensus'),
                            'stabilization_shrink': stabilization_meta.get('shrink_factor'),
                        }
                        horizon_confidence['1h'] = self._compose_horizon_confidence(
                            all_scores['1h'],
                            reference_price,
                            prediction_intervals['1h'],
                            h1_drift_score,
                            h1_backtest,
                        )
                        logger.info(f"✅ 1H Prediction using REAL HOURLY data: ${predictions['1h']:.2f}")
                    else:
                        raise ValueError("No valid hourly predictions generated")
                except Exception as e:
                    logger.warning(f"Hourly pipeline prediction failed: {e}")
                    # FIX #4: Multiple fallback levels with guards
                    horizon_fallbacks['1h'] = True
                    all_scores['1h'] = 0.0
                    horizon_drift_scores['1h'] = 0.0
                    if '1d' in predictions and isinstance(predictions['1d'], (int, float)) and predictions['1d'] > 0:
                        predictions['1h'] = reference_price + (predictions['1d'] - reference_price) * 0.1
                        prediction_intervals['1h'] = {
                            'lower': float(min(predictions['1h'], reference_price)),
                            'upper': float(max(predictions['1h'], reference_price)),
                            'std': float(abs(predictions['1h'] - reference_price) / 1.64),
                            'calibrated_margin': float(abs(predictions['1h'] - reference_price)),
                        }
                        horizon_confidence['1h'] = self.confidence_floor
                        logger.info(f"1H: Using 1D fallback: ${predictions['1h']:.2f}")
                    elif '1w' in predictions and isinstance(predictions['1w'], (int, float)) and predictions['1w'] > 0:
                        predictions['1h'] = reference_price + (predictions['1w'] - reference_price) * 0.05
                        prediction_intervals['1h'] = {
                            'lower': float(min(predictions['1h'], reference_price)),
                            'upper': float(max(predictions['1h'], reference_price)),
                            'std': float(abs(predictions['1h'] - reference_price) / 1.64),
                            'calibrated_margin': float(abs(predictions['1h'] - reference_price)),
                        }
                        horizon_confidence['1h'] = self.confidence_floor
                        logger.info(f"1H: Using 1W fallback: ${predictions['1h']:.2f}")
                    else:
                        predictions['1h'] = reference_price
                        prediction_intervals['1h'] = {
                            'lower': float(reference_price),
                            'upper': float(reference_price),
                            'std': 0.0,
                            'calibrated_margin': 0.0,
                        }
                        horizon_confidence['1h'] = self.confidence_floor
                        logger.error("❌ All horizons failed - using current price for 1H")
            else:
                logger.warning("⚠️ Using daily fallback for 1H prediction (insufficient hourly data)")
                # FIX #4: Guard against missing 1d before using it
                horizon_fallbacks['1h'] = True
                all_scores['1h'] = 0.0
                horizon_drift_scores['1h'] = 0.0
                if '1d' in predictions and isinstance(predictions['1d'], (int, float)) and predictions['1d'] > 0:
                    predictions['1h'] = reference_price + (predictions['1d'] - reference_price) * 0.1
                    prediction_intervals['1h'] = {
                        'lower': float(min(predictions['1h'], reference_price)),
                        'upper': float(max(predictions['1h'], reference_price)),
                        'std': float(abs(predictions['1h'] - reference_price) / 1.64),
                        'calibrated_margin': float(abs(predictions['1h'] - reference_price)),
                    }
                    horizon_confidence['1h'] = self.confidence_floor
                    logger.info(f"1H: Using 1D fallback: ${predictions['1h']:.2f}")
                elif '1w' in predictions and isinstance(predictions['1w'], (int, float)) and predictions['1w'] > 0:
                    predictions['1h'] = reference_price + (predictions['1w'] - reference_price) * 0.05
                    prediction_intervals['1h'] = {
                        'lower': float(min(predictions['1h'], reference_price)),
                        'upper': float(max(predictions['1h'], reference_price)),
                        'std': float(abs(predictions['1h'] - reference_price) / 1.64),
                        'calibrated_margin': float(abs(predictions['1h'] - reference_price)),
                    }
                    horizon_confidence['1h'] = self.confidence_floor
                    logger.info(f"1H: Using 1W fallback: ${predictions['1h']:.2f}")
                else:
                    predictions['1h'] = reference_price  # Last resort
                    prediction_intervals['1h'] = {
                        'lower': float(reference_price),
                        'upper': float(reference_price),
                        'std': 0.0,
                        'calibrated_margin': 0.0,
                    }
                    horizon_confidence['1h'] = self.confidence_floor
                    logger.error("❌ No horizon predictions available - using current price for 1H")

            # Ensure all horizons have uncertainty fields
            for horizon in ['1h', '1d', '1w']:
                if horizon not in prediction_intervals:
                    prediction_intervals[horizon] = {
                        'lower': float(predictions[horizon]),
                        'upper': float(predictions[horizon]),
                        'std': 0.0,
                        'calibrated_margin': 0.0,
                    }
                if horizon not in horizon_drift_scores:
                    horizon_drift_scores[horizon] = 0.0
                if horizon not in horizon_confidence:
                    horizon_confidence[horizon] = self._compose_horizon_confidence(
                        all_scores.get(horizon, 0.0),
                        reference_price,
                        prediction_intervals[horizon],
                        horizon_drift_scores[horizon],
                        horizon_backtests.get(horizon, {}),
                    )
            horizon_quality = {
                horizon: self._assess_horizon_quality(
                    horizon,
                    horizon_confidence.get(horizon, 0.0),
                    horizon_drift_scores.get(horizon, 0.0),
                    horizon_backtests.get(horizon, {}),
                )
                for horizon in ['1h', '1d', '1w']
            }
            quality_qualified_horizons = [
                horizon for horizon, quality in horizon_quality.items()
                if isinstance(quality, dict) and quality.get('qualified')
            ]
            timings['daily_pipeline_seconds'] = float(time.perf_counter() - daily_pipeline_start)
            
            
            processing_time = time.time() - start_time
            
            # Calculate percentage changes using the live contract quote baseline.
            percentage_changes = {}
            horizons = ['1h', '1d', '1w']
            for horizon in horizons:
                change_pct = ((predictions[horizon] - reference_price) / reference_price) * 100 if reference_price > 0 else 0.0
                percentage_changes[horizon] = change_pct
            
            # Store predictions with timestamp
            timestamp = self._current_timestamp_iso()
            # Get feature count from first available horizon model
            first_horizon = next(iter(horizon_models.values()), None)
            feature_count = len(first_horizon['selected_features']) if first_horizon else 0
            has_any_fallback = any(horizon_fallbacks.values())
            has_critical_fallback = horizon_fallbacks.get('1d', False) or horizon_fallbacks.get('1w', False)
            
            prediction_record = {
                'schema_version': 2,
                'timestamp': timestamp,
                'predictions': predictions,
                'prediction_intervals': prediction_intervals,
                'horizon_confidence': horizon_confidence,
                'horizon_drift_scores': horizon_drift_scores,
                'percentage_changes': percentage_changes,
                'current_price': reference_price,
                'processing_time': processing_time,
                'feature_count': feature_count,
                'model_count': total_model_count,
                # BUG23 FIX: Proper division guard for data_quality_score calculation
                'data_quality_score': min(100, sum(data.get('data_quality', 0) for data in external_data.values()) / max(1, len(external_data))) if external_data else 0,
                'is_real_prediction': not has_critical_fallback,
                'is_full_real_prediction': not has_any_fallback,
                'fallbacks': horizon_fallbacks,
                'horizon_quality': horizon_quality,
                'quality_qualified_horizons': quality_qualified_horizons,
                'external_data_sources': len(external_data),
                'premium_features': True,
                'pipeline_timings': timings,
                'cache_stats': cache_stats,
                'horizon_backtests': horizon_backtests,
                'market_data_sources': copy.deepcopy(self._market_source_info),
                'contract_metadata': {
                    'contract_symbol': self.contract_symbol,
                    'quote_symbol': self.yfinance_symbol,
                    'history_symbol': self.history_symbol,
                },
                'geopolitical_risk': external_data.get('geopolitical', {}),
                'scenario_analysis': self.get_geopolitical_scenario_analysis(
                    external_data.get('geopolitical', {}),
                    reference_price,
                ),
            }
            
            # Store in main predictions file
            self.stored_predictions[timestamp] = prediction_record
            self._save_predictions()
            
            # Store in horizon-specific files
            for horizon in horizons:
                horizon_data = getattr(self, f'predictions_{horizon}')
                horizon_confidence_pct = float(horizon_confidence.get(horizon, all_scores.get(horizon, 0.5) * 100.0))
                horizon_interval = prediction_intervals.get(horizon, {})
                horizon_data[timestamp] = {
                    'timestamp': timestamp,
                    'prediction': predictions[horizon],
                    'percentage_change': percentage_changes[horizon],
                    'current_price': reference_price,
                    'confidence': horizon_confidence_pct,
                    'drift_score': float(horizon_drift_scores.get(horizon, 0.0)),
                    'interval_lower': float(horizon_interval.get('lower', predictions[horizon])),
                    'interval_upper': float(horizon_interval.get('upper', predictions[horizon])),
                    'interval_std': float(horizon_interval.get('std', 0.0)),
                    'model_count': horizon_model_counts.get(horizon, 0),
                    'processing_time': processing_time
                }
                self._save_horizon_predictions(horizon, horizon_data)
            
            # Store current actual price with the latest observed contract volume.
            self.store_actual_price(reference_price, self.contract_info.get('volume'))
            
            logger.info(f"Premium multi-horizon predictions completed in {processing_time:.2f}s")
            logger.info(f"1H: {predictions['1h']:.2f} ({percentage_changes['1h']:+.2f}%)")
            logger.info(f"1D: {predictions['1d']:.2f} ({percentage_changes['1d']:+.2f}%)")
            logger.info(f"1W: {predictions['1w']:.2f} ({percentage_changes['1w']:+.2f}%)")
            logger.info(f"Diagnostics: cache hits={cache_stats['hits']}, misses={cache_stats['misses']}")

            self.latest_diagnostics = {
                'timings': timings,
                'cache_stats': cache_stats,
                'horizon_backtests': horizon_backtests,
            }
            
            return prediction_record
            
        except Exception as e:
            logger.error(f"Premium prediction engine failed: {e}")
            raise Exception(f"Cannot generate real predictions: {e}")
    
    def store_actual_price(self, price, volume=None, force=False):
        """Store actual price with dedupe/session guards so closed-market heartbeats do not pollute evaluation."""
        timestamp = self._current_timestamp_iso()
        new_time = self._safe_parse_iso(timestamp)
        normalized_volume = int(volume) if volume is not None else 0

        last_items = self._sorted_time_items(self.stored_actual_prices)
        if last_items:
            last_timestamp, last_row = last_items[-1]
            last_time = self._safe_parse_iso(last_timestamp)
            last_price = last_row.get('price') if isinstance(last_row, dict) else None
            last_volume = last_row.get('volume') if isinstance(last_row, dict) else 0
            same_quote = self._prices_match(last_price, last_volume, price, normalized_volume)

            gap_seconds = None
            if new_time is not None and last_time is not None:
                gap_seconds = (new_time - last_time).total_seconds()

            if not force and same_quote:
                if not self._is_cme_cl_session_open():
                    return False
                if gap_seconds is not None and gap_seconds < self.actual_quote_heartbeat_seconds:
                    return False

        self.stored_actual_prices[timestamp] = {
            'timestamp': timestamp,
            'price': float(price),
            'volume': normalized_volume,
        }
        self._save_actual_prices()
        return True
    
    def calculate_prediction_accuracy(self):
        """Calculate prediction accuracy from stored data"""
        logger.info("Calculating prediction accuracy...")
        
        accuracy_metrics = {
            'schema_version': 2,
            'overall': {
                'total_predictions': 0,
                'correct_directions': 0,
                'direction_accuracy': 0,
                'mae': 0,
                'rmse': 0,
                'mape': 0,
                'interval_coverage': 0,
            },
            '1h': {'total_predictions': 0, 'correct_directions': 0, 'direction_accuracy': 0, 'mae': 0, 'rmse': 0, 'mape': 0, 'avg_strategy_return_pct': 0, 'sharpe_like': 0, 'interval_coverage': 0},
            '1d': {'total_predictions': 0, 'correct_directions': 0, 'direction_accuracy': 0, 'mae': 0, 'rmse': 0, 'mape': 0, 'avg_strategy_return_pct': 0, 'sharpe_like': 0, 'interval_coverage': 0},
            '1w': {'total_predictions': 0, 'correct_directions': 0, 'direction_accuracy': 0, 'mae': 0, 'rmse': 0, 'mape': 0, 'avg_strategy_return_pct': 0, 'sharpe_like': 0, 'interval_coverage': 0}
        }
        
        # Calculate accuracy for each horizon
        for horizon in ['1h', '1d', '1w']:
            horizon_data = getattr(self, f'predictions_{horizon}')
            
            if len(horizon_data) < 2:
                continue
            
            horizon_accuracy = self._calculate_horizon_accuracy(horizon_data, horizon)
            accuracy_metrics[horizon] = horizon_accuracy
        
        # Calculate overall accuracy
        total_predictions = sum(accuracy_metrics[h]['total_predictions'] for h in ['1h', '1d', '1w'])
        total_correct = sum(accuracy_metrics[h]['correct_directions'] for h in ['1h', '1d', '1w'])
        
        if total_predictions > 0:
            weighted_mae = sum(accuracy_metrics[h]['mae'] * accuracy_metrics[h]['total_predictions'] for h in ['1h', '1d', '1w']) / total_predictions
            weighted_mse = sum((accuracy_metrics[h]['rmse'] ** 2) * accuracy_metrics[h]['total_predictions'] for h in ['1h', '1d', '1w']) / total_predictions
            weighted_mape = sum(accuracy_metrics[h]['mape'] * accuracy_metrics[h]['total_predictions'] for h in ['1h', '1d', '1w']) / total_predictions

            coverage_numerator = 0.0
            coverage_denominator = 0.0
            for h in ['1h', '1d', '1w']:
                interval_total = float(accuracy_metrics[h].get('interval_total', 0) or 0)
                if interval_total > 0:
                    coverage_numerator += float(accuracy_metrics[h].get('interval_hits', 0) or 0)
                    coverage_denominator += interval_total

            accuracy_metrics['overall'] = {
                'total_predictions': total_predictions,
                'correct_directions': total_correct,
                'direction_accuracy': (total_correct / total_predictions) * 100,
                'mae': float(weighted_mae),
                'rmse': float(np.sqrt(weighted_mse)),
                'mape': float(weighted_mape),
                'interval_coverage': float((coverage_numerator / coverage_denominator) * 100) if coverage_denominator > 0 else 0.0,
            }
        
        # Store accuracy metrics
        self.accuracy_metrics = accuracy_metrics
        self._save_accuracy_metrics()
        
        logger.info(f"📊 Accuracy calculated: {accuracy_metrics['overall']['direction_accuracy']:.1f}% "
                   f"({total_correct}/{total_predictions} predictions)")
        
        return accuracy_metrics
    
    def _calculate_horizon_accuracy(self, horizon_data, horizon):
        """Calculate accuracy for a specific horizon"""
        if len(horizon_data) < 2:
            return {
                'total_predictions': 0,
                'correct_directions': 0,
                'direction_accuracy': 0,
                'mae': 0,
                'rmse': 0,
                'mape': 0,
                'avg_strategy_return_pct': 0,
                'sharpe_like': 0,
                'interval_hits': 0,
                'interval_total': 0,
                'interval_coverage': 0,
                'rolling_direction_accuracy_20': 0,
                'rolling_mae_20': 0,
            }

        delta, search_window = self._get_horizon_delta_and_window(horizon)
        
        correct_directions = 0
        total_predictions = 0
        absolute_errors = []
        squared_errors = []
        actual_price_values = []
        strategy_returns = []
        direction_flags = []
        interval_hits = 0
        interval_total = 0

        sorted_predictions = sorted(
            horizon_data.items(),
            key=lambda kv: self._sort_timestamp_key(kv[0]),
        )
        
        for pred_timestamp, pred_data in sorted_predictions:
            try:
                pred_time = self._safe_parse_iso(pred_timestamp)
                if pred_time is None:
                    continue
                target_time = pred_time + delta
                
                closest_actual = self._find_closest_actual_price(target_time, search_window)
                
                if closest_actual is not None:
                    predicted_price = pred_data['prediction']
                    current_price = pred_data['current_price']
                    actual_price = closest_actual
                    
                    # Direction accuracy
                    predicted_direction = 1 if predicted_price > current_price else (-1 if predicted_price < current_price else 0)
                    actual_direction = 1 if actual_price > current_price else (-1 if actual_price < current_price else 0)
                    
                    if predicted_direction == actual_direction:
                        correct_directions += 1
                        direction_flags.append(1)
                    else:
                        direction_flags.append(0)

                    if current_price > 0:
                        realized_return = (actual_price - current_price) / current_price
                        strategy_returns.append(predicted_direction * realized_return)
                    
                    total_predictions += 1
                    
                    # Price accuracy
                    abs_error = abs(predicted_price - actual_price)
                    absolute_errors.append(abs_error)
                    squared_errors.append(abs_error ** 2)
                    actual_price_values.append(actual_price)

                    interval_lower = pred_data.get('interval_lower')
                    interval_upper = pred_data.get('interval_upper')
                    if interval_lower is not None and interval_upper is not None:
                        interval_total += 1
                        if float(interval_lower) <= actual_price <= float(interval_upper):
                            interval_hits += 1
                else:
                    logger.debug(f"⚠️ {horizon} prediction at {pred_timestamp}: no actual price within {search_window}")
                    
            except Exception as e:
                logger.debug(f"Error calculating accuracy for {pred_timestamp}: {e}")
                continue
        
        if total_predictions == 0:
            return {
                'total_predictions': 0,
                'correct_directions': 0,
                'direction_accuracy': 0,
                'mae': 0,
                'rmse': 0,
                'mape': 0,
                'avg_strategy_return_pct': 0,
                'sharpe_like': 0,
                'interval_hits': 0,
                'interval_total': 0,
                'interval_coverage': 0,
                'rolling_direction_accuracy_20': 0,
                'rolling_mae_20': 0,
            }
        
        direction_accuracy = (correct_directions / total_predictions) * 100
        mae = np.mean(absolute_errors) if absolute_errors else 0
        rmse = np.sqrt(np.mean(squared_errors)) if squared_errors else 0
        if absolute_errors and actual_price_values:
            mape = float(np.mean(np.asarray(absolute_errors) / np.maximum(1e-6, np.abs(np.asarray(actual_price_values, dtype=float)))) * 100)
        else:
            mape = 0.0
        avg_strategy_return_pct = (np.mean(strategy_returns) * 100) if strategy_returns else 0
        strategy_std = np.std(strategy_returns) if strategy_returns else 0
        sharpe_like = (np.mean(strategy_returns) / strategy_std) if strategy_std > 1e-12 else 0

        rolling_window = min(20, len(direction_flags))
        if rolling_window > 0:
            rolling_direction_accuracy_20 = float(np.mean(direction_flags[-rolling_window:]) * 100)
            rolling_mae_20 = float(np.mean(absolute_errors[-rolling_window:])) if absolute_errors else 0.0
        else:
            rolling_direction_accuracy_20 = 0.0
            rolling_mae_20 = 0.0

        interval_coverage = float((interval_hits / interval_total) * 100) if interval_total > 0 else 0.0
        
        return {
            'total_predictions': total_predictions,
            'correct_directions': correct_directions,
            'direction_accuracy': direction_accuracy,
            'mae': mae,
            'rmse': rmse,
            'mape': float(mape),
            'avg_strategy_return_pct': avg_strategy_return_pct,
            'sharpe_like': float(sharpe_like),
            'interval_hits': int(interval_hits),
            'interval_total': int(interval_total),
            'interval_coverage': interval_coverage,
            'rolling_direction_accuracy_20': rolling_direction_accuracy_20,
            'rolling_mae_20': rolling_mae_20,
        }

# Global predictor instance
premium_predictor_instance = None

def get_premium_predictor():
    """Get or create premium predictor instance"""
    global premium_predictor_instance
    if premium_predictor_instance is None:
        premium_predictor_instance = PremiumWTIPredictor()
    return premium_predictor_instance

def get_multi_horizon_wti_predictions():
    """Get multi-horizon WTI predictions using premium ML system - NO SHORTCUTS"""
    logger.info("🎯 Getting multi-horizon WTI predictions...")
    predictor = get_premium_predictor()
    if predictor.strict_premium_api_required:
        logger.info("🚨 STRICT ML MODE - all premium external APIs required")
    else:
        logger.info("🟢 FREE API MODE - using available real sources with quality gating")
    
    try:
        # ALWAYS use full ML system - NO FALLBACKS ALLOWED
        result = predictor.get_multi_horizon_predictions()
        logger.info(f"✅ Full ML predictions generated with {result['model_count']} models")
        
        # Convert to expected format for server.py compatibility  
        return {
            'prediction_1h': result['predictions']['1h'],
            'prediction_1d': result['predictions']['1d'], 
            'prediction_1w': result['predictions']['1w'],
            'prediction_intervals': result.get('prediction_intervals', {}),
            'horizon_confidence': result.get('horizon_confidence', {}),
            'horizon_drift_scores': result.get('horizon_drift_scores', {}),
            'horizon_backtests': result.get('horizon_backtests', {}),
            'current_price': result['current_price'],
            'processing_time': result['processing_time'],
            'feature_count': result['feature_count'],
            'data_quality_score': result['data_quality_score'],
            'is_real_prediction': result['is_real_prediction'],
            'is_full_real_prediction': result.get('is_full_real_prediction', result['is_real_prediction']),
            'fallbacks': result.get('fallbacks', {}),
            'horizon_quality': result.get('horizon_quality', {}),
            'quality_qualified_horizons': result.get('quality_qualified_horizons', []),
            'premium_features': result['premium_features'],
            'model_count': result['model_count'],
            'external_data_sources': result['external_data_sources'],
            'pipeline_timings': result.get('pipeline_timings', {}),
            'cache_stats': result.get('cache_stats', {}),
            'market_data_sources': result.get('market_data_sources', {}),
            'contract_metadata': result.get('contract_metadata', {}),
            'geopolitical_risk': result.get('geopolitical_risk', {}),
            'scenario_analysis': result.get('scenario_analysis', {}),
            'timestamp': result['timestamp']
        }
        
    except Exception as e:
        logger.error(f"❌ ML prediction system failed: {e}")
        logger.error("❌ NO SHORTCUTS ALLOWED - System refuses to bypass ML logic")
        # STRICT POLICY: Fail completely rather than use shortcuts
        raise Exception(f"ML prediction system failed - no shortcuts permitted: {e}")

def get_prediction_accuracy_metrics():
    """Get prediction accuracy metrics"""
    predictor = get_premium_predictor()
    return predictor.calculate_prediction_accuracy()

def store_actual_price_update(price, volume=None):
    """Store actual price update with optional volume snapshot."""
    predictor = get_premium_predictor()
    predictor.store_actual_price(price, volume=volume)

def get_historical_data(limit=50):
    """Get historical stored data for chart display"""
    predictor = get_premium_predictor()
    backend_timezone = getattr(predictor, 'storage_timezone', None) or datetime.now().astimezone().tzinfo or timezone.utc

    # Keep enough points for smooth charting even when callers request fewer.
    max_points = max(int(limit or 0), 720)

    def _normalized_chart_datetime(timestamp_value):
        try:
            parsed = pd.Timestamp(timestamp_value)
        except Exception:
            parsed = predictor._safe_parse_iso(str(timestamp_value))
            if parsed is None:
                return None
            parsed = pd.Timestamp(parsed)

        try:
            if parsed.tzinfo is not None:
                parsed = parsed.tz_convert('UTC').tz_localize(None)
            else:
                parsed = parsed.tz_localize(backend_timezone).tz_convert('UTC').tz_localize(None)
        except Exception:
            pass

        try:
            return parsed.to_pydatetime()
        except Exception:
            return None

    def _normalize_chart_timestamp(timestamp_value):
        try:
            parsed = pd.Timestamp(timestamp_value)
        except Exception:
            parsed = predictor._safe_parse_iso(str(timestamp_value))
            if parsed is None:
                return None
            parsed = pd.Timestamp(parsed)

        try:
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(backend_timezone)
            else:
                parsed = parsed.tz_convert('UTC')
            return parsed.tz_convert('UTC').isoformat().replace('+00:00', 'Z')
        except Exception:
            pass

        try:
            return parsed.to_pydatetime().isoformat()
        except Exception:
            return None

    def _datetime_to_chart_timestamp(datetime_value):
        if datetime_value is None:
            return None
        if getattr(datetime_value, 'tzinfo', None) is None:
            normalized = datetime_value.replace(tzinfo=timezone.utc)
        else:
            normalized = datetime_value.astimezone(timezone.utc)
        return normalized.isoformat().replace('+00:00', 'Z')

    def _chart_sort_key(timestamp_value):
        normalized = _normalized_chart_datetime(timestamp_value)
        return normalized.timestamp() if normalized is not None else float('-inf')

    actual_point_map = {}

    def _store_actual_point(timestamp_value, price_value, volume_value=0):
        price_numeric = pd.to_numeric(price_value, errors='coerce')
        if pd.isna(price_numeric) or float(price_numeric) <= 0:
            return

        normalized_timestamp = _normalize_chart_timestamp(timestamp_value)
        if normalized_timestamp is None:
            return

        volume_numeric = pd.to_numeric(volume_value, errors='coerce')
        actual_point_map[normalized_timestamp] = {
            'timestamp': normalized_timestamp,
            'price': float(price_numeric),
            'volume': int(volume_numeric) if not pd.isna(volume_numeric) and float(volume_numeric) > 0 else 0,
        }

    broad_history = None
    intraday_history = None

    try:
        broad_history = predictor.get_wti_historical_data(period="6mo", interval="1d")
    except Exception as e:
        logger.warning(f"Daily chart history unavailable: {e}")

    try:
        intraday_history = predictor.get_wti_historical_data(period="1mo", interval="1h")
    except Exception as e:
        logger.warning(f"Intraday chart history unavailable: {e}")

    intraday_start = None
    if intraday_history is not None and not intraday_history.empty:
        try:
            intraday_start = _normalized_chart_datetime(intraday_history.index[0])
        except Exception:
            intraday_start = None

    if broad_history is not None and not broad_history.empty:
        for idx, row in broad_history.iterrows():
            point_time = _normalized_chart_datetime(idx)
            if intraday_start is not None and point_time is not None and point_time >= intraday_start:
                continue
            _store_actual_point(idx, row.get('Close'), row.get('Volume'))

    if intraday_history is not None and not intraday_history.empty:
        for idx, row in intraday_history.iterrows():
            _store_actual_point(idx, row.get('Close'), row.get('Volume'))

    # Overlay the freshest stored live points so the chart reaches the current session.
    sorted_prices = sorted(
        predictor.stored_actual_prices.items(),
        key=lambda item: _chart_sort_key(item[0]),
    )
    for timestamp, data in sorted_prices:
        if isinstance(data, dict):
            _store_actual_point(timestamp, data.get('price'), data.get('volume'))

    sorted_actual_points = sorted(
        actual_point_map.values(),
        key=lambda item: _chart_sort_key(item.get('timestamp')),
    )
    if len(sorted_actual_points) > max_points:
        step = max(1, len(sorted_actual_points) // max_points)
        sampled_points = sorted_actual_points[::step]
        if sampled_points and sampled_points[-1]['timestamp'] != sorted_actual_points[-1]['timestamp']:
            sampled_points.append(sorted_actual_points[-1])
        sorted_actual_points = sampled_points[-max_points:]

    actual_values = [round(point['price'], 4) for point in sorted_actual_points]
    actual_timestamps = [point['timestamp'] for point in sorted_actual_points]
    actual_volumes = [point['volume'] for point in sorted_actual_points]
    last_actual_time = _normalized_chart_datetime(actual_timestamps[-1]) if actual_timestamps else None

    # Get stored predictions sorted by timestamp
    sorted_predictions = sorted(
        predictor.stored_predictions.items(),
        key=lambda item: _chart_sort_key(item[0]),
    )
    prediction_points = max(int(limit or 0), 180)
    recent_predictions = sorted_predictions[-prediction_points:]
    horizon_offsets = {'1h': timedelta(hours=1), '1d': timedelta(days=1), '1w': timedelta(weeks=1)}

    historical_by_horizon = {
        '1h': {'values': [], 'timestamps': [], 'issue_timestamps': [], 'target_timestamps': [], 'upper_bound': [], 'lower_bound': [], 'current_prices': []},
        '1d': {'values': [], 'timestamps': [], 'issue_timestamps': [], 'target_timestamps': [], 'upper_bound': [], 'lower_bound': [], 'current_prices': []},
        '1w': {'values': [], 'timestamps': [], 'issue_timestamps': [], 'target_timestamps': [], 'upper_bound': [], 'lower_bound': [], 'current_prices': []},
    }
    issued_by_horizon = {
        '1h': {'values': [], 'timestamps': [], 'issue_timestamps': [], 'target_timestamps': [], 'upper_bound': [], 'lower_bound': [], 'current_prices': []},
        '1d': {'values': [], 'timestamps': [], 'issue_timestamps': [], 'target_timestamps': [], 'upper_bound': [], 'lower_bound': [], 'current_prices': []},
        '1w': {'values': [], 'timestamps': [], 'issue_timestamps': [], 'target_timestamps': [], 'upper_bound': [], 'lower_bound': [], 'current_prices': []},
    }

    for timestamp, pred_data in recent_predictions:
        if not isinstance(pred_data, dict) or 'predictions' not in pred_data:
            continue

        prediction_intervals = pred_data.get('prediction_intervals', {}) if isinstance(pred_data, dict) else {}
        current_price = pred_data.get('current_price')
        issue_time = _normalized_chart_datetime(timestamp)

        for horizon in ['1h', '1d', '1w']:
            pred_value = pred_data.get('predictions', {}).get(horizon)
            if pred_value is None:
                continue

            target_time = None
            if issue_time is not None:
                target_time = issue_time + horizon_offsets[horizon]

            horizon_interval = prediction_intervals.get(horizon, {}) if isinstance(prediction_intervals, dict) else {}
            normalized_issue_timestamp = _datetime_to_chart_timestamp(issue_time) if issue_time is not None else (_normalize_chart_timestamp(timestamp) or timestamp)
            normalized_target_timestamp = _datetime_to_chart_timestamp(target_time) if target_time is not None else normalized_issue_timestamp
            issued_by_horizon[horizon]['values'].append(float(pred_value))
            issued_by_horizon[horizon]['timestamps'].append(normalized_issue_timestamp)
            issued_by_horizon[horizon]['issue_timestamps'].append(normalized_issue_timestamp)
            issued_by_horizon[horizon]['target_timestamps'].append(normalized_target_timestamp)
            issued_by_horizon[horizon]['upper_bound'].append(horizon_interval.get('upper'))
            issued_by_horizon[horizon]['lower_bound'].append(horizon_interval.get('lower'))
            issued_by_horizon[horizon]['current_prices'].append(float(current_price) if current_price is not None else None)

            if target_time is not None and last_actual_time is not None and target_time > last_actual_time:
                continue

            historical_by_horizon[horizon]['values'].append(float(pred_value))
            historical_by_horizon[horizon]['timestamps'].append(normalized_target_timestamp)
            historical_by_horizon[horizon]['issue_timestamps'].append(normalized_issue_timestamp)
            historical_by_horizon[horizon]['target_timestamps'].append(normalized_target_timestamp)
            historical_by_horizon[horizon]['upper_bound'].append(horizon_interval.get('upper'))
            historical_by_horizon[horizon]['lower_bound'].append(horizon_interval.get('lower'))
            historical_by_horizon[horizon]['current_prices'].append(float(current_price) if current_price is not None else None)

    predicted_values = historical_by_horizon['1h']['values']
    predicted_timestamps = historical_by_horizon['1h']['timestamps']
    predicted_upper = historical_by_horizon['1h']['upper_bound']
    predicted_lower = historical_by_horizon['1h']['lower_bound']

    future_values = []
    future_timestamps = []
    future_upper = []
    future_lower = []
    future_by_horizon = {}
    if sorted_predictions:
        latest_ts, latest_pred = sorted_predictions[-1]
        if isinstance(latest_pred, dict):
            preds = latest_pred.get('predictions', {}) or {}
            intervals = latest_pred.get('prediction_intervals', {}) or {}
            base_time = _normalized_chart_datetime(latest_ts) or datetime.now(timezone.utc).replace(tzinfo=None)
            for horizon in ['1h', '1d', '1w']:
                pred_val = preds.get(horizon)
                if pred_val is None:
                    continue
                horizon_time = base_time + horizon_offsets[horizon]
                horizon_interval = intervals.get(horizon, {}) if isinstance(intervals, dict) else {}
                normalized_horizon_timestamp = _datetime_to_chart_timestamp(horizon_time) or horizon_time.isoformat()
                future_values.append(float(pred_val))
                future_timestamps.append(normalized_horizon_timestamp)
                future_upper.append(horizon_interval.get('upper'))
                future_lower.append(horizon_interval.get('lower'))
                future_by_horizon[horizon] = {
                    'value': float(pred_val),
                    'timestamp': normalized_horizon_timestamp,
                    'upper': horizon_interval.get('upper'),
                    'lower': horizon_interval.get('lower'),
                }
    
    return {
        'actual': {
            'values': actual_values,
            'timestamps': actual_timestamps,
            'volumes': actual_volumes,
        },
        'predicted': {
            'historical': {
                'values': predicted_values,
                'timestamps': predicted_timestamps,
                'upper_bound': predicted_upper,
                'lower_bound': predicted_lower,
            },
            'historical_by_horizon': historical_by_horizon,
            'issued_by_horizon': issued_by_horizon,
            'future': {
                'values': future_values,
                'timestamps': future_timestamps,
                'upper_bound': future_upper,
                'lower_bound': future_lower,
                'by_horizon': future_by_horizon,
            }
        }
    }

def main():
    """Main function for testing premium system"""
    try:
        logger.info("Testing Premium WTI Prediction System...")
        
        # Test contract detection
        contract_info = get_current_wti_contract()
        logger.info(f"✅ Contract: {contract_info['symbol']} @ ${contract_info['current_price']:.2f}")
        
        # Test predictions
        predictions = get_multi_horizon_wti_predictions()
        logger.info(f"✅ Predictions: 1H=${predictions['prediction_1h']:.2f}, "
                   f"1D=${predictions['prediction_1d']:.2f}, 1W=${predictions['prediction_1w']:.2f}")
        
        # Test accuracy calculation
        accuracy = get_prediction_accuracy_metrics()
        logger.info(f"✅ Accuracy: {accuracy['overall']['direction_accuracy']:.1f}%")
        
        logger.info("✅ Premium WTI system test completed successfully")
        
    except Exception as e:
        logger.error(f"❌ Premium system test failed: {e}")
        raise

if __name__ == '__main__':
    main()
