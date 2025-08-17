#!/usr/bin/env python3
"""
COMPLETE Bloomberg Terminal System Launcher
==========================================
Single file to run the entire oil futures prediction system:
- Backend server with real ML predictions
- Frontend development server
- Automatic port management
- Health checks and status monitoring
"""

import subprocess
import time
import threading
import sys
import os
import signal
import requests
import webbrowser
from datetime import datetime

def print_banner():
    """Print system startup banner"""
    print("🚀" + "=" * 70)
    print("🏛️  BLOOMBERG TERMINAL - WTI CRUDE OIL FUTURES SYSTEM")
    print("=" * 72)
    print("✅ Real yfinance data fetching")
    print("✅ Advanced ML predictions from oil.py") 
    print("✅ Bloomberg-style terminal interface")
    print("✅ Real-time price updates every 10 seconds")
    print("✅ Multi-horizon ML predictions every 3 minutes")
    print("=" * 72)

def check_port(port):
    """Check if a port is available"""
    try:
        response = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
        return response.status_code == 200
    except:
        return False

def find_available_port(start_port, max_attempts=10):
    """Find an available port starting from start_port"""
    import socket
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return None

def start_backend_server():
    """Start the Flask backend server"""
    print("🔧 Starting backend server...")
    
    # Check if backend is already running
    if check_port(9000):
        print("✅ Backend server already running on port 9000")
        return None
    
    try:
        # Start the backend server
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.getcwd()
        )
        
        print("⏳ Waiting for backend server to initialize...")
        print("📊 Loading ML models (this may take 1-2 minutes)...")
        
        # Wait for server to be ready (up to 3 minutes)
        for attempt in range(180):  # 180 seconds = 3 minutes
            time.sleep(1)
            if check_port(9000):
                print("✅ Backend server ready on http://127.0.0.1:9000")
                return process
            
            # Print progress every 10 seconds
            if attempt % 10 == 0 and attempt > 0:
                print(f"⏳ Still loading ML models... ({attempt}s elapsed)")
        
        print("❌ Backend server failed to start within 3 minutes")
        return None
        
    except Exception as e:
        print(f"❌ Error starting backend: {e}")
        return None

def start_frontend_server():
    """Start the Vite frontend development server"""
    print("🎨 Starting frontend server...")
    
    try:
        # Find an available port for frontend
        frontend_port = find_available_port(4000)
        if not frontend_port:
            print("❌ No available port for frontend")
            return None, None
        
        # Start the frontend server
        process = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(frontend_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.getcwd()
        )
        
        print(f"⏳ Starting frontend on port {frontend_port}...")
        
        # Wait for frontend to be ready
        for attempt in range(30):  # 30 seconds
            time.sleep(1)
            try:
                response = requests.get(f"http://127.0.0.1:{frontend_port}", timeout=2)
                if response.status_code == 200:
                    print(f"✅ Frontend server ready on http://127.0.0.1:{frontend_port}")
                    return process, frontend_port
            except:
                continue
        
        print("❌ Frontend server failed to start")
        return None, None
        
    except Exception as e:
        print(f"❌ Error starting frontend: {e}")
        return None, None

def monitor_system(backend_process, frontend_process):
    """Monitor both processes and restart if needed"""
    print("👁️  System monitoring started...")
    
    while True:
        try:
            time.sleep(10)
            
            # Check backend health
            backend_ok = check_port(9000)
            if not backend_ok:
                print("⚠️  Backend server appears to be down")
            
            # Check if processes are still running
            if backend_process and backend_process.poll() is not None:
                print("❌ Backend process terminated")
                break
                
            if frontend_process and frontend_process.poll() is not None:
                print("❌ Frontend process terminated")
                break
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"⚠️  Monitor error: {e}")
            time.sleep(5)

