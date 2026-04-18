import React from 'react';

const Toggle = ({ enabled, onToggle, size = 'md' }) => {
  const sizeClasses = size === 'sm' ? 'h-4 w-7' : 'h-5 w-9';
  const dotClasses = size === 'sm' ? 'h-3 w-3' : 'h-3.5 w-3.5';
  const translateClasses =
    size === 'sm'
      ? enabled
        ? 'translate-x-3'
        : 'translate-x-0.5'
      : enabled
        ? 'translate-x-4'
        : 'translate-x-0.5';

  return (
    <button
      type="button"
      onClick={() => onToggle(!enabled)}
      className={`relative inline-flex ${sizeClasses} flex-shrink-0 items-center rounded-full transition-colors duration-200 focus:outline-none ${
        enabled ? 'bg-accent text-inverse' : 'bg-gray-300'
      }`}
    >
      <span className={`inline-block ${dotClasses} transform rounded-full bg-surface shadow transition-transform duration-200 ${translateClasses}`} />
    </button>
  );
};

export default Toggle;
