import React from 'react';

const EntryExitSettings = ({ entrySettings, exitSettings, onEntryChange, onExitChange }) => {
  // Generate days before expiry options based on expiry type
  const getDaysBeforeOptions = (expiryType) => {
    if (expiryType === 'Weekly') {
      return [0, 1, 2, 3, 4];
    } else if (expiryType === 'Monthly') {
      return Array.from({ length: 25 }, (_, i) => i); // 0-24
    }
    return [0];
  };

  const handleEntryChange = (field, value) => {
    onEntryChange({ ...entrySettings, [field]: value });
  };

  const handleExitChange = (field, value) => {
    onExitChange({ ...exitSettings, [field]: value });
  };

  const entryDaysOptions = getDaysBeforeOptions(entrySettings.expiry_type);
  const exitDaysOptions = getDaysBeforeOptions(entrySettings.expiry_type);

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-lg font-semibold mb-4">Entry & Exit Settings</h3>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Entry Settings */}
        <div className="space-y-4">
          <h4 className="font-medium text-gray-900">Entry Settings</h4>
          
          {/* Strategy Type */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Strategy Type
            </label>
            <div className="flex gap-2">
              {['Intraday', 'STBT', 'Positional'].map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => handleEntryChange('strategy_type', type)}
                  className={`flex-1 py-2 px-3 text-sm rounded-md border font-medium ${
                    entrySettings.strategy_type === type
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  {type}
                </button>
              ))}
            </div>
          </div>

          {/* Expiry Type */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Positional expires on
            </label>
            <div className="flex gap-2">
              <select
                value={entrySettings.expiry_type}
                onChange={(e) => handleEntryChange('expiry_type', e.target.value)}
                className="flex-1 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="Weekly">Weekly Expiry</option>
                <option value="Monthly">Monthly Expiry</option>
              </select>
              <button
                type="button"
                className="px-3 py-2 border border-gray-300 rounded-md text-sm text-gray-700 hover:bg-gray-50"
              >
                basis
              </button>
            </div>
          </div>

          {/* Entry Time */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Entry Time
            </label>
            <div className="flex gap-2">
              <input
                type="time"
                value={entrySettings.entry_time}
                onChange={(e) => handleEntryChange('entry_time', e.target.value)}
                className="flex-1 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              />
              <button
                type="button"
                className="px-3 py-2 border border-gray-300 rounded-md text-gray-700 hover:bg-gray-50"
                title="Reset to default"
              >
                ⟳
              </button>
            </div>
          </div>

          {/* Entry Days Before */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Entry
            </label>
            <div className="flex items-center gap-2">
              <select
                value={entrySettings.entry_days_before}
                onChange={(e) => handleEntryChange('entry_days_before', parseInt(e.target.value))}
                className="w-20 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              >
                {entryDaysOptions.map((day) => (
                  <option key={day} value={day}>{day}</option>
                ))}
              </select>
              <span className="text-sm text-gray-600">trading days before expiry</span>
            </div>
          </div>
        </div>

        {/* Exit Settings */}
        <div className="space-y-4">
          <h4 className="font-medium text-gray-900">Exit Settings</h4>
          
          {/* Exit Time */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Exit Time
            </label>
            <div className="flex gap-2">
              <input
                type="time"
                value={exitSettings.exit_time}
                onChange={(e) => handleExitChange('exit_time', e.target.value)}
                className="flex-1 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              />
              <button
                type="button"
                className="px-3 py-2 border border-gray-300 rounded-md text-gray-700 hover:bg-gray-50"
                title="Reset to default"
              >
                ⟳
              </button>
            </div>
          </div>

          {/* Exit Days Before */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Exit
            </label>
            <div className="flex items-center gap-2">
              <select
                value={exitSettings.exit_days_before}
                onChange={(e) => handleExitChange('exit_days_before', parseInt(e.target.value))}
                className="w-20 p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              >
                {exitDaysOptions.map((day) => (
                  <option key={day} value={day}>{day}</option>
                ))}
              </select>
              <span className="text-sm text-gray-600">trading days before expiry</span>
            </div>
          </div>

          {/* Validation Message */}
          {exitSettings.exit_days_before > entrySettings.entry_days_before && (
            <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-md">
              <p className="text-sm text-yellow-800">
                ⚠️ Exit days should be less than or equal to entry days
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default EntryExitSettings;
