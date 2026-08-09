import { t } from '../i18n';

export const SORT_OPTIONS = [
  { value: '', label: t('most_relevant') },
  { value: 'newest', label: t('newest_publication_year') },
  { value: 'oldest', label: t('oldest_publication_year') },
  { value: 'largest', label: t('largest_filesize') },
  { value: 'smallest', label: t('smallest_filesize') },
  { value: 'newest_added', label: t('newest_open_sourced') },
  { value: 'oldest_added', label: t('oldest_open_sourced') },
];

// Note: Metadata mode sort options are now dynamic per provider
// They come from the /api/config endpoint as metadata_sort_options

// Direct download mode content type options
export const CONTENT_OPTIONS = [
  { value: '', label: t('all') },
  { value: 'book_nonfiction', label: t('book_non_fiction') },
  { value: 'book_fiction', label: t('book_fiction') },
  { value: 'book_unknown', label: t('book_unknown') },
  { value: 'magazine', label: t('magazine') },
  { value: 'book_comic', label: t('comic_book') },
  { value: 'standards_document', label: t('standards_document') },
  { value: 'other', label: t('other') },
  { value: 'musical_score', label: t('musical_score') },
];