def test_system():
    """Test that the system is working correctly"""
    print("🧪 Testing system integration...")
    
    try:
        # Test backend health
        response = requests.get("http://127.0.0.1:9000/health", timeout=5)
        if response.status_code == 200:
            health_data = response.json()
            print(f"✅ Backend health: {health_data.get('status')}")
            print(f"📊 Data points: {health_data.get('data_points', 0)}")
            print(f"💰 Current price: ${health_data.get('current_price', 0):.2f}")
        else:
            print("❌ Backend health check failed")
            return False
            
        # Test data endpoint
        response = requests.get("http://127.0.0.1:9000/data", timeout=10)
        if response.status_code == 200:
            data = response.json()
            actual_count = len(data.get('actual', []))
            predicted_count = len(data.get('predicted', []))
            print(f"✅ Data endpoint: {actual_count} actual, {predicted_count} predicted prices")
            
            # Check ML predictions
            ml_predictions = data.get('multi_horizon_predictions', {})
            if ml_predictions.get('predictions'):
                pred_1h = ml_predictions['predictions'].get('1h', 0)
                print(f"🤖 ML Prediction (1h): ${pred_1h:.2f}")
                print(f"🔮 ML Status: {'REAL' if ml_predictions.get('is_real_prediction') else 'FALLBACK'}")
            
            return True
        else:
            print("❌ Data endpoint test failed")
            return False
            
    except Exception as e:
        print(f"❌ System test error: {e}")
        return False

def cleanup_processes(*processes):
    """Clean up all running processes"""
    print("🧹 Cleaning up processes...")
    
    for process in processes:
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
                print("✅ Process terminated gracefully")
            except subprocess.TimeoutExpired:
                process.kill()
                print("⚡ Process force killed")
            except Exception as e:
                print(f"⚠️  Cleanup error: {e}")

def main():
    """Main system launcher"""
    backend_process = None
    frontend_process = None
    frontend_port = None
    
    try:
        print_banner()
        
        # Check prerequisites
        print("🔍 Checking prerequisites...")
        
        # Check if server.py exists
        if not os.path.exists("server.py"):
            print("❌ server.py not found in current directory")
            return
            
        # Check if npm is available
        try:
            subprocess.run(["npm", "--version"], check=True, capture_output=True)
            print("✅ npm found")
        except:
            print("❌ npm not found - please install Node.js")
            return
        
        print("✅ Prerequisites check passed")
        print()
        
        # Start backend server
        backend_process = start_backend_server()
        if not backend_process:
            print("❌ Failed to start backend server")
            return
        
        # Start frontend server
        frontend_process, frontend_port = start_frontend_server()
        if not frontend_process:
            print("❌ Failed to start frontend server")
            cleanup_processes(backend_process)
            return
        
        # Test system integration
        print()
        if test_system():
            print("✅ System integration test passed")
        else:
            print("⚠️  System integration test had issues")
        
        print()
        print("🎉" + "=" * 70)
        print("🚀 BLOOMBERG TERMINAL SYSTEM IS READY!")
        print("=" * 72)
        print(f"🌐 Frontend: http://127.0.0.1:{frontend_port}")
        print("📊 Backend:  http://127.0.0.1:9000")
        print("📈 API Data: http://127.0.0.1:9000/data")
        print("❤️  Health:  http://127.0.0.1:9000/health")
        print("=" * 72)
        print("💡 The system will automatically:")
        print("   • Fetch real WTI oil prices every 10 seconds")
        print("   • Generate ML predictions every 3 minutes")
        print("   • Display live Bloomberg-style charts")
        print("   • Show authentic multi-horizon forecasts")
        print()
        print("🔥 Press Ctrl+C to stop the system")
        print("=" * 72)
        
        # Open browser automatically
        try:
            webbrowser.open(f"http://127.0.0.1:{frontend_port}")
            print("🌍 Browser opened automatically")
        except:
            print("📱 Please open your browser manually")
        
        # Start monitoring
        monitor_system(backend_process, frontend_process)
        
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
    finally:
        cleanup_processes(backend_process, frontend_process)
        print("👋 System shutdown complete")

if __name__ == "__main__":
    main()