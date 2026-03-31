/**
 * AUTO-08: Centralized selector map for EA Web App DOM elements.
 * All selectors discovered via live DevTools inspection (D-04).
 * Phase 8 extends this file with automation click-target selectors.
 *
 * IMPORTANT: These are NATIVE EA Web App selectors only.
 * Do NOT add selectors for FC Enhancer, FUTNEXT, or other extensions.
 *
 * DOM structure (FC26 Web App):
 *   .ut-transfer-list-view > .ut-sectioned-item-list-view > ul.itemList > li.listFUTItem
 *   Each .listFUTItem contains:
 *     .entityContainer > .player (card visual with .name for player name)
 *     .auction > .auctionStartPrice .value (start price)
 *                .auctionValue:nth-child(3) .value (BIN price)
 *                .auction-state .time (status text: "Expired", "55 Minutes", etc.)
 *   Sold items have class "won" on the .listFUTItem.
 *   The card's .currency-coins.item-slot-price shows the price on the card visual.
 *
 * Click note: EA Web App ignores programmatic .click() on most buttons.
 * Use the full pointer event sequence instead (see eaClick helper in automation.ts):
 *   el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
 *   el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
 *   el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
 *   el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
 *   el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
 * This was verified live: search, buy, list, clear sold, re-list all confirmed working.
 */

// ── Transfer List (Phase 7.1 — verified live) ────────────────────────

/** Container element wrapping the entire transfer list / trade pile view */
export const TRANSFER_LIST_CONTAINER = '.ut-transfer-list-view';

/** Individual player card/row in the transfer list or search results */
export const TRANSFER_LIST_ITEM = '.listFUTItem';

/**
 * Status label on a player card.
 * Shows "Expired" for unsold, time remaining (e.g. "55 Minutes") for active,
 * or "Expired" for sold items that were won via bid.
 * Parent .auction-state contains the full state; .time is the human-readable text.
 */
export const ITEM_STATUS_LABEL = '.auction-state .time';

/** Player name text element within a card */
export const ITEM_PLAYER_NAME = '.name';

/** BIN (Buy It Now) price displayed in the auction info section */
export const ITEM_BIN_PRICE = '.auction .auctionValue:nth-child(3) .value';

/** Start price / current bid displayed in the auction info section */
export const ITEM_START_PRICE = '.auctionStartPrice .value';

/** Player overall rating number on the card */
export const ITEM_RATING = '.rating';

/** Player position abbreviation on the card (e.g. "CAM", "ST") */
export const ITEM_POSITION = '.position';

// ── Navigation (Phase 8 — verified live) ─────────────────────────────

/** Sidebar navigation bar containing all nav buttons */
export const NAV_SIDEBAR = '.ut-tab-bar-view';

/** Individual nav button (append .icon-transfer, .icon-home, etc. to target specific) */
export const NAV_ITEM = '.ut-tab-bar-item';

/** Transfers nav button — click to open Transfers hub */
export const NAV_TRANSFERS = '.ut-tab-bar-item.icon-transfer';

/** Back button (◀) in the top-left header area */
export const NAV_BACK_BUTTON = '.ut-navigation-button-control';

// ── Transfers Hub (Phase 8 — verified live) ──────────────────────────

/** "Search the Transfer Market" tile on the Transfers hub page */
export const TILE_SEARCH_MARKET = '.ut-tile-transfer-market';

/** "Transfer List" tile on the Transfers hub page */
export const TILE_TRANSFER_LIST = '.ut-tile-transfer-list';

// ── Transfer Market Search (Phase 8 — verified live) ─────────────────

/** Player name text input on the search page */
export const SEARCH_PLAYER_NAME_INPUT = '.ut-player-search-control input';

/**
 * Player autocomplete suggestion list (appears after typing in name input).
 * Contains <button> children; click the one matching the desired player name.
 */
export const SEARCH_PLAYER_SUGGESTIONS = '.playerResultsList';

