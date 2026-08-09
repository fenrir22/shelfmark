import { it, type TranslationKey } from './it'

export function t(key: string, params?: Record<string, string | number>): string {
  const translation = (it as Record<string, string>)[key] ?? key
  if (!params) return translation
  return translation.replace(/\{(\w+)\}/g, (_, k: string) =>
    params[k] !== undefined ? String(params[k]) : `{${k}}`
  )
}

export function useTranslation() {
  return { t }
}

export type { TranslationKey }
