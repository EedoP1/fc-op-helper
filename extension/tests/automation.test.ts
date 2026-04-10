import { describe, it, expect } from 'vitest';
import { jitter } from '../src/automation';

describe('jitter', () => {
  it('resolves after a delay within the specified range', async () => {
    const start = Date.now();
    await jitter(50, 100);
    const elapsed = Date.now() - start;
    expect(elapsed).toBeGreaterThanOrEqual(45);
    expect(elapsed).toBeLessThan(200);
  });
});
