import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Loader2, Info, Upload, FileText, X } from 'lucide-react';
import Toggle from './ui/Toggle';
import { STR_FILTER_OPTIONS } from './constants';

const parseSegments = (rawSegments = []) => {
  return rawSegments
    .map(seg => ({
      start: seg?.start ? new Date(seg.start) : null,
      end: seg?.end ? new Date(seg.end) : null,
    }))
    .filter(seg => seg.start && seg.end)
    .sort((a, b) => a.start - b.start);
};

const formatDate = (date) => {
  if (!date) return '';
  const iso = date.toISOString().split('T')[0];
  return iso;
};

const formatDateDisplay = (date) => {
  if (!date) return '';
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' });
};

const SkeletonLine = () => (
  <div className="h-4 w-24 rounded-full bg-slate-200 animate-pulse" />
);

const SuperTrendFilter = ({ enabled, onToggle, onFilterChange }) => {
  const [selected, setSelected] = useState('5x1');
  const [segments, setSegments] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [csvUploading, setCsvUploading] = useState(false);
  const [csvFileName, setCsvFileName] = useState('');
  const [customSegments, setCustomSegments] = useState([]);
  const fileInputRef = useRef(null);
  const dropdownRef = useRef(null);

  const selectedOption = useMemo(
    () => STR_FILTER_OPTIONS.find(opt => opt.value === selected) ?? STR_FILTER_OPTIONS[0],
    [selected],
  );

  const handleOutsideClick = useCallback((event) => {
    if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
      setDropdownOpen(false);
    }
  }, []);

  useEffect(() => {
    window.addEventListener('click', handleOutsideClick);
    return () => window.removeEventListener('click', handleOutsideClick);
  }, [handleOutsideClick]);

  const summarize = (items) => {
    if (!items?.length) return null;
    const starts = items.map(i => i.start?.getTime() ?? Infinity);
    const ends = items.map(i => i.end?.getTime() ?? 0);
    const range = {
      from: new Date(Math.min(...starts)),
      to: new Date(Math.max(...ends)),
    };
    return {
      count: items.length,
      range,
    };
  };

  // Handle CSV file upload
  const handleCsvUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setCsvUploading(true);
    setCsvFileName(file.name);
    setError('');

    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch('/api/upload-filter-csv', {
        method: 'POST',
        body: formData,
      });

      const data = await res.json();

      if (!data.success) {
        throw new Error(data.message || 'Failed to parse CSV');
      }

      const parsed = parseSegments(data.segments || []);
      setCustomSegments(parsed);
      setSegments(parsed);
      const summaryPayload = summarize(parsed);
      setSummary(summaryPayload);

      // Auto-select custom when CSV is uploaded
      setSelected('custom');

      onFilterChange?.({
        enabled: true,
        configId: 'custom',
        configLabel: 'Custom CSV',
        summary: summaryPayload,
        segments: data.segments || [],
      });
    } catch (err) {
      setError(err.message || 'Failed to upload CSV');
      setCustomSegments([]);
    } finally {
      setCsvUploading(false);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  useEffect(() => {
    if (!enabled) {
      setSummary(null);
      setSegments([]);
      onFilterChange?.({
        enabled: false,
        configId: selected,
        configLabel: selectedOption.label,
        summary: null,
        segments: [],
      });
      return;
    }

    // If custom is selected and we have custom segments, use them
    if (selected === 'custom' && customSegments.length > 0) {
      const summaryPayload = summarize(customSegments);
      setSummary(summaryPayload);
      onFilterChange?.({
        enabled: true,
        configId: 'custom',
        configLabel: 'Custom CSV',
        summary: summaryPayload,
        segments: customSegments.map(s => ({ start: formatDate(s.start), end: formatDate(s.end) })),
      });
      return;
    }

    const fetchSegments = async () => {
      setLoading(true);
      setError('');
      try {
        // Fetch filter counts
        const res = await fetch('/api/filter-segments');
        const data = await res.json();

        // Use the filter config directly (5x1, 5x2, base2)
        const filterRes = await fetch(`/api/str-segments?config=${selected}`);
        if (!filterRes.ok) throw new Error('Unable to load filter segments');
        
        const filterData = await filterRes.json();
        const parsed = parseSegments(filterData?.[selected] ?? []);
        setSegments(parsed);
        const summaryPayload = summarize(parsed);
        setSummary(summaryPayload);

        onFilterChange?.({
          enabled: true,
          configId: selected,
          configLabel: selectedOption.label,
          summary: summaryPayload,
          segments: parsed.map(s => ({ start: formatDate(s.start), end: formatDate(s.end) })),
        });
      } catch (err) {
        setError(err.message || 'Failed to load filter segments.');
        setSegments([]);
        setSummary(null);
        onFilterChange?.({
          enabled: true,
          configId: selected,
          configLabel: selectedOption.label,
          summary: null,
          segments: [],
        });
      } finally {
        setLoading(false);
      }
    };

    fetchSegments();
  }, [enabled, selected, selectedOption.label, customSegments, onFilterChange]);

  const badgeText = summary
    ? `${summary.count} segments | ${formatDateDisplay(summary.range.from)} to ${formatDateDisplay(summary.range.to)}`
    : 'No segments loaded yet';

  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-4 space-y-3" style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-widest text-gray-600 border-l-4 border-blue-600 pl-2">
            Filter
          </span>
        </div>
        <Toggle enabled={enabled} onToggle={onToggle} size="sm" />
      </div>

      {/* CSV Upload Button - Always visible when enabled */}
      {enabled && (
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            onChange={handleCsvUpload}
            className="hidden"
            id="filter-csv-upload"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={csvUploading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-50 border border-gray-200 rounded-lg hover:bg-gray-100 transition-colors disabled:opacity-50"
          >
            {csvUploading ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Upload className="w-3.5 h-3.5" />
            )}
            Upload CSV
          </button>
          {csvFileName && (
            <span className="flex items-center gap-1 text-xs text-green-600">
              <FileText className="w-3 h-3" />
              {csvFileName}
              <button
                type="button"
                onClick={() => {
                  setCsvFileName('');
                  setCustomSegments([]);
                  setSegments([]);
                  setSummary(null);
                }}
                className="ml-1 hover:text-red-600"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          )}
        </div>
      )}

      {enabled && (
        <div className="space-y-3">
          {/* Dropdown */}
          <div ref={dropdownRef} className="relative">
            <button
              type="button"
              onClick={() => setDropdownOpen(v => !v)}
              className="w-full flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 shadow-sm hover:border-blue-400 transition duration-200"
            >
              <span>{selectedOption.label}</span>
              <ChevronDown size={16} />
            </button>
            {dropdownOpen && (
              <div className="absolute z-20 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg">
                {STR_FILTER_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => {
                      setSelected(opt.value);
                      setDropdownOpen(false);
                    }}
                    className={`w-full text-left px-4 py-3 text-sm transition-colors ${
                      opt.value === selected ? 'bg-blue-600 text-white' : 'text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Loading / Segments Display */}
          {loading ? (
            <div className="space-y-2">
              <div className="flex items-center gap-3 text-xs text-gray-500">
                <Loader2 className="animate-spin text-blue-600" size={18} />
                <span>Loading segments…</span>
              </div>
              <SkeletonLine />
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700 shadow-sm">
                {badgeText}
              </span>
            </div>
          )}
          {error && (
            <p className="text-xs text-red-600">{error}</p>
          )}
        </div>
      )}

      {/* Exit Rule Reminder - Always visible */}
      <div className="pt-2 border-t border-gray-100">
        <div className="flex items-center gap-1 text-xs text-gray-500">
          <span className="font-medium">Exit:</span>
          <span className="px-1.5 py-0.5 bg-red-50 text-red-600 rounded">SL</span>
          <span>·</span>
          <span className="px-1.5 py-0.5 bg-green-50 text-green-600 rounded">Target</span>
          <span>·</span>
          <span className="px-1.5 py-0.5 bg-orange-50 text-orange-600 rounded">Filter End</span>
          <span>·</span>
          <span className="px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded">Expiry</span>
        </div>
      </div>
    </div>
  );
};

export default SuperTrendFilter;
