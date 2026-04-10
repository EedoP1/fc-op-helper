/**
 * Content script for EA sign-in page (signin.ea.com).
 *
 * Auto-fills email/password when the algo master needs login.
 * Reads credentials from chrome.storage.local, then injects a <script>
 * tag to run jQuery-based form filling in the page's MAIN world.
 */

export default defineContentScript({
  matches: ['https://signin.ea.com/*'],
  runAt: 'document_idle',
  main() {
    console.log('[OP Seller Signin] Content script loaded');

    // Check if algo master needs us to log in
    chrome.storage.local.get(['algoMasterState', 'algoCredentials'], (stored: Record<string, any>) => {
      const state = stored.algoMasterState as { status: string } | undefined;
      const creds = stored.algoCredentials as { email: string; password: string } | undefined;

      console.log('[OP Seller Signin] Master state:', state?.status, 'Has creds:', !!creds);

      if (!state || !creds) return;
      if (state.status !== 'SPAWNING' && state.status !== 'RECOVERING') return;

      // Wait a moment for the page to fully render
      setTimeout(() => {
        fillAndSubmit(creds.email, creds.password);
      }, 2000);
    });

    function fillAndSubmit(email: string, password: string) {
      // Check which step we're on
      const passwordInput = document.querySelector('input[type="password"]');
      const emailInput = document.querySelector('input[placeholder*="email"]');

      if (passwordInput) {
        // Step 2: Password page
        console.log('[OP Seller Signin] On password page — filling password');
        injectMainWorldScript(`
          (function() {
            var jq = window.$ || window.jQuery;
            if (!jq) { console.error('[OP Seller Signin] No jQuery'); return; }
            jq('input[type="password"]').val(${JSON.stringify(password)}).trigger('input').trigger('change');
            setTimeout(function() {
              jq('#logInBtn').trigger('mousedown').trigger('mouseup').trigger('click');
            }, 500);
          })();
        `);
      } else if (emailInput) {
        // Step 1: Email page
        console.log('[OP Seller Signin] On email page — filling email');
        injectMainWorldScript(`
          (function() {
            var jq = window.$ || window.jQuery;
            if (!jq) { console.error('[OP Seller Signin] No jQuery'); return; }
            jq('#email').val(${JSON.stringify(email)}).trigger('input').trigger('change');
            setTimeout(function() {
              jq('#logInBtn').trigger('mousedown').trigger('mouseup').trigger('click');
            }, 500);
          })();
        `);
      } else {
        console.log('[OP Seller Signin] No email or password input found');
      }
    }

    function injectMainWorldScript(code: string) {
      const script = document.createElement('script');
      script.textContent = code;
      document.documentElement.appendChild(script);
      script.remove();
    }
  },
});
