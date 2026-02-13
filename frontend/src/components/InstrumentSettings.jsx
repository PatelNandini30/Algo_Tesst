import React from 'react';

const InstrumentSettings = ({ settings, onChange }) => {
  const handleChange = (field, value) => {
    onChange({ ...settings, [field]: value });
  };

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-lg font-semibold mb-4">Instrument Settings</h3>
      
      <div className="space-y-4">
        {/* Index Selection */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Index
          </label>
          <select
            value={settings.index}
            onChange={(e) => handleChange('index', e.target.value)}
            className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
          >
            <option value="NIFTY">NIFTY</option>
            <option value="SENSEX">SENSEX</option>
            <option value="BANKNIFTY">BANKNIFTY</option>
            <option value="FINNIFTY">FINNIFTY</option>
            <option value="MIDCPNIFTY">MIDCPNIFTY</option>
          </select>
        </div>

        {/* Underlying Type */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Underlying from
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => handleChange('underlying_type', 'Cash')}
              className={`flex-1 py-2 px-4 rounded-md border font-medium ${
                settings.underlying_type === 'Cash'
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
              }`}
            >
              Cash
            </button>
            <button
              type="button"
              onClick={() => handleChange('underlying_type', 'Futures')}
              className={`flex-1 py-2 px-4 rounded-md border font-medium ${
                settings.underlying_type === 'Futures'
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
              }`}
            >
              Futures
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default InstrumentSettings;
