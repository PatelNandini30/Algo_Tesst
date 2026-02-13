import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check } from 'lucide-react';

const Select = ({
  label,
  value,
  onChange,
  options = [],
  placeholder = 'Select an option',
  error,
  hint,
  required,
  disabled,
  className = '',
  searchable = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const selectRef = useRef(null);
  
  const selectedOption = options.find(opt => opt.value === value);
  
  const filteredOptions = searchable
    ? options.filter(opt => 
        opt.label.toLowerCase().includes(searchTerm.toLowerCase())
      )
    : options;
  
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (selectRef.current && !selectRef.current.contains(event.target)) {
        setIsOpen(false);
        setSearchTerm('');
      }
    };
    
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);
  
  const handleSelect = (optionValue) => {
    onChange(optionValue);
    setIsOpen(false);
    setSearchTerm('');
  };
  
  return (
    <div className={`space-y-1.5 ${className}`} ref={selectRef}>
      {label && (
        <label className="block text-sm font-medium text-gray-300">
          {label}
          {required && <span className="text-error ml-1">*</span>}
        </label>
      )}
      
      <div className="relative">
        <button
          type="button"
          onClick={() => !disabled && setIsOpen(!isOpen)}
          disabled={disabled}
          className={`
            w-full px-4 py-2.5 bg-dark-card border rounded-lg text-left
            transition-all duration-200
            focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 focus:ring-offset-dark-bg
            disabled:opacity-50 disabled:cursor-not-allowed
            ${error ? 'border-error' : 'border-dark-border'}
            ${isOpen ? 'ring-2 ring-primary-500' : ''}
          `}
        >
          <span className={selectedOption ? 'text-gray-100' : 'text-gray-500'}>
            {selectedOption?.icon && <span className="mr-2">{selectedOption.icon}</span>}
            {selectedOption?.label || placeholder}
          </span>
          <ChevronDown className={`absolute right-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>
        
        {isOpen && (
          <div className="absolute z-50 w-full mt-2 bg-dark-card border border-dark-border rounded-lg shadow-lg max-h-60 overflow-auto">
            {searchable && (
              <div className="p-2 border-b border-dark-border">
                <input
                  type="text"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Search..."
                  className="w-full px-3 py-2 bg-dark-hover border border-dark-border rounded text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
                  onClick={(e) => e.stopPropagation()}
                />
              </div>
            )}
            
            <div className="py-1">
              {filteredOptions.length === 0 ? (
                <div className="px-4 py-2 text-sm text-gray-500">No options found</div>
              ) : (
                filteredOptions.map((option) => (
                  <button
                    key={option.value}
                    onClick={() => handleSelect(option.value)}
                    className={`
                      w-full px-4 py-2 text-left text-sm flex items-center justify-between
                      transition-colors duration-150
                      ${option.value === value 
                        ? 'bg-primary-600 text-white' 
                        : 'text-gray-300 hover:bg-dark-hover'
                      }
                    `}
                  >
                    <span>
                      {option.icon && <span className="mr-2">{option.icon}</span>}
                      {option.label}
                    </span>
                    {option.value === value && <Check className="h-4 w-4" />}
                  </button>
                ))
              )}
            </div>
          </div>
        )}
      </div>
      
      {hint && !error && (
        <p className="text-xs text-gray-500">{hint}</p>
      )}
      
      {error && (
        <p className="text-xs text-error">{error}</p>
      )}
    </div>
  );
};

export default Select;