/**
 * All search filter dropdowns (Quality, Evolution Status, Rarity, Position, etc.).
 * They are indexed 0-8 in DOM order:
 *   0=Quality, 1=EvolutionStatus, 2=Rarity, 3=Position, 4=ChemistryStyle,
 *   5=Country/Region, 6=League, 7=Club, 8=PlayStyles
 * To open: eaClick the .ut-search-filter-control--row child.
 * To select: eaClick the matching <li> in the open dropdown.
 * Quality options: Any, Bronze, Silver, Gold, Special
 * Rarity options: Any, Common, Rare, + 50+ special rarities
 */
export const SEARCH_FILTER_DROPDOWN = '.inline-list-select.ut-search-filter-control';

/**
 * The clickable target to open/close a dropdown filter.
 * IMPORTANT: .ut-search-filter-control--row does NOT respond to eaClick.
 * Use .inline-container instead — verified working on Rarity dropdown.
 */
export const SEARCH_FILTER_CLICKABLE = '.inline-container';

/** The search filter view container (holds all dropdowns and price inputs) */
export const SEARCH_FILTERS_VIEW = '.ut-market-search-filters-view';

/**
 * Price section containers within .search-prices.
 * Index 0 = Bid Price, Index 1 = Buy Now Price.
 * Each contains .price-filter divs with Min/Max inputs.
 */
export const SEARCH_PRICE_SECTION = '.search-prices .ut-market-search-filters-view--criteria-container';

/** Individual price filter row (contains a .label and an input) */
export const SEARCH_PRICE_FILTER = '.price-filter';

/** Numeric price input within a price filter row */
export const SEARCH_PRICE_INPUT = '.price-filter input.ut-number-input-control';

/**
 * Native EA Search button.
 * Located in .button-container, class "btn-standard primary", text "Search".
 * NOTE: .click() does NOT work — use PointerEvent dispatch.
 */
export const SEARCH_SUBMIT_BUTTON = '.button-container > button.btn-standard.primary';

/** Reset button to clear all search filters */
export const SEARCH_RESET_BUTTON = '.button-container > button.btn-standard:not(.primary)';

// ── Search Results (Phase 8 — verified live) ─────────────────────────

/** Paginated search results container */
export const SEARCH_RESULTS_LIST = '.paginated-item-list.ut-pinned-list';

/** Currently selected result item */
export const SEARCH_RESULT_SELECTED = '.listFUTItem.selected';

/** Expired auction item */
export const SEARCH_RESULT_EXPIRED = '.listFUTItem.expired';

/** Next page button in search results or transfer list pagination */
export const PAGINATION_NEXT = 'button.pagination.next';

/** Previous page button */
export const PAGINATION_PREV = 'button.pagination.prev';

// ── Detail Panel — Buy Actions (Phase 8 — verified live) ─────────────

/** Detail panel container (right side when an item is selected) */
export const DETAIL_PANEL = '.DetailPanel';

/** Auction info block in the detail panel (contains Time Remaining, prices) */
export const DETAIL_AUCTION_INFO = '.auctionInfo';

/** Bid options container (holds bid spinner, bid button, buy button) */
export const DETAIL_BID_OPTIONS = '.bidOptions';

/**
 * "Buy Now" button in the detail panel.
 * Class: btn-standard buyButton currency-coins
 * Text: "Buy Now for {price}"
 * Disabled when item is expired or already bought.
 */
export const BUY_NOW_BUTTON = 'button.buyButton';

/** "Make Bid" button in the detail panel */
export const MAKE_BID_BUTTON = 'button.bidButton';

/** Bid amount input spinner in the detail panel */
export const BID_AMOUNT_SPINNER = '.bidOptions .ut-numeric-input-spinner-control';

// ── EA Dialogs / Confirmations (Phase 8 — verified live) ─────────────

