import React from 'react';
import { Loader2 } from 'lucide-react';

const Button = ({ 
  children, 
  variant = 'primary', 
  size = 'md', 
  loading = false, 
  disabled = false,
  icon: Icon,
  iconPosition = 'left',
  className = '',
  ...props 
}) => {
  const baseStyles = 'inline-flex items-center justify-center font-medium rounded-lg transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed';
  
  const variants = {
    primary: 'bg-primary-600 hover:bg-primary-700 text-white focus:ring-primary-500 shadow-sm hover:shadow-md',
    secondary: 'bg-dark-card hover:bg-dark-hover text-gray-100 border border-dark-border focus:ring-primary-500',
    success: 'bg-success hover:bg-green-600 text-white focus:ring-success shadow-sm hover:shadow-md',
    danger: 'bg-error hover:bg-red-600 text-white focus:ring-error shadow-sm hover:shadow-md',
    ghost: 'hover:bg-dark-hover text-gray-300 focus:ring-primary-500',
    link: 'text-primary-400 hover:text-primary-300 underline-offset-4 hover:underline',
  };
  
  const sizes = {
    sm: 'px-3 py-1.5 text-sm',
    md: 'px-4 py-2 text-sm',
    lg: 'px-6 py-3 text-base',
    xl: 'px-8 py-4 text-lg',
  };
  
  return (
    <button
      className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
      {!loading && Icon && iconPosition === 'left' && <Icon className="mr-2 h-4 w-4" />}
      {children}
      {!loading && Icon && iconPosition === 'right' && <Icon className="ml-2 h-4 w-4" />}
    </button>
  );
};

export default Button;
