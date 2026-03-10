import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Loader2, Info } from 'lucide-react';
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

const intersectSegments = (aSegments, bSegments) => {
  const result = [];
  let i = 0;
  let j = 0;
  while (i < aSegments.length && j < bSegments.length) {
    const start = new Date(Math.max(aSegments[i].start, bSegments[j].start));
    const end = new Date(Math.min(aSegments[i].end, bSegments[j].end));
    if (start <= end) {
      result.push({ start, end });
    }
    if (aSegments[i].end < bSegments[j].end) i += 1;
    else j += 1;
  }
  return result;
};

const formatDate = (date) => {
  if (!date) return '';
  const iso = date.toISOString().split('T')[0];
  return iso;
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

  useEffect(() => {
    if (!enabled) {
      setSummary(null);
      setSegments([]);
      onFilterChange?.({
        enabled: false,
        configId: selected,
        configLabel: selectedOption.label,
        summary: null,
      });
      return;
    }

    const fetchSegments = async () => {
      setLoading(true);
      setError('');
      try {
        if (selected === 'both') {
          const [aRes, bRes] = await Promise.all([
            fetch('/api/str-segments?config=5x1'),
            fetch('/api/str-segments?config=5x2'),
          ]);
          if (!aRes.ok || !bRes.ok) {
            throw new Error('Unable to load STR segments');
          }
          const aJson = await aRes.json();
          const bJson = await bRes.json();
          const aSegments = parseSegments(aJson?.['5x1'] ?? []);
          const bSegments = parseSegments(bJson?.['5x2'] ?? []);
          const intersected = intersectSegments(aSegments, bSegments);
          setSegments(intersected);
          const summaryPayload = summarize(intersected);
          setSummary(summaryPayload);
          onFilterChange?.({
            enabled: true,
            configId: 'both',
            configLabel: selectedOption.label,
            summary: summaryPayload,
          });
        } else {
          const res = await fetch(`/api/str-segments?config=${selected}`);
          if (!res.ok) throw new Error('Unable to load STR segments');
          const data = await res.json();
          const parsed = parseSegments(data?.[selected] ?? []);
          setSegments(parsed);
          const summaryPayload = summarize(parsed);
          setSummary(summaryPayload);
          onFilterChange?.({
            enabled: true,
            configId: selected,
            configLabel: selectedOption.label,
            summary: summaryPayload,
          });
        }
      } catch (err) {
        setError(err.message || 'Failed to load STR segments.');
        setSegments([]);
        setSummary(null);
        onFilterChange?.({
          enabled: true,
          configId: selected,
          configLabel: selectedOption.label,
          summary: null,
        });
      } finally {
        setLoading(false);
      }
    };

    fetchSegments();
  }, [enabled, selected, selectedOption.label, onFilterChange]);

  const badgeText = summary
    ? `${summary.count} segments | ${formatDate(summary.range.from)} to ${formatDate(summary.range.to)}`
    : 'No segments loaded yet';

  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-4 space-y-4" style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-widest text-gray-600 border-l-4 border-blue-600 pl-2">
            SuperTrend Filter
          </span>
          <Info size={14} className="text-gray-400" />
        </div>
        <Toggle enabled={enabled} onToggle={onToggle} size="sm" />
      </div>

      {enabled && (
        <div className="space-y-3">
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
              {summary && (
                <span className="text-xs text-blue-500">Preview range enforced in results</span>
              )}
            </div>
          )}
          {error && (
            <p className="text-xs text-red-600">{error}</p>
          )}
        </div>
      )}
    </div>
  );
};

export default SuperTrendFilter;
