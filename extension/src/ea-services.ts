/**
 * EA Service Layer — the ONLY module that references EA's internal globals.
 *
 * EA's FC Web App exposes globals on `window` at runtime:
 *   - services.Item, services.User — service singletons
 *   - repositories.Item — item repository
 *   - ItemPile — pile name constants
 *   - UTSearchCriteriaDTO — search criteria constructor
 *   - UTCurrencyInputControl — price tier definitions
 *
 * All other automation code imports from this module instead of touching
 * EA globals directly. This mirrors the approach used by FUT Enhancer.
 */

// ── EA Ambient Type Declarations ─────────────────────────────────────────────

/** Auction data attached to an EA item. */
export interface EAAuctionData {
  readonly buyNowPrice: number;
  readonly currentBid: number;
  readonly startingBid: number;
  readonly tradeId: number;
  readonly expires: number;
  isSold(): boolean;
  isExpired(): boolean;
  isSelling(): boolean;
  isInactive(): boolean;
}

/** EA item representation. */
export interface EAItem {
  readonly definitionId: number;
  readonly id: number;
  readonly resourceId: number;
  readonly type: string;
  readonly rating: number;
  readonly lastSalePrice: number;
  readonly _staticData: {
    readonly name: string;
    readonly firstName: string;
    readonly lastName: string;
  };
  getAuctionData(): EAAuctionData;
}

/** EA observable — the pattern EA uses for async callbacks. */
interface EAObservable {
  observe(scope: unknown, callback: (sender: any, response: any) => void): void;
  unobserve(scope: unknown): void;
}

/** Price tier definition from UTCurrencyInputControl. */
interface PriceTier {
  min: number;
  inc: number;
}

// Declare EA globals that exist on window at runtime
declare const services: {
  Item: {
    searchTransferMarket(criteria: any, page?: number): EAObservable;
    clearTransferMarketCache(): void;
    list(item: EAItem, startBid: number, buyNow: number, duration: number): EAObservable;
    bid(item: EAItem, price: number): EAObservable;
    move(item: EAItem, pile: number): EAObservable;
    relistExpiredAuctions(): EAObservable;
    clearSoldItems(): EAObservable;
    refreshAuctions(items: EAItem[]): EAObservable;
    requestTransferItems(): EAObservable;
    requestUnassignedItems(): EAObservable;
  };
  User: {
    getUser(): { coins: { amount: number } };
  };
};

declare const repositories: {
  Item: {
    isPileFull(pile: number): boolean;
  };
};

declare const ItemPile: {
  ANY: number;
  TRANSFER: number;
  PURCHASED: number;
  CLUB: number;
  INBOX: number;
  GIFT: number;
  STORAGE: number;
  EVOLUTION: number;
};

declare class UTSearchCriteriaDTO {
  defId: number[];
  maxBuy: number;
  minBid: number;
  type: string;
}

declare const UTCurrencyInputControl: {
  PRICE_TIERS: PriceTier[];
};

// ── Constants ────────────────────────────────────────────────────────────────

/** Maximum price allowed by EA's transfer market. */
export const MAX_PRICE = 14_999_000;

/**
 * EA's price tiers, sorted descending by min for lookup.
 * Defined here so tests can use the same values without needing the EA global.
 */
export const PRICE_TIERS: PriceTier[] = [
  { min: 100_000, inc: 1_000 },
  { min: 50_000, inc: 500 },
  { min: 10_000, inc: 250 },
  { min: 1_000, inc: 100 },
  { min: 150, inc: 50 },
  { min: 0, inc: 150 },
];

// ── Observable-to-Promise Utility ────────────────────────────────────────────

/**
 * Result from any EA service call. Mirrors FUT Enhancer's pattern exactly:
 *   const {data, error, status, success} = await observableToPromise(...)
 *
 * Always resolves — never rejects. Callers check `success` and `error`.
 */
export interface EAResponse<T = any> {
  data: T | null;
  error: number | undefined;
  status: number | undefined;
  success: boolean;
}

