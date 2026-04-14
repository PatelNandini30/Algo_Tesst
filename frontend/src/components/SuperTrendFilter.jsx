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
  const [filterCatalog, setFilterCatalog] = useState(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [csvUploading, setCsvUploading] = useState(false);
  const [csvFileName, setCsvFileName] = useState('');
  const [customSegments, setCustomSegments] = useState([]);
  const [uploadError, setUploadError] = useState('');
  const fileInputRef = useRef(null);
  const dropdownRef = useRef(null);
  const [entryMode, setEntryMode] = useState('dte');

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

  const handleCsvUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setCsvUploading(true);
    setCsvFileName(file.name);
    setUploadError('');
    setCatalogError('');

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
      setSelected('custom');
    } catch (err) {
      setUploadError(err.message || 'Failed to upload CSV');
      setCustomSegments([]);
      setCsvFileName('');
    } finally {
      setCsvUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const fetchFilterCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError('');
    try {
      const res = await fetch('/api/filter-segments');
      if (!res.ok) {
        throw new Error('Unable to fetch filter metadata');
      }
      const data = await res.json();
      if (!data.success) {
        throw new Error(data.message || 'Failed to load filter segments');
      }
      setFilterCatalog(data.filters || {});
    } catch (err) {
      setCatalogError(err.message || 'Failed to load filter segments.');
      setFilterCatalog({});
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  const prevEnabledRef = useRef(false);
  useEffect(() => {
    if (enabled && !prevEnabledRef.current) {
      fetchFilterCatalog();
    }
    prevEnabledRef.current = enabled;
  }, [enabled, fetchFilterCatalog]);

  const activeFilterMeta = useMemo(() => filterCatalog?.[selected] ?? null, [filterCatalog, selected]);
  const customSegmentsPayload = useMemo(
    () => customSegments.map(s => ({ start: formatDate(s.start), end: formatDate(s.end) })),
    [customSegments]
  );
  const summaryPayload = useMemo(() => {
    if (!enabled) return null;
    if (selected === 'custom') {
      return customSegments.length ? summarize(customSegments) : null;
    }
    if (!activeFilterMeta?.range?.from || !activeFilterMeta?.range?.to) return null;
    return {
      count: activeFilterMeta.count ?? 0,
      range: {
        from: new Date(activeFilterMeta.range.from),
        to: new Date(activeFilterMeta.range.to),
      },
    };
  }, [enabled, selected, customSegments, activeFilterMeta]);

  const prevFilterChangeRef = useRef(null);
  useEffect(() => {
    const payloadSegments = selected === 'custom' ? customSegmentsPayload : null;
    const filterState = {
      enabled,
      configId: selected,
      configLabel: selectedOption.label,
      summary: enabled ? summaryPayload : null,
      segments: payloadSegments,
      entryMode,
    };

    const prevState = prevFilterChangeRef.current;
    const stateChanged = !prevState ||
      prevState.enabled !== filterState.enabled ||
      prevState.configId !== filterState.configId ||
      prevState.segments !== filterState.segments ||
      prevState.entryMode !== filterState.entryMode;

    prevFilterChangeRef.current = filterState;

    if (!stateChanged) return;

    onFilterChange?.(filterState);
  }, [enabled, selected, selectedOption.label, summaryPayload, customSegmentsPayload, entryMode, onFilterChange]);

  const previewRows = useMemo(() => {
    if (!enabled) return [];
    if (selected === 'custom') {
      return customSegments.slice(0, 5).map(seg => ({
        start: formatDateDisplay(seg.start),
        end: formatDateDisplay(seg.end),
      }));
    }
    if (selected === 'base2') return [];
    const previewData = filterCatalog?.[selected]?.preview ?? [];
    return previewData.map(seg => ({
      start: formatDateDisplay(new Date(seg.start)),
      end: formatDateDisplay(new Date(seg.end)),
    }));
  }, [enabled, selected, customSegments, filterCatalog]);

  const badgeText = useMemo(() => {
    if (!enabled) return 'Filter disabled';
    if (catalogLoading) return 'Loading segments…';
    if (selected === 'base2' && activeFilterMeta?.display_range) {
      return activeFilterMeta.display_range;
    }
    if (summaryPayload && summaryPayload.range?.from && summaryPayload.range?.to) {
      return `${summaryPayload.count} segments · ${formatDateDisplay(summaryPayload.range.from)} → ${formatDateDisplay(summaryPayload.range.to)}`;
    }
    if (selected === 'custom') return 'Upload a CSV to set date ranges';
    return 'Filter enabled but no segments loaded';
  }, [enabled, catalogLoading, selected, summaryPayload, activeFilterMeta]);

  const showNoSegmentsWarning = enabled && !catalogLoading && (
    selected === 'custom'
      ? customSegments.length === 0
      : (activeFilterMeta?.count ?? 0) === 0
  );

  return (
    <div className="bg-surface shadow-sm border border-default rounded-xl p-4 space-y-3" style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-widest text-secondary border-l-4 border-accent-border pl-2">
            Filter
          </span>
        </div>
        <Toggle enabled={enabled} onToggle={onToggle} size="sm" />
      </div>

      {enabled && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
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
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-secondary bg-hover border border-default rounded-lg hover:bg-base transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {csvUploading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Upload className="w-3.5 h-3.5" />
              )}
              Upload CSV
            </button>
            {csvFileName && (
              <span className="flex items-center gap-1 text-xs text-profit">
                <FileText className="w-3 h-3" />
                {csvFileName}
                <button
                  type="button"
                  onClick={() => {
                    setCsvFileName('');
                    setCustomSegments([]);
                    setUploadError('');
                    setSelected('5x1');
                  }}
                  className="ml-1 hover:text-loss"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            )}
          </div>
          {uploadError && (
            <p className="text-xs text-loss">{uploadError}</p>
          )}

          <div className="space-y-3">
            <div ref={dropdownRef} className="relative">
              <button
                type="button"
                onClick={() => setDropdownOpen(v => !v)}
                className="w-full flex items-center justify-between rounded-lg border border-blue-200 bg-hover px-4 py-2 text-sm font-medium text-blue-700 shadow-sm hover:border-blue-400 transition duration-200"
              >
                <span>{selectedOption.label}</span>
                <ChevronDown size={16} />
              </button>
              {dropdownOpen && (
                <div className="absolute z-20 mt-1 w-full rounded-lg border border-default bg-surface shadow-lg">
                  {STR_FILTER_OPTIONS.map(opt => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => {
                        setSelected(opt.value);
                        setDropdownOpen(false);
                      }}
                      className={`w-full text-left px-4 py-3 text-sm transition-colors ${
                        opt.value === selected ? 'bg-accent text-inverse text-white' : 'text-secondary hover:bg-hover'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center gap-2">
              {catalogLoading && <Loader2 className="text-accent animate-spin" size={16} />}
              <span className="rounded-full border border-subtle bg-hover px-3 py-1 text-xs font-semibold text-blue-700 shadow-sm">
                {badgeText}
              </span>
            </div>
            {catalogError && (
              <div className="flex flex-wrap items-center gap-2 text-xs text-loss">
                <span>{catalogError}</span>
                <button
                  type="button"
                  onClick={fetchFilterCatalog}
                  className="text-loss underline underline-offset-2"
                >
                  Retry
                </button>
              </div>
            )}
            {showNoSegmentsWarning && (
              <p className="text-xs text-yellow-700">
                Filter is enabled but no segments were loaded — zero trades will be executed. {selected === 'custom' ? 'Upload a CSV or try a different filter.' : 'Check the database or try another config.'}
              </p>
            )}

            {/* Entry Mode */}
            <div className="space-y-1.5">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide">
                Entry Mode
              </p>
              <div className="relative">
                <select
                  value={entryMode}
                  onChange={e => setEntryMode(e.target.value)}
                  className="w-full appearance-none rounded-lg border border-blue-200 bg-hover pl-4 pr-9 py-2 text-sm font-medium text-blue-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-400 cursor-pointer transition-colors duration-200 hover:border-blue-400"
                >
                  <option value="dte">DTE Entry — N days before each expiry</option>
                  <option value="fixed">Fixed Entry — Pinned to segment start</option>
                </select>
                <ChevronDown
                  size={14}
                  className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-accent"
                />
              </div>
              <p className="text-[11px] text-muted leading-relaxed">
                {entryMode === 'dte'
                  ? 'Enter N days before each expiry within the segment. Current behaviour — unchanged.'
                  : 'Enter on segment start date, then re-enter the next trading day after each exit. Stays active throughout the segment with no gap.'}
              </p>
            </div>

            {previewRows.length > 0 && (
              <div className="space-y-1 text-[11px] text-secondary">
                <p className="text-xs font-semibold text-muted uppercase tracking-wide">Segment preview</p>
                {previewRows.map((row, idx) => (
                  <div key={`preview-${selected}-${idx}`} className="flex items-center justify-between text-secondary">
                    <span>{row.start}</span>
                    <span className="text-muted">→</span>
                    <span>{row.end}</span>
                  </div>
                ))}
              </div>
            )}

            {selected === 'custom' && (
              <div className="text-[11px] text-muted space-y-1">
                <p>CSV requires exactly two columns: start/start_date/startdt/from/from_date or entry/entry_date/entrydt, plus the matching end/exit column.</p>
                <p>Supported formats: DD-MM-YYYY, YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY, YYYY/MM/DD, DD-Mon-YYYY, YYYYMMDD.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default SuperTrendFilter;
