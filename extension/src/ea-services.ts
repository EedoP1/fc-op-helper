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
interface EAObservable<T> {
  observe(scope: unknown, callback: (sender: unknown, data: T) => void): void;
  unobserve(scope: unknown): void;
}

/** EA pile response shape. */
interface EAPileResponse {
  items: EAItem[];
  totalResults?: number;
}

/** EA search response shape. */
interface EASearchResponse {
  items: EAItem[];
  totalResults: number;
}

/** EA bid response shape. */
interface EABidResponse {
  success: boolean;
  errorCode?: number;
  status?: number;
}

/** Price tier definition from UTCurrencyInputControl. */
interface PriceTier {
  min: number;
  inc: number;
}

/** EA search criteria instance. */
interface EASearchCriteria {
  defId: number[];
  maxBuy: number;
  minBid: number;
  type: string;
}

/** EA pile info for capacity checking. */
interface EAPileInfo {
  isFull: boolean;
}

// Declare EA globals that exist on window at runtime
declare const services: {
  Item: {
    searchTransferMarket(criteria: EASearchCriteria, page?: number): EAObservable<EASearchResponse>;
    list(item: EAItem, startBid: number, buyNow: number, duration: number): EAObservable<EABidResponse>;
    bid(item: EAItem, price: number): EAObservable<EABidResponse>;
    move(item: EAItem, pile: number): EAObservable<{ success: boolean }>;
    relistExpired(): EAObservable<{ success: boolean }>;
    clearSold(): EAObservable<{ success: boolean }>;
    refreshAuctions(items: EAItem[]): EAObservable<{ items: EAItem[] }>;
    requestTransferItems(): EAObservable<EAPileResponse>;
    requestUnassignedItems(): EAObservable<EAPileResponse>;
  };
  User: {
    getUser(): { coins: { amount: number } };
  };
};

declare const repositories: {
  Item: {
    reset(): void;
    setDirty(): void;
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
 * Convert EA's `.observe(scope, callback)` pattern to a standard Promise.
 * Automatically unobserves after the callback fires once.
 */
export function observableToPromise<T>(observable: EAObservable<T>): Promise<T> {
  return new Promise<T>((resolve) => {
    const scope = {};
    observable.observe(scope, (_sender: unknown, data: T) => {
      observable.unobserve(scope);
      resolve(data);
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
  // Fallback to lowest tier (should not happen with min: 0)
  return PRICE_TIERS[PRICE_TIERS.length - 1];
}

/**
 * Round a price to the nearest valid EA price step.
 * Clamps to MAX_PRICE. If floor is true, always rounds down; otherwise
 * rounds to nearest.
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
 * Useful for undercutting: "one step below current BIN".
 */
export function getBeforeStepValue(price: number): number {
  if (price <= 0) return 0;

  // Round down to a valid step first
  const rounded = roundToNearestStep(price, true);

  // If the price was already on a step, go one step back
  if (rounded === price) {
    // Step back by 1, then floor to the valid step in that tier
    const prev_price = price - 1;
    if (prev_price <= 0) return 0;
    return roundToNearestStep(prev_price, true);
  }

  // Price was between steps — rounding down already gave us the previous step
  return rounded;
}

// ── Market Operations ────────────────────────────────────────────────────────

/**
 * Build a UTSearchCriteriaDTO for searching by EA ID.
 * Sets defId, maxBuy, and optionally minBid for cache-busting.
 */
export function buildCriteria(
  ea_id: number,
  max_buy: number,
  min_bid?: number,
): EASearchCriteria {
  const criteria = new UTSearchCriteriaDTO();
  criteria.defId = [ea_id];
  criteria.maxBuy = max_buy;
  criteria.minBid = min_bid ?? 0;
  criteria.type = 'player';
  return criteria;
}

/**
 * Search the transfer market. Clears repository cache first to avoid stale
 * results (mirrors FUT Enhancer's approach).
 *
 * Returns items found and total result count.
 */
export async function searchMarket(
  criteria: EASearchCriteria,
  page = 0,
): Promise<{ items: EAItem[]; totalResults: number }> {
  repositories.Item.reset();
  repositories.Item.setDirty();

  const response = await observableToPromise(
    services.Item.searchTransferMarket(criteria, page),
  );

  return {
    items: response.items ?? [],
    totalResults: response.totalResults ?? 0,
  };
}

/**
 * Attempt to buy an item at the given price via bid.
 * Returns success status and optional error code.
 */
export async function buyItem(
  item: EAItem,
  price: number,
): Promise<{ success: boolean; errorCode?: number }> {
  const response = await observableToPromise(
    services.Item.bid(item, price),
  );

  return {
    success: response.success ?? false,
    errorCode: response.errorCode ?? response.status,
  };
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
): Promise<{ success: boolean; errorCode?: number }> {
  const response = await observableToPromise(
    services.Item.list(item, start_bid, buy_now, duration),
  );

  return {
    success: response.success ?? false,
    errorCode: response.errorCode ?? response.status,
  };
}

/** Relist all expired items on the transfer list. */
export async function relistAll(): Promise<{ success: boolean }> {
  return observableToPromise(services.Item.relistExpired());
}

/** Clear all sold items from the transfer list. */
export async function clearSold(): Promise<{ success: boolean }> {
  return observableToPromise(services.Item.clearSold());
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
export async function getTransferList(): Promise<TransferListResult> {
  const response = await observableToPromise<any>(
    services.Item.requestTransferItems(),
  );

  // EA's response shape varies: .data.items for some calls, .response.items for others
  const all: EAItem[] = response.data?.items ?? response.response?.items ?? response.items ?? [];

  return {
    sold: all.filter(item => item.getAuctionData().isSold()),
    expired: all.filter(item => !item.getAuctionData().isSold() && item.getAuctionData().isExpired()),
    active: all.filter(item => item.getAuctionData().isSelling()),
    unlisted: all.filter(item => item.getAuctionData().isInactive()),
    all,
  };
}

/** Fetch unassigned pile items. */
export async function getUnassigned(): Promise<EAItem[]> {
  const response = await observableToPromise(
    services.Item.requestUnassignedItems(),
  );
  return response.items ?? [];
}

/**
 * Move an item to a pile (e.g. ItemPile.TRANSFER, ItemPile.CLUB).
 * Returns false if the target pile is full (for TRANSFER and STORAGE).
 */
export async function moveItem(item: EAItem, pile: number): Promise<boolean> {
  if ((pile === ItemPile.TRANSFER || pile === ItemPile.STORAGE) && isPileFull(pile)) {
    return false;
  }
  await observableToPromise(services.Item.move(item, pile));
  return true;
}

/**
 * Refresh auction data for a list of items.
 * Returns the items with updated auction info.
 */
export async function refreshAuctions(items: EAItem[]): Promise<EAItem[]> {
  const response = await observableToPromise(
    services.Item.refreshAuctions(items),
  );
  return response.items ?? [];
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