/**
 * EA native dialog container.
 * Used for buy confirmations, error messages, session warnings.
 * Check visibility: dialog.offsetParent !== null
 */
export const EA_DIALOG = '.ea-dialog-view';

/** Dialog title text (e.g. "Buy Now", "Resize Window") */
export const EA_DIALOG_TITLE = '.ea-dialog-view--title';

/** Dialog body message text */
export const EA_DIALOG_MESSAGE = '.ea-dialog-view--msg';

/** Button group inside a dialog (contains confirm/cancel buttons) */
export const EA_DIALOG_BUTTONS = '.ea-dialog-view .ut-st-button-group';

/**
 * Dialog button ordering is NOT consistent across dialogs:
 *   Buy confirm: [Ok (primary), Cancel (text)]  — Ok is first
 *   Re-list All: [Cancel (text), Yes (primary)]  — Yes is last
 * Always match by text content ("Ok", "Yes") or by .primary class,
 * never rely on :first-child / :last-child ordering.
 */
export const EA_DIALOG_PRIMARY_BUTTON = '.ea-dialog-view .ut-st-button-group button.btn-standard.primary';

// ── Transfer List Sections (Phase 8 — verified live) ─────────────────

/**
 * Transfer list section container.
 * Sections: "Sold Items", "Unsold Items", "Available Items", "Active Transfers"
 * Each is a .ut-sectioned-item-list-view inside .ut-transfer-list-view.
 */
export const TL_SECTION = '.ut-transfer-list-view section';

/** Section title element (h2.title) — text matches section name */
export const TL_SECTION_TITLE = 'h2.title';

/**
 * "Clear Sold" button — inside the "Sold Items" section header.
 * Class: btn-standard section-header-btn mini primary
 */
export const TL_CLEAR_SOLD = '.ut-transfer-list-view .section-header-btn';

/**
 * "Re-list All" button — inside the "Unsold Items" section header.
 * Same class as Clear Sold; distinguished by parent section's h2.title text.
 * Use findSectionButton('Unsold Items') helper to locate.
 */
export const TL_RELIST_ALL_CLASS = 'btn-standard section-header-btn mini primary';

/** "Remove" button in the detail panel (for sold items) */
export const TL_REMOVE_BUTTON = '.DetailPanel button.btn-standard.primary';

// ── Quick List Panel — Post-Buy Listing (Phase 8 — verified live) ────

/**
 * Quick list panel container (appears after clicking "List on Transfer Market").
 * Triggered by eaClick on the accordion button with text "List on Transfer Market".
 */
export const QUICK_LIST_PANEL = '.ut-quick-list-panel-view';

/**
 * Price inputs inside the quick list panel.
 * querySelectorAll returns [0]=Start Price, [1]=BIN Price.
 */
export const QUICK_LIST_PRICE_INPUTS = '.ut-quick-list-panel-view input.ut-number-input-control';

/** Duration dropdown in the quick list panel */
export const QUICK_LIST_DURATION = '.ut-quick-list-panel-view .ut-drop-down-control';

/**
 * Native "List for Transfer" button.
 * Class: btn-standard primary (WITHOUT call-to-action — those are Enhancer buttons).
 * Match by text content "List for Transfer" to avoid Enhancer buttons.
 */
export const QUICK_LIST_CONFIRM_CLASS = 'btn-standard primary';

/**
 * "List on Transfer Market" accordion toggle in the detail panel.
 * Click to expand the listing panel. Class: "accordian" (EA's typo, not ours).
 */
export const LIST_ON_MARKET_ACCORDION = 'button.accordian';

// ── Notifications & Session (Phase 8 — verified live) ────────────────

/** Notification layer container (shows toast messages for errors, success, etc.) */
export const NOTIFICATION_LAYER = '#NotificationLayer';

/** Login view — appears when session expires and user needs to re-authenticate */
export const SESSION_LOGIN_VIEW = '.ut-login-view';
