/**
 * Shared discriminated union message types for service worker <-> content script communication.
 * Phase 6 defines only PING/PONG to prove the channel works. Future phases (7, 8) add
 * EXECUTE_ACTION, ACTION_RESULT, STATUS_UPDATE types as needed (per D-05, D-06).
 */
export type ExtensionMessage =
  | { type: 'PING' }
  | { type: 'PONG' };

/**
 * Compile-time exhaustiveness helper for switch statements over ExtensionMessage.
 * Add to the default branch of any switch on msg.type — TypeScript will emit a
 * compile error if a new message variant is added but not handled.
 */
export function assertNever(x: never): never {
  throw new Error(`Unhandled message type: ${(x as any).type}`);
}
