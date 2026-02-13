import React, { useState } from 'react';
import { Plus, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import StrikeCriteriaSelector from './StrikeCriteriaSelector';

const DynamicLegBuilder = ({ legs, onLegsChange }) => {
  const [expandedLegIndex, setExpandedLegIndex] = useState(null);

  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      leg_number: legs.length + 1,
      instrument: 'OPTION',
      option_type: 'CE',
      position: 'SELL',
      total_lot: 1,
      expiry: 'Weekly',
      strike_criteria: {
        type: 'Strike Type',
        strike_type: 'ATM',
        strikes_away: 1
      }
    };
    onLegsChange([...legs, newLeg]);
    setExpandedLegIndex(legs.length);
  };

  const removeLeg = (index) => {
    const newLegs = legs.filter((_, i) => i !== index);
    // Renumber legs
    const renumberedLegs = newLegs.map((leg, i) => ({ ...leg, leg_number: i + 1 }));
    onLegsChange(renumberedLegs);
    if (expandedLegIndex === index) {
      setExpandedLegIndex(null);
    }
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index] = { ...newLegs[index], [field]: value };
    onLegsChange(newLegs);
  };

  const updateStrikeCriteria = (index, criteria) => {
    const newLegs = [...legs];
    newLegs[index] = { ...newLegs[index], strike_criteria: criteria };
    onLegsChange(newLegs);
  };

  const toggleExpand = (index) => {
    setExpandedLegIndex(expandedLegIndex === index ? null : index);
  };

  const getLegSummary = (leg) => {
    if (leg.instrument === 'FUTURE') {
      return `Future ${leg.position} (${leg.total_lot} lot)`;
    } else {
      const strikeInfo = leg.strike_criteria.type === 'Strike Type' 
        ? leg.strike_criteria.strike_type 
        : leg.strike_criteria.type;
      return `${leg.option_type} ${leg.position} - ${strikeInfo} (${leg.total_lot} lot)`;
    }
  };

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold">Leg Builder</h3>
        <button
          type="button"
          onClick={() => setExpandedLegIndex(expandedLegIndex === -1 ? null : -1)}
          className="text-sm text-blue-600 hover:text-blue-700"
        >
          {expandedLegIndex === -1 ? 'Expand' : 'Collapse'}
        </button>
      </div>

      {/* Legs List */}
      <div className="space-y-3 mb-4">
        {legs.map((leg, index) => (
          <div key={leg.id} className="border rounded-lg overflow-hidden">
            {/* Leg Header */}
            <div
              className={`flex justify-between items-center p-3 cursor-pointer transition-colors ${
                expandedLegIndex === index ? 'bg-blue-50' : 'bg-gray-50 hover:bg-gray-100'
              }`}
              onClick={() => toggleExpand(index)}
            >
              <div className="flex items-center gap-3">
                <span className="font-medium text-gray-900">
                  Leg {leg.leg_number}:
                </span>
                <span className="text-sm text-gray-600">
                  {getLegSummary(leg)}
                </span>
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
                {expandedLegIndex === index ? (
                  <ChevronUp size={18} className="text-gray-500" />
                ) : (
                  <ChevronDown size={18} className="text-gray-500" />
                )}
              </div>
            </div>

            {/* Leg Details (Expanded) */}
            {expandedLegIndex === index && (
              <div className="p-4 bg-white border-t">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Select Segments */}
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Select segments
                    </label>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => updateLeg(index, 'instrument', 'FUTURE')}
                        className={`flex-1 py-2 px-4 rounded-md border font-medium ${
                          leg.instrument === 'FUTURE'
                            ? 'bg-blue-600 text-white border-blue-600'
                            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        Futures
                      </button>
                      <button
                        type="button"
                        onClick={() => updateLeg(index, 'instrument', 'OPTION')}
                        className={`flex-1 py-2 px-4 rounded-md border font-medium ${
                          leg.instrument === 'OPTION'
                            ? 'bg-blue-600 text-white border-blue-600'
                            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        Options
                      </button>
                    </div>
                  </div>

                  {/* Total Lot */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Total Lot
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="100"
                      value={leg.total_lot}
                      onChange={(e) => updateLeg(index, 'total_lot', parseInt(e.target.value))}
                      className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>

                  {/* Position */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Position
                    </label>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => updateLeg(index, 'position', 'BUY')}
                        className={`flex-1 py-2 px-3 text-sm rounded-md border font-medium ${
                          leg.position === 'BUY'
                            ? 'bg-green-600 text-white border-green-600'
                            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        Buy
                      </button>
                      <button
                        type="button"
                        onClick={() => updateLeg(index, 'position', 'SELL')}
                        className={`flex-1 py-2 px-3 text-sm rounded-md border font-medium ${
                          leg.position === 'SELL'
                            ? 'bg-red-600 text-white border-red-600'
                            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        Sell
                      </button>
                    </div>
                  </div>

                  {/* Option Type (only for options) */}
                  {leg.instrument === 'OPTION' && (
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Option Type
                      </label>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => updateLeg(index, 'option_type', 'CE')}
                          className={`flex-1 py-2 px-3 text-sm rounded-md border font-medium ${
                            leg.option_type === 'CE'
                              ? 'bg-blue-600 text-white border-blue-600'
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          Call
                        </button>
                        <button
                          type="button"
                          onClick={() => updateLeg(index, 'option_type', 'PE')}
                          className={`flex-1 py-2 px-3 text-sm rounded-md border font-medium ${
                            leg.option_type === 'PE'
                              ? 'bg-purple-600 text-white border-purple-600'
                              : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          Put
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Expiry */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Expiry
                    </label>
                    <select
                      value={leg.expiry}
                      onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                      className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                    >
                      {leg.instrument === 'OPTION' ? (
                        <>
                          <option value="Weekly">Weekly</option>
                          <option value="Next Weekly">Next Weekly</option>
                          <option value="Monthly">Monthly</option>
                          <option value="Next Monthly">Next Monthly</option>
                        </>
                      ) : (
                        <>
                          <option value="Monthly">Monthly</option>
                          <option value="Next Monthly">Next Monthly</option>
                        </>
                      )}
                    </select>
                  </div>

                  {/* Strike Criteria (only for options) */}
                  {leg.instrument === 'OPTION' && (
                    <div className="col-span-2">
                      <StrikeCriteriaSelector
                        criteria={leg.strike_criteria}
                        onChange={(criteria) => updateStrikeCriteria(index, criteria)}
                        optionType={leg.option_type}
                      />
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Add Leg Button */}
      <button
        type="button"
        onClick={addLeg}
        className="w-full py-3 px-4 bg-blue-600 text-white rounded-md hover:bg-blue-700 flex items-center justify-center gap-2 font-medium"
      >
        <Plus size={18} />
        Add Leg
      </button>

      {/* Leg Count Info */}
      {legs.length > 0 && (
        <div className="mt-3 text-sm text-gray-600 text-center">
          {legs.length} leg{legs.length !== 1 ? 's' : ''} added (max 4)
        </div>
      )}
    </div>
  );
};

export default DynamicLegBuilder;
