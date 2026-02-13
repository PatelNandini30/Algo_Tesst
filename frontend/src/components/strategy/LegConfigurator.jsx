import React, { useState } from 'react';
import { Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import Card from '../ui/Card';
import Button from '../ui/Button';
import Select from '../ui/Select';
import Input from '../ui/Input';
import StrikeSelector from './StrikeSelector';

const LegConfigurator = ({ leg, index, onUpdate, onRemove }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  
  return (
    <Card className="border-l-4 border-l-primary-500">
      <div className="space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-lg font-bold text-primary-400">Leg {index + 1}</span>
            <Select
              value={leg.optionType || 'CE'}
              onChange={(value) => onUpdate({ ...leg, optionType: value })}
              options={[
                { value: 'CE', label: 'Call (CE)' },
                { value: 'PE', label: 'Put (PE)' },
                { value: 'FUT', label: 'Future (FUT)' },
              ]}
              className="w-40"
            />
            <Select
              value={leg.action || 'BUY'}
              onChange={(value) => onUpdate({ ...leg, action: value })}
              options={[
                { value: 'BUY', label: 'Buy' },
                { value: 'SELL', label: 'Sell' },
              ]}
              className="w-32"
            />
          </div>
          
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              icon={isExpanded ? ChevronUp : ChevronDown}
              onClick={() => setIsExpanded(!isExpanded)}
            />
            <Button
              variant="danger"
              size="sm"
              icon={Trash2}
              onClick={onRemove}
            >
              Remove
            </Button>
          </div>
        </div>
        
        {isExpanded && (
          <>
            {/* Basic Configuration */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Input
                label="Lots"
                type="number"
                value={leg.lots || 1}
                onChange={(e) => onUpdate({ ...leg, lots: parseInt(e.target.value) })}
                min="1"
                hint="Number of lots to trade"
              />
              
              <Select
                label="Expiry"
                value={leg.expiry || 'CURRENT_WEEK'}
                onChange={(value) => onUpdate({ ...leg, expiry: value })}
                options={[
                  { value: 'CURRENT_WEEK', label: 'Current Week' },
                  { value: 'NEXT_WEEK', label: 'Next Week' },
                  { value: 'CURRENT_MONTH', label: 'Current Month' },
                  { value: 'NEXT_MONTH', label: 'Next Month' },
                  { value: 'FAR_MONTH', label: 'Far Month' },
                ]}
              />
              
              <Input
                label="Days Before Expiry"
                type="number"
                value={leg.daysBeforeExpiry || 0}
                onChange={(e) => onUpdate({ ...leg, daysBeforeExpiry: parseInt(e.target.value) })}
                min="0"
                hint="Exit N days before expiry"
              />
            </div>
            
            {/* Strike Selection */}
            {leg.optionType !== 'FUT' && (
              <StrikeSelector leg={leg} onUpdate={onUpdate} />
            )}
            
            {/* Entry/Exit Conditions */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-4">
                <h4 className="text-sm font-semibold text-gray-100">Entry Conditions</h4>
                <Input
                  label="Entry Time"
                  type="time"
                  value={leg.entryTime || '09:20'}
                  onChange={(e) => onUpdate({ ...leg, entryTime: e.target.value })}
                />
                <Input
                  label="Entry Days"
                  type="text"
                  value={leg.entryDays || 'Mon,Tue,Wed,Thu,Fri'}
                  onChange={(e) => onUpdate({ ...leg, entryDays: e.target.value })}
                  hint="Comma-separated: Mon,Tue,Wed,Thu,Fri"
                />
              </div>
              
              <div className="space-y-4">
                <h4 className="text-sm font-semibold text-gray-100">Exit Conditions</h4>
                <Input
                  label="Stop Loss %"
                  type="number"
                  step="0.1"
                  value={leg.stopLoss || ''}
                  onChange={(e) => onUpdate({ ...leg, stopLoss: e.target.value })}
                  suffix="%"
                  placeholder="50"
                />
                <Input
                  label="Target %"
                  type="number"
                  step="0.1"
                  value={leg.target || ''}
                  onChange={(e) => onUpdate({ ...leg, target: e.target.value })}
                  suffix="%"
                  placeholder="100"
                />
              </div>
            </div>
          </>
        )}
      </div>
    </Card>
  );
};

export default LegConfigurator;
