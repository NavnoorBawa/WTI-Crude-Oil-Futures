// contractUtils.js - WTI Contract Management Utilities

// Month codes for futures contracts
const MONTH_CODES = {
  1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
  7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'
};

// Reverse mapping for month codes
const CODE_TO_MONTH = {
  'F': 1, 'G': 2, 'H': 3, 'J': 4, 'K': 5, 'M': 6,
  'N': 7, 'Q': 8, 'U': 9, 'V': 10, 'X': 11, 'Z': 12
};

// Month names
const MONTH_NAMES = {
  1: 'January', 2: 'February', 3: 'March', 4: 'April',
  5: 'May', 6: 'June', 7: 'July', 8: 'August',
  9: 'September', 10: 'October', 11: 'November', 12: 'December'
};

/**
 * Get the current active WTI contract
 * @returns {Object} Contract information
 */
export function getCurrentWTIContract() {
  const now = new Date();
  const currentMonth = now.getMonth() + 1; // JavaScript months are 0-indexed
  const currentYear = now.getFullYear();
  const currentDay = now.getDate();
  
  let contractMonth = currentMonth;
  let contractYear = currentYear;
  
  // If we're past the 20th of the month, move to next month's contract
  if (currentDay >= 20) {
    contractMonth += 1;
    if (contractMonth > 12) {
      contractMonth = 1;
      contractYear += 1;
    }
  }
  
  const monthCode = MONTH_CODES[contractMonth];
  const yearCode = contractYear.toString().slice(-2);
  
  return {
    symbol: `CL${monthCode}${yearCode}`,
    fullSymbol: `CL${monthCode}${yearCode}`,
    yfinanceSymbol: "CL=F",
    month: contractMonth,
    year: contractYear,
    monthCode: monthCode,
    yearCode: yearCode,
    monthName: MONTH_NAMES[contractMonth],
    description: `WTI CRUDE OIL FUTURE ${MONTH_NAMES[contractMonth].toUpperCase().slice(0, 3)} 20${yearCode}`,
    fullDescription: `WTI Crude Oil Future ${MONTH_NAMES[contractMonth]} 20${yearCode}`
  };
}

/**
 * Get contract expiry date
 * @param {number} month - Contract month (1-12)
 * @param {number} year - Contract year (full year)
 * @returns {Date} Expiry date
 */
export function getContractExpiryDate(month, year) {
  // WTI contracts typically expire on the third business day prior to the 25th calendar day
  // For simplicity, we'll use the 22nd of the contract month
  return new Date(year, month - 1, 22); // JavaScript months are 0-indexed
}

/**
 * Parse contract symbol to get contract details
 * @param {string} symbol - Contract symbol (e.g., "CLZ25")
 * @returns {Object} Contract details
 */
export function parseContractSymbol(symbol) {
  if (!symbol || symbol.length < 5) {
    return null;
  }
  
  // Extract components: CL + Month Code + Year Code
  const prefix = symbol.slice(0, 2);
  const monthCode = symbol[2];
  const yearCode = symbol.slice(3);
  
  if (prefix !== 'CL' || !CODE_TO_MONTH[monthCode]) {
    return null;
  }
  
  const month = CODE_TO_MONTH[monthCode];
  const year = parseInt(`20${yearCode}`);
  
  return {
    symbol,
    month,
    year,
    monthCode,
    yearCode,
    monthName: MONTH_NAMES[month],
    description: `WTI CRUDE OIL FUTURE ${MONTH_NAMES[month].toUpperCase().slice(0, 3)} 20${yearCode}`,
    expiryDate: getContractExpiryDate(month, year)
  };
}

/**
 * Get the next contract after the given one
 * @param {number} month - Current contract month
 * @param {number} year - Current contract year
 * @returns {Object} Next contract info
 */
export function getNextContract(month, year) {
  let nextMonth = month + 1;
  let nextYear = year;
  
  if (nextMonth > 12) {
    nextMonth = 1;
    nextYear += 1;
  }
  
  const monthCode = MONTH_CODES[nextMonth];
  const yearCode = nextYear.toString().slice(-2);
  
  return {
    symbol: `CL${monthCode}${yearCode}`,
    month: nextMonth,
    year: nextYear,
    monthCode,
    yearCode,
    monthName: MONTH_NAMES[nextMonth],
    description: `WTI CRUDE OIL FUTURE ${MONTH_NAMES[nextMonth].toUpperCase().slice(0, 3)} 20${yearCode}`,
    expiryDate: getContractExpiryDate(nextMonth, nextYear)
  };
}