/**
 * Convert EA's `.observe(scope, callback)` pattern to a standard Promise.
 * Returns {data, error, status, success} — same shape as FUT Enhancer.
 * Always resolves, never rejects. Callers check success/error per-call.
 */
/**
 * FUT Enhancer uses `this` (module scope) as the observer context, not a fresh object.
 * In their minified code: `e.observe(this, (r, {...}) => { r.unobserve(this); ... })`
 * We use a module-level object to match this pattern.
 */
const OBSERVER_SCOPE = {};

export function observableToPromise<T = any>(observable: EAObservable): Promise<EAResponse<T>> {
  return new Promise((resolve) => {
    observable.observe(OBSERVER_SCOPE, (sender: any, response: any) => {
      sender.unobserve(OBSERVER_SCOPE);
      resolve({
        data: response.response ?? response.data ?? null,
        error: response.error?.code,
        status: response.status,
        success: response.success ?? false,
      });
    });
  });
}

// ── Price Tier Utilities ─────────────────────────────────────────────────────

/**
 * Find which price tier applies for a given price.
 * Tiers are searched descending by min — first tier where price >= min wins.
 */
export function findTier(price: number): PriceTier {
  for (const tier of PRICE_TIERS) {
    if (price >= tier.min) {
      return tier;
    }
  }
  return PRICE_TIERS[PRICE_TIERS.length - 1];
}

/**
 * Round a price to the nearest valid EA price step.
 * Clamps to MAX_PRICE. If floor is true, always rounds down.
 */
export function roundToNearestStep(price: number, floor = false): number {
  if (price <= 0) return 0;
  if (price >= MAX_PRICE) return MAX_PRICE;

  const tier = findTier(price);
  const inc = tier.inc;

  if (floor) {
    return Math.floor(price / inc) * inc;
  }
  return Math.round(price / inc) * inc;
}

/**
 * Get the previous valid price step below the given price.
 */
export function getBeforeStepValue(price: number): number {
  if (price <= 0) return 0;

  const rounded = roundToNearestStep(price, true);

  if (rounded === price) {
    const prev_price = price - 1;
    if (prev_price <= 0) return 0;
    return roundToNearestStep(prev_price, true);
  }

  return rounded;
}

// ── Market Operations ────────────────────────────────────────────────────────

/**
 * Build a UTSearchCriteriaDTO for searching by EA ID.
 */
export function buildCriteria(
  ea_id: number,
  max_buy: number,
  min_bid?: number,
): any {
  const criteria = new UTSearchCriteriaDTO();
  criteria.defId = [ea_id];
  criteria.maxBuy = max_buy;
  criteria.minBid = min_bid ?? 0;
  criteria.type = 'player';
  return criteria;
}

/**
 * Search the transfer market. Clears cache first for fresh results.
 * Returns {items, totalResults, success, error}.
 */
export async function searchMarket(
  criteria: any,
  page = 1,
): Promise<{ items: EAItem[]; totalResults: number; success: boolean; error?: number }> {
  services.Item.clearTransferMarketCache();

  const result = await observableToPromise(
    services.Item.searchTransferMarket(criteria, page),
  );
  console.log('[ea-services] searchMarket response:', JSON.stringify({ success: result.success, error: result.error, status: result.status, itemCount: result.data?.items?.length ?? 0, dataKeys: result.data ? Object.keys(result.data) : [] }));

  return {
    items: result.data?.items ?? [],
    totalResults: result.data?.count ?? result.data?.totalResults ?? 0,
    success: result.success,
    error: result.error,
  };
}

/**
 * Attempt to buy an item at the given price via BIN.
 * Error code 461 = sniped. Check success first, then error for specifics.
 */
export async function buyItem(
  item: EAItem,
  price: number,
): Promise<{ success: boolean; error?: number }> {
  const result = await observableToPromise(
    services.Item.bid(item, price),
  );
  console.log('[ea-services] buyItem response:', JSON.stringify({ success: result.success, error: result.error, status: result.status, hasData: !!result.data }));
  return { success: result.success, error: result.error };
}

