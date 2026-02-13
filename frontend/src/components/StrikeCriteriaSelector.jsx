import React from 'react';

const StrikeCriteriaSelector = ({ criteria, onChange, optionType }) => {
  const handleChange = (field, value) => {
    onChange({ ...criteria, [field]: value });
  };

  const criteriaTypes = [
    'Strike Type',
    'Premium Range',
    'Closest Premium',
    'Premium >=',
    'Premium <=',
    'Straddle Width',
    '% of ATM',
    'Synthetic Future',
    'ATM Straddle Premium %'
  ];

  const renderConditionalFields = () => {
    switch (criteria.type) {
      case 'Strike Type':
        return (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Strike Type
              </label>
              <select
                value={criteria.strike_type || 'ATM'}
                onChange={(e) => handleChange('strike_type', e.target.value)}
                className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="ATM">ATM</option>
                <option value="OTM">OTM</option>
                <option value="ITM">ITM</option>
              </select>
            </div>

            {criteria.strike_type === 'OTM' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Strikes OTM
                </label>
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={criteria.strikes_away || 1}
                  onChange={(e) => handleChange('strikes_away', parseInt(e.target.value))}
                  className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
            )}

            {criteria.strike_type === 'ITM' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Strikes ITM
                </label>
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={criteria.strikes_away || 1}
                  onChange={(e) => handleChange('strikes_away', parseInt(e.target.value))}
                  className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
            )}
          </div>
        );

      case 'Premium Range':
        return (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Lower Range
              </label>
              <input
                type="number"
                min="0"
                step="0.5"
                value={criteria.lower_range || 0}
                onChange={(e) => handleChange('lower_range', parseFloat(e.target.value))}
                className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                placeholder="50"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Upper Range
              </label>
              <input
                type="number"
                min="0"
                step="0.5"
                value={criteria.upper_range || 0}
                onChange={(e) => handleChange('upper_range', parseFloat(e.target.value))}
                className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                placeholder="150"
              />
            </div>
          </div>
        );

      case 'Closest Premium':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Premium
            </label>
            <input
              type="number"
              min="0"
              step="0.5"
              value={criteria.premium || 0}
              onChange={(e) => handleChange('premium', parseFloat(e.target.value))}
              className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              placeholder="100"
            />
          </div>
        );

      case 'Premium >=':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Premium
            </label>
            <input
              type="number"
              min="0"
              step="0.5"
              value={criteria.premium || 0}
              onChange={(e) => handleChange('premium', parseFloat(e.target.value))}
              className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              placeholder="75"
            />
          </div>
        );

      case 'Premium <=':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Premium
            </label>
            <input
              type="number"
              min="0"
              step="0.5"
              value={criteria.premium || 0}
              onChange={(e) => handleChange('premium', parseFloat(e.target.value))}
              className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              placeholder="200"
            />
          </div>
        );

      case 'Straddle Width':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              % of ATM
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="0"
                max="100"
                step="0.1"
                value={criteria.straddle_width_pct || 0}
                onChange={(e) => handleChange('straddle_width_pct', parseFloat(e.target.value))}
                className="flex-1 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                placeholder="2.5"
              />
              <span className="text-sm text-gray-600">%</span>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              {optionType === 'CE' ? 'ATM + ' : 'ATM - '}{criteria.straddle_width_pct || 0}%
            </p>
          </div>
        );

      case '% of ATM':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              ATM +/-
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="-50"
                max="50"
                step="0.1"
                value={criteria.pct_of_atm || 0}
                onChange={(e) => handleChange('pct_of_atm', parseFloat(e.target.value))}
                className="flex-1 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                placeholder="2.0"
              />
              <span className="text-sm text-gray-600">%</span>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              {criteria.pct_of_atm >= 0 ? '+' : ''}{criteria.pct_of_atm || 0}% from ATM
            </p>
          </div>
        );

      case 'Synthetic Future':
        return (
          <div className="space-y-2">
            <p className="text-sm text-gray-600">
              Synthetic Future = CE_ATM - PE_ATM + ATM Strike
            </p>
            <p className="text-xs text-gray-500">
              Used for directional pricing logic. No additional inputs required.
            </p>
          </div>
        );

      case 'ATM Straddle Premium %':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              % of Straddle
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="0"
                max="100"
                step="1"
                value={criteria.straddle_pct || 0}
                onChange={(e) => handleChange('straddle_pct', parseFloat(e.target.value))}
                className="flex-1 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                placeholder="40"
              />
              <span className="text-sm text-gray-600">%</span>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              Target premium = {criteria.straddle_pct || 0}% of ATM straddle premium
            </p>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="space-y-3">
      {/* Strike Criteria Type Selector */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Strike Criteria
        </label>
        <select
          value={criteria.type}
          onChange={(e) => handleChange('type', e.target.value)}
          className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
        >
          {criteriaTypes.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </div>

      {/* Conditional Fields Based on Selection */}
      {renderConditionalFields()}
    </div>
  );
};

export default StrikeCriteriaSelector;
