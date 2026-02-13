import React from 'react';

const Card = ({ 
  children, 
  title, 
  subtitle,
  headerAction,
  className = '',
  padding = 'normal',
  hover = false,
  ...props 
}) => {
  const paddingClasses = {
    none: '',
    sm: 'p-3',
    normal: 'p-6',
    lg: 'p-8',
  };
  
  return (
    <div 
      className={`
        bg-dark-card border border-dark-border rounded-xl
        shadow-card
        ${hover ? 'transition-shadow duration-200 hover:shadow-card-hover cursor-pointer' : ''}
        ${className}
      `}
      {...props}
    >
      {(title || headerAction) && (
        <div className={`flex items-center justify-between pb-4 border-b border-dark-border ${paddingClasses[padding]} pb-4`}>
          <div>
            {title && <h3 className="text-lg font-semibold text-gray-100">{title}</h3>}
            {subtitle && <p className="text-sm text-gray-400 mt-1">{subtitle}</p>}
          </div>
          {headerAction && <div>{headerAction}</div>}
        </div>
      )}
      
      <div className={title || headerAction ? `${paddingClasses[padding]} pt-4` : paddingClasses[padding]}>
        {children}
      </div>
    </div>
  );
};

export default Card;
