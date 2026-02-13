import React, { useState } from 'react';
import Select from '../ui/Select';
import Input from '../ui/Input';
import { Info } from 'lucide-react';

const StrikeSelector = ({ leg, onUpdate }) => {
  const [strikeMethod, setStrikeMethod] = useState(leg?.strikeMethod || 'STRIKE_TYPE');
  
  const strikeMethods = [
    { value: 'STRIKE_TYPE', label: 'Strike Type (ATM/ITM/OTM)', icon: '🎯' },
    { value: 'PREMIUM_RANGE', label: 'Premium Range', icon: '📊' },
    { value: 'CLOSEST_PREMIUM', label: 'Closest Premium', icon: '🔍' },
    { value: 'PREMIUM_THRESHOLD', label: 'Premium Threshold (>= / <=)', icon: '📈' },
    { value: 'STRADDLE_WIDTH', label: 'Straddle Width', icon: '↔️' },
    { value: 'PERCENT_ATM', label: '% of ATM', icon: '%' },
    { value: 'ATM_STRADDLE_PCT', label: 'ATM Straddle Premium %', icon: '💹' },
  ];
  
  const handleMethodChange = (method) => {
    setStrikeMethod(method);
    if (onUpdate) {
      onUpdate({ ...leg, strikeMethod: method });
    }
  };
  
  const renderMethodConfig = () => {
    switch(strikeMethod) {
      case 'STRIKE_TYPE':
        return (
          <div className="space-y-4 mt-4">
            <Select
              label="Strike Selection"
              value={leg?.strikeType || 'ATM'}
              onChange={(value) => onUpdate({ ...leg, strikeType: value })}
              options={[
                { value: 'ITM5', label: 'ITM5 (5 strikes In-The-Money)' },
                { value: 'ITM4', label: 'ITM4 (4 strikes In-The-Money)' },
                { value: 'ITM3', label: 'ITM3 (3 strikes In-The-Money)' },
                { value: 'ITM2', label: 'ITM2 (2 strikes In-The-Money)' },
                { value: 'ITM1', label: 'ITM1 (1 strike In-The-Money)' },
                { value: 'ATM', label: 'ATM (At-The-Money)' },
                { value: 'OTM1', label: 'OTM1 (1 strike Out-of-The-Money)' },
                { value: 'OTM2', label: 'OTM2 (2 strikes Out-of-The-Money)' },
                { value: 'OTM3', label: 'OTM3 (3 strikes Out-of-The-Money)' },
                { value: 'OTM4', label: 'OTM4 (4 strikes Out-of-The-Money)' },
                { value: 'OTM5', label: 'OTM5 (5 strikes Out-of-The-Money)' },
              ]}
              hint="Select how many strikes away from ATM"
            />
          </div>
        );
        
      case 'PREMIUM_RANGE':
        return (
          <div className="grid grid-cols-2 gap-4 mt-4">
            <Input
              label="Lower Range"
              type="number"
              value={leg?.premiumLower || ''}
              onChange={(e) => onUpdate({ ...leg, premiumLower: e.target.value })}
              suffix="₹"
              placeholder="50"
              hint="Minimum premium value"
            />
            <Input
              label="Upper Range"
              type="number"
              value={leg?.premiumUpper || ''}
              onChange={(e) => onUpdate({ ...leg, premiumUpper: e.target.value })}
              suffix="₹"
              placeholder="200"
              hint="Maximum premium value"
            />
          </div>
        );
        
      case 'CLOSEST_PREMIUM':
        return (
          <div className="mt-4">
            <Input
              label="Target Premium"
              type="number"
              value={leg?.targetPremium || ''}
              onChange={(e) => onUpdate({ ...leg, targetPremium: e.target.value })}
              suffix="₹"
              placeholder="150"
              hint="Strike with premium closest to this value will be selected"
            />
          </div>
        );
        
      case 'PREMIUM_THRESHOLD':
        return (
          <div className="space-y-4 mt-4">
            <Select
              label="Operator"
              value={leg?.thresholdOperator || '>='}
              onChange={(value) => onUpdate({ ...leg, thresholdOperator: value })}
              options={[
                { value: '>=', label: 'Premium >= (Greater than or equal)' },
                { value: '<=', label: 'Premium <= (Less than or equal)' },
              ]}
            />
            <Input
              label="Threshold Value"
              type="number"
              value={leg?.thresholdValue || ''}
              onChange={(e) => onUpdate({ ...leg, thresholdValue: e.target.value })}
              suffix="₹"
              placeholder="100"
            />
          </div>
        );
        
      case 'STRADDLE_WIDTH':
        return (
          <div className="mt-4">
            <Select
              label="Straddle Width"
              value={leg?.straddleWidth || '0'}
              onChange={(value) => onUpdate({ ...leg, straddleWidth: value })}
              options={[
                { value: '0', label: 'ATM Straddle (0 strikes)' },
                { value: '1', label: '± 1 strike from ATM' },
                { value: '2', label: '± 2 strikes from ATM' },
                { value: '3', label: '± 3 strikes from ATM' },
                { value: '4', label: '± 4 strikes from ATM' },
              ]}
              hint="Distance between call and put strikes"
            />
          </div>
        );
        
      case 'PERCENT_ATM':
        return (
          <div className="space-y-4 mt-4">
            <div className="flex gap-2">
              <button 
                onClick={() => onUpdate({ ...leg, percentDirection: 'below' })}
                className={`flex-1 py-2 px-4 rounded-lg transition-colors ${
                  leg?.percentDirection === 'below' 
                    ? 'bg-primary-600 text-white' 
                    : 'bg-dark-hover text-gray-100 hover:bg-dark-border'
                }`}
              >
                - (Below ATM)
              </button>
              <button 
                onClick={() => onUpdate({ ...leg, percentDirection: 'above' })}
                className={`flex-1 py-2 px-4 rounded-lg transition-colors ${
                  leg?.percentDirection === 'above' 
                    ? 'bg-primary-600 text-white' 
                    : 'bg-dark-hover text-gray-100 hover:bg-dark-border'
                }`}
              >
                + (Above ATM)
              </button>
            </div>
            <Input
              label="Percentage"
              type="number"
              step="0.1"
              value={leg?.percentValue || ''}
              onChange={(e) => onUpdate({ ...leg, percentValue: e.target.value })}
              suffix="%"
              placeholder="1.5"
              hint="e.g., +1.5% for 1.5% above ATM"
            />
          </div>
        );
        
      case 'ATM_STRADDLE_PCT':
        return (
          <div className="mt-4">
            <Input
              label="ATM Straddle Premium %"
              type="number"
              min="0"
              max="100"
              value={leg?.straddlePct || ''}
              onChange={(e) => onUpdate({ ...leg, straddlePct: e.target.value })}
              suffix="%"
              placeholder="50"
              hint="Percentage of combined ATM Call + Put premium"
            />
          </div>
        );
        
      default:
        return null;
    }
  };
  
  const getMethodExplanation = (method) => {
    const explanations = {
      STRIKE_TYPE: 'Selects strike based on number of strikes away from ATM. OTM1 = one strike above ATM for calls.',
      PREMIUM_RANGE: 'Finds strikes where premium falls within specified lower and upper bounds.',
      CLOSEST_PREMIUM: 'Selects the strike with premium closest to your target value.',
      PREMIUM_THRESHOLD: 'Filters strikes based on premium being above or below threshold.',
      STRADDLE_WIDTH: 'Creates symmetric positions around ATM with specified width.',
      PERCENT_ATM: 'Calculates strike as percentage offset from ATM strike price.',
      ATM_STRADDLE_PCT: 'Selects strike based on percentage of combined ATM straddle premium.',
    };
    
    return explanations[method] || 'Select a method to see explanation';
  };
  
  return (
    <div className="bg-dark-hover rounded-lg p-4 border border-dark-border">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-2xl">🎯</span>
        <h4 className="text-sm font-semibold text-gray-100">Strike Selection Criteria</h4>
      </div>
      
      <Select
        label="Selection Method"
        value={strikeMethod}
        onChange={handleMethodChange}
        options={strikeMethods}
      />
      
      {renderMethodConfig()}
      
      {/* Info Box */}
      <div className="mt-4 p-3 bg-primary-900/20 border border-primary-800/30 rounded-lg">
        <div className="flex gap-2">
          <Info className="h-4 w-4 text-primary-400 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-primary-300">
            <p className="font-medium mb-1">How this works:</p>
            <p className="text-primary-400">
              {getMethodExplanation(strikeMethod)}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default StrikeSelector;
