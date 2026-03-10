export const DATA_UPLOAD_OPTIONS = [
  { value: 'option_data', label: 'Option quotes (cleaned_csvs)' },
  { value: 'spot_data', label: 'Spot/strike data (strikeData)' },
  { value: 'expiry_calendar', label: 'Weekly/monthly expiry calendar' },
  { value: 'trading_holidays', label: 'Base2 / holiday ranges' },
  { value: 'super_trend_segments', label: 'SuperTrend STR segments' },
];

export const STR_FILTER_OPTIONS = [
  { value: '5x1', label: 'STR 5x1' },
  { value: '5x2', label: 'STR 5x2' },
  { value: 'both', label: 'Both (5x1 + 5x2)' },
];
