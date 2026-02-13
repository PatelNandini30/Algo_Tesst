import React from 'react';

const Tabs = ({ tabs, activeTab, onChange, className = '' }) => {
  return (
    <div className={`border-b border-dark-border ${className}`}>
      <nav className="flex space-x-8" aria-label="Tabs">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.value;
          const Icon = tab.icon;
          
          return (
            <button
              key={tab.value}
              onClick={() => onChange(tab.value)}
              className={`
                flex items-center gap-2 py-4 px-1 border-b-2 font-medium text-sm
                transition-colors duration-200
                ${isActive
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-gray-500 hover:text-gray-300 hover:border-gray-300'
                }
              `}
            >
              {Icon && <Icon className="h-5 w-5" />}
              {tab.label}
              {tab.badge && (
                <span className={`
                  ml-2 py-0.5 px-2 rounded-full text-xs font-medium
                  ${isActive ? 'bg-primary-100 text-primary-800' : 'bg-gray-100 text-gray-800'}
                `}>
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>
    </div>
  );
};

export default Tabs;
