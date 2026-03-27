/**
 * AUTO-08: Centralized selector map for EA Web App DOM elements.
 * All selectors discovered via live DevTools inspection (D-04).
 * Phase 8 extends this file with automation click-target selectors.
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
 */

/** Container element wrapping the entire transfer list / trade pile view */
export const TRANSFER_LIST_CONTAINER = '.ut-transfer-list-view';

/** Individual player card/row in the transfer list */
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
