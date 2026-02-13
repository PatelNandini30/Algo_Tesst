import React, { useState } from 'react';
import { Plus, Save, Play, Copy, Trash2 } from 'lucide-react';
import Card from '../ui/Card';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Select from '../ui/Select';
import LegConfigurator from './LegConfigurator';
import ValidationPanel from './ValidationPanel';

const StrategyBuilder = ({ onRunBacktest }) => {
  const [strategy, setStrategy] = useState({
    name: 'Untitled Strategy',
    description: '',
    index: 'NIFTY',
    legs: [],
    parameters: {
      reEntryMode: 'None',
      reEntryPercent: 1.0,
      useBase2Filter: true,
    },
    dateRange: {
      from: '2019-01-01',
      to: '2026-01-01',
    }
  });
  
  const [validation, setValidation] = useState({ valid: true, errors: [], warnings: [] });
  
  const addLeg = () => {
    const newLeg = {
      id: Date.now(),
      optionType: 'CE',
      action: 'BUY',
      lots: 1,
      expiry: 'CURRENT_WEEK',
      strikeMethod: 'STRIKE_TYPE',
      strikeType: 'ATM',
      entryTime: '09:20',
      entryDays: 'Mon,Tue,Wed,Thu,Fri',
      daysBeforeExpiry: 0,
    };
    
    setStrategy({
      ...strategy,
      legs: [...strategy.legs, newLeg]
    });
  };
  
  const updateLeg = (index, updatedLeg) => {
    const newLegs = [...strategy.legs];
    newLegs[index] = updatedLeg;
    setStrategy({ ...strategy, legs: newLegs });
  };
  
  const removeLeg = (index) => {
    const newLegs = strategy.legs.filter((_, i) => i !== index);
    setStrategy({ ...strategy, legs: newLegs });
  };
  
  const validateStrategy = () => {
    const errors = [];
    const warnings = [];
    
    if (!strategy.name || strategy.name === 'Untitled Strategy') {
      warnings.push('Consider giving your strategy a descriptive name');
    }
    
    if (strategy.legs.length === 0) {
      errors.push('Strategy must have at least one leg');
    }
    
    if (!strategy.dateRange.from || !strategy.dateRange.to) {
      errors.push('Date range is required');
    }
    
    if (new Date(strategy.dateRange.from) >= new Date(strategy.dateRange.to)) {
      errors.push('Start date must be before end date');
    }
    
    setValidation({
      valid: errors.length === 0,
      errors,
      warnings
    });
    
    return errors.length === 0;
  };
  
  const handleRunBacktest = () => {
    if (validateStrategy() && onRunBacktest) {
      onRunBacktest(strategy);
    }
  };
  
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Strategy Builder</h1>
          <p className="text-sm text-gray-400 mt-1">Configure and backtest your options strategies</p>
        </div>
        
        <div className="flex items-center gap-3">
          <Button variant="ghost" icon={Copy} size="sm">
            Duplicate
          </Button>
          <Button variant="secondary" icon={Save} size="sm">
            Save Template
          </Button>
          <Button variant="success" icon={Play} size="md" onClick={handleRunBacktest}>
            Run Backtest
          </Button>
        </div>
      </div>
      
      {/* Validation Status */}
      <ValidationPanel validation={validation} />
      
      {/* Main Configuration */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Panel - Strategy Info */}
        <div className="lg:col-span-1 space-y-6">
          <Card title="Strategy Information">
            <div className="space-y-4">
              <Input
                label="Strategy Name"
                value={strategy.name}
                onChange={(e) => setStrategy({...strategy, name: e.target.value})}
                placeholder="Enter strategy name"
                required
              />
              
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1.5">
                  Description
                </label>
                <textarea
                  value={strategy.description}
                  onChange={(e) => setStrategy({...strategy, description: e.target.value})}
                  className="w-full px-4 py-2.5 bg-dark-card border border-dark-border rounded-lg text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
                  rows="3"
                  placeholder="Optional description..."
                />
              </div>
              
              <Select
                label="Index"
                value={strategy.index}
                onChange={(value) => setStrategy({...strategy, index: value})}
                options={[
                  { value: 'NIFTY', label: 'NIFTY 50' },
                  { value: 'BANKNIFTY', label: 'BANK NIFTY' },
                  { value: 'FINNIFTY', label: 'FIN NIFTY' },
                  { value: 'MIDCPNIFTY', label: 'MIDCAP NIFTY' },
                  { value: 'SENSEX', label: 'SENSEX' },
                ]}
              />
            </div>
          </Card>
          
          <Card title="Backtest Period">
            <div className="space-y-4">
              <Input
                label="From Date"
                type="date"
                value={strategy.dateRange.from}
                onChange={(e) => setStrategy({
                  ...strategy, 
                  dateRange: {...strategy.dateRange, from: e.target.value}
                })}
              />
              
              <Input
                label="To Date"
                type="date"
                value={strategy.dateRange.to}
                onChange={(e) => setStrategy({
                  ...strategy, 
                  dateRange: {...strategy.dateRange, to: e.target.value}
                })}
              />
              
              <Button variant="secondary" size="sm" className="w-full">
                Use Maximum Available Range
              </Button>
            </div>
          </Card>
        </div>
        
        {/* Right Panel - Leg Configuration */}
        <div className="lg:col-span-2">
          <Card 
            title="Strategy Legs"
            subtitle={`${strategy.legs.length} leg(s) configured`}
            headerAction={
              <Button 
                variant="primary" 
                icon={Plus} 
                size="sm"
                onClick={addLeg}
              >
                Add Leg
              </Button>
            }
          >
            {strategy.legs.length === 0 ? (
              <div className="text-center py-12 text-gray-400">
                <div className="mx-auto w-16 h-16 mb-4 rounded-full bg-dark-hover flex items-center justify-center">
                  <Plus className="h-8 w-8" />
                </div>
                <p className="text-lg font-medium mb-2">No legs configured</p>
                <p className="text-sm mb-4">Add your first leg to start building your strategy</p>
                <Button variant="primary" icon={Plus} onClick={addLeg}>
                  Add First Leg
                </Button>
              </div>
            ) : (
              <div className="space-y-4">
                {strategy.legs.map((leg, index) => (
                  <LegConfigurator
                    key={leg.id}
                    leg={leg}
                    index={index}
                    onUpdate={(updatedLeg) => updateLeg(index, updatedLeg)}
                    onRemove={() => removeLeg(index)}
                  />
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};

export default StrategyBuilder;
