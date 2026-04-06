/**
 * Unit tests for price tier utilities in ea-services.ts.
 *
 * Tests cover:
 *   - findTier: correct tier selection for each price range
 *   - roundToNearestStep: rounding for each tier, MAX_PRICE clamping, floor param
 *   - getBeforeStepValue: same-tier steps and tier boundary crossing
 */

import { describe, it, expect } from 'vitest';
import {
  findTier,
  roundToNearestStep,
  getBeforeStepValue,
  MAX_PRICE,
  PRICE_TIERS,
} from '../src/ea-services';

// ── findTier ─────────────────────────────────────────────────────────────────

describe('findTier', () => {
  it('returns inc=1000 for prices >= 100,000', () => {
    expect(findTier(100_000).inc).toBe(1_000);
    expect(findTier(500_000).inc).toBe(1_000);
    expect(findTier(14_999_000).inc).toBe(1_000);
  });

  it('returns inc=500 for prices 50,000–99,999', () => {
    expect(findTier(50_000).inc).toBe(500);
    expect(findTier(75_000).inc).toBe(500);
    expect(findTier(99_999).inc).toBe(500);
  });

  it('returns inc=250 for prices 10,000–49,999', () => {
    expect(findTier(10_000).inc).toBe(250);
    expect(findTier(25_000).inc).toBe(250);
    expect(findTier(49_999).inc).toBe(250);
  });

  it('returns inc=100 for prices 1,000–9,999', () => {
    expect(findTier(1_000).inc).toBe(100);
    expect(findTier(5_000).inc).toBe(100);
    expect(findTier(9_999).inc).toBe(100);
  });

  it('returns inc=50 for prices 150–999', () => {
    expect(findTier(150).inc).toBe(50);
    expect(findTier(500).inc).toBe(50);
    expect(findTier(999).inc).toBe(50);
  });

  it('returns inc=150 for prices 0–149', () => {
    expect(findTier(0).inc).toBe(150);
    expect(findTier(100).inc).toBe(150);
    expect(findTier(149).inc).toBe(150);
  });
});

// ── roundToNearestStep ───────────────────────────────────────────────────────

describe('roundToNearestStep', () => {
  it('returns 0 for zero or negative prices', () => {
    expect(roundToNearestStep(0)).toBe(0);
    expect(roundToNearestStep(-100)).toBe(0);
  });

  it('clamps to MAX_PRICE', () => {
    expect(roundToNearestStep(15_000_000)).toBe(MAX_PRICE);
    expect(roundToNearestStep(20_000_000)).toBe(MAX_PRICE);
    expect(roundToNearestStep(MAX_PRICE)).toBe(MAX_PRICE);
  });

  // Tier: inc=1000 (>= 100,000)
  it('rounds to nearest 1000 for prices >= 100,000', () => {
    expect(roundToNearestStep(100_400)).toBe(100_000);
    expect(roundToNearestStep(100_500)).toBe(101_000);
    expect(roundToNearestStep(100_700)).toBe(101_000);
    expect(roundToNearestStep(150_000)).toBe(150_000);
  });

  it('floors to 1000 for prices >= 100,000 with floor=true', () => {
    expect(roundToNearestStep(100_999, true)).toBe(100_000);
    expect(roundToNearestStep(101_500, true)).toBe(101_000);
  });

  // Tier: inc=500 (50,000–99,999)
  it('rounds to nearest 500 for prices 50,000–99,999', () => {
    expect(roundToNearestStep(50_200)).toBe(50_000);
    expect(roundToNearestStep(50_250)).toBe(50_500);
    expect(roundToNearestStep(50_400)).toBe(50_500);
  });

  it('floors to 500 for prices 50,000–99,999 with floor=true', () => {
    expect(roundToNearestStep(50_499, true)).toBe(50_000);
    expect(roundToNearestStep(99_999, true)).toBe(99_500);
  });

  // Tier: inc=250 (10,000–49,999)
  it('rounds to nearest 250 for prices 10,000–49,999', () => {
    expect(roundToNearestStep(10_100)).toBe(10_000);
    expect(roundToNearestStep(10_125)).toBe(10_250);
    expect(roundToNearestStep(10_200)).toBe(10_250);
  });

  // Tier: inc=100 (1,000–9,999)
  it('rounds to nearest 100 for prices 1,000–9,999', () => {
    expect(roundToNearestStep(1_050)).toBe(1_100);
    expect(roundToNearestStep(1_049)).toBe(1_000);
    expect(roundToNearestStep(5_000)).toBe(5_000);
  });

  // Tier: inc=50 (150–999)
  it('rounds to nearest 50 for prices 150–999', () => {
    expect(roundToNearestStep(175)).toBe(200);
    expect(roundToNearestStep(174)).toBe(150);
    expect(roundToNearestStep(200)).toBe(200);
  });

  // Tier: inc=150 (0–149)
  it('rounds to nearest 150 for prices 1–149', () => {
    expect(roundToNearestStep(74)).toBe(0);
    expect(roundToNearestStep(75)).toBe(150);
    expect(roundToNearestStep(100)).toBe(150);
    expect(roundToNearestStep(149)).toBe(150);
  });

  it('handles exact tier boundary prices', () => {
    expect(roundToNearestStep(150)).toBe(150);
    expect(roundToNearestStep(1_000)).toBe(1_000);
    expect(roundToNearestStep(10_000)).toBe(10_000);
    expect(roundToNearestStep(50_000)).toBe(50_000);
    expect(roundToNearestStep(100_000)).toBe(100_000);
  });
});

