import React from 'react';

const Skeleton = ({ 
  width = 'w-full', 
  height = 'h-4', 
  className = '',
  variant = 'default' 
}) => {
  const variants = {
    default: 'bg-dark-hover',
    card: 'bg-dark-card',
    text: 'bg-dark-hover rounded',
    circle: 'bg-dark-hover rounded-full',
  };
  
  return (
    <div 
      className={`animate-pulse-subtle ${variants[variant]} ${width} ${height} ${className}`}
    />
  );
};

export const SkeletonCard = () => (
  <div className="bg-dark-card border border-dark-border rounded-xl p-6 space-y-4">
    <Skeleton width="w-1/3" height="h-6" />
    <Skeleton width="w-full" height="h-4" />
    <Skeleton width="w-2/3" height="h-4" />
  </div>
);

export const SkeletonTable = ({ rows = 5, cols = 4 }) => (
  <div className="space-y-3">
    <div className="flex gap-4">
      {Array.from({ length: cols }).map((_, i) => (
        <Skeleton key={i} width="flex-1" height="h-8" />
      ))}
    </div>
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} className="flex gap-4">
        {Array.from({ length: cols }).map((_, j) => (
          <Skeleton key={j} width="flex-1" height="h-6" />
        ))}
      </div>
    ))}
  </div>
);

export default Skeleton;
