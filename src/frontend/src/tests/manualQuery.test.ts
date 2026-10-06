import { describe, it, expect } from 'vitest';

import { getDefaultManualQuery } from '../hooks/releaseModal/useReleaseSearchSession';
import type { Book } from '../types/index';

const makeBook = (overrides: Partial<Book>): Book =>
  ({
    id: 'telegram_group:abc',
    title: 'Manuale del Giocatore',
    provider: 'telegram_group',
    provider_id: 'tg:1:2:3',
    ...overrides,
  }) as Book;

describe('getDefaultManualQuery', () => {
  it('skips placeholder authors from source-backed books', () => {
    expect(getDefaultManualQuery(makeBook({ author: 'Unknown author' }))).toBe(
      'Manuale del Giocatore',
    );
    expect(getDefaultManualQuery(makeBook({ author: 'Unknown' }))).toBe(
      'Manuale del Giocatore',
    );
    expect(getDefaultManualQuery(makeBook({ author: '  UNKNOWN AUTHOR ' }))).toBe(
      'Manuale del Giocatore',
    );
  });

  it('keeps real authors and prefers search fields', () => {
    expect(getDefaultManualQuery(makeBook({ author: 'Brandon Sanderson' }))).toBe(
      'Manuale del Giocatore Brandon Sanderson',
    );
    expect(
      getDefaultManualQuery(
        makeBook({ search_title: 'Elantris', search_author: 'Sanderson' }),
      ),
    ).toBe('Elantris Sanderson');
  });
});
