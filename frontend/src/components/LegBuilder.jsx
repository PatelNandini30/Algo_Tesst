import React from 'react';

const LegBuilder = ({ legs = [], onLegsChange }) => {
  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      instrumentType: 'CE',
      buySell: 'SELL',
      lots: 1,
      strikeType: 'ATM',
      strikeValue: '',
      expiry: 'Current Weekly',
      stopLoss: '',
      stopLossType: 'Points',
      target: '',
      targetType: 'Points'
    };
    onLegsChange([...legs, newLeg]);
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index] = { ...newLegs[index], [field]: value };
    onLegsChange(newLegs);
  };

  const removeLeg = (index) => {
    const newLegs = legs.filter((_, i) => i !== index);
    onLegsChange(newLegs);
  };

  const moveLeg = (fromIndex, toIndex) => {
    const newLegs = [...legs];
    const leg = newLegs[fromIndex];
    newLegs.splice(fromIndex, 1);
    newLegs.splice(toIndex, 0, leg);
    onLegsChange(newLegs);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold">Option Legs</h3>
        <button
          type="button"
          onClick={addLeg}
          className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium"
        >
          + Add Leg
        </button>
      </div>

      {legs.map((leg, index) => (
        <div key={leg.id} className="border border-gray-200 rounded-md p-4 bg-gray-50">
          <div className="grid grid-cols-12 gap-2 items-center">
            {/* Instrument Type */}
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-700 mb-1">Instrument</label>
              <select
                value={leg.instrumentType}
                onChange={(e) => updateLeg(index, 'instrumentType', e.target.value)}
                className="w-full p-1 text-xs border border-gray-300 rounded focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="CE">CE</option>
                <option value="PE">PE</option>
                <option value="FUT">FUT</option>
              </select>
            </div>

            {/* Buy/Sell */}
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-700 mb-1">Direction</label>
              <div className="flex rounded-sm shadow-sm">
                <button
                  type="button"
                  className={`flex-1 py-1 text-xs font-medium rounded-l ${
                    leg.buySell === 'BUY'
                      ? 'bg-green-600 text-white'
                      : 'bg-white text-gray-700'
                  }`}
                  onClick={() => updateLeg(index, 'buySell', 'BUY')}
                >
                  BUY
                </button>
                <button
                  type="button"
                  className={`flex-1 py-1 text-xs font-medium rounded-r ${
                    leg.buySell === 'SELL'
                      ? 'bg-red-600 text-white'
                      : 'bg-white text-gray-700'
                  }`}
                  onClick={() => updateLeg(index, 'buySell', 'SELL')}
                >
                  SELL
                </button>
              </div>
            </div>

            {/* Lots */}
            <div className="col-span-1">
              <label className="block text-xs font-medium text-gray-700 mb-1">Lots</label>
              <input
                type="number"
                min="1"
                value={leg.lots}
                onChange={(e) => updateLeg(index, 'lots', parseInt(e.target.value) || 1)}
                className="w-full p-1 text-xs border border-gray-300 rounded focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            {/* Strike Type */}
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-700 mb-1">Strike Type</label>
              <select
                value={leg.strikeType}
                onChange={(e) => updateLeg(index, 'strikeType', e.target.value)}
                className="w-full p-1 text-xs border border-gray-300 rounded focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="ATM">ATM</option>
                <option value="OTM%">OTM%</option>
                <option value="ITM%">ITM%</option>
                <option value="Spot%">Spot%</option>
                <option value="Premium-Based">Premium-Based</option>
              </select>
            </div>

            {/* Strike Value - only shown when type is not ATM */}
            {(leg.strikeType !== 'ATM') && (
              <div className="col-span-1">
                <label className="block text-xs font-medium text-gray-700 mb-1">Value</label>
                <input
                  type="number"
                  step="0.01"
                  value={leg.strikeValue}
                  onChange={(e) => updateLeg(index, 'strikeValue', parseFloat(e.target.value) || '')}
                  className="w-full p-1 text-xs border border-gray-300 rounded focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
            )}

            {/* Expiry */}
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-700 mb-1">Expiry</label>
              <select
                value={leg.expiry}
                onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                className="w-full p-1 text-xs border border-gray-300 rounded focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="Current Weekly">Current Weekly</option>
                <option value="Next Weekly">Next Weekly</option>
                <option value="Current Monthly">Current Monthly</option>
                <option value="Next Monthly">Next Monthly</option>
                <option value="Month+2">Month+2</option>
              </select>
            </div>

            {/* Stop Loss */}
            <div className="col-span-1">
              <label className="block text-xs font-medium text-gray-700 mb-1">SL</label>
              <div className="flex">
                <input
                  type="number"
                  step="0.01"
                  value={leg.stopLoss}
                  onChange={(e) => updateLeg(index, 'stopLoss', parseFloat(e.target.value) || '')}
                  placeholder="Value"
                  className="w-1/2 p-1 text-xs border border-gray-300 rounded-l focus:ring-blue-500 focus:border-blue-500"
                />
                <select
                  value={leg.stopLossType}
                  onChange={(e) => updateLeg(index, 'stopLossType', e.target.value)}
                  className="w-1/2 p-1 text-xs border border-l-0 border-gray-300 rounded-r focus:ring-blue-500 focus:border-blue-500"
                >
                  <option value="Points">Pts</option>
                  <option value="%">%</option>
                  <option value="Premium%">Prem%</option>
                </select>
              </div>
            </div>

            {/* Target */}
            <div className="col-span-1">
              <label className="block text-xs font-medium text-gray-700 mb-1">Target</label>
              <div className="flex">
                <input
                  type="number"
                  step="0.01"
                  value={leg.target}
                  onChange={(e) => updateLeg(index, 'target', parseFloat(e.target.value) || '')}
                  placeholder="Value"
                  className="w-1/2 p-1 text-xs border border-gray-300 rounded-l focus:ring-blue-500 focus:border-blue-500"
                />
                <select
                  value={leg.targetType}
                  onChange={(e) => updateLeg(index, 'targetType', e.target.value)}
                  className="w-1/2 p-1 text-xs border border-l-0 border-gray-300 rounded-r focus:ring-blue-500 focus:border-blue-500"
                >
                  <option value="Points">Pts</option>
                  <option value="%">%</option>
                  <option value="Premium%">Prem%</option>
                </select>
              </div>
            </div>

            {/* Move and Delete Buttons */}
            <div className="col-span-1 flex space-x-1">
              <button
                type="button"
                onClick={() => index > 0 && moveLeg(index, index - 1)}
                disabled={index === 0}
                className="p-1 text-xs bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50"
                title="Move Up"
              >
                ↑
              </button>
              <button
                type="button"
                onClick={() => index < legs.length - 1 && moveLeg(index, index + 1)}
                disabled={index === legs.length - 1}
                className="p-1 text-xs bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50"
                title="Move Down"
              >
                ↓
              </button>
              <button
                type="button"
                onClick={() => removeLeg(index)}
                className="p-1 text-xs bg-red-100 hover:bg-red-200 rounded text-red-600"
                title="Remove Leg"
              >
                ×
              </button>
            </div>
          </div>
        </div>
      ))}

      {legs.length === 0 && (
        <div className="text-center py-4 text-gray-500 text-sm">
          No legs added yet. Click "Add Leg" to configure your strategy.
        </div>
      )}
    </div>
  );
};

export default LegBuilder;