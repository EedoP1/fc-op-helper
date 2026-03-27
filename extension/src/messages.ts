/**
 * Shared discriminated union message types for service worker <-> content script communication.
 * Phase 6 defines PING/PONG to prove the channel works.
 * Phase 7 adds PORTFOLIO_* types for generate, confirm, swap, and load operations.
 */
import type { PortfolioPlayer, ConfirmedPortfolio } from './storage';

export type ExtensionMessage =
  | { type: 'PING' }
  | { type: 'PONG' }
  // Portfolio operations (content script -> service worker -> backend)
  | { type: 'PORTFOLIO_GENERATE'; budget: number }
  | { type: 'PORTFOLIO_GENERATE_RESULT'; data: PortfolioPlayer[]; budget_used: number; budget_remaining: number; error?: string }
  | { type: 'PORTFOLIO_CONFIRM'; players: PortfolioPlayer[] }
  | { type: 'PORTFOLIO_CONFIRM_RESULT'; confirmed: number; error?: string }
  | { type: 'PORTFOLIO_SWAP'; ea_id: number; freed_budget: number; excluded_ea_ids: number[] }
  | { type: 'PORTFOLIO_SWAP_RESULT'; replacements: PortfolioPlayer[]; error?: string }
  | { type: 'PORTFOLIO_LOAD' }
  | { type: 'PORTFOLIO_LOAD_RESULT'; portfolio: ConfirmedPortfolio | null };

/**
 * Compile-time exhaustiveness helper for switch statements over ExtensionMessage.
 * Add to the default branch of any switch on msg.type — TypeScript will emit a
 * compile error if a new message variant is added but not handled.
 */
export function assertNever(x: never): never {
  throw new Error(`Unhandled message type: ${(x as any).type}`);
}
