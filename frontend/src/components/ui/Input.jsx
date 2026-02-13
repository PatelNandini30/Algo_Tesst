import React, { useState } from 'react';
import { AlertCircle, CheckCircle, Info } from 'lucide-react';

const Input = ({
  label,
  type = 'text',
  value,
  onChange,
  error,
  success,
  hint,
  required,
  disabled,
  prefix,
  suffix,
  className = '',
  ...props
}) => {
  const [focused, setFocused] = useState(false);
  
  const hasError = Boolean(error);
  const hasSuccess = Boolean(success);
  
  const inputClasses = `
    w-full px-4 py-2.5 bg-dark-card border rounded-lg text-gray-100 placeholder-gray-500
    transition-all duration-200
    focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-dark-bg
    disabled:opacity-50 disabled:cursor-not-allowed
    ${hasError ? 'border-error focus:ring-error' : ''}
    ${hasSuccess ? 'border-success focus:ring-success' : ''}
    ${!hasError && !hasSuccess ? 'border-dark-border focus:ring-primary-500' : ''}
    ${prefix ? 'pl-10' : ''}
    ${suffix ? 'pr-10' : ''}
  `;
  
  return (
    <div className={`space-y-1.5 ${className}`}>
      {label && (
        <label className="block text-sm font-medium text-gray-300">
          {label}
          {required && <span className="text-error ml-1">*</span>}
        </label>
      )}
      
      <div className="relative">
        {prefix && (
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <span className="text-gray-500 text-sm">{prefix}</span>
          </div>
        )}
        
        <input
          type={type}
          value={value}
          onChange={onChange}
          disabled={disabled}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className={inputClasses}
          {...props}
        />
        
        {suffix && !hasError && !hasSuccess && (
          <div className="absolute inset-y-0 right-0 pr-3 flex items-center pointer-events-none">
            <span className="text-gray-500 text-sm">{suffix}</span>
          </div>
        )}
        
        {hasError && (
          <div className="absolute inset-y-0 right-0 pr-3 flex items-center pointer-events-none">
            <AlertCircle className="h-5 w-5 text-error" />
          </div>
        )}
        
        {hasSuccess && (
          <div className="absolute inset-y-0 right-0 pr-3 flex items-center pointer-events-none">
            <CheckCircle className="h-5 w-5 text-success" />
          </div>
        )}
      </div>
      
      {hint && !error && (
        <p className="text-xs text-gray-500 flex items-center gap-1">
          <Info className="h-3 w-3" />
          {hint}
        </p>
      )}
      
      {error && (
        <p className="text-xs text-error flex items-center gap-1">
          <AlertCircle className="h-3 w-3" />
          {error}
        </p>
      )}
      
      {success && (
        <p className="text-xs text-success flex items-center gap-1">
          <CheckCircle className="h-3 w-3" />
          {success}
        </p>
      )}
    </div>
  );
};

export default Input;