// ── getBeforeStepValue ───────────────────────────────────────────────────────

describe('getBeforeStepValue', () => {
  it('returns 0 for zero or negative prices', () => {
    expect(getBeforeStepValue(0)).toBe(0);
  });

  // Same-tier steps
  it('steps back within the 1000-inc tier', () => {
    expect(getBeforeStepValue(102_000)).toBe(101_000);
    expect(getBeforeStepValue(200_000)).toBe(199_000);
  });

  it('steps back within the 500-inc tier', () => {
    expect(getBeforeStepValue(51_000)).toBe(50_500);
    expect(getBeforeStepValue(75_000)).toBe(74_500);
  });

  it('steps back within the 250-inc tier', () => {
    expect(getBeforeStepValue(10_250)).toBe(10_000);
    expect(getBeforeStepValue(25_000)).toBe(24_750);
  });

  it('steps back within the 100-inc tier', () => {
    expect(getBeforeStepValue(1_100)).toBe(1_000);
    expect(getBeforeStepValue(5_000)).toBe(4_900);
  });

  it('steps back within the 50-inc tier', () => {
    expect(getBeforeStepValue(200)).toBe(150);
    expect(getBeforeStepValue(500)).toBe(450);
  });

  // Tier boundary crossing
  it('crosses from 1000-inc tier to 500-inc tier', () => {
    // At 100,000 (first step in 1000-inc tier), stepping back should go to 99,500
    expect(getBeforeStepValue(100_000)).toBe(99_500);
  });

  it('crosses from 500-inc tier to 250-inc tier', () => {
    expect(getBeforeStepValue(50_000)).toBe(49_750);
  });

  it('crosses from 250-inc tier to 100-inc tier', () => {
    expect(getBeforeStepValue(10_000)).toBe(9_900);
  });

  it('crosses from 100-inc tier to 50-inc tier', () => {
    expect(getBeforeStepValue(1_000)).toBe(950);
  });

  it('crosses from 50-inc tier to 150-inc tier', () => {
    expect(getBeforeStepValue(150)).toBe(0);
  });

  it('handles non-step-aligned prices by flooring', () => {
    // 10,123 is between steps (10,000 and 10,250) — should return 10,000
    expect(getBeforeStepValue(10_123)).toBe(10_000);
    // 1,050 is between 1,000 and 1,100 — should return 1,000
    expect(getBeforeStepValue(1_050)).toBe(1_000);
  });
});
