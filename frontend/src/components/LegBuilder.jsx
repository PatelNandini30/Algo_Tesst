import React, { useState } from 'react';
import { Plus, Trash2, ChevronDown, ChevronUp, Info } from 'lucide-react';

const LegBuilder = ({ 
  legs = [], 
  onLegsChange,
  maxLegs = 6,
  showAdvanced = true,
  readOnly = false 
}) => {
  const [expandedLegIndex, setExpandedLegIndex] = useState(null);

  const addLeg = () => {
    if (legs.length >= maxLegs) return;
    
    const newLeg = {
      id: Date.now(),
      segment: 'options',
      lot: 1,
      position: 'sell',
      option_type: 'call',
      expiry: 'weekly',
      strike_criteria: 'strike_type',
      strike_type: 'atm',
      premium_value: 0,
      premium_min: 0,
      premium_max: 0,
      stop_loss_enabled: false,
      stop_loss: null,
      stop_loss_type: 'pct',
      target_enabled: false,
      target: null,
      target_type: 'pct',
    };
    
    onLegsChange([...legs, newLeg]);
    setExpandedLegIndex(legs.length);
  };

  const removeLeg = (index) => {
    const newLegs = legs.filter((_, i) => i !== index);
    onLegsChange(newLegs);
    if (expandedLegIndex === index) {
      setExpandedLegIndex(null);
    } else if (expandedLegIndex > index) {
      setExpandedLegIndex(expandedLegIndex - 1);
    }
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    newLegs[index] = { ...newLegs[index], [field]: value };
    onLegsChange(newLegs);
  };

  const toggleExpand = (index) => {
    setExpandedLegIndex(expandedLegIndex === index ? null : index);
  };

  const getLegSummary = (leg) => {
    const position = leg.position === 'sell' ? 'Sell' : 'Buy';
    const segment = leg.segment === 'options' ? 'Options' : 'Futures';
    
    if (leg.segment === 'options') {
      const optionType = leg.option_type === 'call' ? 'CE' : 'PE';
      const strikeInfo = leg.strike_criteria === 'strike_type' 
        ? leg.strike_type.toUpperCase()
        : `${leg.strike_criteria}: ${leg.premium_value}`;
      return `${optionType} ${position} - ${strikeInfo} (${leg.lot} lot)`;
    }
    
    return `${segment} ${position} (${leg.lot} lot)`;
  };

  const segmentOptions = [
    { value: 'options', label: 'Options' },
    { value: 'futures', label: 'Futures' }
  ];

  const positionOptions = [
    { value: 'buy', label: 'Buy', color: 'green' },
    { value: 'sell', label: 'Sell', color: 'red' }
  ];

  const optionTypeOptions = [
    { value: 'call', label: 'Call (CE)' },
    { value: 'put', label: 'Put (PE)' }
  ];

  const expiryOptions = [
    { value: 'weekly', label: 'Weekly' },
    { value: 'monthly', label: 'Monthly' }
  ];

  const strikeTypeOptions = [
    { value: 'itm20', label: 'ITM 20' },
    { value: 'itm15', label: 'ITM 15' },
    { value: 'itm10', label: 'ITM 10' },
    { value: 'itm5', label: 'ITM 5' },
    { value: 'itm2', label: 'ITM 2' },
    { value: 'itm1', label: 'ITM 1' },
    { value: 'atm', label: 'ATM' },
    { value: 'otm1', label: 'OTM 1' },
    { value: 'otm2', label: 'OTM 2' },
    { value: 'otm5', label: 'OTM 5' },
    { value: 'otm10', label: 'OTM 10' },
    { value: 'otm15', label: 'OTM 15' },
    { value: 'otm20', label: 'OTM 20' },
  ];

  const renderSegmentButton = (option, legIndex) => {
    const isSelected = legs[legIndex].segment === option.value;
    return (
      <button
        type="button"
        onClick={() => updateLeg(legIndex, 'segment', option.value)}
        disabled={readOnly}
        className={`flex-1 py-2 px-3 text-sm font-medium rounded-md border transition-colors ${
          isSelected
            ? 'bg-blue-600 text-white border-blue-600'
            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
        } ${readOnly ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        {option.label}
      </button>
    );
  };

  const renderPositionButton = (option, legIndex) => {
    const isSelected = legs[legIndex].position === option.value;
    const bgColor = option.color === 'green' ? 'green' : 'red';
    return (
      <button
        type="button"
        onClick={() => updateLeg(legIndex, 'position', option.value)}
        disabled={readOnly}
        className={`flex-1 py-2 px-3 text-sm font-medium rounded-md border transition-colors ${
          isSelected
            ? `bg-${bgColor}-600 text-white border-${bgColor}-600`
            : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
        } ${readOnly ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        {option.label}
      </button>
    );
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 flex justify-between items-center">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">Strategy Legs</h3>
          <p className="text-xs text-gray-500">{legs.length} of {maxLegs} legs configured</p>
        </div>
        {!readOnly && (
          <button
            type="button"
            onClick={addLeg}
            disabled={legs.length >= maxLegs}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              legs.length >= maxLegs
                ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                : 'bg-blue-600 text-white hover:bg-blue-700'
            }`}
          >
            <Plus size={16} />
            Add Leg
          </button>
        )}
      </div>

      {/* Legs List */}
      <div className="divide-y divide-gray-100">
        {legs.length === 0 ? (
          <div className="px-4 py-12 text-center">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gray-100 flex items-center justify-center">
              <Plus size={24} className="text-gray-400" />
            </div>
            <p className="text-gray-600 font-medium">No legs added yet</p>
            <p className="text-gray-500 text-sm mt-1">Click "Add Leg" to start building your strategy</p>
          </div>
        ) : (
          legs.map((leg, index) => (
            <div key={leg.id} className="overflow-hidden">
              {/* Leg Header - Always Visible */}
              <div
                className={`px-4 py-3 cursor-pointer transition-colors ${
                  expandedLegIndex === index 
                    ? 'bg-blue-50' 
                    : 'bg-white hover:bg-gray-50'
                }`}
                onClick={() => toggleExpand(index)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-semibold text-gray-800">
                      Leg {index + 1}
                    </span>
                    <span className="text-xs text-gray-600 bg-gray-100 px-2 py-0.5 rounded">
                      {getLegSummary(leg)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {!readOnly && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeLeg(index);
                        }}
                        className="p-1.5 text-red-500 hover:bg-red-50 rounded transition-colors"
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
                    {expandedLegIndex === index ? (
                      <ChevronUp size={18} className="text-gray-400" />
                    ) : (
                      <ChevronDown size={18} className="text-gray-400" />
                    )}
                  </div>
                </div>
              </div>

              {/* Leg Details - Expandable */}
              {expandedLegIndex === index && !readOnly && (
                <div className="px-4 py-4 bg-white border-t border-gray-100">
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {/* Segment Selection */}
                    <div className="col-span-full">
                      <label className="block text-xs font-medium text-gray-600 mb-2">
                        Instrument Type
                      </label>
                      <div className="flex gap-2">
                        {segmentOptions.map(option => renderSegmentButton(option, index))}
                      </div>
                    </div>

                    {/* Position */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-2">
                        Position
                      </label>
                      <div className="flex gap-2">
                        {positionOptions.map(option => renderPositionButton(option, index))}
                      </div>
                    </div>

                    {/* Lot/Quantity */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-2">
                        Lot Size
                      </label>
                      <input
                        type="number"
                        min="1"
                        max="100"
                        value={leg.lot}
                        onChange={(e) => updateLeg(index, 'lot', parseInt(e.target.value) || 1)}
                        className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                      />
                    </div>

                    {/* Options-specific fields */}
                    {leg.segment === 'options' && (
                      <>
                        {/* Option Type */}
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-2">
                            Option Type
                          </label>
                          <div className="flex gap-2">
                            {optionTypeOptions.map(option => (
                              <button
                                key={option.value}
                                type="button"
                                onClick={() => updateLeg(index, 'option_type', option.value)}
                                className={`flex-1 py-2 px-3 text-sm font-medium rounded-md border transition-colors ${
                                  leg.option_type === option.value
                                    ? 'bg-blue-600 text-white border-blue-600'
                                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                                }`}
                              >
                                {option.label}
                              </button>
                            ))}
                          </div>
                        </div>

                        {/* Expiry */}
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-2">
                            Expiry
                          </label>
                          <select
                            value={leg.expiry}
                            onChange={(e) => updateLeg(index, 'expiry', e.target.value)}
                            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                          >
                            {expiryOptions.map(option => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>

                        {/* Strike Type */}
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-2">
                            Strike Selection
                          </label>
                          <select
                            value={leg.strike_type}
                            onChange={(e) => updateLeg(index, 'strike_type', e.target.value)}
                            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                          >
                            {strikeTypeOptions.map(option => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </>
                    )}

                    {/* Advanced Options */}
                    {showAdvanced && leg.segment === 'options' && (
                      <>
                        {/* Stop Loss */}
                        <div className="col-span-full pt-2 border-t border-gray-100">
                          <div className="flex items-center gap-2 mb-3">
                            <input
                              type="checkbox"
                              id={`sl-${leg.id}`}
                              checked={leg.stop_loss_enabled}
                              onChange={(e) => updateLeg(index, 'stop_loss_enabled', e.target.checked)}
                              className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                            />
                            <label htmlFor={`sl-${leg.id}`} className="text-xs font-medium text-gray-700">
                              Enable Stop Loss
                            </label>
                          </div>
                          
                          {leg.stop_loss_enabled && (
                            <div className="grid grid-cols-2 gap-3">
                              <div>
                                <label className="block text-xs text-gray-500 mb-1">Stop Loss %</label>
                                <input
                                  type="number"
                                  min="0"
                                  step="0.5"
                                  value={leg.stop_loss || ''}
                                  onChange={(e) => updateLeg(index, 'stop_loss', parseFloat(e.target.value))}
                                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md"
                                  placeholder="e.g., 50"
                                />
                              </div>
                              <div>
                                <label className="block text-xs text-gray-500 mb-1">Type</label>
                                <select
                                  value={leg.stop_loss_type}
                                  onChange={(e) => updateLeg(index, 'stop_loss_type', e.target.value)}
                                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md"
                                >
                                  <option value="pts">Points (Pts)</option>
                                  <option value="underlying_pts">Underlying Pts</option>
                                  <option value="pct">Percent (%)</option>
                                  <option value="underlying_pct">Underlying %</option>
                                </select>
                              </div>
                            </div>
                          )}
                        </div>

                        {/* Target */}
                        <div className="col-span-full pt-2 border-t border-gray-100">
                          <div className="flex items-center gap-2 mb-3">
                            <input
                              type="checkbox"
                              id={`tgt-${leg.id}`}
                              checked={leg.target_enabled}
                              onChange={(e) => updateLeg(index, 'target_enabled', e.target.checked)}
                              className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                            />
                            <label htmlFor={`tgt-${leg.id}`} className="text-xs font-medium text-gray-700">
                              Enable Target
                            </label>
                          </div>
                          
                          {leg.target_enabled && (
                            <div className="grid grid-cols-2 gap-3">
                              <div>
                                <label className="block text-xs text-gray-500 mb-1">Target %</label>
                                <input
                                  type="number"
                                  min="0"
                                  step="0.5"
                                  value={leg.target || ''}
                                  onChange={(e) => updateLeg(index, 'target', parseFloat(e.target.value))}
                                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md"
                                  placeholder="e.g., 100"
                                />
                              </div>
                              <div>
                                <label className="block text-xs text-gray-500 mb-1">Type</label>
                                <select
                                  value={leg.target_type}
                                  onChange={(e) => updateLeg(index, 'target_type', e.target.value)}
                                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md"
                                >
                                  <option value="pts">Points (Pts)</option>
                                  <option value="underlying_pts">Underlying Pts</option>
                                  <option value="pct">Percent (%)</option>
                                  <option value="underlying_pct">Underlying %</option>
                                </select>
                              </div>
                            </div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Footer Info */}
      {legs.length > 0 && (
        <div className="px-4 py-3 bg-gray-50 border-t border-gray-200 text-xs text-gray-500">
          <div className="flex items-center gap-4">
            <span>Total Lots: {legs.reduce((sum, leg) => sum + (leg.lot || 0), 0)}</span>
            <span>•</span>
            <span>Buy: {legs.filter(l => l.position === 'buy').reduce((sum, leg) => sum + (leg.lot || 0), 0)}</span>
            <span>•</span>
            <span>Sell: {legs.filter(l => l.position === 'sell').reduce((sum, leg) => sum + (leg.lot || 0), 0)}</span>
          </div>
        </div>
      )}
    </div>
  );
};

export default LegBuilder;
