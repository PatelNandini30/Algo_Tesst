import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Database, UploadCloud, CheckCircle2, XCircle, Loader2, ChevronDown } from 'lucide-react';
import Toggle from './ui/Toggle';
import { DATA_UPLOAD_OPTIONS } from './constants';

const formatBytes = (bytes) => {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let index = 0;
  let value = bytes;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${units[index]}`;
};

const CsvUpload = () => {
  const [selectedTable, setSelectedTable] = useState(DATA_UPLOAD_OPTIONS[0]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [file, setFile] = useState(null);
  const [force, setForce] = useState(false);
  const [status, setStatus] = useState('idle'); // idle, uploading, success, error
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState('');
  const inputRef = useRef(null);
  const dropdownRef = useRef(null);

  const handleOutsideClick = useCallback((event) => {
    if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
      setMenuOpen(false);
    }
  }, []);

  useEffect(() => {
    window.addEventListener('click', handleOutsideClick);
    return () => window.removeEventListener('click', handleOutsideClick);
  }, [handleOutsideClick]);

  const handleFileSelect = (incoming) => {
    if (!incoming) {
      setFile(null);
      setStatus('idle');
      return;
    }
    if (!incoming.name.toLowerCase().endsWith('.csv')) {
      setError('Please select a .csv file only.');
      return;
    }
    setFile(incoming);
    setStatus('idle');
    setError('');
    setSummary(null);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer.files && event.dataTransfer.files.length > 0) {
      handleFileSelect(event.dataTransfer.files[0]);
      event.dataTransfer.clearData();
    }
  };

  const handleUpload = useCallback(async () => {
    if (!file) {
      setError('Select a CSV file before uploading.');
      return;
    }
    setStatus('uploading');
    setError('');
    setSummary(null);
    const formData = new FormData();
    formData.append('data_type', selectedTable.value);
    formData.append('file', file);
    formData.append('force', force ? 'true' : 'false');

    try {
      const response = await fetch('/api/data/upload', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || 'Upload failed');
      }

      const payload = await response.json();
      setSummary(payload);
      setStatus('success');
    } catch (err) {
      setError(err.message || 'Upload failed');
      setStatus('error');
    }
  }, [file, selectedTable, force]);

  const buttonIcon = useMemo(() => {
    if (status === 'uploading') return <Loader2 className="animate-spin" size={16} />;
    if (status === 'success') return <CheckCircle2 size={16} />;
    if (status === 'error') return <XCircle size={16} />;
    return <UploadCloud size={16} />;
  }, [status]);

  const buttonLabel = useMemo(() => {
    if (status === 'uploading') return 'Importing...';
    if (status === 'success') return 'Import Complete';
    if (status === 'error') return 'Retry Upload';
    return 'Upload & Import';
  }, [status]);

  return (
    <div className="bg-surface border border-default shadow-sm rounded-xl p-5 space-y-5" style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <div className="flex items-center gap-3">
        <Database size={20} className="text-accent" />
        <div>
          <p className="text-xs font-semibold tracking-[0.2em] text-secondary uppercase">Data Import</p>
          <p className="text-sm text-muted">Feed CSV files into PostgreSQL</p>
        </div>
      </div>

      <div>
        <p className="text-xs font-semibold text-muted mb-2">Target table</p>
        <div ref={dropdownRef} className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen(v => !v)}
            className="w-full flex items-center justify-between rounded-md border border-blue-200 bg-hover px-4 py-2 text-sm font-semibold text-blue-700 hover:border-blue-300 transition duration-200"
          >
            <span>{selectedTable.label}</span>
            <ChevronDown size={16} className="text-accent" />
          </button>
          {menuOpen && (
            <div className="absolute z-20 mt-2 w-full rounded-lg border border-default bg-surface shadow-lg">
              {DATA_UPLOAD_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    setSelectedTable(opt);
                    setMenuOpen(false);
                  }}
                  className={`w-full px-4 py-2 text-left text-sm transition ${
                    opt.value === selectedTable.value ? 'bg-accent text-inverse text-white' : 'text-secondary hover:bg-hover'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); }}
        onDrop={handleDrop}
        className="relative flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-strong bg-surface px-4 py-6 text-center transition duration-200 hover:border-blue-500 hover:bg-hover cursor-pointer"
      >
        <UploadCloud size={32} className="text-accent" />
        <p className="text-sm font-semibold text-secondary">Drop CSV files here or click to browse</p>
        <p className="text-xs text-muted">Only .csv files are accepted</p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="absolute inset-0 opacity-0 cursor-pointer"
          onChange={(e) => handleFileSelect(e.target.files?.[0] ?? null)}
        />
        {file && (
          <div className="text-xs text-secondary mt-2">
            <strong>{file.name}</strong> · {formatBytes(file.size)}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-secondary">Force re-import (overwrite existing)</p>
        <Toggle enabled={force} onToggle={() => setForce(v => !v)} size="sm" />
      </div>

      <button
        type="button"
        onClick={handleUpload}
        disabled={!file || status === 'uploading'}
        className="w-full flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 px-4 py-3 text-sm font-semibold text-white shadow-lg transition duration-200 hover:scale-[1.02] focus:outline-none disabled:opacity-70 disabled:cursor-not-allowed"
      >
        {buttonIcon}
        <span>{buttonLabel}</span>
      </button>

      {status !== 'idle' && summary && (
        <div className="rounded-xl border border-subtle bg-hover p-3 text-xs text-blue-800 shadow transition-opacity duration-300 opacity-100">
          <p className="font-semibold text-sm text-blue-700">Import summary</p>
          <div className="mt-1 flex flex-wrap gap-3 text-xs">
            <span>Valid rows: {summary.rows_valid ?? summary.rows_read ?? 0}</span>
            <span>Inserted: {summary.rows_inserted ?? 0}</span>
            <span>Skipped: {summary.rows_skipped ?? 0}</span>
          </div>
        </div>
      )}

      {status === 'error' && error && (
        <div className="rounded-xl border border-red-200 bg-loss-bg px-3 py-2 text-xs text-red-700 flex items-center gap-2 animate-pulse">
          <XCircle size={14} />
          {error}
        </div>
      )}
    </div>
  );
};

export default CsvUpload;
