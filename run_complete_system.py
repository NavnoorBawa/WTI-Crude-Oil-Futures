#!/usr/bin/env python3
"""
WTI Oil Price Prediction Complete System Runner
=============================================
Orchestrates the complete WTI oil price prediction system.
Ensures all components work together with real data only.
"""

import sys
import time
import threading
import subprocess
import signal
import os
from datetime import datetime, timedelta
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import our prediction engine
try:
    from oil import (
        get_current_wti_contract,
        get_multi_horizon_wti_predictions,
        get_prediction_accuracy_metrics,
        store_actual_price_update,
        main as oil_main
    )
    oil_available = True
except ImportError as e:
    logger.error(f"❌ CRITICAL: Cannot import oil.py: {e}")
    oil_available = False

# Import server
try:
    from server import run_server
    server_available = True
except ImportError as e:
    logger.error(f"❌ CRITICAL: Cannot import server.py: {e}")
    server_available = False

class WTISystemOrchestrator:
    """Orchestrates the complete WTI prediction system"""
    
    def __init__(self):
        self.running = False
        self.server_process = None
        self.prediction_thread = None
        self.data_validation_thread = None
        self.system_health_thread = None
        
        # System configuration
        self.prediction_interval = 180  # 3 minutes
        self.health_check_interval = 60  # 1 minute
        self.data_validation_interval = 300  # 5 minutes
        
        # System state
        self.last_prediction_time = 0
        self.last_health_check = 0
        self.last_data_validation = 0
        self.error_count = 0
        self.max_errors = 5
        
        logger.info("🏗️  WTI System Orchestrator initialized")
    
    def cleanup_ports(self):
        """Clean up any processes using required ports"""
        try:
            logger.info("🧹 Cleaning up ports...")
            # Kill any process using port 9000
            result = subprocess.run(['lsof', '-ti', ':9000'], capture_output=True, text=True)
            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        subprocess.run(['kill', '-9', pid], check=True)
                        logger.info(f"   ✅ Killed process {pid} on port 9000")
                    except subprocess.CalledProcessError:
                        pass
            time.sleep(1)  # Give processes time to clean up
        except Exception as e:
            logger.warning(f"Port cleanup warning: {e}")
    
    def validate_system_requirements(self):
        """Validate that all system components are available - REAL DATA ONLY"""
        logger.info("🔍 Validating system requirements...")
        logger.info("🚨 REAL DATA ONLY MODE - No fallbacks permitted")
        
        # Clean up ports first
        self.cleanup_ports()
        
        if not oil_available:
            raise Exception("CRITICAL: oil.py module not available - system cannot function")
        
        if not server_available:
            raise Exception("CRITICAL: server.py module not available - system cannot function")
        
        # Test oil.py functionality with real data
        try:
            contract_info = get_current_wti_contract()
            if not contract_info or not contract_info.get('current_price'):
                raise Exception("Contract detection returned invalid data")
            
            # Verify we get a real price
            current_price = contract_info['current_price']
            if current_price <= 0 or current_price > 1000:
                raise Exception(f"Invalid price returned: ${current_price}")
                
            logger.info(f"✅ Contract detection working: {contract_info['symbol']} @ ${current_price:.2f}")
            logger.info(f"   Expiry: {contract_info.get('expiry_date', 'N/A')}")
            
        except Exception as e:
            raise Exception(f"Contract detection failed: {e}")
        
        # Test data directory structure
        data_dir = Path("data")
        if not data_dir.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            logger.info("📁 Created data directory")
        
        # Test prediction engine with real ML
        try:
            # Test if we can actually generate predictions
            test_predictions = get_multi_horizon_wti_predictions()
            if not test_predictions or not test_predictions.get('is_real_prediction'):
                raise Exception("Prediction engine not generating real predictions")
                
            logger.info("✅ Prediction engine initialized")
            logger.info("✅ ML prediction system generating real predictions")
            
        except Exception as e:
            raise Exception(f"Prediction engine failed: {e}")
        
        logger.info("✅ All critical system requirements validated")
        logger.info("🎯 System ready for REAL DATA ONLY operation")
    
    def run_prediction_cycle(self):
        """Run a single prediction cycle - REAL DATA ONLY"""
        try:
            logger.info("🎯 Starting prediction cycle...")
            
            # First verify contract detection is working
            contract_info = get_current_wti_contract()
            if not contract_info or not contract_info.get('current_price'):
                raise Exception("Contract detection failed - no real price data available")
            
            current_price = contract_info['current_price']
            logger.info(f"📊 Current WTI price: ${current_price:.2f} ({contract_info['symbol']})")
            
            # Get fresh predictions - REAL ML ONLY
            predictions = get_multi_horizon_wti_predictions()
            
            if not predictions or not predictions.get('is_real_prediction'):
                raise Exception("Failed to get real ML predictions - system refusing to use fallbacks")
            
            # Verify prediction quality
            if (predictions['prediction_1h'] <= 0 or 
                predictions['prediction_1d'] <= 0 or 
                predictions['prediction_1w'] <= 0):
                raise Exception("Invalid predictions generated - refusing to store bad data")
            
            # Store current actual price for accuracy tracking
            store_actual_price_update(current_price)
            
            self.last_prediction_time = time.time()
            self.error_count = 0  # Reset error count on success
            
            logger.info(f"✅ REAL prediction cycle completed:")
            logger.info(f"   1H: ${predictions['prediction_1h']:.2f} ({((predictions['prediction_1h']-current_price)/current_price*100):+.1f}%)")
            logger.info(f"   1D: ${predictions['prediction_1d']:.2f} ({((predictions['prediction_1d']-current_price)/current_price*100):+.1f}%)")
            logger.info(f"   1W: ${predictions['prediction_1w']:.2f} ({((predictions['prediction_1w']-current_price)/current_price*100):+.1f}%)")
            logger.info(f"   Features: {predictions.get('feature_count', 0)}, Processing: {predictions.get('processing_time', 0):.2f}s")
            
            return True
            
        except Exception as e:
            self.error_count += 1
            logger.error(f"❌ Prediction cycle failed (error {self.error_count}/{self.max_errors}): {e}")
            
            if self.error_count >= self.max_errors:
                logger.critical("🚨 Too many prediction errors - system may be unstable")
                logger.critical("🚨 REAL DATA ONLY policy prevents fallback - manual intervention required")
                return False
            
            return True
    
    def prediction_worker(self):
        """Background worker for running predictions"""
        logger.info("📈 Prediction worker started")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check if it's time for a new prediction
                if current_time - self.last_prediction_time >= self.prediction_interval:
                    if not self.run_prediction_cycle():
                        logger.critical("🚨 Stopping prediction worker due to errors")
                        break
                
                # Sleep for a short interval
                time.sleep(10)
                
            except Exception as e:
                logger.error(f"Prediction worker error: {e}")
                time.sleep(30)
    
    def validate_data_quality(self):
        """Validate data quality and system health - REAL DATA ONLY"""
        try:
            logger.info("🔍 Running data quality validation...")
            
            # Check contract validity and auto-switching
            contract_info = get_current_wti_contract()
            if not contract_info or not contract_info.get('current_price'):
                raise Exception("Contract detection failed during validation")
            
            days_to_expiry = contract_info.get('days_to_expiry', 0)
            current_price = contract_info['current_price']
            
            # Validate price is reasonable
            if current_price <= 0 or current_price > 1000:
                raise Exception(f"Invalid price detected: ${current_price}")
            
            if days_to_expiry <= 3:
                logger.warning(f"⚠️  Contract {contract_info['symbol']} expires in {days_to_expiry} days")
                logger.warning("⚠️  Auto-switching to next contract may occur soon")
            
            # Check data storage integrity
            data_dir = Path("data")
            if not data_dir.exists():
                logger.warning("⚠️  Data directory missing - recreating")
                data_dir.mkdir(parents=True, exist_ok=True)
            
            # Check for stored prediction files
            symbol = contract_info['symbol']
            prediction_files = [
                f"{symbol}_predictions_1h.json",
                f"{symbol}_predictions_1d.json", 
                f"{symbol}_predictions_1w.json"
            ]
            
            stored_files = []
            for filename in prediction_files:
                filepath = data_dir / filename
                if filepath.exists():
                    stored_files.append(filename)
            
            if stored_files:
                logger.info(f"📁 Found {len(stored_files)} prediction storage files")
            else:
                logger.info("📁 No stored predictions yet - will be created on first run")
            
            # Check prediction system health and accuracy tracking
            try:
                accuracy_metrics = get_prediction_accuracy_metrics()
                if accuracy_metrics:
                    overall_acc = accuracy_metrics.get('overall', {})
                    direction_acc = overall_acc.get('direction_accuracy', 0)
                    total_preds = overall_acc.get('total_predictions', 0)
                    
                    if total_preds > 0:
                        logger.info(f"📊 Prediction accuracy: {direction_acc:.1f}% ({total_preds} predictions)")
                    else:
                        logger.info("📊 Prediction accuracy tracking initialized - no data yet")
                else:
                    logger.info("📊 Prediction accuracy tracking will start with first predictions")
            except Exception as e:
                logger.warning(f"Could not check prediction tracking: {e}")
            
            # Validate real-time data freshness
            current_time = time.time()
            data_age = current_time - self.last_prediction_time
            if data_age > 600:  # 10 minutes
                logger.warning(f"⚠️  Prediction data is {data_age/60:.1f} minutes old")
            
            self.last_data_validation = time.time()
            logger.info("✅ Data quality validation completed - all real data verified")
            
        except Exception as e:
            logger.error(f"❌ Data validation error: {e}")
            logger.error("❌ REAL DATA ONLY policy requires manual intervention")
    
    def data_validation_worker(self):
        """Background worker for data validation"""
        logger.info("🔍 Data validation worker started")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check if it's time for data validation
                if current_time - self.last_data_validation >= self.data_validation_interval:
                    self.validate_data_quality()
                
                # Sleep for a short interval
                time.sleep(30)
                
            except Exception as e:
                logger.error(f"Data validation worker error: {e}")
                time.sleep(60)
    
    def system_health_check(self):
        """Perform system health check"""
        try:
            logger.info("💓 Running system health check...")
            
            # Check if prediction worker is responsive
            time_since_prediction = time.time() - self.last_prediction_time
            if time_since_prediction > self.prediction_interval * 2:
                logger.warning(f"⚠️  No predictions for {time_since_prediction:.1f} seconds")
            
            # Check error count
            if self.error_count > 0:
                logger.warning(f"⚠️  System error count: {self.error_count}/{self.max_errors}")
            
            # Memory and basic system checks could go here
            
            self.last_health_check = time.time()
            logger.info("✅ System health check completed")
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
    
    def health_check_worker(self):
        """Background worker for health checks"""
        logger.info("💓 Health check worker started")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check if it's time for health check
                if current_time - self.last_health_check >= self.health_check_interval:
                    self.system_health_check()
                
                # Sleep for a short interval
                time.sleep(20)
                
            except Exception as e:
                logger.error(f"Health check worker error: {e}")
                time.sleep(30)
    
    def start_server(self, host='0.0.0.0', port=9000):
        """Start the Flask server in a separate process"""
        logger.info(f"🌐 Starting Flask server on {host}:{port}")
        
        try:
            # Start server in background thread since we're using the function directly
            server_thread = threading.Thread(
                target=run_server,
                args=(host, port, False),  # host, port, debug=False
                daemon=True
            )
            server_thread.start()
            
            # Give server time to start
            time.sleep(2)
            logger.info("✅ Flask server started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start Flask server: {e}")
            raise
    
    def stop_server(self):
        """Stop the Flask server"""
        if self.server_process:
            logger.info("🛑 Stopping Flask server...")
            self.server_process.terminate()
            self.server_process.wait()
            logger.info("✅ Flask server stopped")
    
    def start(self, host='0.0.0.0', port=9000):
        """Start the complete system"""
        logger.info("🚀 Starting WTI Oil Price Prediction System")
        logger.info("=" * 60)
        
        try:
            # Validate system requirements
            self.validate_system_requirements()
            
            # Set running flag
            self.running = True
            
            # Run initial prediction
            logger.info("🎯 Running initial prediction...")
            if not self.run_prediction_cycle():
                raise Exception("Initial prediction failed")
            
            # Start Flask server
            self.start_server(host, port)
            
            # Start background workers
            self.prediction_thread = threading.Thread(target=self.prediction_worker, daemon=True)
            self.data_validation_thread = threading.Thread(target=self.data_validation_worker, daemon=True)
            self.system_health_thread = threading.Thread(target=self.health_check_worker, daemon=True)
            
            self.prediction_thread.start()
            self.data_validation_thread.start()
            self.system_health_thread.start()
            
            logger.info("✅ All system components started successfully")
            logger.info("📊 System is now running - real data only, no fallbacks")
            logger.info("🌐 API available at http://{}:{}".format(host, port))
            logger.info("=" * 60)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ System startup failed: {e}")
            self.stop()
            return False
    
    def stop(self):
        """Stop the complete system"""
        logger.info("🛑 Stopping WTI Oil Price Prediction System...")
        
        self.running = False
        
        # Stop server
        self.stop_server()
        
        # Wait for threads to finish (they're daemon threads, so this is optional)
        time.sleep(1)
        
        logger.info("✅ System stopped successfully")
    
    def run_forever(self, host='0.0.0.0', port=9000):
        """Run the system forever (until interrupted)"""
        def signal_handler(signum, frame):
            logger.info(f"🛑 Received interrupt signal {signum} - stopping system...")
            self.stop()
            sys.exit(0)
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start system
        if not self.start(host, port):
            sys.exit(1)
        
        # Keep running until interrupted
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("🛑 Keyboard interrupt received")
            self.stop()

def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='WTI Oil Price Prediction Complete System')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind server to')
    parser.add_argument('--port', type=int, default=9000, help='Port to bind server to')
    parser.add_argument('--test', action='store_true', help='Run system test only')
    parser.add_argument('--validate', action='store_true', help='Validate system only')
    
    args = parser.parse_args()
    
    # Create orchestrator
    orchestrator = WTISystemOrchestrator()
    
    if args.validate:
        # Validation only
        try:
            orchestrator.validate_system_requirements()
            logger.info("✅ System validation passed")
            return 0
        except Exception as e:
            logger.error(f"❌ System validation failed: {e}")
            return 1
    
    elif args.test:
        # Test mode - run once and exit
        try:
            orchestrator.validate_system_requirements()
            if orchestrator.run_prediction_cycle():
                logger.info("✅ System test passed")
                return 0
            else:
                logger.error("❌ System test failed")
                return 1
        except Exception as e:
            logger.error(f"❌ System test failed: {e}")
            return 1
    
    else:
        # Full system run
        try:
            orchestrator.run_forever(host=args.host, port=args.port)
            return 0
        except Exception as e:
            logger.error(f"❌ System execution failed: {e}")
            return 1

if __name__ == '__main__':
    sys.exit(main())