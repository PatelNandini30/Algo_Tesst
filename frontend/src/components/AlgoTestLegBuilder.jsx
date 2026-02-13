import React, { useState } from 'react';

const AlgoTestLegBuilder = ({ legs, onLegsChange }) => {
  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      segment: 'OPTIONS',
      option_type: 'CE',
      position: 'SELL',
      lots: 1,
      strike_selection: 'ATM',
      expiry: 'WEEKLY'
    };
    
    onLegsChange([...legs, newLeg]);
  };
  
  const removeLeg = (index) => {
    const newLegs = legs.filter((_, i) => i !== index);
    onLegsChange(newLegs);
  };
  
  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index][field] = value;
    onLegsChange(newLegs);
  };
  
  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold">Strategy Legs</h3>
        <button
          onClick={addLeg}
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          + Add Leg
        </button>
      </div>
      
      {legs.map((leg, index) => (
        <div key={leg.id} className="border rounded-lg p-4 space-y-4">
          <div className="flex justify-between items-center">
            <h4 className="font-medium">Leg {index + 1}</h4>
            <button
              onClick={() => removeLeg(index)}
              className="px-3 py-1 bg-red-500 text-white rounded hover:bg-red-600"
            >
              Remove
            </button>
          </div>
          
          {/* Segment Selection */}
          <div>
            <label className="block text-sm font-medium mb-1">Segment</label>
            <div className="flex gap-2">
              <button
                onClick={() => updateLeg(index, 'segment', 'FUTURES')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.segment === 'FUTURES' 
                    ? 'bg-blue-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Futures
              </button>
              <button
                onClick={() => updateLeg(index, 'segment', 'OPTIONS')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.segment === 'OPTIONS' 
                    ? 'bg-blue-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Options
              </button>
            </div>
          </div>
          
          {/* Total Lot */}
          <div>
            <label className="block text-sm font-medium mb-1">Total Lot</label>
            <input
              type="number"
              min="1"
              value={leg.lots}
              onChange={(e) => updateLeg(index, 'lots', parseInt(e.target.value))}
              className="w-full p-2 border rounded"
            />
          </div>
          
          {/* Position */}
          <div>
            <label className="block text-sm font-medium mb-1">Position</label>
            <div className="flex gap-2">
              <button
                onClick={() => updateLeg(index, 'position', 'BUY')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.position === 'BUY' 
                    ? 'bg-green-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Buy
              </button>
              <button
                onClick={() => updateLeg(index, 'position', 'SELL')}
                className={`flex-1 py-2 px-4 rounded ${
                  leg.position === 'SELL' 
                    ? 'bg-red-600 text-white' 
                    : 'bg-gray-200'
                }`}
              >
                Sell
              </button>
            </div>
          </div>
          
          {/* OPTIONS-SPECIFIC FIELDS */}
          {leg.segment === 'OPTIONS' && (
            <>
              {/* Option Type */}
              <div>
                <label className="block text-sm font-medium mb-1">Option Type</label>
                <div className="flex gap-2">
                  <button
                    onClick={() => updateLeg(index, 'option_type', 'CE')}
                    className={`flex-1 py-2 px-4 rounded ${
                      leg.option_type === 'CE' 
                        ? 'bg-blue-600 text-white' 
                        : 'bg-gray-200'
                    }`}
                  >
                    Call
                  </button>
                  <button
                    onClick={() => updateLeg(index, 'option_type', 'PE')}
                    className={`flex-1 py-2 px-4 rounded ${
                      leg.option_type === 'PE' 
                        ? 'bg-blue-600 text-white' 
                        : 'bg-gray-200'
                    }`}
                  >
                    Put
                  </button>
                </div>
              </div>
              
              {/* Expiry */}
              <div>
                <label className="block text-sm font-medium mb-1">Expiry</label>
                <select
                  value={leg.expiry}
                  onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                  className="w-full p-2 border rounded"
                >
                  <option value="WEEKLY">Weekly</option>
                  <option value="MONTHLY">Monthly</option>
                </select>
              </div>
              
              {/* Strike Criteria */}
              <div>
                <label className="block text-sm font-medium mb-1">Strike Criteria</label>
                <select
                  value={leg.strike_selection}
                  onChange={(e) => updateLeg(index, 'strike_selection', e.target.value)}
                  className="w-full p-2 border rounded"
                >
                  <optgroup label="In The Money">
                    {[20,19,18,17,16,15,14,13,12,11,10,9,8,7,6,5,4,3,2,1].map(i => (
                      <option key={`ITM${i}`} value={`ITM${i}`}>ITM {i}</option>
                    ))}
                  </optgroup>
                  <option value="ATM">ATM (At The Money)</option>
                  <optgroup label="Out of The Money">
                    {[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30].map(i => (
                      <option key={`OTM${i}`} value={`OTM${i}`}>OTM {i}</option>
                    ))}
                  </optgroup>
                </select>
              </div>
            </>
          )}
        </div>
      ))}
      
      {legs.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          No legs added. Click "Add Leg" to start building your strategy.
        </div>
      )}
    </div>
  );
};

export default AlgoTestLegBuilder;