/**
 * List an item on the transfer market.
 * Duration is in seconds — defaults to 3600 (1 hour).
 */
export async function listItem(
  item: EAItem,
  start_bid: number,
  buy_now: number,
  duration = 3600,
): Promise<{ success: boolean; error?: number }> {
  console.log('[ea-services] listItem params:', JSON.stringify({
    defId: item.definitionId,
    startBid: start_bid,
    buyNow: buy_now,
    duration,
    isSelling: item.getAuctionData().isSelling(),
    isInactive: item.getAuctionData().isInactive(),
    isExpired: item.getAuctionData().isExpired(),
    tradeId: item.getAuctionData().tradeId,
  }));
  const { success, error } = await observableToPromise(
    services.Item.list(item, start_bid, buy_now, duration),
  );
  console.log('[ea-services] listItem result:', JSON.stringify({ success, error }));
  return { success, error };
}

/** Relist all expired items on the transfer list. */
export async function relistAll(): Promise<{ success: boolean; error?: number }> {
  const { success, error } = await observableToPromise(
    services.Item.relistExpiredAuctions(),
  );
  return { success, error };
}

/** Clear all sold items from the transfer list. */
export async function clearSold(): Promise<{ success: boolean; error?: number }> {
  const { success, error } = await observableToPromise(
    services.Item.clearSoldItems(),
  );
  return { success, error };
}

// ── Pile Operations ──────────────────────────────────────────────────────────

/** Transfer list item categories. */
export interface TransferListResult {
  sold: EAItem[];
  expired: EAItem[];
  active: EAItem[];
  unlisted: EAItem[];
  all: EAItem[];
}

/**
 * Fetch the transfer list and categorize items by auction state.
 * Uses EA's getAuctionData() methods for accurate categorization.
 */
export async function getTransferList(): Promise<{ groups: TransferListResult; success: boolean; error?: number }> {
  const { data, success, error } = await observableToPromise(
    services.Item.requestTransferItems(),
  );

  const all: EAItem[] = data?.items ?? [];

  return {
    groups: {
      sold: all.filter(item => item.getAuctionData().isSold()),
      expired: all.filter(item => !item.getAuctionData().isSold() && item.getAuctionData().isExpired()),
      active: all.filter(item => item.getAuctionData().isSelling()),
      unlisted: all.filter(item => item.getAuctionData().isInactive()),
      all,
    },
    success,
    error,
  };
}

/** Fetch unassigned pile items. */
export async function getUnassigned(): Promise<{ items: EAItem[]; success: boolean; error?: number }> {
  const { data, success, error } = await observableToPromise(
    services.Item.requestUnassignedItems(),
  );
  return { items: data?.items ?? [], success, error };
}

/**
 * Move an item to a pile (e.g. ItemPile.TRANSFER, ItemPile.CLUB).
 * Returns false if the target pile is full (for TRANSFER and STORAGE).
 */
export async function moveItem(item: EAItem, pile: number): Promise<{ success: boolean; error?: number }> {
  if ((pile === ItemPile.TRANSFER || pile === ItemPile.STORAGE) && isPileFull(pile)) {
    return { success: false, error: undefined };
  }
  const { success, error } = await observableToPromise(
    services.Item.move(item, pile),
  );
  return { success, error };
}

/**
 * Refresh auction data for a list of items.
 */
export async function refreshAuctions(items: EAItem[]): Promise<{ success: boolean; error?: number }> {
  if (items.length === 0) return { success: true, error: undefined };
  const { success, error } = await observableToPromise(
    services.Item.refreshAuctions(items),
  );
  return { success, error };
}

// ── User Data ────────────────────────────────────────────────────────────────

/** Get the current coin balance. */
export function getCoins(): number {
  return services.User.getUser().coins.amount;
}

/**
 * Check if a pile is at capacity.
 * Delegates to EA's repositories.Item.isPileFull().
 */
export function isPileFull(pile: number): boolean {
  return repositories.Item.isPileFull(pile);
}
