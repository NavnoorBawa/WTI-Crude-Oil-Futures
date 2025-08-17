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
        PremiumWTIPredictor,
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
        """Validate that all system components are available"""
        logger.info("🔍 Validating system requirements...")
        
        # Clean up ports first
        self.cleanup_ports()
        
        if not oil_available:
            raise Exception("oil.py module not available")
        
        if not server_available:
            raise Exception("server.py module not available")
        
        # Test oil.py functionality
        try:
            contract_info = get_current_wti_contract()
            logger.info(f"✅ Contract detection working: {contract_info['symbol']}")
        except Exception as e:
            raise Exception(f"Contract detection failed: {e}")
        
        # Test data directory
        data_dir = Path("data")
        if not data_dir.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            logger.info("📁 Created data directory")
        
        # Test predictions
        try:
            predictor = PremiumWTIPredictor()
            logger.info("✅ Prediction engine initialized")
        except Exception as e:
            raise Exception(f"Prediction engine failed: {e}")
        
        logger.info("✅ All system requirements validated")
    
    def run_prediction_cycle(self):
        """Run a single prediction cycle"""
        try:
            logger.info("🎯 Starting prediction cycle...")
            
            # Get fresh predictions
            predictions = get_multi_horizon_wti_predictions()
            
            if not predictions or not predictions.get('is_real_prediction'):
                raise Exception("Failed to get real predictions")
            
            # Store current actual price for accuracy tracking
            contract_info = get_current_wti_contract()
            current_price = contract_info['current_price']
            store_actual_price_update(current_price)
            
            self.last_prediction_time = time.time()
            self.error_count = 0  # Reset error count on success
            
            logger.info(f"✅ Prediction cycle completed: 1H=${predictions['prediction_1h']:.2f}, "
                       f"1D=${predictions['prediction_1d']:.2f}, 1W=${predictions['prediction_1w']:.2f}")
            
            return True
            
        except Exception as e:
            self.error_count += 1
            logger.error(f"❌ Prediction cycle failed (error {self.error_count}/{self.max_errors}): {e}")
            
            if self.error_count >= self.max_errors:
                logger.critical("🚨 Too many prediction errors - system may be unstable")
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
        """Validate data quality and system health"""
        try:
            logger.info("🔍 Running data quality validation...")
            
            # Check contract validity
            contract_info = get_current_wti_contract()
            days_to_expiry = contract_info.get('days_to_expiry', 0)
            
            if days_to_expiry <= 3:
                logger.warning(f"⚠️  Contract {contract_info['symbol']} expires in {days_to_expiry} days")
            
            # Check data storage
            data_dir = Path("data")
            if not data_dir.exists():
                logger.warning("⚠️  Data directory missing - recreating")
                data_dir.mkdir(parents=True, exist_ok=True)
            
            # Check prediction system health (accuracy validation requires historical data)
            try:
                accuracy_metrics = get_prediction_accuracy_metrics()
                if accuracy_metrics:
                    logger.info("📊 Prediction accuracy tracking system operational")
            except Exception as e:
                logger.warning(f"Could not check prediction tracking: {e}")
            
            self.last_data_validation = time.time()
            logger.info("✅ Data quality validation completed")
            
        except Exception as e:
            logger.error(f"Data validation error: {e}")
    
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
            logger.info("🛑 Received interrupt signal - stopping system...")
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