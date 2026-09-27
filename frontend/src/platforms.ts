export type PlatformId = 'youtube' | 'vk' | 'tiktok' | 'instagram' | 'dzen' | 'rutube'

type PlatformConfig = {
  label: string
  shortLabel: string
  accountPlaceholder: string
  publicationPlaceholder: string
  automaticReadings: boolean
}

export const platforms: Record<PlatformId, PlatformConfig> = {
  youtube: { label: 'YouTube', shortLabel: 'YT', accountPlaceholder: 'https://youtube.com/@channel', publicationPlaceholder: 'https://youtube.com/shorts/...', automaticReadings: true },
  vk: { label: 'VK', shortLabel: 'VK', accountPlaceholder: 'https://vk.com/author', publicationPlaceholder: 'https://vk.com/video-1_...', automaticReadings: true },
  tiktok: { label: 'TikTok', shortLabel: 'TT', accountPlaceholder: 'https://tiktok.com/@author', publicationPlaceholder: 'https://tiktok.com/@author/video/...', automaticReadings: true },
  instagram: { label: 'Instagram', shortLabel: 'IG', accountPlaceholder: 'https://instagram.com/author', publicationPlaceholder: 'https://instagram.com/reel/...', automaticReadings: false },
  dzen: { label: 'Дзен', shortLabel: 'ДЗ', accountPlaceholder: 'https://dzen.ru/author', publicationPlaceholder: 'https://dzen.ru/video/watch/...', automaticReadings: false },
  rutube: { label: 'RUTUBE', shortLabel: 'RT', accountPlaceholder: 'https://rutube.ru/channel/...', publicationPlaceholder: 'https://rutube.ru/video/...', automaticReadings: true },
}

export const platformIds = Object.keys(platforms) as PlatformId[]

export function platformConfig(value?: string | null): PlatformConfig {
  return platforms[value as PlatformId] ?? {
    label: 'Другая площадка', shortLabel: '—',
    accountPlaceholder: 'https://...', publicationPlaceholder: 'https://...', automaticReadings: false,
  }
}

export const enrichmentLabels: Record<string, string> = {
  not_requested: 'Обновление ещё не запускалось', pending: 'Ожидает обновления',
  processing: 'Данные обновляются', succeeded: 'Данные обновлены',
  retry_wait: 'Повторная попытка позже', failed: 'Не удалось обновить данные',
}

export const availabilityLabels: Record<string, string> = {
  unknown: 'Доступность уточняется', available: 'Публикация доступна', unavailable: 'Публикация недоступна',
}

export const readingSourceLabels: Record<string, string> = {
  manual: 'Внесено вручную', youtube_api: 'YouTube', tiktok_public: 'TikTok',
  vk_public: 'VK', rutube_public: 'RUTUBE',
}

export const enrichmentLabel = (value: unknown) =>
  typeof value === 'string' ? enrichmentLabels[value] ?? 'Обновление данных' : 'Обновление данных'

export const availabilityLabel = (value: unknown) =>
  typeof value === 'string' ? availabilityLabels[value] ?? 'Доступность уточняется' : 'Доступность уточняется'

export const readingSourceLabel = (value: unknown) =>
  typeof value === 'string' ? readingSourceLabels[value] ?? 'Автоматический сбор' : 'Автоматический сбор'

export const visibleRiskFlags = (flags: unknown): string[] =>
  Array.isArray(flags) ? flags.filter((flag): flag is string =>
    typeof flag === 'string' && flag !== 'approximate_public_counter',
  ) : []
