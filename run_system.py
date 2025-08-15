#!/usr/bin/env python3
"""
Bloomberg Terminal ML System Startup Script - FINAL OPTIMIZED VERSION
======================================================================
Integrated all features from run_system_simple.py for professional deployment.
Enhanced process management, port handling, and system monitoring.
"""

import subprocess
import sys
import time
import os
import signal
import threading
import requests
from datetime import datetime

def run_command(cmd, ignore_errors=True):
    """Run a shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 or ignore_errors:
            return result.stdout.strip()
        return ""
    except Exception:
        return ""

def kill_processes_using_ports(ports):
    """Kill all processes using specified ports using lsof"""
    killed_count = 0
    for port in ports:
        try:
            # Find PIDs using the port
            pids = run_command(f"lsof -ti:{port}")
            if pids:
                pid_list = pids.split('\n')
                for pid in pid_list:
                    if pid.strip():
                        print(f"🔫 Killing process {pid} using port {port}")
                        run_command(f"kill -9 {pid}")
                        killed_count += 1
        except Exception as e:
            print(f"⚠️ Error checking port {port}: {e}")
    
    if killed_count > 0:
        print(f"✅ Killed {killed_count} processes using ports")
        time.sleep(2)
    
    return killed_count

def cleanup_old_instances():
    """Clean up any existing instances of this system"""
    print("🧹 Cleaning up old instances...")
    
    killed_count = 0
    
    # Kill any run_system.py processes (except current one)
    current_pid = str(os.getpid())
    pids = run_command("pgrep -f 'run_system'")
    if pids:
        for pid in pids.split('\n'):
            if pid.strip() and pid.strip() != current_pid:
                print(f"🔫 Killing old run_system process: {pid}")
                run_command(f"kill -9 {pid}")
                killed_count += 1
    
    # Kill any server.py processes
    pids = run_command("pgrep -f 'server.py'")
    if pids:
        for pid in pids.split('\n'):
            if pid.strip():
                print(f"🔫 Killing old server.py process: {pid}")
                run_command(f"kill -9 {pid}")
                killed_count += 1
    
    # Kill npm dev processes
    run_command("pkill -f 'npm.*dev'")
    run_command("pkill -f 'vite'")
    
    # Clean up ports
    ports_cleaned = kill_processes_using_ports([8000, 3000, 9000, 4000, 5173, 5500])
    
    if killed_count > 0 or ports_cleaned > 0:
        print("⏳ Waiting for cleanup to complete...")
        time.sleep(3)
    
    print(f"✅ Cleanup complete: {killed_count} processes killed")

def verify_ports_free(ports):
    """Verify that required ports are free using lsof"""
    busy_ports = []
    for port in ports:
        result = run_command(f"lsof -i:{port}")
        if result:  # If lsof returns output, port is in use
            busy_ports.append(port)
    
    if busy_ports:
        print(f"❌ Ports still in use: {busy_ports}")
        print("Port details:")
        for port in busy_ports:
            details = run_command(f"lsof -i:{port}")
            print(f"  Port {port}:")
            for line in details.split('\n')[:3]:  # Show first 3 lines
                print(f"    {line}")
        return False
    
    print(f"✅ All required ports are free: {ports}")
    return True

class ProfessionalSystemMonitor:
    def __init__(self):
        self.backend_process = None
        self.frontend_process = None
        self.running = True
        self.startup_errors = []
        self.health_check_failures = 0
        self.max_health_failures = 5
        
    def start_backend(self):
        """Start the unified ML backend server with comprehensive validation"""
        print("🚀 Starting Unified Bloomberg Terminal ML Server...")
        print("=" * 70)
        
        # Verify port 9000 is free
        if not verify_ports_free([9000]):
            print("❌ Backend port 9000 is not free!")
            return False
        
        try:
            # Check if required files exist
            required_files = ['oil.py', 'server.py']
            missing_files = []
            
            for file in required_files:
                if not os.path.exists(file):
                    missing_files.append(file)
            
            if missing_files:
                print(f"❌ ERROR: Missing required files: {', '.join(missing_files)}")
                print("   Please ensure all required files are in the current directory.")
                self.startup_errors.append(f"Missing files: {missing_files}")
                return False
            
            # Validate Python environment
            print("🔍 Validating Python environment...")
            required_packages = ['yfinance', 'flask', 'numpy', 'requests']
            missing_packages = []
            
            for package in required_packages:
                try:
                    __import__(package)
                except ImportError:
                    missing_packages.append(package)
            
            if missing_packages:
                print(f"⚠️ WARNING: Missing Python packages: {', '.join(missing_packages)}")
                print("   Install with: pip install " + " ".join(missing_packages))
            
            # Start the server with enhanced monitoring
            print("🔧 Launching unified ML server...")
            self.backend_process = subprocess.Popen(
                [sys.executable, 'server.py'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            print("✅ Backend server launching...")
            print("📊 Expected features:")
            print("   • Unified visualization data structure")
            print("   • Actual prices as line data")
            print("   • Predicted prices as area data with confidence bands")
            print("   • 25-30 second ML processing time")
            print("   • 8-minute intelligent caching")
            print("   • Real-time ML status tracking")
            print()
            
            # Wait and verify startup
            print("⏳ Waiting for backend initialization...")
            time.sleep(5)
            
            if self.backend_process.poll() is not None:
                print("❌ Backend process exited immediately")
                # Try to capture error output
                try:
                    stdout, stderr = self.backend_process.communicate(timeout=3)
                    if stderr:
                        print(f"Error output: {stderr}")
                        self.startup_errors.append(f"Backend startup error: {stderr}")
                except:
                    pass
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to start backend: {e}")
            self.startup_errors.append(f"Backend startup exception: {e}")
            return False
    
    def start_frontend(self):
        """Start the professional frontend development server"""
        print("🌐 Starting Professional Frontend Development Server...")
        print("=" * 70)
        
        # Verify port 4000 is free
        if not verify_ports_free([4000]):
            print("❌ Frontend port 4000 is not free!")
            return False
        
        try:
            # Comprehensive frontend validation
            if not os.path.exists('package.json'):
                print("❌ ERROR: package.json not found!")
                print("   Please run this script from the project root directory.")
                self.startup_errors.append("Missing package.json")
                return False
            
            # Check Node.js availability
            try:
                node_version = run_command("node --version")
                npm_version = run_command("npm --version")
                print(f"📦 Node.js version: {node_version}")
                print(f"📦 NPM version: {npm_version}")
            except:
                print("⚠️ WARNING: Could not detect Node.js/NPM versions")
            
            # Install dependencies if needed
            if not os.path.exists('node_modules'):
                print("📦 Installing dependencies (this may take a few minutes)...")
                install_process = subprocess.run(['npm', 'install'], 
                                               capture_output=True, text=True, timeout=300)
                if install_process.returncode != 0:
                    print("❌ Failed to install dependencies")
                    print("STDOUT:", install_process.stdout)
                    print("STDERR:", install_process.stderr)
                    self.startup_errors.append(f"NPM install failed: {install_process.stderr}")
                    return False
                print("✅ Dependencies installed successfully")
            else:
                print("✅ Dependencies already installed")
            
            # Start the development server
            print("🔧 Launching React development server...")
            self.frontend_process = subprocess.Popen(
                ['npm', 'run', 'dev'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            print("✅ Frontend server launching...")
            print("🌐 Expected URL: http://localhost:4000")
            print("🎨 Professional Bloomberg Terminal interface")
            print("📈 Unified visualization with actual/predicted data")
            print()
            
            # Wait and verify startup
            print("⏳ Waiting for frontend initialization...")
            time.sleep(5)
            
            if self.frontend_process.poll() is not None:
                print("❌ Frontend process exited immediately")
                try:
                    stdout, stderr = self.frontend_process.communicate(timeout=3)
                    if stderr:
                        print(f"Error output: {stderr}")
                        self.startup_errors.append(f"Frontend startup error: {stderr}")
                except:
                    pass
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to start frontend: {e}")
            self.startup_errors.append(f"Frontend startup exception: {e}")
            return False
    
    def monitor_backend_output(self):
        """Enhanced backend output monitoring with error detection"""
        if not self.backend_process:
            return
            
        print("📊 BACKEND OUTPUT MONITOR:")
        print("-" * 70)
        
        try:
            for line in iter(self.backend_process.stdout.readline, ''):
                if not self.running:
                    break
                
                line = line.rstrip()
                print(f"[BACKEND] {line}")
                
                # Detect important events
                if "PREDICTION:" in line or "Generated prediction" in line:
                    print("🎯 " + "="*60)
                    print("🎯 NEW ML PREDICTION GENERATED!")
                    print("🎯 " + "="*60)
                
                if "✅ All systems operational!" in line:
                    print("🌟 " + "="*60)
                    print("🌟 BACKEND FULLY OPERATIONAL!")
                    print("🌟 " + "="*60)
                
                if "❌" in line or "ERROR" in line:
                    print(f"⚠️ Backend error detected: {line}")
                    self.startup_errors.append(f"Backend runtime error: {line}")
                
        except Exception as e:
            print(f"❌ Error monitoring backend: {e}")
    
    def monitor_frontend_output(self):
        """Enhanced frontend output monitoring"""
        if not self.frontend_process:
            return
            
        print("🌐 FRONTEND OUTPUT MONITOR:")
        print("-" * 70)
        
        try:
            for line in iter(self.frontend_process.stdout.readline, ''):
                if not self.running:
                    break
                
                line = line.rstrip()
                print(f"[FRONTEND] {line}")
                
                # Detect when frontend is ready
                if any(keyword in line.lower() for keyword in ['local:', 'localhost:4000', 'ready in']):
                    print("🌐 " + "="*60)
                    print("🌐 FRONTEND READY!")
                    print("🌐 Open http://localhost:4000 in your browser")
                    print("🌐 " + "="*60)
                
                if "error" in line.lower() or "failed" in line.lower():
                    if "warning" not in line.lower():  # Ignore warnings
                        print(f"⚠️ Frontend error detected: {line}")
                        self.startup_errors.append(f"Frontend runtime error: {line}")
                
        except Exception as e:
            print(f"❌ Error monitoring frontend: {e}")
    
    def comprehensive_health_check(self):
        """Comprehensive system health verification"""
        backend_healthy = False
        frontend_healthy = False
        
        max_attempts = 8  # Increased attempts for complex ML system
        
        print("🔍 Comprehensive Health Check:")
        print("=" * 60)
        
        # Check backend health with multiple attempts
        for attempt in range(max_attempts):
            try:
                print(f"   Backend health check {attempt + 1}/{max_attempts}...")
                response = requests.get('http://127.0.0.1:9000/health', timeout=10)
                if response.status_code == 200:
                    backend_healthy = True
                    data = response.json()
                    print(f"✅ Backend healthy - Status: {data.get('status', 'Unknown')}")
                    print(f"   ML System: {data.get('ml_current_status', 'Unknown')}")
                    print(f"   Complex ML: {'Enabled' if data.get('complex_ml_enabled') else 'Disabled'}")
                    print(f"   Version: {data.get('version', 'Unknown')}")
                    break
                else:
                    print(f"   ❌ Backend returned status {response.status_code}")
            except Exception as e:
                print(f"   ❌ Backend attempt {attempt + 1} failed: {e}")
                if attempt < max_attempts - 1:
                    time.sleep(4)  # Longer wait for complex ML startup
        
        if not backend_healthy:
            print("❌ Backend health check failed after all attempts")
            self.health_check_failures += 1
        
        # Check frontend health
        for attempt in range(max_attempts):
            try:
                print(f"   Frontend health check {attempt + 1}/{max_attempts}...")
                response = requests.get('http://localhost:4000', timeout=8)
                if response.status_code == 200:
                    frontend_healthy = True
                    print("✅ Frontend healthy and serving content")
                    break
                else:
                    print(f"   ❌ Frontend returned status {response.status_code}")
            except Exception as e:
                print(f"   ❌ Frontend attempt {attempt + 1} failed: {e}")
                if attempt < max_attempts - 1:
                    time.sleep(3)
        
        if not frontend_healthy:
            print("❌ Frontend health check failed after all attempts")
            self.health_check_failures += 1
        
        # Additional API endpoint checks
        if backend_healthy:
            try:
                print("   Checking ML status endpoint...")
                ml_response = requests.get('http://127.0.0.1:9000/ml-status', timeout=8)
                if ml_response.status_code == 200:
                    ml_data = ml_response.json()
                    print(f"✅ ML Status: {ml_data.get('ml_model_status', 'Unknown')}")
                    print(f"   Processing Time: {ml_data.get('expected_processing_time', 'Unknown')}")
                    print(f"   Cache Duration: {ml_data.get('cache_duration_minutes', 'Unknown')} minutes")
            except Exception as e:
                print(f"   ⚠️ ML status check failed: {e}")
        
        system_healthy = backend_healthy and frontend_healthy
        
        if system_healthy:
            self.health_check_failures = 0  # Reset on success
        
        return system_healthy
    
    def run_periodic_health_checks(self):
        """Run periodic health monitoring with failure tracking"""
        while self.running:
            time.sleep(120)  # Check every 2 minutes
            if self.running:
                print(f"\\n🏥 Periodic Health Check - {datetime.now().strftime('%H:%M:%S')}")
                print("-" * 50)
                
                system_healthy = self.comprehensive_health_check()
                
                if not system_healthy:
                    print(f"⚠️ Health check failure #{self.health_check_failures}")
                    if self.health_check_failures >= self.max_health_failures:
                        print("❌ Too many consecutive health check failures!")
                        print("   System may need manual intervention.")
                        # Could add automatic restart logic here
                else:
                    print("✅ System operating normally")
                
                if self.startup_errors:
                    print(f"⚠️ Accumulated startup errors: {len(self.startup_errors)}")
                    for i, error in enumerate(self.startup_errors[-3:], 1):  # Show last 3
                        print(f"   {i}. {error}")
                
                print()
    
    def shutdown(self):
        """Professional graceful shutdown with comprehensive cleanup"""
        print("\\n🛑 Initiating Professional System Shutdown...")
        print("=" * 60)
        self.running = False
        
        # Stop processes gracefully first
        shutdown_timeout = 15
        
        if self.backend_process:
            print("🔄 Stopping backend server gracefully...")
            try:
                self.backend_process.terminate()
                self.backend_process.wait(timeout=shutdown_timeout)
                print("✅ Backend stopped gracefully")
            except subprocess.TimeoutExpired:
                print("⚠️ Backend graceful shutdown timeout, force killing...")
                self.backend_process.kill()
                try:
                    self.backend_process.wait(timeout=5)
                    print("✅ Backend force killed")
                except:
                    print("❌ Backend kill failed")
        
        if self.frontend_process:
            print("🔄 Stopping frontend server gracefully...")
            try:
                self.frontend_process.terminate()
                self.frontend_process.wait(timeout=shutdown_timeout)
                print("✅ Frontend stopped gracefully")
            except subprocess.TimeoutExpired:
                print("⚠️ Frontend graceful shutdown timeout, force killing...")
                self.frontend_process.kill()
                try:
                    self.frontend_process.wait(timeout=5)
                    print("✅ Frontend force killed")
                except:
                    print("❌ Frontend kill failed")
        
        # Final comprehensive port cleanup
        print("🧹 Final comprehensive port cleanup...")
        killed_ports = kill_processes_using_ports([9000, 4000, 3000, 8000, 5173])
        
        # Clean up any remaining processes
        run_command("pkill -f 'server.py'")
        run_command("pkill -f 'npm.*dev'")
        run_command("pkill -f 'vite'")
        
        print("✅ System shutdown complete")
        
        # Print final summary
        if self.startup_errors:
            print("\\n📊 Session Summary:")
            print(f"   Startup errors encountered: {len(self.startup_errors)}")
            print(f"   Health check failures: {self.health_check_failures}")
        else:
            print("\\n🎉 Session completed without startup errors")

def main():
    """Main function with professional system orchestration"""
    print("🚀 Bloomberg Terminal Unified ML System - FINAL OPTIMIZED VERSION")
    print("=" * 80)
    print("🎨 UNIFIED VISUALIZATION SYSTEM:")
    print("   • Professional Bloomberg Terminal interface")
    print("   • Actual prices displayed as clean line charts")
    print("   • Predicted prices as filled areas with confidence bands")
    print("   • Seamless historical and future forecasting")
    print("   • Advanced 25-30 second ML prediction engine")
    print("   • 8-minute intelligent caching system")
    print("   • Real-time ML status and progress tracking")
    print("   • Enterprise-grade error handling and monitoring")
    print("=" * 80)
    print()
    
    # Step 1: Comprehensive cleanup
    cleanup_old_instances()
    
    # Step 2: Port verification
    if not verify_ports_free([9000, 4000]):
        print("\\n❌ Critical: Required ports are not free after cleanup.")
        print("   Manual cleanup commands:")
        print("   pkill -f 'run_system'; pkill -f 'server.py'; pkill -f 'npm.*dev'")
        print("   lsof -ti:9000 | xargs kill -9")
        print("   lsof -ti:4000 | xargs kill -9")
        return 1
    
    monitor = ProfessionalSystemMonitor()
    
    # Enhanced signal handlers
    def signal_handler(signum, frame):
        print(f"\\n🔔 Received signal {signum}, initiating shutdown...")
        monitor.shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start backend with extended initialization time
        print("🚀 Starting unified backend system...")
        if not monitor.start_backend():
            print("❌ Failed to start backend. Check logs above.")
            return 1
        
        print("⏳ Waiting for backend ML system initialization (15 seconds)...")
        time.sleep(15)  # Longer wait for complex ML initialization
        
        # Start frontend
        print("🌐 Starting professional frontend...")
        if not monitor.start_frontend():
            print("❌ Failed to start frontend. Cleaning up...")
            monitor.shutdown()
            return 1
        
        # Start monitoring threads
        backend_thread = threading.Thread(target=monitor.monitor_backend_output, daemon=True)
        frontend_thread = threading.Thread(target=monitor.monitor_frontend_output, daemon=True)
        health_thread = threading.Thread(target=monitor.run_periodic_health_checks, daemon=True)
        
        backend_thread.start()
        frontend_thread.start()
        
        # Extended wait for both services
        print("⏳ Waiting for both services to fully initialize (25 seconds)...")
        time.sleep(25)
        
        # Comprehensive health verification
        print("\\n🏥 Initial Comprehensive Health Verification:")
        print("=" * 60)
        system_healthy = monitor.comprehensive_health_check()
        
        if system_healthy:
            print("\\n🎉 SYSTEM FULLY OPERATIONAL!")
            print("=" * 80)
            print("🌐 Professional Frontend: http://localhost:4000")
            print("🖥️  Backend API: http://127.0.0.1:9000")
            print("📊 ML Status API: http://127.0.0.1:9000/ml-status")
            print("🏥 Health Check: http://127.0.0.1:9000/health")
            print("=" * 80)
            print("\\n💡 Professional System Information:")
            print("   • First ML prediction: 25-30 seconds (complex ML engine)")
            print("   • Subsequent predictions: Cached for 8 minutes")
            print("   • Real-time oil futures data with confidence bands")
            print("   • Unified visualization: Line (actual) + Area (predicted)")
            print("   • Professional Bloomberg Terminal interface")
            print("   • Enterprise-grade monitoring and error handling")
            print("   • Press Ctrl+C for graceful shutdown")
            print()
            
            # Start health monitoring
            health_thread.start()
            
        else:
            print("\\n⚠️ PARTIAL STARTUP - Some services not responding optimally")
            print("   The system may still be initializing complex ML components.")
            print("   Monitor the output above for detailed status information.")
            print("   URLs to check manually:")
            print("   • Frontend: http://localhost:4000")
            print("   • Backend: http://127.0.0.1:9000/health")
            print("   • ML Status: http://127.0.0.1:9000/ml-status")
        
        # Keep main thread alive with enhanced monitoring
        print("🔄 System running with professional monitoring... (Ctrl+C to stop)")
        try:
            while monitor.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\\n👋 Graceful shutdown requested by user")
        
    except Exception as e:
        print(f"❌ Critical error during system orchestration: {e}")
        monitor.startup_errors.append(f"Critical orchestration error: {e}")
        monitor.shutdown()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())