/**
 * Get list of nearby contracts (current + next few months)
 * @param {number} count - Number of contracts to return
 * @returns {Array} List of contract objects
 */
export function getNearbyContracts(count = 6) {
  const contracts = [];
  const current = getCurrentWTIContract();
  
  let month = current.month;
  let year = current.year;
  
  for (let i = 0; i < count; i++) {
    const monthCode = MONTH_CODES[month];
    const yearCode = year.toString().slice(-2);
    
    contracts.push({
      symbol: `CL${monthCode}${yearCode}`,
      month,
      year,
      monthCode,
      yearCode,
      monthName: MONTH_NAMES[month],
      description: `WTI CRUDE OIL FUTURE ${MONTH_NAMES[month].toUpperCase().slice(0, 3)} 20${yearCode}`,
      expiryDate: getContractExpiryDate(month, year),
      isCurrent: i === 0
    });
    
    // Move to next month
    month += 1;
    if (month > 12) {
      month = 1;
      year += 1;
    }
  }
  
  return contracts;
}

/**
 * Check if a contract is near expiry
 * @param {number} month - Contract month
 * @param {number} year - Contract year
 * @param {number} daysThreshold - Days before expiry to consider "near"
 * @returns {boolean} True if near expiry
 */
export function isNearExpiry(month, year, daysThreshold = 7) {
  const expiryDate = getContractExpiryDate(month, year);
  const now = new Date();
  const daysToExpiry = Math.ceil((expiryDate - now) / (1000 * 60 * 60 * 24));
  
  return daysToExpiry <= daysThreshold && daysToExpiry >= 0;
}

/**
 * Format contract for display
 * @param {Object} contract - Contract object
 * @returns {string} Formatted string
 */
export function formatContractDisplay(contract) {
  return `${contract.symbol} (${contract.monthName} ${contract.year})`;
}

/**
 * Get market hours information
 * @returns {Object} Market hours info
 */
export function getMarketHours() {
  return {
    // NYMEX WTI trading hours (ET)
    regularSession: {
      open: "09:00",
      close: "14:30"
    },
    electronicSession: {
      open: "18:00", // Sunday 6 PM ET
      close: "17:00"  // Friday 5 PM ET
    },
    timezone: "America/New_York"
  };
}

/**
 * Check if market is currently open
 * @returns {Object} Market status
 */
export function isMarketOpen() {
  const now = new Date();
  const nyTime = new Date(now.toLocaleString("en-US", {timeZone: "America/New_York"}));
  const hour = nyTime.getHours();
  const day = nyTime.getDay(); // 0 = Sunday, 6 = Saturday
  
  // Electronic trading: Sunday 6 PM ET - Friday 5 PM ET
  // Regular trading: Monday-Friday 9 AM - 2:30 PM ET
  
  if (day === 0 && hour >= 18) {
    return { isOpen: true, session: "Electronic", status: "OPEN" };
  }
  
  if (day >= 1 && day <= 5) {
    if (hour >= 9 && hour < 14 || (hour === 14 && nyTime.getMinutes() < 30)) {
      return { isOpen: true, session: "Regular", status: "OPEN" };
    } else if (hour >= 18 || hour < 17) {
      return { isOpen: true, session: "Electronic", status: "OPEN" };
    }
  }
  
  if (day === 5 && hour < 17) {
    if (hour >= 9 && hour < 14 || (hour === 14 && nyTime.getMinutes() < 30)) {
      return { isOpen: true, session: "Regular", status: "OPEN" };
    } else {
      return { isOpen: true, session: "Electronic", status: "OPEN" };
    }
  }
  
  return { isOpen: false, session: "Closed", status: "CLOSED" };
}

// Export all functions for use in React components
export default {
  getCurrentWTIContract,
  getContractExpiryDate,
  parseContractSymbol,
  getNextContract,
  getNearbyContracts,
  isNearExpiry,
  formatContractDisplay,
  getMarketHours,
  isMarketOpen,
  MONTH_CODES,
  CODE_TO_MONTH,
  MONTH_NAMES
};