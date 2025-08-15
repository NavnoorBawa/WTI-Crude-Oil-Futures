import React, { useState, useEffect } from "react";
import { getCurrentWTIContract } from "./contractUtils.js";

export default function Header() {
  const [currentTime, setCurrentTime] = useState(new Date());
  const [contract, setContract] = useState(getCurrentWTIContract());

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
      // Update contract info daily (in case we cross rollover date)
      setContract(getCurrentWTIContract());
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  const formatTime = (date, timezone) => {
    return new Intl.DateTimeFormat('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZone: timezone,
      hour12: false
    }).format(date);
  };

  const formatDate = (date) => {
    return new Intl.DateTimeFormat('en-US', {
      weekday: 'short',
      month: 'short',
      day: '2-digit',
      year: 'numeric'
    }).format(date);
  };

  const getMarketIndicator = () => {
    const now = new Date();
    const nyTime = new Date(now.toLocaleString("en-US", {timeZone: "America/New_York"}));
    const hour = nyTime.getHours();
    const day = nyTime.getDay();
    
    // NYMEX trading hours: 6 PM ET Sunday - 5 PM ET Friday
    if ((day === 0 && hour >= 18) || (day >= 1 && day <= 5) || (day === 5 && hour < 17)) {
      return { status: "MARKET OPEN", color: "text-green-400", icon: "●" };
    }
    return { status: "MARKET CLOSED", color: "text-red-400", icon: "●" };
  };

  const marketStatus = getMarketIndicator();

  return (
    <div className="bg-black text-orange-400 font-mono">
      {/* Bloomberg Top Bar - Authentic design */}
      <div className="bg-orange-500 text-black px-4 py-2 flex items-center justify-between">
        <div className="flex items-center space-x-6">
          <div className="flex items-center space-x-2">
            <span className="text-black font-bold text-lg">BLOOMBERG</span>
            <span className="text-gray-700 text-sm">TERMINAL</span>
            <span className="text-gray-700 text-sm">- {contract.fullSymbol}</span>
          </div>
        </div>
        
        <div className="flex items-center space-x-6 text-sm">
          <div className="text-center">
            <div className="text-black text-xs font-semibold">NYC</div>
            <div className="text-black font-mono">{formatTime(currentTime, 'America/New_York')}</div>
          </div>
          <div className="text-center">
            <div className="text-black text-xs font-semibold">LDN</div>
            <div className="text-black font-mono">{formatTime(currentTime, 'Europe/London')}</div>
          </div>
          <div className="text-center">
            <div className="text-black text-xs font-semibold">TKY</div>
            <div className="text-black font-mono">{formatTime(currentTime, 'Asia/Tokyo')}</div>
          </div>
          <div className="text-center">
            <div className="text-black text-xs font-semibold">UTC</div>
            <div className="text-black font-mono">{formatTime(currentTime, 'UTC')}</div>
          </div>
        </div>
      </div>

      {/* Main Terminal Header */}
      <div className="bg-black border-b border-orange-400 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <span className="text-orange-400 text-sm">COMMAND:</span>
            <div className="flex items-center space-x-2">
              <span className="bg-yellow-400 text-black px-2 py-1 font-bold text-sm">{contract.symbol}</span>
              <span className="text-orange-400 text-sm">&lt;Comdty&gt;</span>
              <span className="bg-yellow-400 text-black px-2 py-1 font-bold text-sm">GP</span>
              <span className="text-orange-400 text-sm">&lt;GO&gt;</span>
            </div>
            <span className="text-gray-400 text-sm">{contract.description}</span>
          </div>
          <div className="flex items-center space-x-4 text-sm">
            <span className="text-orange-400">LAST UPDATE:</span>
            <span className="text-green-400 font-bold">{formatTime(currentTime, 'UTC')} UTC</span>
            <span className="text-gray-400">|</span>
            <span className="text-cyan-400">DELAYED: 0ms</span>
          </div>
        </div>
      </div>

      {/* Function Path Bar - Bloomberg Navigation */}
      <div className="bg-gray-800 border-b border-orange-400 px-4 py-2 text-sm">
        <div className="flex items-center space-x-2 text-gray-400">
          <span>BLOOMBERG</span>
          <span>&gt;</span>
          <span>COMMODITIES</span>
          <span>&gt;</span>
          <span>ENERGY</span>
          <span>&gt;</span>
          <span className="text-orange-400">{contract.fullSymbol}</span>
          <span>&gt;</span>
          <span className="text-white">GP - GRAPHICAL ANALYSIS</span>
        </div>
      </div>
    </div>
  );
}