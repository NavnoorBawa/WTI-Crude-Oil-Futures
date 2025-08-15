#!/bin/bash

echo "🧹 Oil Futures System Complete Cleanup and Restart"
echo "=================================================="

# Step 1: Kill ALL related processes
echo "🔍 Step 1: Identifying and killing all oil futures processes..."

# Kill all run_system.py processes
echo "   Killing run_system.py processes..."
pkill -f "run_system.py" || echo "   No run_system.py processes found"

# Kill all server.py processes  
echo "   Killing server.py processes..."
pkill -f "server.py" || echo "   No server.py processes found"

# Kill all npm/node processes in this directory
echo "   Killing npm/node processes..."
pkill -f "npm.*dev" || echo "   No npm dev processes found" 
pkill -f "vite" || echo "   No vite processes found"

# Step 2: Clean up ports forcefully
echo "🔌 Step 2: Forcefully cleaning up ports..."

# Kill processes using ports 8000, 3000, 9000, 4000, 5173, 5500
for port in 8000 3000 9000 4000 5173 5500; do
    echo "   Checking port $port..."
    lsof -ti:$port | xargs -r kill -9 2>/dev/null && echo "   ✅ Cleaned port $port" || echo "   ✅ Port $port already free"
done

# Step 3: Wait for processes to fully terminate
echo "⏳ Step 3: Waiting for processes to terminate..."
sleep 5

# Step 4: Verify cleanup
echo "🔍 Step 4: Verifying cleanup..."
remaining=$(ps aux | grep -E "(run_system|server\.py|npm.*dev|vite)" | grep -v grep | wc -l)
if [ $remaining -eq 0 ]; then
    echo "   ✅ All processes cleaned successfully"
else
    echo "   ⚠️  Warning: $remaining processes still running"
    ps aux | grep -E "(run_system|server\.py|npm.*dev|vite)" | grep -v grep
fi

# Step 5: Check port availability
echo "🔌 Step 5: Verifying port availability..."
for port in 9000 4000; do
    if ! lsof -i:$port >/dev/null 2>&1; then
        echo "   ✅ Port $port is free"
    else
        echo "   ❌ Port $port is still in use:"
        lsof -i:$port
    fi
done

echo ""
echo "🚀 Step 6: Starting fresh system..."
echo "   Launching run_system.py..."

# Change to the correct directory and start the system
cd "/Users/navnoorbawa/Downloads/Oil-futures-prediction-main"
python3 run_system.py

echo "✅ Cleanup and restart complete!"