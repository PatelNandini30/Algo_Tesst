import React from 'react';
import { AlertCircle, CheckCircle, AlertTriangle } from 'lucide-react';

const ValidationPanel = ({ validation }) => {
  if (!validation || (validation.valid && validation.errors?.length === 0 && validation.warnings?.length === 0)) {
    return null;
  }
  
  const hasErrors = validation.errors && validation.errors.length > 0;
  const hasWarnings = validation.warnings && validation.warnings.length > 0;
  
  return (
    <div className="space-y-3">
      {/* Errors */}
      {hasErrors && (
        <div className="bg-red-900/20 border border-red-800/30 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-red-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <h4 className="text-sm font-semibold text-red-300 mb-2">
                {validation.errors.length} Error{validation.errors.length > 1 ? 's' : ''} Found
              </h4>
              <ul className="space-y-1">
                {validation.errors.map((error, index) => (
                  <li key={index} className="text-sm text-red-400">
                    • {error}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
      
      {/* Warnings */}
      {hasWarnings && (
        <div className="bg-yellow-900/20 border border-yellow-800/30 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-yellow-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <h4 className="text-sm font-semibold text-yellow-300 mb-2">
                {validation.warnings.length} Warning{validation.warnings.length > 1 ? 's' : ''}
              </h4>
              <ul className="space-y-1">
                {validation.warnings.map((warning, index) => (
                  <li key={index} className="text-sm text-yellow-400">
                    • {warning}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
      
      {/* Success */}
      {validation.valid && !hasErrors && !hasWarnings && (
        <div className="bg-green-900/20 border border-green-800/30 rounded-lg p-4">
          <div className="flex items-center gap-3">
            <CheckCircle className="h-5 w-5 text-green-400" />
            <p className="text-sm text-green-300 font-medium">
              Strategy configuration is valid and ready to backtest
            </p>
          </div>
        </div>
      )}
    </div>
  );
};

export default ValidationPanel;
