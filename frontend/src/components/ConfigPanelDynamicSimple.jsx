import React, { useState, useEffect } from 'react';

const ConfigPanelDynamic = ({ onBacktestRun }) => {
  // ==================== STATE ====================
  const [strategyName, setStrategyName] = useState('Custom Strategy');
  const [index, setIndex] = useState('NIFTY');
  const [legs, setLegs] = useState([createDefaultLeg(1)]);
  const [reEntryMode, setReEntryMode] = useState('None');
  const [reEntryPercent, setReEntryPercent] = useState(1.0);
  const [useBase2Filter, setUseBase2Filter] = useState(true);
  const [inverseBase2, setInverseBase2] = useState(false);
  const [fromDate, setFromDate] = useState('2024-01-01');
  const [toDate, setToDate] = useState('2025-01-31');

  // ==================== LEG MANAGEMENT ====================
  function createDefaultLeg(legNumber) {
    return {
      leg_number: legNumber,
      instrument: 'Option',
      option_type: 'CE',
      position: 'Sell',
      lots: 1,
      expiry_type: 'Weekly',
      strike_selection: {
        type: 'ATM',
        value: 0
      },
      entry_condition: {
        type: 'Days Before Expiry',
        days_before_expiry: 5
      },
      exit_condition: {
        type: 'Days Before Expiry',
        days_before_expiry: 3
      }
    };
  }

  const addLeg = () => {
    if (legs.length < 4) {
      setLegs([...legs, createDefaultLeg(legs.length + 1)]);
    }
  };

  const removeLeg = (index) => {
    if (legs.length > 1) {
      const newLegs = legs.filter((_, i) => i !== index);
      // Renumber legs
      newLegs.forEach((leg, i) => leg.leg_number = i + 1);
      setLegs(newLegs);
    }
  };

  const updateLeg = (index, field, value) => {
    const newLegs = [...legs];
    if (field.includes('.')) {
      const [parent, child] = field.split('.');
      newLegs[index][parent][child] = value;
    } else {
      newLegs[index][field] = value;
    }
    setLegs(newLegs);
  };

  // ==================== RENDER FUNCTIONS ====================
  const renderStrikeSelection = (leg, legIndex) => {
    const strikeType = leg.strike_selection.type;

    return (
      <div style={{ marginBottom: '15px' }}>
        <div style={{ marginBottom: '10px' }}>
          <label>Strike Selection: </label>
          <select
            value={strikeType}
            onChange={(e) => updateLeg(legIndex, 'strike_selection.type', e.target.value)}
            style={{ marginLeft: '10px', padding: '5px' }}
          >
            <option value="ATM">ATM (At The Money)</option>
            <option value="Closest Premium">Closest Premium</option>
            <option value="Premium Range">Premium Range</option>
            <option value="Straddle Width">Straddle Width</option>
            <option value="% of ATM">% of ATM</option>
            <option value="Delta">Delta</option>
            <option value="Strike Type">Strike Type (ATM/ITM/OTM)</option>
            <option value="OTM %">OTM %</option>
            <option value="ITM %">ITM %</option>
          </select>
        </div>

        {/* Conditional fields based on strike type */}
        {strikeType === '% of ATM' && (
          <div>
            <label>% of ATM (e.g., 1.5 for 1.5% OTM): </label>
            <input
              type="number"
              value={leg.strike_selection.value || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.value', parseFloat(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '100px' }}
            />
          </div>
        )}

        {strikeType === 'Closest Premium' && (
          <div>
            <label>Target Premium (₹): </label>
            <input
              type="number"
              value={leg.strike_selection.value || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.value', parseFloat(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '100px' }}
            />
          </div>
        )}

        {strikeType === 'Premium Range' && (
          <div>
            <label>Min Premium (₹): </label>
            <input
              type="number"
              value={leg.strike_selection.premium_min || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.premium_min', parseFloat(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '80px' }}
            />
            <label style={{ marginLeft: '15px' }}>Max Premium (₹): </label>
            <input
              type="number"
              value={leg.strike_selection.premium_max || 0}
              onChange={(e) => updateLeg(legIndex, 'strike_selection.premium_max', parseFloat(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '80px' }}
            />
          </div>
        )}
      </div>
    );
  };

  const renderEntryCondition = (leg, legIndex) => {
    return (
      <div style={{ marginBottom: '15px' }}>
        <div style={{ marginBottom: '10px' }}>
          <label>Entry Timing: </label>
          <select
            value={leg.entry_condition.type}
            onChange={(e) => updateLeg(legIndex, 'entry_condition.type', e.target.value)}
            style={{ marginLeft: '10px', padding: '5px' }}
          >
            <option value="Days Before Expiry">X Days Before Expiry</option>
            <option value="Specific Time">Specific Time</option>
            <option value="Market Open">Market Open</option>
            <option value="Market Close">Market Close</option>
          </select>
        </div>

        {leg.entry_condition.type === 'Days Before Expiry' && (
          <div>
            <label>Days Before Expiry: </label>
            <input
              type="number"
              value={leg.entry_condition.days_before_expiry || 5}
              onChange={(e) => updateLeg(legIndex, 'entry_condition.days_before_expiry', parseInt(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '80px' }}
            />
          </div>
        )}
      </div>
    );
  };

  const renderExitCondition = (leg, legIndex) => {
    return (
      <div style={{ marginBottom: '15px' }}>
        <div style={{ marginBottom: '10px' }}>
          <label>Exit Timing: </label>
          <select
            value={leg.exit_condition.type}
            onChange={(e) => updateLeg(legIndex, 'exit_condition.type', e.target.value)}
            style={{ marginLeft: '10px', padding: '5px' }}
          >
            <option value="Days Before Expiry">X Days Before Expiry</option>
            <option value="At Expiry">At Expiry</option>
            <option value="Specific Time">Specific Time</option>
            <option value="Stop Loss">Stop Loss</option>
            <option value="Target">Target</option>
          </select>
        </div>

        {leg.exit_condition.type === 'Days Before Expiry' && (
          <div>
            <label>Days Before Expiry: </label>
            <input
              type="number"
              value={leg.exit_condition.days_before_expiry || 3}
              onChange={(e) => updateLeg(legIndex, 'exit_condition.days_before_expiry', parseInt(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '80px' }}
            />
          </div>
        )}

        {leg.exit_condition.type === 'Stop Loss' && (
          <div>
            <label>Stop Loss %: </label>
            <input
              type="number"
              value={leg.exit_condition.stop_loss_percent || 50}
              onChange={(e) => updateLeg(legIndex, 'exit_condition.stop_loss_percent', parseFloat(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '80px' }}
            />
          </div>
        )}
      </div>
    );
  };

  const renderLeg = (leg, index) => {
    return (
      <div key={index} style={{ 
        border: '1px solid #ddd', 
        borderRadius: '8px', 
        padding: '15px', 
        marginBottom: '15px',
        backgroundColor: '#f9f9f9'
      }}>
        <div style={{ 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'center',
          marginBottom: '15px'
        }}>
          <h3 style={{ margin: 0 }}>
            Leg {leg.leg_number}: {leg.position} {leg.instrument === 'Option' ? leg.option_type : ''} {leg.instrument}
          </h3>
          {legs.length > 1 && (
            <button
              onClick={() => removeLeg(index)}
              style={{
                backgroundColor: '#dc3545',
                color: 'white',
                border: 'none',
                padding: '5px 10px',
                borderRadius: '4px',
                cursor: 'pointer'
              }}
            >
              Remove
            </button>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
          {/* Instrument Type */}
          <div>
            <label>Instrument: </label>
            <select
              value={leg.instrument}
              onChange={(e) => updateLeg(index, 'instrument', e.target.value)}
              style={{ marginLeft: '10px', padding: '5px' }}
            >
              <option value="Option">Option</option>
              <option value="Future">Future</option>
            </select>
          </div>

          {/* Option Type (only for options) */}
          {leg.instrument === 'Option' && (
            <div>
              <label>Option Type: </label>
              <select
                value={leg.option_type}
                onChange={(e) => updateLeg(index, 'option_type', e.target.value)}
                style={{ marginLeft: '10px', padding: '5px' }}
              >
                <option value="CE">CE (Call)</option>
                <option value="PE">PE (Put)</option>
              </select>
            </div>
          )}

          {/* Position */}
          <div>
            <label>Position: </label>
            <select
              value={leg.position}
              onChange={(e) => updateLeg(index, 'position', e.target.value)}
              style={{ marginLeft: '10px', padding: '5px' }}
            >
              <option value="Buy">Buy</option>
              <option value="Sell">Sell</option>
            </select>
          </div>

          {/* Lots */}
          <div>
            <label>Lots: </label>
            <input
              type="number"
              value={leg.lots}
              onChange={(e) => updateLeg(index, 'lots', parseInt(e.target.value))}
              style={{ marginLeft: '10px', padding: '5px', width: '80px' }}
            />
          </div>

          {/* Expiry Type */}
          <div>
            <label>Expiry: </label>
            <select
              value={leg.expiry_type}
              onChange={(e) => updateLeg(index, 'expiry_type', e.target.value)}
              style={{ marginLeft: '10px', padding: '5px' }}
            >
              <option value="Weekly">Weekly</option>
              <option value="Monthly">Monthly</option>
              <option value="Weekly_T1">Weekly T+1</option>
              <option value="Weekly_T2">Weekly T+2</option>
              <option value="Monthly_T1">Monthly T+1</option>
            </select>
          </div>
        </div>

        <div style={{ marginTop: '20px' }}>
          <h4>Strike Selection</h4>
          {leg.instrument === 'Option' && renderStrikeSelection(leg, index)}
        </div>

        <div style={{ marginTop: '20px' }}>
          <h4>Entry Condition</h4>
          {renderEntryCondition(leg, index)}
        </div>

        <div style={{ marginTop: '20px' }}>
          <h4>Exit Condition</h4>
          {renderExitCondition(leg, index)}
        </div>
      </div>
    );
  };

  // ==================== MAIN RENDER ====================
  return (
    <div style={{ padding: '20px', maxWidth: '1200px', margin: '0 auto' }}>
      <h1>Dynamic Strategy Builder</h1>
      
      {/* Strategy Details */}
      <div style={{ 
        border: '1px solid #ddd', 
        borderRadius: '8px', 
        padding: '20px', 
        marginBottom: '20px',
        backgroundColor: '#f8f9fa'
      }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
          <div>
            <label>Strategy Name: </label>
            <input
              type="text"
              value={strategyName}
              onChange={(e) => setStrategyName(e.target.value)}
              style={{ marginLeft: '10px', padding: '8px', width: '200px' }}
            />
          </div>
          <div>
            <label>Index: </label>
            <select
              value={index}
              onChange={(e) => setIndex(e.target.value)}
              style={{ marginLeft: '10px', padding: '8px' }}
            >
              <option value="NIFTY">NIFTY</option>
              <option value="BANKNIFTY">BANKNIFTY</option>
              <option value="FINNIFTY">FINNIFTY</option>
              <option value="MIDCPNIFTY">MIDCPNIFTY</option>
            </select>
          </div>
          <div>
            <label>From Date: </label>
            <input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              style={{ marginLeft: '10px', padding: '8px' }}
            />
          </div>
          <div>
            <label>To Date: </label>
            <input
              type="date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              style={{ marginLeft: '10px', padding: '8px' }}
            />
          </div>
        </div>
      </div>

      {/* Legs */}
      <h2>Strategy Legs ({legs.length}/4)</h2>
      {legs.map((leg, index) => renderLeg(leg, index))}
      
      {legs.length < 4 && (
        <button
          onClick={addLeg}
          style={{
            backgroundColor: '#007bff',
            color: 'white',
            border: 'none',
            padding: '10px 20px',
            borderRadius: '4px',
            cursor: 'pointer',
            marginTop: '10px'
          }}
        >
          + Add Leg
        </button>
      )}

      {/* Re-entry Settings */}
      <div style={{ 
        border: '1px solid #ddd', 
        borderRadius: '8px', 
        padding: '20px', 
        marginTop: '20px',
        backgroundColor: '#f8f9fa'
      }}>
        <h3>Re-entry Settings</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
          <div>
            <label>Re-entry Mode: </label>
            <select
              value={reEntryMode}
              onChange={(e) => setReEntryMode(e.target.value)}
              style={{ marginLeft: '10px', padding: '8px' }}
            >
              <option value="None">None</option>
              <option value="Up Move">Up Move</option>
              <option value="Down Move">Down Move</option>
              <option value="Either Move">Either Move</option>
            </select>
          </div>
          {reEntryMode !== 'None' && (
            <div>
              <label>Re-entry % Move: </label>
              <input
                type="number"
                value={reEntryPercent}
                onChange={(e) => setReEntryPercent(parseFloat(e.target.value))}
                style={{ marginLeft: '10px', padding: '8px', width: '100px' }}
              />
            </div>
          )}
          <div>
            <label>
              <input
                type="checkbox"
                checked={useBase2Filter}
                onChange={(e) => setUseBase2Filter(e.target.checked)}
                style={{ marginRight: '8px' }}
              />
              Use Base2 Range Filter
            </label>
          </div>
          {useBase2Filter && (
            <div>
              <label>
                <input
                  type="checkbox"
                  checked={inverseBase2}
                  onChange={(e) => setInverseBase2(e.target.checked)}
                  style={{ marginRight: '8px' }}
                />
                Inverse Base2 (Trade OUTSIDE ranges)
              </label>
            </div>
          )}
        </div>
      </div>

      {/* Run Backtest Button */}
      <button
        onClick={() => {
          const strategyDef = {
            name: strategyName,
            legs: legs,
            index: index,
            re_entry_mode: reEntryMode,
            re_entry_percent: reEntryPercent,
            use_base2_filter: useBase2Filter,
            inverse_base2: inverseBase2
          };
          onBacktestRun(strategyDef, fromDate, toDate);
        }}
        style={{
          backgroundColor: '#28a745',
          color: 'white',
          border: 'none',
          padding: '15px 30px',
          borderRadius: '4px',
          cursor: 'pointer',
          fontSize: '16px',
          marginTop: '20px'
        }}
      >
        Run Backtest
      </button>
    </div>
  );
};

export default ConfigPanelDynamic;