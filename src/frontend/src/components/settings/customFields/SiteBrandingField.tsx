import { useRef, useState, type RefObject } from 'react';

import { t } from '../../../i18n';
import { withBasePath } from '../../../utils/basePath';
import type { CustomSettingsFieldRendererProps } from './types';

type AssetKind = 'logo' | 'favicon' | 'mascot';
type BusyKind = AssetKind | 'reset-logo' | 'reset-favicon' | 'reset-mascot';

const ASSET_URLS: Record<AssetKind, string> = {
  logo: '/logo.png',
  favicon: '/favicon.ico',
  mascot: '/mascot.png',
};

interface UploadResponse {
  success?: boolean;
  message?: string;
  error?: string;
}

async function uploadAsset(kind: AssetKind, file: File): Promise<string> {
  const formData = new FormData();
  formData.append('kind', kind);
  formData.append('file', file);

  const res = await fetch(withBasePath('/api/admin/branding/asset'), {
    method: 'POST',
    credentials: 'include',
    body: formData,
  });

  let data: UploadResponse = {};
  try {
    const parsed: unknown = await res.json();
    if (parsed && typeof parsed === 'object') {
      data = parsed;
    }
  } catch {
    // Non-JSON error response (e.g. HTML error page).
  }

  if (!res.ok) {
    throw new Error(data.message || data.error || `Upload failed (${res.status})`);
  }
  return data.message || 'Uploaded.';
}

async function resetAsset(kind: AssetKind): Promise<string> {
  const res = await fetch(withBasePath('/api/admin/branding/asset'), {
    method: 'DELETE',
    credentials: 'include',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `kind=${encodeURIComponent(kind)}`,
  });

  let data: UploadResponse = {};
  try {
    const parsed: unknown = await res.json();
    if (parsed && typeof parsed === 'object') {
      data = parsed;
    }
  } catch {
    // Non-JSON error response.
  }

  if (!res.ok) {
    throw new Error(data.message || data.error || `Reset failed (${res.status})`);
  }
  return data.message || 'Reset.';
}

export const SiteBrandingField = ({ onShowToast }: CustomSettingsFieldRendererProps) => {
  const logoInputRef = useRef<HTMLInputElement>(null);
  const faviconInputRef = useRef<HTMLInputElement>(null);
  const mascotInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<BusyKind | null>(null);
  const [revision, setRevision] = useState(0);

  const notify = (message: string, type: 'success' | 'error' | 'info') => {
    onShowToast?.(message, type);
  };

  const handleUpload = async (kind: AssetKind, file: File) => {
    setBusy(kind);
    try {
      const message = await uploadAsset(kind, file);
      setRevision((value) => value + 1);
      notify(message, 'success');
    } catch (error) {
      notify(error instanceof Error ? error.message : t('action_failed'), 'error');
    } finally {
      setBusy(null);
      if (kind === 'logo' && logoInputRef.current) {
        logoInputRef.current.value = '';
      }
      if (kind === 'favicon' && faviconInputRef.current) {
        faviconInputRef.current.value = '';
      }
      if (kind === 'mascot' && mascotInputRef.current) {
        mascotInputRef.current.value = '';
      }
    }
  };

  const handleReset = async (kind: AssetKind) => {
    setBusy(`reset-${kind}`);
    try {
      const message = await resetAsset(kind);
      setRevision((value) => value + 1);
      notify(message, 'success');
    } catch (error) {
      notify(error instanceof Error ? error.message : t('action_failed'), 'error');
    } finally {
      setBusy(null);
    }
  };

  const previewUrl = (kind: AssetKind): string => {
    const base = ASSET_URLS[kind];
    return `${base}?t=${revision}`;
  };

  const renderAssetRow = (kind: AssetKind, ref: RefObject<HTMLInputElement | null>) => {
    const isBusy = busy === kind;
    const isResetting = busy === `reset-${kind}`;
    const disabled = busy !== null;

    return (
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-(--border-muted) bg-(--bg-soft)">
          {kind === 'favicon' ? (
            <img src={previewUrl('favicon')} alt="" className="h-8 w-8 object-contain" />
          ) : (
            <img src={previewUrl(kind)} alt="" className="max-h-full max-w-full object-contain" />
          )}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <button
            type="button"
            disabled={disabled}
            onClick={() => ref.current?.click()}
            className="inline-flex items-center gap-2 rounded-lg bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isBusy && (
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                  fill="none"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
            )}
            {isBusy ? t('uploading') : t('choose_image')}
          </button>
          <input
            ref={ref}
            type="file"
            accept=".png,.jpg,.jpeg,.webp,.gif,.ico,.avif,image/png,image/jpeg,image/webp,image/gif,image/vnd.microsoft.icon,image/x-icon,image/avif"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                void handleUpload(kind, file);
              }
            }}
          />
          <button
            type="button"
            disabled={disabled}
            onClick={() => {
              void handleReset(kind);
            }}
            className="inline-flex items-center gap-2 rounded-lg border border-(--border-muted) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isResetting && (
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                  fill="none"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
            )}
            {isResetting ? t('reset') : t('reset')}
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="min-w-0 space-y-4">
      <div className="space-y-1.5">
        <div className="text-sm font-medium">{t('site_logo')}</div>
        <p className="text-xs opacity-60">{t('site_logo_description')}</p>
        {renderAssetRow('logo', logoInputRef)}
      </div>
      <div className="space-y-1.5">
        <div className="text-sm font-medium">{t('site_favicon')}</div>
        <p className="text-xs opacity-60">{t('site_favicon_description')}</p>
        {renderAssetRow('favicon', faviconInputRef)}
      </div>
      <div className="space-y-1.5">
        <div className="text-sm font-medium">{t('site_mascot')}</div>
        <p className="text-xs opacity-60">{t('site_mascot_description')}</p>
        {renderAssetRow('mascot', mascotInputRef)}
      </div>
    </div>
  );
};
