import React, { useState } from 'react';
import { Plus, Trash2, GripVertical } from 'lucide-react';

const LegBuilder = ({ legs, onLegsChange }) => {
  const [expandedLegIndex, setExpandedLegIndex] = useState(0);

  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      instrument: 'OPTION',
      optionType: 'CE',
      position: 'SELL',
      strikeSelection: {
        type: 'ATM',
        value: 0,
        spotAdjustmentMode: 0,
        spotAdjustment: 0
      },
      quantity: 1,
      expiryType: 'WEEKLY'
    };
    onLegsChange([...legs, newLeg]);
    setExpandedLegIndex(legs.length); // Expand the newly added leg
  };

  const removeLeg = (index) => {
    const newLegs = [...legs];
    newLegs.splice(index, 1);
    onLegsChange(newLegs);
    if (expandedLegIndex >= newLegs.length && newLegs.length > 0) {
      setExpandedLegIndex(newLegs.length - 1);
    } else if (newLegs.length === 0) {
      setExpandedLegIndex(-1);
    }
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    if (field.startsWith('strikeSelection.')) {
      const strikeField = field.split('.')[1];
      newLegs[index].strikeSelection = {
        ...newLegs[index].strikeSelection,
        [strikeField]: value
      };
    } else {
      newLegs[index][field] = value;
    }
    onLegsChange(newLegs);
  };

  const toggleExpand = (index) => {
    setExpandedLegIndex(expandedLegIndex === index ? -1 : index);
  };

  const getLegLabel = (leg) => {
    const instrumentLabel = leg.instrument === 'OPTION' ? 
      `${leg.optionType} ${leg.position}` : 
      `${leg.instrument} ${leg.position}`;
    return `${instrumentLabel} (${leg.quantity})`;
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold">Strategy Legs</h3>
        <button
          type="button"
          onClick={addLeg}
          className="flex items-center gap-2 py-2 px-4 bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          <Plus size={16} />
          Add Leg
        </button>
      </div>

      {legs.length === 0 ? (
        <div className="text-center py-8 text-gray-500">
          No legs added yet. Click "Add Leg" to start building your strategy.
        </div>
      ) : (
        <div className="space-y-3">
          {legs.map((leg, index) => (
            <div key={leg.id} className="border rounded-lg overflow-hidden">
              <div
                className={`flex justify-between items-center p-3 cursor-pointer ${
                  expandedLegIndex === index ? 'bg-gray-50' : 'bg-white'
                }`}
                onClick={() => toggleExpand(index)}
              >
                <div className="flex items-center gap-3">
                  <GripVertical size={16} className="text-gray-400" />
                  <span className="font-medium">Leg {index + 1}: {getLegLabel(leg)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      removeLeg(index);
                    }}
                    className="p-1 text-red-500 hover:bg-red-50 rounded"
                  >
                    <Trash2 size={16} />
                  </button>
                  <span>{expandedLegIndex === index ? '▼' : '▶'}</span>
                </div>
              </div>

              {expandedLegIndex === index && (
                <div className="p-4 bg-white border-t">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* Instrument */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Instrument
                      </label>
                      <select
                        value={leg.instrument}
                        onChange={(e) => updateLeg(index, 'instrument', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="OPTION">Option</option>
                        <option value="FUTURE">Future</option>
                      </select>
                    </div>

                    {/* Option Type (only shown if instrument is OPTION) */}
                    {leg.instrument === 'OPTION' && (
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          Option Type
                        </label>
                        <select
                          value={leg.optionType}
                          onChange={(e) => updateLeg(index, 'optionType', e.target.value)}
                          className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        >
                          <option value="CE">CE (Call)</option>
                          <option value="PE">PE (Put)</option>
                        </select>
                      </div>
                    )}

                    {/* Position */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Position
                      </label>
                      <select
                        value={leg.position}
                        onChange={(e) => updateLeg(index, 'position', e.target.value)}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="BUY">Buy</option>
                        <option value="SELL">Sell</option>
                      </select>
                    </div>

                    {/* Quantity */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Quantity
                      </label>
                      <input
                        type="number"
                        min="1"
                        value={leg.quantity}
                        onChange={(e) => updateLeg(index, 'quantity', parseInt(e.target.value))}
                        className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                      />
                    </div>

                    {/* Expiry Type (only for options) */}
                    {leg.instrument === 'OPTION' && (
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          Expiry Type
                        </label>
                        <select
                          value={leg.expiryType}
                          onChange={(e) => updateLeg(index, 'expiryType', e.target.value)}
                          className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        >
                          <option value="WEEKLY">Weekly</option>
                          <option value="MONTHLY">Monthly</option>
                        </select>
                      </div>
                    )}

                    {/* Strike Selection Type */}
                    {leg.instrument === 'OPTION' && (
                      <>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            Strike Selection
                          </label>
                          <select
                            value={leg.strikeSelection.type}
                            onChange={(e) => updateLeg(index, 'strikeSelection.type', e.target.value)}
                            className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                          >
                            <option value="ATM">ATM (At The Money)</option>
                            <option value="ITM">ITM (In The Money)</option>
                            <option value="OTM">OTM (Out Of The Money)</option>
                            <option value="SPOT">Spot-Adjusted</option>
                          </select>
                        </div>

                        {/* Strike Selection Value */}
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            {leg.strikeSelection.type === 'ATM' || leg.strikeSelection.type === 'SPOT' 
                              ? 'Adjustment Value' 
                              : `${leg.strikeSelection.type} Distance`}
                          </label>
                          <input
                            type="number"
                            step="0.01"
                            value={leg.strikeSelection.value}
                            onChange={(e) => updateLeg(index, 'strikeSelection.value', parseFloat(e.target.value))}
                            className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                            placeholder={leg.strikeSelection.type === 'ATM' || leg.strikeSelection.type === 'SPOT' 
                              ? '0 for no adjustment' 
                              : 'Positive for OTM, Negative for ITM'}
                          />
                        </div>

                        {/* Spot Adjustment Mode */}
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            Spot Adjustment Mode
                          </label>
                          <select
                            value={leg.strikeSelection.spotAdjustmentMode}
                            onChange={(e) => updateLeg(index, 'strikeSelection.spotAdjustmentMode', parseInt(e.target.value))}
                            className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                          >
                            <option value={0}>0: Unadjusted spot</option>
                            <option value={1}>1: Spot rises by X%</option>
                            <option value={2}>2: Spot falls by X%</option>
                            <option value={3}>3: Spot may rise or fall</option>
                            <option value={4}>4: Custom spot shift</option>
                          </select>
                        </div>

                        {/* Spot Adjustment Value */}
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            Spot Adjustment Value
                          </label>
                          <input
                            type="number"
                            step="0.01"
                            value={leg.strikeSelection.spotAdjustment}
                            onChange={(e) => updateLeg(index, 'strikeSelection.spotAdjustment', parseFloat(e.target.value))}
                            className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                          />
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default LegBuilder;